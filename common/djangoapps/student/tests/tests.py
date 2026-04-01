"""
Miscellaneous tests for the student app.
"""

import logging
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

import ddt
from config_models.models import cache
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase, override_settings
from django.test.client import Client
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_switch
from markupsafe import escape
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locations import CourseLocator
from pyquery import PyQuery as pq

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.helpers import _cert_info, process_survey_link
from common.djangoapps.student.models import (
    AnonymousUserId,
    CourseEnrollment,
    LinkedInAddToProfileConfiguration,
    UserAttribute,
    anonymous_id_for_user,
    unique_id_for_user,
    user_by_anonymous_id
)
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.student.toggles import REDIRECT_TO_COURSEWARE_AFTER_ENROLLMENT
from common.djangoapps.student.views import complete_course_mode_info
from common.djangoapps.util.model_utils import USER_SETTINGS_CHANGED_EVENT_NAME
from common.djangoapps.util.testing import EventTestMixin
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.verify_student.tests import TestVerificationBase
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory as CatalogCourseFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory, ProgramFactory, generate_course_run_key
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.programs.tests.mixins import ProgramsApiConfigMixin
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, check_mongo_calls  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.data import CertificatesDisplayBehaviors  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context

log = logging.getLogger(__name__)

BETA_TESTER_METHOD = 'common.djangoapps.student.helpers.access.is_beta_tester'


@skip_unless_lms
@ddt.ddt
class CourseEndingTest(ModuleStoreTestCase):
    """Test things related to course endings: certificates, surveys, etc"""

    def test_process_survey_link(self):
        username = "fred"
        user = Mock(username=username)
        user_id = unique_id_for_user(user)
        link1 = "http://www.mysurvey.com"
        assert process_survey_link(link1, user) == link1

        link2 = "http://www.mysurvey.com?unique={UNIQUE_ID}"
        link2_expected = f"http://www.mysurvey.com?unique={user_id}"
        assert process_survey_link(link2, user) == link2_expected

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': False})
    def test_cert_info(self):
        user = UserFactory.create()
        survey_url = "http://a_survey.com"
        course = CourseOverviewFactory.create(
            end_of_course_survey_url=survey_url,
            certificates_display_behavior=CertificatesDisplayBehaviors.END,
            end=datetime.now(ZoneInfo("UTC")) - timedelta(days=2)
        )
        cert = GeneratedCertificateFactory.create(
            user=user,
            course_id=course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
            grade='67',
            download_url='http://s3.edx/cert'
        )
        enrollment = CourseEnrollmentFactory(user=user, course_id=course.id, mode=CourseMode.VERIFIED)
        # LinkedIn sharing disabled as we are expecting 'linked_in_url': None in our test case
        SITE_CONFIGURATION = {
            'SOCIAL_SHARING_SETTINGS': {
                'CERTIFICATE_LINKEDIN': False
            }
        }
        with with_site_configuration_context(configuration=SITE_CONFIGURATION):
            assert _cert_info(user, enrollment, None) == {
                'status': 'processing',
                'show_survey_button': False,
                'can_unenroll': True,
            }

            cert_status = {'status': 'unavailable', 'mode': 'honor', 'uuid': None}
            assert _cert_info(user, enrollment, cert_status) == {
                'status': 'processing',
                'show_survey_button': False,
                'mode': 'honor',
                'linked_in_url': None,
                'can_unenroll': True,
            }

            cert_status = {'status': 'generating', 'grade': '0.67', 'mode': 'honor', 'uuid': None}
            with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as patch_persisted_grade:
                patch_persisted_grade.return_value = Mock(percent=1.0)
                assert _cert_info(user, enrollment, cert_status) == {
                    'status': 'generating',
                    'show_survey_button': True,
                    'survey_url': survey_url,
                    'grade': '1.0',
                    'mode': 'honor',
                    'linked_in_url': None,
                    'can_unenroll': False,
                }

            cert_status = {'status': 'generating', 'grade': '0.67', 'mode': 'honor', 'uuid': None}
            assert _cert_info(user, enrollment, cert_status) == {
                'status': 'generating',
                'show_survey_button': True,
                'survey_url': survey_url,
                'grade': '0.67',
                'mode': 'honor',
                'linked_in_url': None,
                'can_unenroll': False,
            }

            cert_status = {
                'status': 'downloadable',
                'grade': '0.67',
                'download_url': cert.download_url,
                'mode': 'honor',
                'uuid': 'fakeuuidbutitsfine',
            }
            assert _cert_info(user, enrollment, cert_status) == {
                'status': 'downloadable',
                'download_url': cert.download_url,
                'show_survey_button': True,
                'survey_url': survey_url,
                'grade': '0.67',
                'mode': 'honor',
                'linked_in_url': None,
                'can_unenroll': False,
            }

            cert_status = {
                'status': 'notpassing',
                'grade': '0.67',
                'download_url': cert.download_url,
                'mode': 'honor',
                'uuid': 'fakeuuidbutitsfine',
            }
            assert _cert_info(user, enrollment, cert_status) == {
                'status': 'notpassing',
                'show_survey_button': True,
                'survey_url': survey_url,
                'grade': '0.67',
                'mode': 'honor',
                'linked_in_url': None,
                'can_unenroll': True,
            }

            # Test a course that doesn't have a survey specified
            course2 = CourseOverviewFactory.create(
                end_of_course_survey_url=None,
                certificates_display_behavior='end',
            )
            enrollment2 = CourseEnrollmentFactory(user=user, course_id=course2.id, mode=CourseMode.VERIFIED)

            cert_status = {
                'status': 'notpassing', 'grade': '0.67',
                'download_url': cert.download_url, 'mode': 'honor', 'uuid': 'fakeuuidbutitsfine'
            }
            assert _cert_info(user, enrollment2, cert_status) == {
                'status': 'notpassing',
                'show_survey_button': False,
                'grade': '0.67',
                'mode': 'honor',
                'linked_in_url': None,
                'can_unenroll': True
            }

            course3 = CourseOverviewFactory.create(
                end_of_course_survey_url=None,
                certificates_display_behavior='early_no_info',
            )
            enrollment3 = CourseEnrollmentFactory(user=user, course_id=course3.id, mode=CourseMode.VERIFIED)
            # test when the display is unavailable or notpassing, we get the correct results out
            course2.certificates_display_behavior = CertificatesDisplayBehaviors.EARLY_NO_INFO
            cert_status = {'status': 'unavailable', 'mode': 'honor', 'uuid': None}
            assert _cert_info(user, enrollment3, cert_status) == {
                'status': 'processing',
                'show_survey_button': False,
                'can_unenroll': True
            }

            cert_status = {
                'status': 'notpassing', 'grade': '0.67',
                'download_url': cert.download_url,
                'mode': 'honor',
                'uuid': 'fakeuuidbutitsfine'
            }
            assert _cert_info(user, enrollment3, cert_status) == {
                'status': 'processing',
                'show_survey_button': False,
                'can_unenroll': True
            }

    def test_cert_info_beta_tester(self):
        user = UserFactory.create()
        course = CourseOverviewFactory.create()
        mode = CourseMode.VERIFIED
        grade = '0.67'
        status = CertificateStatuses.downloadable
        cert = GeneratedCertificateFactory.create(
            user=user,
            course_id=course.id,
            status=status,
            mode=mode
        )
        enrollment = CourseEnrollmentFactory(user=user, course_id=course.id, mode=mode)

        cert_status = {
            'status': status,
            'grade': grade,
            'download_url': cert.download_url,
            'mode': mode,
            'uuid': 'blah',
        }
        with patch(BETA_TESTER_METHOD, return_value=False):
            # LinkedIn sharing disabled as we are expecting 'linked_in_url': None in our test case
            SITE_CONFIGURATION = {
                'SOCIAL_SHARING_SETTINGS': {
                    'CERTIFICATE_LINKEDIN': False
                }
            }
            with with_site_configuration_context(configuration=SITE_CONFIGURATION):
                assert _cert_info(user, enrollment, cert_status) == {
                    'status': status,
                    'download_url': cert.download_url,
                    'show_survey_button': False,
                    'grade': grade,
                    'mode': mode,
                    'linked_in_url': None,
                    'can_unenroll': False
                }

        with patch(BETA_TESTER_METHOD, return_value=True):
            assert _cert_info(user, enrollment, cert_status) == {
                'status': 'processing',
                'show_survey_button': False,
                'can_unenroll': True
            }

    @ddt.data(
        (0.70, 0.60),
        (0.60, 0.70),
        (None, 0.70),
        (None, 0.0),
        (0.70, None),
        (0.0, None),
        (0.70, 0.0),
        (0.0, 0.70),
    )
    @ddt.unpack
    def test_cert_grade(self, persisted_grade, cert_grade):
        """
        Tests that the higher of the persisted grade and the grade
        from the certs table is used on the learner dashboard.
        """
        expected_grade = max(filter(lambda x: x is not None, [persisted_grade, cert_grade]))
        user = UserFactory.create()
        survey_url = "http://a_survey.com"
        course = CourseOverviewFactory.create(
            end_of_course_survey_url=survey_url,
            certificates_display_behavior=CertificatesDisplayBehaviors.END,
            end=datetime.now(ZoneInfo("UTC")) - timedelta(days=2),
        )
        enrollment = CourseEnrollmentFactory(user=user, course_id=course.id, mode=CourseMode.VERIFIED)

        if cert_grade is not None:
            cert_status = {'status': 'generating', 'grade': str(cert_grade), 'mode': 'honor', 'uuid': None}
        else:
            cert_status = {'status': 'generating', 'mode': 'honor', 'uuid': None}

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as patch_persisted_grade:
            patch_persisted_grade.return_value = Mock(percent=persisted_grade)
            assert _cert_info(user, enrollment, cert_status) == {'status': 'generating', 'show_survey_button': True,
                                                                 'survey_url': survey_url, 'grade': str(expected_grade),
                                                                 'mode': 'honor', 'linked_in_url': None,
                                                                 'can_unenroll': False}

    def test_cert_grade_no_grades(self):
        """
        Tests that the default cert info is returned
        when the learner has no persisted grade or grade
        in the certs table.
        """
        user = UserFactory.create()
        survey_url = "http://a_survey.com"
        course = CourseOverviewFactory.create(
            end_of_course_survey_url=survey_url,
            certificates_display_behavior=CertificatesDisplayBehaviors.END,
            end=datetime.now(ZoneInfo("UTC")) - timedelta(days=2),
        )
        cert_status = {'status': 'generating', 'mode': 'honor', 'uuid': None}
        enrollment = CourseEnrollmentFactory(user=user, course_id=course.id, mode=CourseMode.VERIFIED)

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as patch_persisted_grade:
            patch_persisted_grade.return_value = None
            assert _cert_info(user, enrollment, cert_status) == {'status': 'processing', 'show_survey_button': False,
                                                                 'can_unenroll': True}


@ddt.ddt
class DashboardTest(ModuleStoreTestCase, TestVerificationBase):
    """
    Tests for dashboard utility functions
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username="jack", email="jack@fake.edx.org", password=self.TEST_PASSWORD)
        self.client = Client()
        cache.clear()

    @skip_unless_lms
    def _check_verification_status_on(self, mode, value):
        """
        Check that the css class and the status message are in the dashboard html.
        """
        CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)
        CourseEnrollment.enroll(self.user, self.course.location.course_key, mode=mode)

        if mode == 'verified':
            # Simulate a successful verification attempt
            attempt = self.create_and_submit_attempt_for_user(self.user)
            attempt.approve()

        response = self.client.get(reverse('dashboard'))
        if mode in ['professional', 'no-id-professional']:
            self.assertContains(response, 'class="course professional"')
        else:
            self.assertContains(response, f'class="course {mode}"')
        self.assertContains(response, value)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': True})
    def test_verification_status_visible(self):
        """
        Test that the certificate verification status for courses is visible on the dashboard.
        """
        self.client.login(username="jack", password=self.TEST_PASSWORD)
        self._check_verification_status_on('verified', 'You&#39;re enrolled as a verified student')
        self._check_verification_status_on('honor', 'You&#39;re enrolled as an honor code student')
        self._check_verification_status_off('audit', '')
        self._check_verification_status_on('professional', 'You&#39;re enrolled as a professional education student')
        self._check_verification_status_on(
            'no-id-professional',
            'You&#39;re enrolled as a professional education student',
        )

    @skip_unless_lms
    def _check_verification_status_off(self, mode, value):
        """
        Check that the css class and the status message are not in the dashboard html.
        """
        CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)
        CourseEnrollment.enroll(self.user, self.course.location.course_key, mode=mode)

        if mode == 'verified':
            # Simulate a successful verification attempt
            attempt = self.create_and_submit_attempt_for_user(self.user)
            attempt.approve()

        response = self.client.get(reverse('dashboard'))

        if mode == 'audit':
            # Audit mode does not have a banner.  Assert no banner element.
            assert pq(response.content)('.sts-enrollment').length == 0
        else:
            self.assertNotContains(response, f"class=\"course {mode}\"")
            self.assertNotContains(response, value)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': False})
    def test_verification_status_invisible(self):
        """
        Test that the certificate verification status for courses is not visible on the dashboard
        if the verified certificates setting is off.
        """
        self.client.login(username="jack", password=self.TEST_PASSWORD)
        self._check_verification_status_off('verified', 'You\'re enrolled as a verified student')
        self._check_verification_status_off('honor', 'You\'re enrolled as an honor code student')
        self._check_verification_status_off('audit', '')

    def test_course_mode_info(self):
        verified_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='Verified',
            expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        )
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        course_mode_info = complete_course_mode_info(self.course.id, enrollment)
        assert course_mode_info['show_upsell']
        assert course_mode_info['days_for_upsell'] == 1

        verified_mode.expiration_datetime = datetime.now(ZoneInfo("UTC")) + timedelta(days=-1)
        verified_mode.save()
        course_mode_info = complete_course_mode_info(self.course.id, enrollment)
        assert not course_mode_info['show_upsell']
        assert course_mode_info['days_for_upsell'] is None

    @skip_unless_lms
    def test_linked_in_add_to_profile_btn_not_appearing_without_config(self):
        # Without linked-in config don't show Add Certificate to LinkedIn button
        self.client.login(username="jack", password=self.TEST_PASSWORD)

        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='verified',
            expiration_datetime=datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        )

        CourseEnrollment.enroll(self.user, self.course.id, mode='honor')

        self.course.start = datetime.now(ZoneInfo("UTC")) - timedelta(days=2)
        self.course.end = datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        self.course.display_name = "Omega"
        self.course = self.update_course(self.course, self.user.id)

        download_url = 'www.edx.org'
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
            grade='67',
            download_url=download_url
        )
        # LinkedIn sharing disabled
        # When CERTIFICATE_LINKEDIN is set to False in site configuration,
        # the LinkedIn "Add to Profile" button should not be visible to users
        SITE_CONFIGURATION = {
            'SOCIAL_SHARING_SETTINGS': {
                'CERTIFICATE_LINKEDIN': False
            }
        }
        with with_site_configuration_context(configuration=SITE_CONFIGURATION):
            response = self.client.get(reverse('dashboard'))

        assert response.status_code == 200
        self.assertNotContains(response, 'Add Certificate to LinkedIn')

        response_url = 'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME'
        self.assertNotContains(response, escape(response_url))

    @skip_unless_lms
    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': False})
    def test_linked_in_add_to_profile_btn_with_certificate(self):
        # If user has a certificate with valid linked-in config then Add Certificate to LinkedIn button
        # should be visible. and it has URL value with valid parameters.
        self.client.login(username="jack", password=self.TEST_PASSWORD)

        linkedin_config = LinkedInAddToProfileConfiguration.objects.create(company_identifier='1337', enabled=True)
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='verified',
            expiration_datetime=datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        )
        CourseEnrollment.enroll(self.user, self.course.id, mode='honor')
        self.course.certificate_available_date = datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        self.course.start = datetime.now(ZoneInfo("UTC")) - timedelta(days=2)
        self.course.end = datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        self.course.display_name = 'Omega'
        self.course.course_organization = 'Omega Org'
        self.course = self.update_course(self.course, self.user.id)

        cert = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
            grade='67',
            download_url='https://www.edx.org'
        )
        # LinkedIn sharing disabled
        # When CERTIFICATE_LINKEDIN is set to False in site configuration,
        # the LinkedIn "Add to Profile" button should not be visible to users
        SITE_CONFIGURATION = {
            'SOCIAL_SHARING_SETTINGS': {
                'CERTIFICATE_LINKEDIN': True
            }
        }
        with with_site_configuration_context(configuration=SITE_CONFIGURATION):
            response = self.client.get(reverse('dashboard'))

        assert response.status_code == 200
        self.assertContains(response, 'Add Certificate to LinkedIn')

        expected_url = (
            'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&'
            'name={platform}+Honor+Code+Certificate+for+Omega&'
            'certUrl={cert_url}&'
            'organizationId={company_identifier}'
        ).format(
            platform=quote(settings.PLATFORM_NAME.encode('utf-8')),
            cert_url=quote(cert.download_url, safe=''),
            company_identifier=linkedin_config.company_identifier,
        )

        # Single assertion for the expected LinkedIn URL
        self.assertContains(response, escape(expected_url))

    @skip_unless_lms
    def test_dashboard_metadata_caching(self):
        """
        Check that the student dashboard makes use of course metadata caching.

        After creating a course, that course's metadata should be cached as a
        CourseOverview. The student dashboard should never have to make calls to
        the modulestore.

        Note to future developers:
            If you break this test so that the "check_mongo_calls(0)" fails,
            please do NOT change it to "check_mongo_calls(n>=1)". Instead, change
            your code to not load courses from the module store. This may
            involve adding fields to CourseOverview so that loading a full
            CourseBlock isn't necessary.
        """
        # Create a course and log in the user.
        # Creating a new course will trigger a publish event and the course will be cached
        test_course = CourseFactory.create(emit_signals=True)
        self.client.login(username="jack", password=self.TEST_PASSWORD)

        with check_mongo_calls(0):
            CourseEnrollment.enroll(self.user, test_course.id)

        # Subsequent requests will only result in SQL queries to load the
        # CourseOverview object that has been created.
        with check_mongo_calls(0):
            response_1 = self.client.get(reverse('dashboard'))
            assert response_1.status_code == 200
            response_2 = self.client.get(reverse('dashboard'))
            assert response_2.status_code == 200

    @skip_unless_lms
    def test_dashboard_header_nav_has_find_courses(self):
        self.client.login(username="jack", password=self.TEST_PASSWORD)
        response = self.client.get(reverse("dashboard"))

        # "Explore courses" is shown in the side panel
        self.assertContains(response, "Explore courses")

        # But other links are hidden in the navigation
        self.assertNotContains(response, "How it Works")
        self.assertNotContains(response, "Schools & Partners")

    def test_course_mode_info_with_honor_enrollment(self):
        """It will be true only if enrollment mode is honor and course has verified mode."""
        course_mode_info = self._enrollment_with_complete_course('honor')
        assert course_mode_info['show_upsell']
        assert course_mode_info['days_for_upsell'] == 1

    @ddt.data('verified', 'credit')
    def test_course_mode_info_with_different_enrollments(self, enrollment_mode):
        """If user enrollment mode is either verified or credit then show_upsell
        will be always false.
        """
        course_mode_info = self._enrollment_with_complete_course(enrollment_mode)
        assert not course_mode_info['show_upsell']
        assert course_mode_info['days_for_upsell'] is None

    def _enrollment_with_complete_course(self, enrollment_mode):
        """"Dry method for course enrollment."""
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='Verified',
            expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        )
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode=enrollment_mode)
        return complete_course_mode_info(self.course.id, enrollment)


@ddt.ddt
class DashboardTestsWithSiteOverrides(SiteMixin, ModuleStoreTestCase):
    """
    Tests for site settings overrides used when rendering the dashboard view
    """

    def setUp(self):
        super().setUp()
        self.org = 'fakeX'
        self.course = CourseFactory.create(org=self.org)
        self.user = UserFactory.create(username='jack', email='jack@fake.edx.org', password=self.TEST_PASSWORD)
        CourseModeFactory.create(mode_slug='no-id-professional', course_id=self.course.id)
        CourseEnrollment.enroll(self.user, self.course.location.course_key, mode='no-id-professional')
        cache.clear()

    @skip_unless_lms
    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': False})
    @ddt.data(
        ('testserver1.com', {'ENABLE_VERIFIED_CERTIFICATES': True}),
        ('testserver2.com', {'ENABLE_VERIFIED_CERTIFICATES': True, 'DISPLAY_COURSE_MODES_ON_DASHBOARD': True}),
    )
    @ddt.unpack
    def test_course_mode_visible(self, site_domain, site_configuration_values):
        """
        Test that the course mode for courses is visible on the dashboard
        when settings have been overridden by site configuration.
        """
        site_configuration_values.update({
            'SITE_NAME': site_domain,
            'course_org_filter': self.org
        })
        self.set_up_site(site_domain, site_configuration_values)
        self.client.login(username='jack', password=self.TEST_PASSWORD)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'class="course professional"')

    @skip_unless_lms
    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': False})
    @ddt.data(
        ('testserver3.com', {'ENABLE_VERIFIED_CERTIFICATES': False}),
        ('testserver4.com', {'DISPLAY_COURSE_MODES_ON_DASHBOARD': False}),
    )
    @ddt.unpack
    def test_course_mode_invisible(self, site_domain, site_configuration_values):
        """
        Test that the course mode for courses is invisible on the dashboard
        when settings have been overridden by site configuration.
        """
        site_configuration_values.update({
            'SITE_NAME': site_domain,
            'course_org_filter': self.org
        })
        self.set_up_site(site_domain, site_configuration_values)
        self.client.login(username='jack', password=self.TEST_PASSWORD)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'class="course professional"')


class UserSettingsEventTestMixin(EventTestMixin):
    """
    Mixin for verifying that user setting events were emitted during a test.
    """
    def setUp(self):  # lint-amnesty, pylint: disable=arguments-differ
        super().setUp('common.djangoapps.util.model_utils.tracker')

    def assert_user_setting_event_emitted(self, **kwargs):
        """
        Helper method to assert that we emit the expected user settings events.

        Expected settings are passed in via `kwargs`.
        """
        if 'truncated' not in kwargs:
            kwargs['truncated'] = []
        self.assert_event_emitted(
            USER_SETTINGS_CHANGED_EVENT_NAME,
            table=self.table,
            user_id=self.user.id,
            **kwargs
        )

    def assert_user_enrollment_occurred(self, course_key):
        """
        Helper method to assert that the user is enrolled in the given course.
        """
        assert CourseEnrollment.is_enrolled(self.user, CourseKey.from_string(course_key))


class EnrollmentEventTestMixin(EventTestMixin):
    """ Mixin with assertions for validating enrollment events. """
    def setUp(self):  # lint-amnesty, pylint: disable=arguments-differ
        super().setUp('common.djangoapps.student.models.course_enrollment.tracker')
        segment_patcher = patch('common.djangoapps.student.models.course_enrollment.segment')
        self.mock_segment_tracker = segment_patcher.start()
        self.addCleanup(segment_patcher.stop)

    def assert_enrollment_mode_change_event_was_emitted(self, user, course_key, mode, course, enrollment):
        """Ensures an enrollment mode change event was emitted"""
        self.mock_tracker.emit.assert_called_once_with(
            'edx.course.enrollment.mode_changed',
            {
                'course_id': str(course_key),
                'user_id': user.pk,
                'mode': mode
            }
        )
        self.mock_tracker.reset_mock()
        properties, traits = self._build_segment_properties_and_traits(user, course_key, course, enrollment)
        self.mock_segment_tracker.track.assert_called_once_with(
            user.id, 'edx.course.enrollment.mode_changed', properties, traits=traits
        )
        self.mock_segment_tracker.reset_mock()

    def assert_enrollment_event_was_emitted(self, user, course_key, course, enrollment):
        """Ensures an enrollment event was emitted since the last event related assertion"""
        self.mock_tracker.emit.assert_called_once_with(
            'edx.course.enrollment.activated',
            {
                'course_id': str(course_key),
                'user_id': user.pk,
                'mode': CourseMode.DEFAULT_MODE_SLUG
            }
        )
        self.mock_tracker.reset_mock()
        properties, traits = self._build_segment_properties_and_traits(user, course_key, course, enrollment, True)
        self.mock_segment_tracker.track.assert_called_once_with(
            user.id, 'edx.course.enrollment.activated', properties, traits=traits
        )
        self.mock_segment_tracker.reset_mock()

    def assert_unenrollment_event_was_emitted(self, user, course_key, course, enrollment):
        """Ensures an unenrollment event was emitted since the last event related assertion"""
        self.mock_tracker.emit.assert_called_once_with(
            'edx.course.enrollment.deactivated',
            {
                'course_id': str(course_key),
                'user_id': user.pk,
                'mode': CourseMode.DEFAULT_MODE_SLUG
            }
        )
        self.mock_tracker.reset_mock()
        properties, traits = self._build_segment_properties_and_traits(user, course_key, course, enrollment)
        self.mock_segment_tracker.track.assert_called_once_with(
            user.id, 'edx.course.enrollment.deactivated', properties, traits=traits
        )
        self.mock_segment_tracker.reset_mock()

    def _build_segment_properties_and_traits(self, user, course_key, course, enrollment, activated=False):
        """ Builds the segment properties and traits that are sent during enrollment events """
        properties = {
            'category': 'conversion',
            'label': str(course_key),
            'org': course_key.org,
            'course': course_key.course,
            'run': course_key.run,
            'mode': enrollment.mode,
        }
        traits = properties.copy()
        traits.update({'course_title': course.display_name, 'email': user.email})

        if activated:
            properties.update({
                'email': user.email,
                # This next property is for an experiment, see method's comments for more information
                # we will just hardcode the default value while the experiment runs
                'external_course_updates': -1,
                'course_start': course.start,
                'course_pacing': course.pacing,
            })
        return properties, traits


class EnrollInCourseTest(EnrollmentEventTestMixin, CacheIsolationTestCase):
    """Tests enrolling and unenrolling in courses."""

    @skip_unless_lms
    def test_enrollment(self):
        user = UserFactory.create(username="joe", email="joe@joe.com", password="password")
        course_id = CourseKey.from_string("edX/Test101/2013")
        course_id_partial = CourseKey.from_string("edX/Test101/")
        course = CourseOverviewFactory.create(id=course_id)

        # Test basic enrollment
        assert not CourseEnrollment.is_enrolled(user, course_id)
        assert not CourseEnrollment.is_enrolled_by_partial(user, course_id_partial)
        enrollment = CourseEnrollment.enroll(user, course_id)
        assert CourseEnrollment.is_enrolled(user, course_id)
        assert CourseEnrollment.is_enrolled_by_partial(user, course_id_partial)
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

        # Enrolling them again should be harmless
        enrollment = CourseEnrollment.enroll(user, course_id)
        assert CourseEnrollment.is_enrolled(user, course_id)
        assert CourseEnrollment.is_enrolled_by_partial(user, course_id_partial)
        self.assert_no_events_were_emitted()

        # Now unenroll the user
        CourseEnrollment.unenroll(user, course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)
        assert not CourseEnrollment.is_enrolled_by_partial(user, course_id_partial)
        self.assert_unenrollment_event_was_emitted(user, course_id, course, enrollment)

        # Unenrolling them again should also be harmless
        CourseEnrollment.unenroll(user, course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)
        assert not CourseEnrollment.is_enrolled_by_partial(user, course_id_partial)
        self.assert_no_events_were_emitted()

        # The enrollment record should still exist, just be inactive
        enrollment_record = CourseEnrollment.objects.get(
            user=user,
            course_id=course_id
        )
        assert not enrollment_record.is_active

        # Make sure mode is updated properly if user unenrolls & re-enrolls
        enrollment = CourseEnrollment.enroll(user, course_id, "verified")
        assert enrollment.mode == 'verified'
        CourseEnrollment.unenroll(user, course_id)
        enrollment = CourseEnrollment.enroll(user, course_id, "audit")
        assert CourseEnrollment.is_enrolled(user, course_id)
        assert enrollment.mode == 'audit'

    def test_enrollment_non_existent_user(self):
        # Testing enrollment of newly unsaved user (i.e. no database entry)
        user = UserFactory(username="rusty", email="rusty@fake.edx.org")
        course_id = CourseLocator("edX", "Test101", "2013")
        course = CourseOverviewFactory.create(id=course_id)

        assert not CourseEnrollment.is_enrolled(user, course_id)

        # Unenroll does nothing
        CourseEnrollment.unenroll(user, course_id)
        self.assert_no_events_were_emitted()

        # Implicit save() happens on new User object when enrolling, so this
        # should still work
        enrollment = CourseEnrollment.enroll(user, course_id)
        assert CourseEnrollment.is_enrolled(user, course_id)
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

    @skip_unless_lms
    def test_enrollment_by_email(self):
        user = UserFactory.create(username="jack", email="jack@fake.edx.org")
        course_id = CourseLocator("edX", "Test101", "2013")
        course = CourseOverviewFactory.create(id=course_id)

        enrollment = CourseEnrollment.enroll_by_email("jack@fake.edx.org", course_id)
        assert CourseEnrollment.is_enrolled(user, course_id)
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

        # This won't throw an exception, even though the user is not found
        assert CourseEnrollment.enroll_by_email('not_jack@fake.edx.org', course_id) is None
        self.assert_no_events_were_emitted()

        self.assertRaises(
            User.DoesNotExist,
            CourseEnrollment.enroll_by_email,
            "not_jack@fake.edx.org",
            course_id,
            ignore_errors=False
        )
        self.assert_no_events_were_emitted()

        # Now unenroll them by email
        CourseEnrollment.unenroll_by_email("jack@fake.edx.org", course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)
        self.assert_unenrollment_event_was_emitted(user, course_id, course, enrollment)

        # Harmless second unenroll
        CourseEnrollment.unenroll_by_email("jack@fake.edx.org", course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)
        self.assert_no_events_were_emitted()

        # Unenroll on non-existent user shouldn't throw an error
        CourseEnrollment.unenroll_by_email("not_jack@fake.edx.org", course_id)
        self.assert_no_events_were_emitted()

    @skip_unless_lms
    def test_enrollment_multiple_classes(self):
        user = UserFactory(username="rusty", email="rusty@fake.edx.org")
        course_id1 = CourseLocator("edX", "Test101", "2013")
        course_id2 = CourseLocator("MITx", "6.003z", "2012")
        course1 = CourseOverviewFactory.create(id=course_id1)
        course2 = CourseOverviewFactory.create(id=course_id2)

        enrollment1 = CourseEnrollment.enroll(user, course_id1)
        self.assert_enrollment_event_was_emitted(user, course_id1, course1, enrollment1)
        enrollment2 = CourseEnrollment.enroll(user, course_id2)
        self.assert_enrollment_event_was_emitted(user, course_id2, course2, enrollment2)
        assert CourseEnrollment.is_enrolled(user, course_id1)
        assert CourseEnrollment.is_enrolled(user, course_id2)

        CourseEnrollment.unenroll(user, course_id1)
        self.assert_unenrollment_event_was_emitted(user, course_id1, course1, enrollment1)
        assert not CourseEnrollment.is_enrolled(user, course_id1)
        assert CourseEnrollment.is_enrolled(user, course_id2)

        CourseEnrollment.unenroll(user, course_id2)
        self.assert_unenrollment_event_was_emitted(user, course_id2, course2, enrollment2)
        assert not CourseEnrollment.is_enrolled(user, course_id1)
        assert not CourseEnrollment.is_enrolled(user, course_id2)

    @skip_unless_lms
    def test_activation(self):
        user = UserFactory.create(username="jack", email="jack@fake.edx.org")
        course_id = CourseLocator("edX", "Test101", "2013")
        course = CourseOverviewFactory.create(id=course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)

        # Creating an enrollment doesn't actually enroll a student
        # (calling CourseEnrollment.enroll() would have)
        enrollment = CourseEnrollment.get_or_create_enrollment(user, course_id)
        assert not CourseEnrollment.is_enrolled(user, course_id)
        self.assert_no_events_were_emitted()

        # Until you explicitly activate it
        enrollment.activate()
        assert CourseEnrollment.is_enrolled(user, course_id)
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

        # Activating something that's already active does nothing
        enrollment.activate()
        assert CourseEnrollment.is_enrolled(user, course_id)
        self.assert_no_events_were_emitted()

        # Now deactivate
        enrollment.deactivate()
        assert not CourseEnrollment.is_enrolled(user, course_id)
        self.assert_unenrollment_event_was_emitted(user, course_id, course, enrollment)

        # Deactivating something that's already inactive does nothing
        enrollment.deactivate()
        assert not CourseEnrollment.is_enrolled(user, course_id)
        self.assert_no_events_were_emitted()

        # A deactivated enrollment should be activated if enroll() is called
        # for that user/course_id combination
        CourseEnrollment.enroll(user, course_id)
        assert CourseEnrollment.is_enrolled(user, course_id)
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

    def test_change_enrollment_modes(self):
        user = UserFactory.create(username="justin", email="jh@fake.edx.org")
        course_id = CourseLocator("edX", "Test101", "2013")
        course = CourseOverviewFactory.create(id=course_id)

        enrollment = CourseEnrollment.enroll(user, course_id, "audit")
        self.assert_enrollment_event_was_emitted(user, course_id, course, enrollment)

        enrollment = CourseEnrollment.enroll(user, course_id, "honor")
        self.assert_enrollment_mode_change_event_was_emitted(user, course_id, "honor", course, enrollment)

        # same enrollment mode does not emit an event
        enrollment = CourseEnrollment.enroll(user, course_id, "honor")
        self.assert_no_events_were_emitted()

        enrollment = CourseEnrollment.enroll(user, course_id, "audit")
        self.assert_enrollment_mode_change_event_was_emitted(user, course_id, "audit", course, enrollment)


@skip_unless_lms
@ddt.ddt
class ChangeEnrollmentViewTest(ModuleStoreTestCase):
    """Tests the student.views.change_enrollment view"""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.url = reverse('change_enrollment')

    def _enroll_through_view(self, course):
        """ Enroll a student in a course. """
        response = self.client.post(
            reverse('change_enrollment'), {
                'course_id': course.id,
                'enrollment_action': 'enroll'
            }
        )
        return response

    def test_enrollment_url_without_redirect(self):
        with override_waffle_switch(REDIRECT_TO_COURSEWARE_AFTER_ENROLLMENT, False):
            response = self._enroll_through_view(self.course)
        assert response.content.decode('utf8') == ''

    def test_enrollment_with_redirect(self):
        with override_waffle_switch(REDIRECT_TO_COURSEWARE_AFTER_ENROLLMENT, True):
            response = self._enroll_through_view(self.course)
        data = make_learning_mfe_courseware_url(self.course.id)
        assert response.content.decode('utf8') == data

    def test_enroll_as_default(self):
        """Tests that a student can successfully enroll through this view"""
        response = self._enroll_through_view(self.course)
        assert response.status_code == 200
        enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert is_active
        assert enrollment_mode == CourseMode.DEFAULT_MODE_SLUG

    def test_cannot_enroll_if_already_enrolled(self):
        """
        Tests that a student will not be able to enroll through this view if
        they are already enrolled in the course
        """
        CourseEnrollment.enroll(self.user, self.course.id)
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        # now try to enroll that student
        response = self._enroll_through_view(self.course)
        assert response.status_code == 400

    def test_change_to_default_if_verified(self):
        """
        Tests that a student that is a currently enrolled verified student cannot
        accidentally change their enrollment mode
        """
        CourseEnrollment.enroll(self.user, self.course.id, mode='verified')
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        # now try to enroll the student in the default mode:
        response = self._enroll_through_view(self.course)
        assert response.status_code == 400
        enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert is_active
        assert enrollment_mode == 'verified'

    def test_change_to_default_if_verified_not_active(self):
        """
        Tests that one can renroll for a course if one has already unenrolled
        """
        # enroll student
        CourseEnrollment.enroll(self.user, self.course.id, mode='verified')
        # now unenroll student:
        CourseEnrollment.unenroll(self.user, self.course.id)
        # check that they are verified but inactive
        enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert not is_active
        assert enrollment_mode == 'verified'
        # now enroll them through the view:
        response = self._enroll_through_view(self.course)
        assert response.status_code == 200
        enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert is_active
        assert enrollment_mode == CourseMode.DEFAULT_MODE_SLUG


class AnonymousLookupTable(ModuleStoreTestCase):
    """
    Tests for anonymous_id_functions
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='honor',
            mode_display_name='Honor Code',
        )
        self.user2 = UserFactory.create()
        patcher = patch('common.djangoapps.student.models.course_enrollment.tracker')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_user_over_multiple_sessions(self):
        """
        Anonymous ids are stored in AnonymousUserId model.
        This tests to make sure stored value is used rather than a creating a new one
        """
        anonymous_id_1 = anonymous_id_for_user(self.user, None)
        delattr(self.user, "_anonymous_id")  # pylint: disable=literal-used-as-attribute
        anonymous_id_2 = anonymous_id_for_user(self.user, None)
        assert anonymous_id_1 == anonymous_id_2

    def test_diff_anonymous_id_for_diff_users(self):
        anonymous_id_1 = anonymous_id_for_user(self.user, None)
        anonymous_id_2 = anonymous_id_for_user(self.user2, None)
        assert anonymous_id_1 != anonymous_id_2

    def test_for_unregistered_user(self):  # same path as for logged out user
        assert anonymous_id_for_user(AnonymousUser(), self.course.id) is None
        assert user_by_anonymous_id(None) is None

    def test_roundtrip_for_logged_user(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        anonymous_id = anonymous_id_for_user(self.user, self.course.id)
        real_user = user_by_anonymous_id(anonymous_id)
        assert self.user == real_user
        assert anonymous_id == anonymous_id_for_user(self.user, self.course.id)

    def test_roundtrip_with_unicode_course_id(self):
        course2 = CourseFactory.create(display_name="Omega Course Ω")
        CourseEnrollment.enroll(self.user, course2.id)
        anonymous_id = anonymous_id_for_user(self.user, course2.id)
        real_user = user_by_anonymous_id(anonymous_id)
        assert self.user == real_user
        assert anonymous_id == anonymous_id_for_user(self.user, course2.id)

    def test_anonymous_id_secret_key_changes_do_not_change_existing_anonymous_ids(self):
        """Test that a same anonymous id is returned when the SECRET_KEY changes."""
        CourseEnrollment.enroll(self.user, self.course.id)
        anonymous_id = anonymous_id_for_user(self.user, self.course.id)
        with override_settings(SECRET_KEY='some_new_and_totally_secret_key'):
            # Recreate user object to clear cached anonymous id.
            self.user = User.objects.get(pk=self.user.id)
            new_anonymous_id = anonymous_id_for_user(self.user, self.course.id)
            assert anonymous_id == new_anonymous_id
            assert self.user == user_by_anonymous_id(anonymous_id)
            assert self.user == user_by_anonymous_id(new_anonymous_id)

    def test_anonymous_id_secret_key_changes_result_in_diff_values_for_same_new_user(self):
        """Test that a different anonymous id is returned when the SECRET_KEY changes."""
        CourseEnrollment.enroll(self.user, self.course.id)
        anonymous_id = anonymous_id_for_user(self.user, self.course.id)
        with override_settings(SECRET_KEY='some_new_and_totally_secret_key'):
            # Recreate user object to clear cached anonymous id.
            self.user = User.objects.get(pk=self.user.id)
            AnonymousUserId.objects.filter(user=self.user).filter(course_id=self.course.id).delete()
            new_anonymous_id = anonymous_id_for_user(self.user, self.course.id)
            assert anonymous_id != new_anonymous_id
            assert self.user == user_by_anonymous_id(new_anonymous_id)


@skip_unless_lms
@patch('openedx.core.djangoapps.programs.utils.get_programs')
class RelatedProgramsTests(ProgramsApiConfigMixin, SharedModuleStoreTestCase):
    """Tests verifying that related programs appear on the course dashboard."""
    maxDiff = None
    related_programs_preface = 'Related Programs'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user = UserFactory(password=cls.TEST_PASSWORD)
        cls.course = CourseFactory()
        cls.enrollment = CourseEnrollmentFactory(user=cls.user, course_id=cls.course.id)  # pylint: disable=no-member

    def setUp(self):
        super().setUp()

        self.url = reverse('dashboard')

        self.create_programs_config()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        course_run = CourseRunFactory(key=str(self.course.id))  # pylint: disable=no-member
        course = CatalogCourseFactory(course_runs=[course_run])
        self.programs = [ProgramFactory(courses=[course]) for __ in range(2)]

    def assert_related_programs(self, response, are_programs_present=True):
        """Assertion for verifying response contents."""
        assertion = getattr(self, 'assert{}Contains'.format('' if are_programs_present else 'Not'))

        for program in self.programs:
            assertion(response, self.expected_link_text(program))

        assertion(response, self.related_programs_preface)

    def expected_link_text(self, program):
        """Construct expected dashboard link text."""
        return '{title} {type}'.format(title=program['title'], type=program['type'])

    def test_related_programs_listed(self, mock_get_programs):
        """Verify that related programs are listed when available."""
        mock_get_programs.return_value = self.programs

        response = self.client.get(self.url)
        self.assert_related_programs(response)

    def test_no_data_no_programs(self, mock_get_programs):
        """Verify that related programs aren't listed when none are available."""
        mock_get_programs.return_value = []

        response = self.client.get(self.url)
        self.assert_related_programs(response, are_programs_present=False)

    def test_unrelated_program_not_listed(self, mock_get_programs):
        """Verify that unrelated programs don't appear in the listing."""
        nonexistent_course_run_id = generate_course_run_key()

        course_run = CourseRunFactory(key=nonexistent_course_run_id)
        course = CatalogCourseFactory(course_runs=[course_run])
        unrelated_program = ProgramFactory(courses=[course])

        mock_get_programs.return_value = self.programs + [unrelated_program]

        response = self.client.get(self.url)
        self.assert_related_programs(response)
        self.assertNotContains(response, unrelated_program['title'])

    def test_program_title_unicode(self, mock_get_programs):
        """Verify that the dashboard can deal with programs whose titles contain Unicode."""
        self.programs[0]['title'] = 'Bases matemáticas para estudiar ingeniería'
        mock_get_programs.return_value = self.programs

        response = self.client.get(self.url)
        self.assert_related_programs(response)


class UserAttributeTests(TestCase):
    """Tests for the UserAttribute model."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.name = 'test'
        self.value = 'test-value'

    def test_get_set_attribute(self):
        assert UserAttribute.get_user_attribute(self.user, self.name) is None
        UserAttribute.set_user_attribute(self.user, self.name, self.value)
        assert UserAttribute.get_user_attribute(self.user, self.name) == self.value
        new_value = 'new_value'
        UserAttribute.set_user_attribute(self.user, self.name, new_value)
        assert UserAttribute.get_user_attribute(self.user, self.name) == new_value

    def test_unicode(self):
        UserAttribute.set_user_attribute(self.user, self.name, self.value)
        for field in (self.name, self.value, self.user.username):
            assert field in str(UserAttribute.objects.get(user=self.user))
