"""
Test signal handlers for program_enrollments
"""


from unittest import mock

import ddt
import pytest
from django.core.cache import cache
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey
from organizations.tests.factories import OrganizationFactory
from social_django.models import UserSocialAuth
from testfixtures import LogCapture

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollmentException
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.third_party_auth.models import SAMLProviderConfig
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from lms.djangoapps.program_enrollments.signals import _listen_for_lms_retire, logger
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory
from openedx.core.djangoapps.catalog.cache import PROGRAM_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.tests.factories import OrganizationFactory as CatalogOrganizationFactory
from openedx.core.djangoapps.catalog.tests.factories import ProgramFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import fake_completed_retirement
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class ProgramEnrollmentRetireSignalTests(ModuleStoreTestCase):
    """
    Test the _listen_for_lms_retire signal
    """

    def create_enrollment_and_history(self, user=None, external_user_key='defaultExternalKey'):
        """
        Create ProgramEnrollment and several History entries
        """
        if user:
            enrollment = ProgramEnrollmentFactory(user=user, external_user_key=external_user_key)
        else:
            enrollment = ProgramEnrollmentFactory(external_user_key=external_user_key)
        for status in ['pending', 'suspended', 'canceled', 'enrolled']:
            enrollment.status = status
            enrollment.save()
        return enrollment

    def assert_enrollment_and_history_retired(self, enrollment):
        """
        Assert that for the enrollment and all histories, external key is None
        """
        enrollment.refresh_from_db()
        assert enrollment.external_user_key is not None
        assert enrollment.external_user_key.startswith('retired_external_key')
        for history_record in enrollment.historical_records.all():
            assert history_record.external_user_key is not None
            assert history_record.external_user_key.startswith('retired_external_key')

    def test_retire_enrollment(self):
        """
        Test basic retirement of program enrollment
        """
        enrollment = self.create_enrollment_and_history()
        _listen_for_lms_retire(sender=self.__class__, user=enrollment.user)
        self.assert_enrollment_and_history_retired(enrollment)

    def test_retire_enrollment_multiple(self):
        """
        Test basic retirement of user with multiple program enrollments
        """
        enrollment = self.create_enrollment_and_history()
        enrollment2 = self.create_enrollment_and_history(user=enrollment.user)
        enrollment3 = self.create_enrollment_and_history(user=enrollment.user)
        _listen_for_lms_retire(sender=self.__class__, user=enrollment.user)
        self.assert_enrollment_and_history_retired(enrollment)
        self.assert_enrollment_and_history_retired(enrollment2)
        self.assert_enrollment_and_history_retired(enrollment3)

    def test_success_no_enrollment(self):
        """
        Basic success path for users who have no enrollments, should simply not error
        """
        user = UserFactory()
        _listen_for_lms_retire(sender=self.__class__, user=user)

    def test_idempotent(self):
        """
        Tests that running a retirement multiple times does not throw an error
        """
        enrollment = self.create_enrollment_and_history()

        # Run twice to make sure no errors are raised
        _listen_for_lms_retire(sender=self.__class__, user=enrollment.user)
        fake_completed_retirement(enrollment.user)
        _listen_for_lms_retire(sender=self.__class__, user=enrollment.user)

        self.assert_enrollment_and_history_retired(enrollment)

    def test_retire_signal_handler_tolerates_extra_kwargs(self):
        """
        Ensure program_enrollments retirement receiver does not break with additional kwargs.
        """
        enrollment = self.create_enrollment_and_history()

        # Simulate the signal being sent with extra kwargs.
        _listen_for_lms_retire(
            sender=self.__class__,
            user=enrollment.user,
            retired_username='retired-test-username',
            retired_email='retired-test@example.com',
        )

        self.assert_enrollment_and_history_retired(enrollment)


@ddt.ddt
class SocialAuthEnrollmentCompletionSignalTest(CacheIsolationTestCase):
    """
    Test post-save handler on UserSocialAuth
    """
    ENABLED_CACHES = ['default']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.external_id = 'learner1a'
        cls.provider_slug = 'uox'
        cls.course_keys = [
            CourseKey.from_string('course-v1:edX+DemoX+Test_Course'),
            CourseKey.from_string('course-v1:edX+DemoX+Another_Test_Course'),
        ]
        cls.organization = OrganizationFactory.create(
            short_name='UoX'
        )
        cls.user = UserFactory.create()

        for course_key in cls.course_keys:
            CourseOverviewFactory(id=course_key)
        cls.provider_config = SAMLProviderConfigFactory.create(
            organization=cls.organization, slug=cls.provider_slug
        )

    def setUp(self):
        super().setUp()
        RequestCache.clear_all_namespaces()
        catalog_org = CatalogOrganizationFactory.create(key=self.organization.short_name)
        self.program_uuid = self._create_catalog_program(catalog_org)['uuid']

    def _create_catalog_program(self, catalog_org):
        """ helper method to create a cached catalog program """
        program = ProgramFactory.create(
            authoring_organizations=[catalog_org]
        )
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']), program, None)
        return program

    def _create_waiting_program_enrollment(self):
        """ helper method to create a waiting program enrollment """
        return ProgramEnrollmentFactory.create(
            user=None,
            external_user_key=self.external_id,
            program_uuid=self.program_uuid,
        )

    def _create_waiting_course_enrollments(self, program_enrollment):
        """ helper method to create waiting course enrollments """
        return [
            ProgramCourseEnrollmentFactory(
                program_enrollment=program_enrollment,
                course_enrollment=None,
                course_key=course_key,
            )
            for course_key in self.course_keys
        ]

    def _assert_program_enrollment_user(self, program_enrollment, user):
        """ validate program enrollment has a user """
        program_enrollment.refresh_from_db()
        assert program_enrollment.user == user

    def _assert_program_course_enrollment(self, program_course_enrollment, mode=CourseMode.MASTERS):
        """ validate program course enrollment has a valid course enrollment """
        program_course_enrollment.refresh_from_db()
        student_course_enrollment = program_course_enrollment.course_enrollment
        assert student_course_enrollment.user == self.user
        assert student_course_enrollment.course.id == program_course_enrollment.course_key
        assert student_course_enrollment.mode == mode

    def test_update_social_auth(self):
        """
        Makes sure we can update a social_auth row to trigger the same program enrollments
        """
        program_enrollment = self._create_waiting_program_enrollment()
        program_course_enrollments = self._create_waiting_course_enrollments(program_enrollment)

        user_social_auth = UserSocialAuth.objects.create(
            user=self.user,
            uid='{}:{}'.format(self.provider_slug, 'gobbledegook')
        )

        # Not yet a thing, didn't match
        program_enrollment.refresh_from_db()
        assert program_enrollment.user is None

        user_social_auth.uid = f'{self.provider_slug}:{self.external_id}'
        user_social_auth.save()

        # now we see the enrollments realized
        self._assert_program_enrollment_user(program_enrollment, self.user)
        for program_course_enrollment in program_course_enrollments:
            self._assert_program_course_enrollment(program_course_enrollment)

    @ddt.data(
        'learner1a',
        'LEarnER1A',
        'LEARNER1A',
    )
    def test_waiting_course_enrollments_completed(self, auth_user_key):
        program_enrollment = self._create_waiting_program_enrollment()
        program_course_enrollments = self._create_waiting_course_enrollments(program_enrollment)

        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{auth_user_key}'
        )

        self._assert_program_enrollment_user(program_enrollment, self.user)
        for program_course_enrollment in program_course_enrollments:
            self._assert_program_course_enrollment(program_course_enrollment)

    def test_same_user_key_in_multiple_organizations(self):
        uox_program_enrollment = self._create_waiting_program_enrollment()

        second_organization = OrganizationFactory.create()
        SAMLProviderConfigFactory.create(organization=second_organization, slug='aiu')
        catalog_org = CatalogOrganizationFactory.create(key=second_organization.short_name)
        program_uuid = self._create_catalog_program(catalog_org)['uuid']

        # aiu enrollment with the same student key as our uox user
        aiu_program_enrollment = ProgramEnrollmentFactory.create(
            user=None,
            external_user_key=self.external_id,
            program_uuid=program_uuid
        )

        UserSocialAuth.objects.create(
            user=UserFactory.create(),
            uid='{}:{}'.format('not_used', self.external_id),
        )

        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{self.external_id}',
        )
        self._assert_program_enrollment_user(uox_program_enrollment, self.user)

        aiu_user = UserFactory.create()
        UserSocialAuth.objects.create(
            user=aiu_user,
            uid='{}:{}'.format('aiu', self.external_id),
        )
        self._assert_program_enrollment_user(aiu_program_enrollment, aiu_user)

    def test_only_active_saml_config_used(self):
        """ makes sure only the active row in SAMLProvider config is used """
        program_enrollment = self._create_waiting_program_enrollment()

        # update will create a second record
        self.provider_config.organization = None
        self.provider_config.save()
        assert len(SAMLProviderConfig.objects.all()) == 2

        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{self.external_id}'
        )
        program_enrollment.refresh_from_db()
        assert program_enrollment.user is None

    def test_learner_already_enrolled_in_course(self):
        course_key = self.course_keys[0]
        course = CourseOverview.objects.get(id=course_key)
        CourseEnrollmentFactory(user=self.user, course=course, mode=CourseMode.VERIFIED)

        program_enrollment = self._create_waiting_program_enrollment()
        program_course_enrollments = self._create_waiting_course_enrollments(program_enrollment)

        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{self.external_id}'
        )

        self._assert_program_enrollment_user(program_enrollment, self.user)

        duplicate_program_course_enrollment = program_course_enrollments[0]
        self._assert_program_course_enrollment(
            duplicate_program_course_enrollment, CourseMode.VERIFIED
        )

        program_course_enrollment = program_course_enrollments[1]
        self._assert_program_course_enrollment(program_course_enrollment)

    def test_enrolled_with_no_course_enrollments(self):
        program_enrollment = self._create_waiting_program_enrollment()

        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{self.external_id}'
        )

        self._assert_program_enrollment_user(program_enrollment, self.user)

    def test_create_social_auth_with_no_waiting_enrollments(self):
        """
        No exceptions should be raised if there are no enrollments to update
        """
        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.provider_slug}:{self.external_id}'
        )

    def test_create_social_auth_provider_has_no_organization(self):
        """
        No exceptions should be raised if provider is not linked to an organization
        """
        provider = SAMLProviderConfigFactory.create()
        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{provider.slug}:{self.external_id}'
        )

    def test_create_social_auth_non_saml_provider(self):
        """
        No exceptions should be raised for a non-SAML uid
        """
        UserSocialAuth.objects.create(
            user=self.user,
            uid='google-oauth-user@gmail.com'
        )
        UserSocialAuth.objects.create(
            user=self.user,
            uid='123:123:123'
        )

    def test_saml_provider_not_found(self):
        """
        An error should be logged for incoming social auth entries with a saml id but
        no matching saml configuration exists
        """
        with LogCapture(logger.name) as log:
            UserSocialAuth.objects.create(
                user=self.user,
                uid='abc:123456'
            )
            log.check_present(
                (
                    logger.name,
                    'WARNING',
                    'Got incoming social auth for provider={} but no such provider exists'.format('abc')
                )
            )

    def test_cannot_find_catalog_program(self):
        """
        An error should be logged if a program enrollment exists but a matching catalog
        program cannot be found
        """
        self.program_uuid = self._create_catalog_program(None)['uuid']
        self._create_waiting_program_enrollment()

        with LogCapture(logger.name) as log:
            UserSocialAuth.objects.create(
                user=self.user,
                uid=f'{self.provider_slug}:{self.external_id}'
            )
            error_template = (
                'Failed to complete waiting enrollments for organization={}.'
                ' No catalog programs with matching authoring_organization exist.'
            )
            log.check_present(
                (
                    logger.name,
                    'WARNING',
                    error_template.format('UoX')
                )
            )

    def test_exception_on_enrollment_failure(self):
        program_enrollment = self._create_waiting_program_enrollment()
        self._create_waiting_course_enrollments(program_enrollment)

        with mock.patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.enroll') as enrollMock:
            enrollMock.side_effect = CourseEnrollmentException('something has gone wrong')
            with pytest.raises(CourseEnrollmentException):
                UserSocialAuth.objects.create(
                    user=self.user,
                    uid=f'{self.provider_slug}:{self.external_id}'
                )

    def test_log_on_unexpected_exception(self):
        """
        unexpected errors as part of the account linking process should be logged and re-raised
        """
        program_enrollment = self._create_waiting_program_enrollment()
        self._create_waiting_course_enrollments(program_enrollment)

        with mock.patch('lms.djangoapps.program_enrollments.api.linking.enroll_in_masters_track') as enrollMock:
            enrollMock.side_effect = Exception('unexpected error')
            with LogCapture(logger.name) as log:
                with self.assertRaisesRegex(Exception, 'unexpected error'):
                    UserSocialAuth.objects.create(
                        user=self.user,
                        uid=f'{self.provider_slug}:{self.external_id}',
                    )
                error_template = 'Unable to link waiting enrollments for user {}, social auth creation failed: {}'
                log.check_present(
                    (
                        logger.name,
                        'WARNING',
                        error_template.format(self.user.id, 'unexpected error')
                    )
                )
