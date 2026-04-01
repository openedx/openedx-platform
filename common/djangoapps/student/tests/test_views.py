"""
Test the student dashboard view.
"""


import itertools
import json
import unittest
from datetime import datetime, timedelta  # lint-amnesty, pylint: disable=unused-import
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ddt
from completion.test_utils import CompletionWaffleTestMixin, submit_completions_for_testing
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.timezone import now
from milestones.tests.utils import MilestonesTestCaseMixin
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from pyquery import PyQuery as pq

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.helpers import DISABLE_UNENROLL_CERT_STATES
from common.djangoapps.student.models import CourseEnrollment, UserProfile
from common.djangoapps.student.signals import REFUND_ORDER
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.student.views.dashboard import check_for_unacknowledged_notices
from common.djangoapps.util.milestones_helpers import (
    get_course_milestones,
    remove_prerequisite_course,
    set_prerequisite_courses
)
from common.djangoapps.util.testing import UrlResetMixin  # lint-amnesty, pylint: disable=unused-import
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.commerce.utils import EcommerceService
from openedx.core.djangoapps.catalog.tests.factories import ProgramFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode
from xmodule.data import CertificatesDisplayBehaviors  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order

TOMORROW = now() + timedelta(days=1)
ONE_WEEK_AGO = now() - timedelta(weeks=1)
THREE_YEARS_FROM_NOW = now() + timedelta(days=365 * 3)
THREE_YEARS_AGO = now() - timedelta(days=365 * 3)

# Name of the method to mock for Content Type Gating.
GATING_METHOD_NAME = 'openedx.features.content_type_gating.models.ContentTypeGatingConfig.enabled_for_enrollment'

# Name of the method to mock for Course Duration Limits.
CDL_METHOD_NAME = 'openedx.features.course_duration_limits.models.CourseDurationLimitConfig.enabled_for_enrollment'


@ddt.ddt
@skip_unless_lms
class TestStudentDashboardUnenrollments(SharedModuleStoreTestCase):
    """
    Test to ensure that the student dashboard does not show the unenroll button for users with certificates.
    """
    UNENROLL_ELEMENT_ID = "#actions-item-unenroll-0"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.user = UserFactory()
        self.enrollment = CourseEnrollmentFactory(course_id=self.course.id, user=self.user)
        self.cert_status = 'processing'
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def mock_cert(self, _user, _course_overview):
        """ Return a preset certificate status. """
        return {
            'status': self.cert_status,
            'can_unenroll': self.cert_status not in DISABLE_UNENROLL_CERT_STATES,
            'download_url': 'fake_url',
            'linked_in_url': False,
            'grade': 100,
            'show_survey_button': False
        }

    @ddt.data(
        ('notpassing', 1),
        ('restricted', 1),
        ('processing', 1),
        ('generating', 0),
        ('downloadable', 0),
    )
    @ddt.unpack
    def test_unenroll_available(self, cert_status, unenroll_action_count):
        """ Assert that the unenroll action is shown or not based on the cert status."""
        self.cert_status = cert_status

        with patch('common.djangoapps.student.views.dashboard.cert_info', side_effect=self.mock_cert):
            response = self.client.get(reverse('dashboard'))

            assert pq(response.content)(self.UNENROLL_ELEMENT_ID).length == unenroll_action_count

    @ddt.data(
        ('notpassing', 200),
        ('restricted', 200),
        ('processing', 200),
        ('generating', 400),
        ('downloadable', 400),
    )
    @ddt.unpack
    @patch.object(CourseEnrollment, 'unenroll')
    def test_unenroll_request(self, cert_status, status_code, course_enrollment):
        """ Assert that the unenroll method is called or not based on the cert status"""
        self.cert_status = cert_status

        with patch('common.djangoapps.student.views.management.cert_info', side_effect=self.mock_cert):
            with patch('lms.djangoapps.commerce.signals.handle_refund_order') as mock_refund_handler:
                REFUND_ORDER.connect(mock_refund_handler)
                response = self.client.post(
                    reverse('change_enrollment'),
                    {'enrollment_action': 'unenroll', 'course_id': self.course.id}
                )

                assert response.status_code == status_code
                if status_code == 200:
                    course_enrollment.assert_called_with(self.user, self.course.id)
                    assert mock_refund_handler.called
                else:
                    course_enrollment.assert_not_called()

    def test_cant_unenroll_status(self):
        """ Assert that the dashboard loads when cert_status does not allow for unenrollment"""
        with patch(
            'lms.djangoapps.certificates.api.certificate_status_for_student',
            return_value={'status': 'downloadable'},
        ):
            response = self.client.get(reverse('dashboard'))

            assert response.status_code == 200

    def test_course_run_refund_status_successful(self):
        """ Assert that view:course_run_refund_status returns correct Json for successful refund call."""
        with patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.refundable', return_value=True):
            response = self.client.get(reverse('course_run_refund_status', kwargs={'course_id': self.course.id}))

        assert json.loads(response.content.decode('utf-8')) == {'course_refundable_status': True}
        assert response.status_code == 200

        REFUNDABLE_METHOD_NAME = 'common.djangoapps.student.models.course_enrollment.CourseEnrollment.refundable'
        with patch(REFUNDABLE_METHOD_NAME, return_value=False):
            response = self.client.get(reverse('course_run_refund_status', kwargs={'course_id': self.course.id}))

        assert json.loads(response.content.decode('utf-8')) == {'course_refundable_status': False}
        assert response.status_code == 200

    def test_course_run_refund_status_invalid_course_key(self):
        """ Assert that view:course_run_refund_status returns correct Json for Invalid Course Key ."""
        test_url = reverse('course_run_refund_status', kwargs={'course_id': self.course.id})
        with patch('common.djangoapps.student.views.management.CourseKey.from_string') as mock_method:
            mock_method.side_effect = InvalidKeyError(
                'CourseKey',
                'The course key used to get refund status caused InvalidKeyError during look up.'
            )
            response = self.client.get(test_url)

        assert json.loads(response.content.decode('utf-8')) == {'course_refundable_status': ''}
        assert response.status_code == 406


@ddt.ddt
@skip_unless_lms
class StudentDashboardTests(SharedModuleStoreTestCase, MilestonesTestCaseMixin, CompletionWaffleTestMixin):
    """
    Tests for the student dashboard.
    """

    EMAIL_SETTINGS_ELEMENT_ID = "#actions-item-email-settings-0"
    ENABLED_SIGNALS = ['course_published']
    MOCK_SETTINGS = {
        'FEATURES': {
            'DISABLE_START_DATES': False,
            'ENABLE_MKTG_SITE': True,
            'DISABLE_SET_JWT_COOKIES_FOR_TESTS': True,
        },
        'SOCIAL_SHARING_SETTINGS': {
            'CUSTOM_COURSE_URLS': True,
            'DASHBOARD_FACEBOOK': True,
            'DASHBOARD_TWITTER': True,
        },
    }
    MOCK_SETTINGS_HIDE_COURSES = {
        'FEATURES': {
            'HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED': True,
            'DISABLE_SET_JWT_COOKIES_FOR_TESTS': True,
        }
    }

    def setUp(self):
        """
        Create a course and user, then log in.
        """
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.path = reverse('dashboard')

    def set_course_sharing_urls(self, set_marketing, set_social_sharing):
        """
        Set course sharing urls (i.e. social_sharing_url, marketing_url)
        """
        course_overview = self.course_enrollment.course_overview
        if set_marketing:
            course_overview.marketing_url = 'http://www.testurl.com/marketing/url/'

        if set_social_sharing:
            course_overview.social_sharing_url = 'http://www.testurl.com/social/url/'

        course_overview.save()

    def test_redirect_account_settings(self):
        """
        Verify if user does not have profile he/she is redirected to account_settings.
        """
        UserProfile.objects.get(user=self.user).delete()
        response = self.client.get(self.path)
        self.assertRedirects(response, settings.ACCOUNT_MICROFRONTEND_URL, target_status_code=302)

    @patch('common.djangoapps.student.views.dashboard.learner_home_mfe_enabled')
    def test_redirect_to_learner_home(self, mock_learner_home_mfe_enabled):
        """
        if learner home mfe is enabled, redirect to learner home mfe
        """
        mock_learner_home_mfe_enabled.return_value = True
        response = self.client.get(self.path)
        self.assertRedirects(response, settings.LEARNER_HOME_MICROFRONTEND_URL, fetch_redirect_response=False)

    def test_course_cert_available_message_after_course_end(self):
        course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        course = CourseOverviewFactory.create(
            id=course_key,
            end_date=THREE_YEARS_AGO,
            certificate_available_date=TOMORROW,
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
            lowest_passing_grade=0.3
        )
        CourseEnrollmentFactory(course_id=course.id, user=self.user, mode=CourseMode.VERIFIED)
        GeneratedCertificateFactory(
            status=CertificateStatuses.downloadable, course_id=course.id, user=self.user, grade=0.45
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Your grade and certificate will be ready after')

    def test_course_cert_available_message_same_day_as_course_end(self):
        course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        course = CourseOverviewFactory.create(
            id=course_key,
            end_date=TOMORROW,
            certificate_available_date=TOMORROW,
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
            lowest_passing_grade=0.3
        )
        CourseEnrollmentFactory(course_id=course.id, user=self.user, mode=CourseMode.VERIFIED)
        GeneratedCertificateFactory(
            status=CertificateStatuses.downloadable, course_id=course.id, user=self.user, grade=0.45
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Your grade and certificate will be ready after')

    def test_cert_available_message_after_course_end(self):
        course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        course = CourseOverviewFactory.create(
            id=course_key,
            end_date=ONE_WEEK_AGO,
            certificate_available_date=now(),
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
            lowest_passing_grade=0.3
        )
        CourseEnrollmentFactory(course_id=course.id, user=self.user, mode=CourseMode.VERIFIED)
        GeneratedCertificateFactory(
            status=CertificateStatuses.downloadable, course_id=course.id, user=self.user, grade=0.45
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Congratulations! Your certificate is ready.')

    @patch.multiple('django.conf.settings', **MOCK_SETTINGS)
    @ddt.data(
        *itertools.product(
            [True, False],
            [True, False],
        )
    )
    @ddt.unpack
    def test_sharing_icons_for_future_course(self, set_marketing, set_social_sharing):
        """
        Verify that the course sharing icons show up if course is starting in future and
        any of marketing or social sharing urls are set.
        """
        self.course = CourseFactory.create(start=TOMORROW, emit_signals=True)  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.course_enrollment = CourseEnrollmentFactory(course_id=self.course.id, user=self.user)  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.set_course_sharing_urls(set_marketing, set_social_sharing)

        # Assert course sharing icons
        response = self.client.get(reverse('dashboard'))
        assert ('Share on Twitter' in response.content.decode('utf-8')) == (set_marketing or set_social_sharing)
        assert ('Share on Facebook' in response.content.decode('utf-8')) == (set_marketing or set_social_sharing)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_PREREQUISITE_COURSES': True})
    def test_pre_requisites_appear_on_dashboard(self):
        """
        When a course has a prerequisite, the dashboard should display the prerequisite.
        If we remove the prerequisite and access the dashboard again, the prerequisite
        should not appear.
        """
        self.pre_requisite_course = CourseFactory.create(org='edx', number='999', display_name='Pre requisite Course')  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.course = CourseFactory.create(  # lint-amnesty, pylint: disable=attribute-defined-outside-init
            org='edx',
            number='998',
            display_name='Test Course',
            pre_requisite_courses=[str(self.pre_requisite_course.id)]
        )
        self.course_enrollment = CourseEnrollmentFactory(course_id=self.course.id, user=self.user)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        set_prerequisite_courses(self.course.id, [str(self.pre_requisite_course.id)])
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '<div class="prerequisites">')

        remove_prerequisite_course(self.course.id, get_course_milestones(self.course.id)[0])
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, '<div class="prerequisites">')

    @patch('openedx.core.djangoapps.programs.utils.get_programs')
    @patch('common.djangoapps.student.views.dashboard.get_visible_sessions_for_entitlement')
    @patch('common.djangoapps.student.views.dashboard.get_pseudo_session_for_entitlement')
    @patch.object(CourseOverview, 'get_from_id')
    def test_unfulfilled_entitlement(self, mock_course_overview, mock_pseudo_session,
                                     mock_course_runs, mock_get_programs):
        """
        When a learner has an unfulfilled entitlement, their course dashboard should have:
            - a hidden 'View Course' button
            - the text 'In order to view the course you must select a session:'
            - an unhidden course-entitlement-selection-container
            - a related programs message
        """
        program = ProgramFactory()
        CourseEntitlementFactory.create(user=self.user, course_uuid=program['courses'][0]['uuid'])
        mock_get_programs.return_value = [program]
        course_key = CourseKey.from_string('course-v1:FAKE+FA1-MA1.X+3T2017')
        mock_course_overview.return_value = CourseOverviewFactory.create(start=TOMORROW, id=course_key)
        mock_course_runs.return_value = [
            {
                'key': str(course_key),
                'enrollment_end': str(TOMORROW),
                'pacing_type': 'instructor_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        mock_pseudo_session.return_value = {
            'key': str(course_key),
            'type': 'verified'
        }
        response = self.client.get(self.path)
        self.assertContains(response, 'class="course-target-link enter-course hidden"')
        self.assertContains(response, 'You must select a session to access the course.')
        self.assertContains(response, '<div class="course-entitlement-selection-container ">')
        self.assertContains(response, 'Related Programs:')

        # If an entitlement has already been redeemed by the user for a course run, do not let the run be selectable
        enrollment = CourseEnrollmentFactory(
            user=self.user, course=mock_course_overview.return_value, mode=CourseMode.VERIFIED
        )
        CourseEntitlementFactory.create(
            user=self.user, course_uuid=program['courses'][0]['uuid'], enrollment_course_run=enrollment
        )

        mock_course_runs.return_value = [
            {
                'key': 'course-v1:edX+toy+2012_Fall',
                'enrollment_end': str(TOMORROW),
                'pacing_type': 'instructor_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        response = self.client.get(self.path)
        # There should be two entitlements on the course page, one prompting for a mandatory session, but no
        # select option for the courses as there is only the single course run which has already been redeemed
        self.assertContains(response, '<li class="course-item">', count=2)
        self.assertContains(response, 'You must select a session to access the course.')
        self.assertNotContains(response, 'To access the course, select a session.')

    @patch('common.djangoapps.student.views.dashboard.get_visible_sessions_for_entitlement')
    @patch.object(CourseOverview, 'get_from_id')
    def test_unfulfilled_expired_entitlement(self, mock_course_overview, mock_course_runs):
        """
        When a learner has an unfulfilled, expired entitlement, a card should NOT appear on the dashboard.
        This use case represents either an entitlement that the user waited too long to fulfill, or an entitlement
        for which they received a refund.
        """
        CourseEntitlementFactory(
            user=self.user,
            created=THREE_YEARS_AGO,
            expired_at=now()
        )
        mock_course_overview.return_value = CourseOverviewFactory(start=TOMORROW)
        mock_course_runs.return_value = [
            {
                'key': 'course-v1:FAKE+FA1-MA1.X+3T2017',
                'enrollment_end': str(TOMORROW),
                'pacing_type': 'instructor_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        response = self.client.get(self.path)
        self.assertNotContains(response, '<li class="course-item">')

    @patch('common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course')
    @patch.object(CourseOverview, 'get_from_id')
    def test_sessions_for_entitlement_course_runs(self, mock_course_overview, mock_course_runs):
        """
        When a learner has a fulfilled entitlement for a course run in the past, there should be no availableSession
        data passed to the JS view. When a learner has a fulfilled entitlement for a course run enrollment ending in the
        future, there should not be an empty availableSession variable. When a learner has a fulfilled entitlement
        for a course that doesn't have an enrollment ending, there should not be an empty availableSession variable.

        NOTE: We commented out the assertions to move this to the catalog utils test suite.
        """
        # noAvailableSessions = "availableSessions: '[]'"

        # Test an enrollment end in the past
        mocked_course_overview = CourseOverviewFactory.create(
            start=TOMORROW, end=THREE_YEARS_FROM_NOW, self_paced=True, enrollment_end=THREE_YEARS_AGO
        )
        mock_course_overview.return_value = mocked_course_overview
        course_enrollment = CourseEnrollmentFactory(user=self.user, course_id=str(mocked_course_overview.id))
        mock_course_runs.return_value = [
            {
                'key': str(mocked_course_overview.id),
                'enrollment_end': str(mocked_course_overview.enrollment_end),
                'pacing_type': 'self_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        CourseEntitlementFactory(user=self.user, enrollment_course_run=course_enrollment)
        # response = self.client.get(self.path)
        # self.assertIn(noAvailableSessions, response.content)

        # Test an enrollment end in the future sets an availableSession
        mocked_course_overview.enrollment_end = TOMORROW
        mocked_course_overview.save()

        mock_course_overview.return_value = mocked_course_overview
        mock_course_runs.return_value = [
            {
                'key': str(mocked_course_overview.id),
                'enrollment_end': str(mocked_course_overview.enrollment_end),
                'pacing_type': 'self_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        # response = self.client.get(self.path)
        # self.assertNotIn(noAvailableSessions, response.content)

        # Test an enrollment end that doesn't exist sets an availableSession
        mocked_course_overview.enrollment_end = None
        mocked_course_overview.save()

        mock_course_overview.return_value = mocked_course_overview
        mock_course_runs.return_value = [
            {
                'key': str(mocked_course_overview.id),
                'enrollment_end': None,
                'pacing_type': 'self_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        # response = self.client.get(self.path)
        # self.assertNotIn(noAvailableSessions, response.content)

    @patch('openedx.core.djangoapps.programs.utils.get_programs')
    @patch('common.djangoapps.student.views.dashboard.get_visible_sessions_for_entitlement')
    @patch.object(CourseOverview, 'get_from_id')
    def test_fulfilled_entitlement(self, mock_course_overview, mock_course_runs, mock_get_programs):
        """
        When a learner has a fulfilled entitlement, their course dashboard should have:
            - exactly one course item, meaning it:
                - has an entitlement card
                - does NOT have a course card referencing the selected session
            - an unhidden Change or Leave Session button
            - a related programs message
        """
        mocked_course_overview = CourseOverviewFactory(
            start=TOMORROW, self_paced=True, enrollment_end=TOMORROW
        )
        mock_course_overview.return_value = mocked_course_overview
        course_enrollment = CourseEnrollmentFactory(user=self.user, course_id=str(mocked_course_overview.id))
        mock_course_runs.return_value = [
            {
                'key': str(mocked_course_overview.id),
                'enrollment_end': str(mocked_course_overview.enrollment_end),
                'pacing_type': 'self_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        entitlement = CourseEntitlementFactory(user=self.user, enrollment_course_run=course_enrollment)
        program = ProgramFactory()
        program['courses'][0]['course_runs'] = [{'key': str(mocked_course_overview.id)}]
        program['courses'][0]['uuid'] = entitlement.course_uuid
        mock_get_programs.return_value = [program]
        response = self.client.get(self.path)
        self.assertContains(response, '<li class="course-item">', count=1)
        self.assertContains(response, '<button class="change-session btn-link "')
        self.assertContains(response, 'Related Programs:')

    @patch('openedx.core.djangoapps.programs.utils.get_programs')
    @patch('common.djangoapps.student.views.dashboard.get_visible_sessions_for_entitlement')
    @patch.object(CourseOverview, 'get_from_id')
    def test_fulfilled_expired_entitlement(self, mock_course_overview, mock_course_runs, mock_get_programs):
        """
        When a learner has a fulfilled entitlement that is expired, their course dashboard should have:
            - exactly one course item, meaning it:
                - has an entitlement card
            - Message that the learner can no longer change sessions
            - a related programs message
        """
        mocked_course_overview = CourseOverviewFactory(
            start=TOMORROW, self_paced=True, enrollment_end=TOMORROW
        )
        mock_course_overview.return_value = mocked_course_overview
        course_enrollment = CourseEnrollmentFactory(user=self.user, course_id=str(mocked_course_overview.id), created=THREE_YEARS_AGO)  # lint-amnesty, pylint: disable=line-too-long
        mock_course_runs.return_value = [
            {
                'key': str(mocked_course_overview.id),
                'enrollment_end': str(mocked_course_overview.enrollment_end),
                'pacing_type': 'self_paced',
                'type': 'verified',
                'status': 'published'
            }
        ]
        entitlement = CourseEntitlementFactory(user=self.user, enrollment_course_run=course_enrollment, created=THREE_YEARS_AGO)  # lint-amnesty, pylint: disable=line-too-long
        program = ProgramFactory()
        program['courses'][0]['course_runs'] = [{'key': str(mocked_course_overview.id)}]
        program['courses'][0]['uuid'] = entitlement.course_uuid
        mock_get_programs.return_value = [program]
        response = self.client.get(self.path)
        self.assertContains(response, '<li class="course-item">', count=1)
        self.assertContains(response, 'You can no longer change sessions.')
        self.assertContains(response, 'Related Programs:')

    @patch('openedx.core.djangoapps.catalog.utils.get_course_runs_for_course')
    @patch('common.djangoapps.student.views.dashboard.is_bulk_email_feature_enabled')
    def test_email_settings_fulfilled_entitlement(self, mock_email_feature, mock_get_course_runs):
        """
        Assert that the Email Settings action is shown when the user has a fulfilled entitlement.
        """
        mock_email_feature.return_value = True
        course_overview = CourseOverviewFactory(
            start=TOMORROW, self_paced=True, enrollment_end=TOMORROW
        )
        course_enrollment = CourseEnrollmentFactory(user=self.user, course_id=course_overview.id)
        entitlement = CourseEntitlementFactory(user=self.user, enrollment_course_run=course_enrollment)
        course_runs = [{
            'key': str(course_overview.id),
            'uuid': entitlement.course_uuid
        }]
        mock_get_course_runs.return_value = course_runs

        response = self.client.get(self.path)
        assert pq(response.content)(self.EMAIL_SETTINGS_ELEMENT_ID).length == 1

    @patch.object(CourseOverview, 'get_from_id')
    @patch('common.djangoapps.student.views.dashboard.is_bulk_email_feature_enabled')
    def test_email_settings_unfulfilled_entitlement(self, mock_email_feature, mock_course_overview):
        """
        Assert that the Email Settings action is not shown when the entitlement is not fulfilled.
        """
        mock_email_feature.return_value = True
        mock_course_overview.return_value = CourseOverviewFactory(start=TOMORROW)
        CourseEntitlementFactory(user=self.user)
        response = self.client.get(self.path)
        assert pq(response.content)(self.EMAIL_SETTINGS_ELEMENT_ID).length == 0

    @patch.multiple('django.conf.settings', **MOCK_SETTINGS_HIDE_COURSES)
    def test_hide_dashboard_courses_until_activated(self):
        """
        Verify that when the HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED feature is enabled,
        inactive users don't see the Courses list, but active users still do.
        """
        # Ensure active users see the course list
        assert self.user.is_active
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'You are not enrolled in any courses yet.')

        # Ensure inactive users don't see the course list
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'You are not enrolled in any courses yet.')

    def test_show_empty_dashboard_message(self):
        """
        Verify that when the EMPTY_DASHBOARD_MESSAGE feature is set,
        its text is displayed in an empty courses list.
        """
        empty_dashboard_message = "Check out our lovely <i>free</i> courses!"
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'You are not enrolled in any courses yet.')
        self.assertNotContains(response, empty_dashboard_message)

        with with_site_configuration_context(configuration={
            "EMPTY_DASHBOARD_MESSAGE": empty_dashboard_message,
        }):
            response = self.client.get(reverse('dashboard'))
            self.assertContains(response, 'You are not enrolled in any courses yet.')
            self.assertContains(response, empty_dashboard_message)

    @patch('django.conf.settings.DASHBOARD_COURSE_LIMIT', 1)
    def test_course_limit_on_dashboard(self):
        course = CourseFactory.create()
        CourseEnrollmentFactory(
            user=self.user,
            course_id=course.id
        )

        course_v1 = CourseFactory.create()
        CourseEnrollmentFactory(
            user=self.user,
            course_id=course_v1.id
        )

        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '1 results successfully populated')

    @staticmethod
    def _remove_whitespace_from_html_string(html):
        return ''.join(html.split())

    @staticmethod
    def _remove_whitespace_from_response(response):
        return ''.join(response.content.decode('utf-8').split())

    @staticmethod
    def _pull_course_run_from_course_key(course_key: CourseKey):  # lint-amnesty, pylint: disable=missing-function-docstring
        return course_key.run.replace('_', ' ')

    @staticmethod
    def _get_html_for_view_course_button(course_key_string, course_run_string):
        return '''
            <a href="http://learning-mfe/course/{course_key}/home"
               class="course-target-link enter-course"
               data-course-key="{course_key}">
              View Course
              <span class="sr">
                &nbsp;{course_run}
              </span>
            </a>
        '''.format(course_key=course_key_string, course_run=course_run_string)

    @staticmethod
    def _get_html_for_resume_course_button(course_key_string, resume_block_key_string, course_run_string):
        return '''
            <a href="/courses/{course_key}/jump_to/{url_to_block}"
               class="course-target-link enter-course"
               data-course-key="{course_key}">
              Resume Course
              <span class="sr">
                &nbsp;{course_run}
              </span>
            </a>
        '''.format(
            course_key=course_key_string,
            url_to_block=resume_block_key_string,
            course_run=course_run_string
        )

    @staticmethod
    def _get_html_for_entitlement_button(course_key: CourseKey):
        return'''
            <div class="course-info">
            <span class="info-university">{org} - </span>
            <span class="info-course-id">{course}</span>
            <span class="info-date-block-container">
            <button class="change-session btn-link ">Change or Leave Session</button>
            </span>
            </div>
        '''.format(
            org=course_key.org,
            course=course_key.course,
        )

    def test_view_course_appears_on_dashboard(self):
        """
        When a course doesn't have completion data, its course card should
        display a "View Course" button.
        """
        self.override_waffle_switch(True)

        course = CourseFactory.create()
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course.id
        )

        response = self.client.get(reverse('dashboard'))

        course_key_string = str(course.id)
        # No completion data means there's no block from which to resume.
        resume_block_key_string = ''
        course_run_string = self._pull_course_run_from_course_key(course.id)

        view_button_html = self._get_html_for_view_course_button(
            course_key_string,
            course_run_string
        )
        resume_button_html = self._get_html_for_resume_course_button(
            course_key_string,
            resume_block_key_string,
            course_run_string
        )

        view_button_html = self._remove_whitespace_from_html_string(view_button_html)
        resume_button_html = self._remove_whitespace_from_html_string(resume_button_html)
        dashboard_html = self._remove_whitespace_from_response(response)

        assert view_button_html in dashboard_html
        assert resume_button_html not in dashboard_html

    def test_resume_course_appears_on_dashboard(self):
        """
        When a course has completion data, its course card should display a
        "Resume Course" button.
        """
        self.override_waffle_switch(True)

        course = CourseFactory.create()
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course.id
        )

        course_key = course.id
        block_keys = [
            BlockFactory.create(
                category='video',
                parent_location=course.location,
                display_name=f'Video {str(number)}'
            ).location
            for number in range(5)
        ]

        submit_completions_for_testing(self.user, block_keys)

        response = self.client.get(reverse('dashboard'))

        course_key_string = str(course_key)
        resume_block_key_string = str(block_keys[-1])
        course_run_string = self._pull_course_run_from_course_key(course_key)

        view_button_html = self._get_html_for_view_course_button(
            course_key_string,
            course_run_string
        )
        resume_button_html = self._get_html_for_resume_course_button(
            course_key_string,
            resume_block_key_string,
            course_run_string
        )

        view_button_html = self._remove_whitespace_from_html_string(view_button_html)
        resume_button_html = self._remove_whitespace_from_html_string(resume_button_html)
        dashboard_html = self._remove_whitespace_from_response(response)

        assert resume_button_html in dashboard_html
        assert view_button_html not in dashboard_html

    def test_content_gating_course_card_changes(self):
        """
        When a course is expired, the links on the course card should be removed.
        Links will be removed from the course title, course image and button (View Course/Resume Course).
        The course card should have an access expired message.
        """
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=THREE_YEARS_AGO - timedelta(days=30))
        self.override_waffle_switch(True)

        course = CourseFactory.create(start=THREE_YEARS_AGO)
        add_course_mode(course, mode_slug=CourseMode.AUDIT)
        add_course_mode(course)
        enrollment = CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course.id
        )
        enrollment.created = THREE_YEARS_AGO + timedelta(days=1)
        enrollment.save()

        response = self.client.get(reverse('dashboard'))
        dashboard_html = self._remove_whitespace_from_response(response)
        access_expired_substring = 'Accessexpired'
        course_link_class = 'course-target-link'

        assert course_link_class not in dashboard_html

        assert access_expired_substring in dashboard_html

    def test_dashboard_with_resume_buttons_and_view_buttons(self):
        '''
        The Test creates a four-course-card dashboard. The user completes course
        blocks in the even-numbered course cards. The test checks that courses
        with completion data have course cards with "Resume Course" buttons;
        those without have "View Course" buttons.

        '''
        self.override_waffle_switch(True)

        isEven = lambda n: n % 2 == 0

        num_course_cards = 4

        html_for_view_buttons = []
        html_for_resume_buttons = []
        html_for_entitlement = []

        for i in range(num_course_cards):

            course = CourseFactory.create()
            course_enrollment = CourseEnrollmentFactory(
                user=self.user,
                course_id=course.id
            )

            course_key = course_enrollment.course_id
            course_key_string = str(course_key)

            if i == 1:
                CourseEntitlementFactory.create(user=self.user, enrollment_course_run=course_enrollment)

            else:
                last_completed_block_string = ''
                course_run_string = self._pull_course_run_from_course_key(course_key)

            # Submit completed course blocks in even-numbered courses.
            if isEven(i):
                block_keys = [
                    BlockFactory.create(
                        category='video',
                        parent_location=course.location,
                        display_name=f'Video {str(number)}'
                    ).location
                    for number in range(5)
                ]
                last_completed_block_string = str(block_keys[-1])

                submit_completions_for_testing(self.user, block_keys)

            html_for_view_buttons.append(
                self._get_html_for_view_course_button(
                    course_key_string,
                    course_run_string
                )
            )
            html_for_resume_buttons.append(
                self._get_html_for_resume_course_button(
                    course_key_string,
                    last_completed_block_string,
                    course_run_string
                )
            )
            html_for_entitlement.append(
                self._get_html_for_entitlement_button(course_key)
            )

        response = self.client.get(reverse('dashboard'))

        html_for_view_buttons = [
            self._remove_whitespace_from_html_string(button)
            for button in html_for_view_buttons
        ]
        html_for_resume_buttons = [
            self._remove_whitespace_from_html_string(button)
            for button in html_for_resume_buttons
        ]
        html_for_entitlement = [
            self._remove_whitespace_from_html_string(button)
            for button in html_for_entitlement
        ]

        dashboard_html = self._remove_whitespace_from_response(response)

        for i in range(num_course_cards):
            expected_button = None
            unexpected_button = None

            if i == 1:
                expected_button = html_for_entitlement[i]
                unexpected_button = html_for_view_buttons[i] + html_for_resume_buttons[i]

            elif isEven(i):
                expected_button = html_for_resume_buttons[i]
                unexpected_button = html_for_view_buttons[i] + html_for_entitlement[i]
            else:
                expected_button = html_for_view_buttons[i]
                unexpected_button = html_for_resume_buttons[i] + html_for_entitlement[i]

            assert expected_button in dashboard_html
            assert unexpected_button not in dashboard_html

    @ddt.data(
        # Ecommerce is not enabled
        (False, True, False, 'abcdef', False),
        # No verified mode
        (True, False, False, 'abcdef', False),
        # User has an entitlement
        (True, True, True, 'abcdef', False),
        # No SKU
        (True, True, False, None, False),
        (True, True, False, 'abcdef', True)
    )
    @ddt.unpack
    def test_course_upgrade_notification(
        self, ecommerce_enabled, has_verified_mode, has_entitlement, sku, should_display
    ):
        """
        Upgrade notification for a course should appear if:
            - Ecommerce service is enabled
            - The course has a paid/verified mode
            - The user doesn't have an entitlement for the course
            - The course has an associated SKU
        """
        with patch.object(EcommerceService, 'is_enabled', return_value=ecommerce_enabled):
            course = CourseFactory.create()

            if has_verified_mode:
                CourseModeFactory.create(
                    course_id=course.id,
                    mode_slug='verified',
                    mode_display_name='Verified',
                    expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=1),
                    sku=sku
                )

            enrollment = CourseEnrollmentFactory(
                user=self.user,
                course_id=course.id
            )

            if has_entitlement:
                CourseEntitlementFactory(user=self.user, enrollment_course_run=enrollment)

            response = self.client.get(reverse('dashboard'))
            html_fragment = '<div class="message message-upsell has-actions is-shown">'
            if should_display:
                self.assertContains(response, html_fragment)
            else:
                self.assertNotContains(response, html_fragment)

    @ddt.data(
        # gated_content_on, course_duration_limits_on, upgrade_message
        (True, True, 'Upgrade to get full access to the course material'),
        (True, False, 'Upgrade to earn'),
        (False, True, 'Upgrade to earn'),
        (False, False, 'Upgrade to earn'),
    )
    @ddt.unpack
    def test_happy_path_upgrade_message(
        self,
        gated_content_on,
        course_duration_limits_on,
        upgrade_message
    ):
        """
        Upgrade message should be different for a course depending if it's happy or non-happy path.
        Happy path requirements:
        - Learner can upgrade (verified_mode)
        - FBE is on (has an audit_access_deadline and is able to see gated_content)
        """
        with patch.object(EcommerceService, 'is_enabled', return_value=True):
            course = CourseFactory.create()
            CourseEnrollmentFactory.create(
                user=self.user,
                course_id=course.id
            )

            CourseModeFactory.create(
                course_id=course.id,
                mode_slug='verified',
                mode_display_name='Verified',
                min_price=149,
                sku='abcdef',
            )

            with patch(GATING_METHOD_NAME, return_value=gated_content_on):
                with patch(CDL_METHOD_NAME, return_value=course_duration_limits_on):
                    response = self.client.get(reverse('dashboard'))
                    self.assertContains(response, upgrade_message)


@skip_unless_lms
@unittest.skipUnless(settings.FEATURES.get("ENABLE_NOTICES"), 'Notices plugin is not enabled')
class TestCourseDashboardNoticesRedirects(SharedModuleStoreTestCase):
    """
    Tests for the Dashboard redirect functionality introduced via the Notices plugin.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.path = reverse('dashboard')

    def test_check_for_unacknowledged_notices(self):
        """
        Happy path. Verifies that we return a URL in the proper form for a user that has an unack'd Notice.
        """
        context = {
            "plugins": {
                "notices": {
                    "unacknowledged_notices": [
                        '/notices/render/1/',
                        '/notices/render/2/',
                    ],
                }
            }
        }

        path = reverse("notices:notice-detail", kwargs={"pk": 1})
        expected_results = f"{settings.LMS_ROOT_URL}{path}?next={settings.LMS_ROOT_URL}/dashboard/"

        results = check_for_unacknowledged_notices(context)
        assert results == expected_results

    def test_check_for_unacknowledged_notices_no_unacknowledged_notices(self):
        """
        Verifies that we will return None if the user has no unack'd Notices in the plugin context data.
        """
        context = {
            "plugins": {
                "notices": {
                    "unacknowledged_notices": [],
                }
            }
        }

        results = check_for_unacknowledged_notices(context)
        assert results is None

    def test_check_for_unacknowledged_notices_incorrect_data(self):
        """
        Verifies that we will return None (and no Exceptions are thrown) if the plugin context data doesn't match the
        expected form.
        """
        context = {
            "plugins": {
                "notices": {
                    "incorrect_key": [
                        '/notices/render/1/',
                        '/notices/render/2/',
                    ],
                }
            }
        }

        results = check_for_unacknowledged_notices(context)

        assert results is None

    @patch('common.djangoapps.student.views.dashboard.check_for_unacknowledged_notices')
    def test_user_with_unacknowledged_notice(self, mock_notices):
        """
        Verifies that we will redirect the learner to the URL returned from the `check_for_unacknowledged_notices`
        function.
        """
        mock_notices.return_value = reverse("about")

        with override_settings(FEATURES={**settings.FEATURES, 'ENABLE_NOTICES': True}):
            response = self.client.get(self.path)

        assert response.status_code == 302
        assert response.url == "/about"
        mock_notices.assert_called_once()

    @patch('common.djangoapps.student.views.dashboard.check_for_unacknowledged_notices')
    def test_user_with_unacknowledged_notice_no_notices(self, mock_notices):
        """
        Verifies that we will NOT redirect the user if the result of calling the `check_for_unacknowledged_notices`
        function is None.
        """
        mock_notices.return_value = None

        with override_settings(FEATURES={**settings.FEATURES, 'ENABLE_NOTICES': True}):
            response = self.client.get(self.path)

        assert response.status_code == 200
        mock_notices.assert_called_once()

    @patch('common.djangoapps.student.views.dashboard.check_for_unacknowledged_notices')
    def test_user_with_unacknowledged_notice_plugin_disabled(self, mock_notices):
        """
        Verifies that the `check_for_unacknowledged_notices` function is NOT called if the feature is disabled.
        """
        mock_notices.return_value = None

        with override_settings(FEATURES={**settings.FEATURES, 'ENABLE_NOTICES': False}):
            response = self.client.get(self.path)

        assert response.status_code == 200
        mock_notices.assert_not_called()
