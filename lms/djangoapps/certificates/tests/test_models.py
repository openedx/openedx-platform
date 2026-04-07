"""Tests for certificate Django models. """


import json
from unittest import mock, skipUnless
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseKey, CourseLocator
from openedx_events.tests.utils import OpenEdxEventsTestMixin
from path import Path as path

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from lms.djangoapps.certificates.models import (
    CertificateAllowlist,
    CertificateGenerationHistory,
    CertificateHtmlViewConfiguration,
    CertificateInvalidation,
    CertificateStatuses,
    CertificateTemplateAsset,
    ExampleCertificate,
    ExampleCertificateSet,
    GeneratedCertificate,
)
from lms.djangoapps.certificates.tests.factories import (
    CertificateAllowlistFactory,
    CertificateInvalidationFactory,
    GeneratedCertificateFactory,
)
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

ENROLLMENT_METHOD = 'common.djangoapps.student.models.course_enrollment.CourseEnrollment.enrollment_mode_for_user'
PROFILE_METHOD = 'common.djangoapps.student.models_api.get_name'

FEATURES_INVALID_FILE_PATH = settings.FEATURES.copy()
FEATURES_INVALID_FILE_PATH['CERTS_HTML_VIEW_CONFIG_PATH'] = 'invalid/path/to/config.json'

TEST_DIR = path(__file__).dirname()
TEST_DATA_DIR = 'common/test/data/'
PLATFORM_ROOT = TEST_DIR.parent.parent.parent.parent
TEST_DATA_ROOT = PLATFORM_ROOT / TEST_DATA_DIR

name_affirmation_service = get_name_affirmation_service()


class ExampleCertificateTest(TestCase, OpenEdxEventsTestMixin):
    """Tests for the ExampleCertificate model. """

    COURSE_KEY = CourseLocator(org='test', course='test', run='test')

    DESCRIPTION = 'test'
    TEMPLATE = 'test.pdf'
    DOWNLOAD_URL = 'https://www.example.com'
    ERROR_REASON = 'Kaboom!'

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.cert_set = ExampleCertificateSet.objects.create(course_key=self.COURSE_KEY)
        self.cert = ExampleCertificate.objects.create(
            example_cert_set=self.cert_set,
            description=self.DESCRIPTION,
            template=self.TEMPLATE
        )

    def test_update_status_success(self):
        self.cert.update_status(
            ExampleCertificate.STATUS_SUCCESS,
            download_url=self.DOWNLOAD_URL
        )
        assert self.cert.status_dict ==\
               {'description': self.DESCRIPTION,
                'status': ExampleCertificate.STATUS_SUCCESS,
                'download_url': self.DOWNLOAD_URL}

    def test_update_status_error(self):
        self.cert.update_status(
            ExampleCertificate.STATUS_ERROR,
            error_reason=self.ERROR_REASON
        )
        assert self.cert.status_dict ==\
               {'description': self.DESCRIPTION,
                'status': ExampleCertificate.STATUS_ERROR,
                'error_reason': self.ERROR_REASON}

    def test_update_status_invalid(self):
        with self.assertRaisesRegex(ValueError, 'status'):
            self.cert.update_status('invalid')

    def test_latest_status_unavailable(self):
        # Delete any existing statuses
        ExampleCertificateSet.objects.all().delete()

        # Verify that the "latest" status is None
        result = ExampleCertificateSet.latest_status(self.COURSE_KEY)
        assert result is None

    def test_latest_status_is_course_specific(self):
        other_course = CourseLocator(org='other', course='other', run='other')
        result = ExampleCertificateSet.latest_status(other_course)
        assert result is None


class CertificateHtmlViewConfigurationTest(TestCase, OpenEdxEventsTestMixin):
    """
    Test the CertificateHtmlViewConfiguration model.
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.configuration_string = """{
            "default": {
                "url": "https://www.edx.org",
                "logo_src": "https://www.edx.org/static/images/logo.png"
            },
            "honor": {
                "logo_src": "https://www.edx.org/static/images/honor-logo.png"
            }
        }"""
        self.config = CertificateHtmlViewConfiguration(configuration=self.configuration_string)

    def test_create(self):
        """
        Tests creation of configuration.
        """
        self.config.save()
        assert self.config.configuration == self.configuration_string

    def test_clean_bad_json(self):
        """
        Tests if bad JSON string was given.
        """
        self.config = CertificateHtmlViewConfiguration(configuration='{"bad":"test"')
        pytest.raises(ValidationError, self.config.clean)

    def test_get(self):
        """
        Tests get configuration from saved string.
        """
        self.config.enabled = True
        self.config.save()
        expected_config = {
            "default": {
                "url": "https://www.edx.org",
                "logo_src": "https://www.edx.org/static/images/logo.png"
            },
            "honor": {
                "logo_src": "https://www.edx.org/static/images/honor-logo.png"
            }
        }
        assert self.config.get_config() == expected_config

    def test_get_not_enabled_returns_blank(self):
        """
        Tests get configuration that is not enabled.
        """
        self.config.enabled = False
        self.config.save()
        assert len(self.config.get_config()) == 0

    @override_settings(FEATURES=FEATURES_INVALID_FILE_PATH)
    def test_get_no_database_no_file(self):
        """
        Tests get configuration that is not enabled.
        """
        self.config.configuration = ''
        self.config.save()
        assert self.config.get_config() == {}


class CertificateTemplateAssetTest(TestCase):
    """
    Test Assets are uploading/saving successfully for CertificateTemplateAsset.
    """
    def test_asset_file_saving_with_actual_name(self):
        """
        Verify that asset file is saving with actual name, No hash tag should be appended with the asset filename.
        """
        CertificateTemplateAsset(description='test description', asset=SimpleUploadedFile(
            'picture1.jpg',
            b'these are the file contents!')).save()
        certificate_template_asset = CertificateTemplateAsset.objects.get(id=1)
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture1.jpg'

        # Now save asset with same file again, New file will be uploaded after deleting the old one with the same name.
        certificate_template_asset.asset = SimpleUploadedFile('picture1.jpg', b'file contents')
        certificate_template_asset.save()
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture1.jpg'

        # Now replace the asset with another file
        certificate_template_asset.asset = SimpleUploadedFile('picture2.jpg', b'file contents')
        certificate_template_asset.save()

        certificate_template_asset = CertificateTemplateAsset.objects.get(id=1)
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture2.jpg'


class EligibleCertificateManagerTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Test the GeneratedCertificate model's object manager for filtering
    out ineligible certs.
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.course1 = CourseOverviewFactory()
        self.course2 = CourseOverviewFactory(
            id=CourseKey.from_string(f'{self.course1.id}a')
        )

        self.eligible_cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course1.id
        )
        self.ineligible_cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.audit_passing,
            user=self.user,
            course_id=self.course2.id
        )

    def test_filter_ineligible_certificates(self):
        """
        Verify that the EligibleAvailableCertificateManager filters out
        certificates marked as ineligible, and that the default object
        manager for GeneratedCertificate does not filter them out.
        """
        assert list(GeneratedCertificate.eligible_available_certificates.filter(user=self.user)) == [self.eligible_cert]
        assert list(GeneratedCertificate.objects.filter(user=self.user)) == [self.eligible_cert, self.ineligible_cert]

    def test_filter_certificates_for_nonexistent_courses(self):
        """
        Verify that the EligibleAvailableCertificateManager filters out
        certificates for courses with no CourseOverview.
        """
        self.course1.delete()
        assert not GeneratedCertificate.eligible_available_certificates.filter(user=self.user)


@ddt.ddt
class TestCertificateGenerationHistory(TestCase, OpenEdxEventsTestMixin):
    """
    Test the CertificateGenerationHistory model's methods
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    @ddt.data(
        ({"student_set": "allowlisted_not_generated"}, "For exceptions", True),
        ({"student_set": "allowlisted_not_generated"}, "For exceptions", False),
        # check "students" key for backwards compatibility
        ({"students": [1, 2, 3]}, "For exceptions", True),
        ({"students": [1, 2, 3]}, "For exceptions", False),
        ({}, "All learners", True),
        ({}, "All learners", False),
        # test single status to regenerate returns correctly
        ({"statuses_to_regenerate": ['downloadable']}, 'already received', True),
        ({"statuses_to_regenerate": ['downloadable']}, 'already received', False),
        # test that list of > 1 statuses render correctly
        ({"statuses_to_regenerate": ['downloadable', 'error']}, 'already received, error states', True),
        ({"statuses_to_regenerate": ['downloadable', 'error']}, 'already received, error states', False),
        # test that only "readable" statuses are returned
        ({"statuses_to_regenerate": ['downloadable', 'not_readable']}, 'already received', True),
        ({"statuses_to_regenerate": ['downloadable', 'not_readable']}, 'already received', False),
    )
    @ddt.unpack
    def test_get_certificate_generation_candidates(self, task_input, expected, is_regeneration):
        staff = AdminFactory.create()
        instructor_task = InstructorTaskFactory.create(
            task_input=json.dumps(task_input),
            requester=staff,
            task_key='',
            task_id='',
        )
        certificate_generation_history = CertificateGenerationHistory(
            course_id=instructor_task.course_id,
            generated_by=staff,
            instructor_task=instructor_task,
            is_regeneration=is_regeneration,
        )
        assert certificate_generation_history.get_certificate_generation_candidates() == expected

    @ddt.data((True, "regenerated"), (False, "generated"))
    @ddt.unpack
    def test_get_task_name(self, is_regeneration, expected):
        staff = AdminFactory.create()
        instructor_task = InstructorTaskFactory.create(
            task_input=json.dumps({}),
            requester=staff,
            task_key='',
            task_id='',
        )
        certificate_generation_history = CertificateGenerationHistory(
            course_id=instructor_task.course_id,
            generated_by=staff,
            instructor_task=instructor_task,
            is_regeneration=is_regeneration,
        )
        assert certificate_generation_history.get_task_name() == expected


class CertificateInvalidationTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Test for the Certificate Invalidation model.
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.course = CourseFactory()
        self.course_overview = CourseOverviewFactory.create(
            id=self.course.id
        )
        self.user = UserFactory()
        self.course_id = self.course.id  # pylint: disable=no-member
        self.certificate = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_id
        )

    def test_is_certificate_invalid_method(self):
        """ Verify that method return false if certificate is valid. """

        assert not CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

    def test_is_certificate_invalid_with_invalid_cert(self):
        """ Verify that method return true if certificate is invalid. """

        invalid_cert = CertificateInvalidationFactory.create(
            generated_certificate=self.certificate,
            invalidated_by=self.user
        )
        # Invalidate user certificate
        self.certificate.invalidate()
        assert CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

        # mark the entry as in-active.
        invalid_cert.active = False
        invalid_cert.save()

        # After making the certificate valid method will return false.
        assert not CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

    @patch('openedx.core.djangoapps.programs.tasks.revoke_program_certificates.delay')
    @patch(
        'openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled',
        return_value=True,
    )
    def test_revoke_program_certificates(self, mock_issuance, mock_revoke_task):    # pylint: disable=unused-argument
        """ Verify that `revoke_program_certificates` is invoked upon invalidation. """
        # Invalidate user certificate
        self.certificate.invalidate()

        assert mock_revoke_task.call_count == 1
        assert mock_revoke_task.call_args[0] == (self.user.username, str(self.course_id))


@ddt.ddt
class GeneratedCertificateTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Test GeneratedCertificates
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.course = CourseOverviewFactory()
        self.course_key = self.course.id

    def _assert_event_data(self, mocked_function_call, expected_event_data):
        """Utility function that verifies the mocked function was called with the expected arguments."""

        mocked_function_call.assert_called_with(
            'revoked',
            self.user,
            str(self.course_key),
            event_data=expected_event_data
        )

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_invalidate(self, mock_emit_certificate_event):
        """
        Test the invalidate method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT,
            name='Fuzzy Hippo'
        )
        mode = CourseMode.VERIFIED
        source = 'invalidated_test'
        cert.invalidate(mode=mode, source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        profile = UserProfile.objects.get(user=self.user)
        assert cert.status == CertificateStatuses.unavailable
        assert cert.mode == mode
        assert cert.name == profile.name

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_invalidate_find_mode(self, mock_emit_certificate_event):
        """
        Test the invalidate method when mode is retrieved from the enrollment
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT
        )

        mode = CourseMode.MASTERS
        source = 'invalidated_test'
        with mock.patch(ENROLLMENT_METHOD, return_value=(mode, None)):
            cert.invalidate(source=source)

            cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
            assert cert.status == CertificateStatuses.unavailable
            assert cert.mode == mode

            expected_event_data = {
                'user_id': self.user.id,
                'course_id': str(self.course_key),
                'certificate_id': cert.verify_uuid,
                'enrollment_mode': mode,
                'source': source,
            }

            self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_invalidate_no_mode(self, mock_emit_certificate_event):
        """
        Test the invalidate method when there is no enrollment mode
        """
        initial_mode = CourseMode.AUDIT
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=initial_mode
        )

        source = 'invalidated_test'
        with mock.patch(ENROLLMENT_METHOD, return_value=(None, None)):
            cert.invalidate(source=source)

            cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
            assert cert.status == CertificateStatuses.unavailable
            assert cert.mode == initial_mode

            expected_event_data = {
                'user_id': self.user.id,
                'course_id': str(self.course_key),
                'certificate_id': cert.verify_uuid,
                'enrollment_mode': initial_mode,
                'source': source,
            }

            self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_invalidate_no_profile(self, mock_emit_certificate_event):
        """
        Test the invalidate method when there is no user profile
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT,
            name='Squeaky Frog'
        )

        mode = CourseMode.VERIFIED
        source = 'invalidated_test'
        with mock.patch(PROFILE_METHOD, return_value=None):
            cert.invalidate(mode=mode, source=source)

            cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
            assert cert.status == CertificateStatuses.unavailable
            assert cert.mode == mode
            assert cert.name == ''

            expected_event_data = {
                'user_id': self.user.id,
                'course_id': str(self.course_key),
                'certificate_id': cert.verify_uuid,
                'enrollment_mode': cert.mode,
                'source': source,
            }

            self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_notpassing(self, mock_emit_certificate_event):
        """
        Test the notpassing method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT
        )
        mode = CourseMode.VERIFIED
        grade = '.3'
        source = "notpassing_test"
        cert.mark_notpassing(mode=mode, grade=grade, source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        assert cert.status == CertificateStatuses.notpassing
        assert cert.mode == mode
        assert cert.grade == grade

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @skipUnless(name_affirmation_service is not None, 'Requires Name Affirmation')
    @ddt.data((True, 'approved'),
              (True, 'denied'),
              (False, 'pending'))
    @ddt.unpack
    def test_invalidate_with_verified_name(self, should_use_verified_name_for_certs, status):
        """
        Test the invalidate method with verified name turned on for the user's certificates
        """
        verified_name = 'Jonathan Doe'
        profile = UserProfile.objects.get(user=self.user)
        name_affirmation_service.create_verified_name(self.user, verified_name, profile.name, status=status)
        name_affirmation_service.create_verified_name_config(
            self.user,
            use_verified_name_for_certs=should_use_verified_name_for_certs
        )

        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT,
            name='Fuzzy Hippo'
        )
        mode = CourseMode.VERIFIED
        source = 'invalidated_test'
        cert.invalidate(mode=mode, source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        if should_use_verified_name_for_certs and status == 'approved':
            assert cert.name == verified_name
        else:
            assert cert.name == profile.name

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_unverified(self, mock_emit_certificate_event):
        """
        Test the unverified method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key,
            mode=CourseMode.AUDIT
        )
        mode = CourseMode.VERIFIED
        source = "unverified_test"
        cert.mark_unverified(mode=mode, source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        assert cert.status == CertificateStatuses.unverified
        assert cert.mode == mode

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)


class CertificateAllowlistTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for the CertificateAllowlist model.
    """

    ENABLED_OPENEDX_EVENTS = []

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        super().setUp()
        self.username = 'fun_username'
        self.user_email = 'a@b.com'
        self.user = UserFactory(username=self.username, email=self.user_email)
        self.second_user = UserFactory()

        self.course_run = CourseFactory()
        self.course_run_key = self.course_run.id  # pylint: disable=no-member

    def test_get_allowlist_empty(self):
        ret = CertificateAllowlist.get_certificate_allowlist(course_id=None, student=None)
        assert len(ret) == 0

    def test_get_allowlist_multiple_users(self):
        CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.user)
        CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.second_user)

        ret = CertificateAllowlist.get_certificate_allowlist(course_id=self.course_run_key)
        assert len(ret) == 2

    def test_get_allowlist_no_cert(self):
        allowlist_item = CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.user)
        CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.second_user)

        ret = CertificateAllowlist.get_certificate_allowlist(course_id=self.course_run_key, student=self.user)
        assert len(ret) == 1

        item = ret[0]
        assert item['id'] == allowlist_item.id
        assert item['user_id'] == self.user.id
        assert item['user_name'] == self.username
        assert item['user_email'] == self.user_email
        assert item['course_id'] == str(self.course_run_key)
        assert item['created'] == allowlist_item.created.strftime("%B %d, %Y")
        assert item['certificate_generated'] == ''
        assert item['notes'] == allowlist_item.notes

    def test_get_allowlist_cert(self):
        allowlist_item = CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.user)
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_run_key
        )

        ret = CertificateAllowlist.get_certificate_allowlist(course_id=self.course_run_key, student=self.user)
        assert len(ret) == 1

        item = ret[0]
        assert item['id'] == allowlist_item.id
        assert item['certificate_generated'] == cert.created_date.strftime("%B %d, %Y")
