"""
Unit tests for instructor API v2 endpoints.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

import ddt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import SimpleTestCase, override_settings
from django.test.client import Client as DjangoClient
from django.urls import NoReverseMatch, reverse
from edx_when.api import set_date_for_block, set_dates_for_course
from opaque_keys import InvalidKeyError
from pytz import UTC
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import ManualEnrollmentAudit
from common.djangoapps.student.models.course_enrollment import CourseEnrollment, CourseEnrollmentAllowed
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseDataResearcherRole,
    CourseInstructorRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import CertificateAllowlist, CertificateGenerationHistory
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from lms.djangoapps.instructor.access import ROLE_DISPLAY_NAMES
from lms.djangoapps.instructor.permissions import InstructorPermission
from lms.djangoapps.instructor.views.serializers_v2 import CourseInformationSerializerV2
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class CourseMetadataViewTest(SharedModuleStoreTestCase, MasqueradeMixin):
    """
    Tests for the CourseMetadataView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='DemoX',
            run='Demo_Course',
            display_name='Demonstration Course',
            self_paced=False,
            enable_proctored_exams=True,
        )
        cls.proctored_course = CourseFactory.create(
            org='edX',
            number='Proctored',
            run='2024',
            display_name='Demonstration Proctored Course',
        )

        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = AdminFactory.create()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.django_staff_user = UserFactory.create(is_staff=True)
        self.data_researcher = UserFactory.create()
        CourseDataResearcherRole(self.course_key).add_users(self.data_researcher)
        CourseInstructorRole(self.proctored_course.id).add_users(self.instructor)
        self.student = UserFactory.create()

        # Create some enrollments for testing
        CourseEnrollmentFactory.create(
            user=self.student,
            course_id=self.course_key,
            mode='audit',
            is_active=True
        )
        CourseEnrollmentFactory.create(
            user=UserFactory.create(),
            course_id=self.course_key,
            mode='verified',
            is_active=True
        )
        CourseEnrollmentFactory.create(
            user=UserFactory.create(),
            course_id=self.course_key,
            mode='honor',
            is_active=True
        )
        CourseEnrollmentFactory.create(
            user=UserFactory.create(),
            course_id=self.proctored_course.id,
            mode='verified',
            is_active=True
        )

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:course_metadata', kwargs={'course_id': course_id})

    @override_settings(
        COURSE_AUTHORING_MICROFRONTEND_URL='http://localhost:2001/authoring',
        ADMIN_CONSOLE_MICROFRONTEND_URL='http://localhost:2025/admin-console',
        # intentionally include trailing slash to test URL joining logic
        WRITABLE_GRADEBOOK_URL='http://localhost:1994/gradebook/',
    )
    def test_get_course_metadata_as_instructor(self):
        """
        Test that an instructor can retrieve comprehensive course metadata.
        """
        with patch(
            'lms.djangoapps.instructor.views.serializers_v2.is_writable_gradebook_enabled',
            return_value=True,
        ):
            self.client.force_authenticate(user=self.instructor)
            response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        data = response.data

        # Verify basic course information
        assert data['course_id'] == str(self.course_key)
        assert data['display_name'] == 'Demonstration Course'
        assert data['org'] == 'edX'
        assert data['course_number'] == 'DemoX'
        assert data['course_run'] == 'Demo_Course'
        assert data['pacing'] == 'instructor'

        # Verify enrollment counts structure
        assert 'enrollment_counts' in data
        assert 'total' in data['enrollment_counts']
        assert 'total_enrollment' in data
        assert data['total_enrollment'] >= 3

        # Verify role-based enrollment counts are present
        assert 'learner_count' in data
        assert 'staff_count' in data
        assert data['total_enrollment'] == data['learner_count'] + data['staff_count']

        # Verify permissions structure
        assert 'permissions' in data
        permissions_data = data['permissions']
        assert 'admin' in permissions_data
        assert 'instructor' in permissions_data
        assert 'staff' in permissions_data
        assert 'forum_admin' in permissions_data
        assert 'finance_admin' in permissions_data
        assert 'sales_admin' in permissions_data
        assert 'data_researcher' in permissions_data

        # Verify sections structure
        assert 'tabs' in data
        assert isinstance(data['tabs'], list)

        # Verify other metadata fields
        assert 'num_sections' in data
        assert 'grade_cutoffs' in data
        assert 'course_errors' in data
        assert 'studio_url' in data
        assert 'disable_buttons' in data
        assert 'has_started' in data
        assert 'has_ended' in data
        assert 'analytics_dashboard_message' in data
        assert 'studio_grading_url' in data
        assert 'admin_console_url' in data
        assert 'gradebook_url' in data

        # Verify current user's username is returned
        assert data['username'] == self.instructor.username

        assert data['studio_grading_url'] == f'http://localhost:2001/authoring/course/{self.course.id}/settings/grading'
        assert data['admin_console_url'] == 'http://localhost:2025/admin-console/authz'
        assert data['gradebook_url'] == f'http://localhost:1994/gradebook/{self.course.id}'

    @override_settings(ADMIN_CONSOLE_MICROFRONTEND_URL='http://localhost:2025/admin-console')
    def test_admin_console_url_requires_instructor_access(self):
        """
        Test that the admin console URL is only available to users with instructor access.
        """
        # data researcher has access to course but is not an instructor
        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        assert 'admin_console_url' in response.data
        data = response.data
        assert data['admin_console_url'] is None

    @override_settings(ADMIN_CONSOLE_MICROFRONTEND_URL='http://localhost:2025/admin-console')
    def test_django_staff_user_without_instructor_access_can_see_admin_console_url(self):
        """
        Test that Django staff users without instructor access can see the admin console URL.
        """
        self.client.force_authenticate(user=self.django_staff_user)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        assert 'admin_console_url' in response.data
        data = response.data
        assert data['admin_console_url'] == 'http://localhost:2025/admin-console/authz'

    def test_get_course_metadata_as_staff(self):
        """
        Test that course staff can retrieve course metadata.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data['course_id'] == str(self.course_key)
        assert 'permissions' in data
        # Staff should have staff permission
        assert data['permissions']['staff'] is True
        assert data['username'] == self.staff.username

    def test_get_course_metadata_unauthorized(self):
        """
        Test that students cannot access course metadata endpoint.
        """
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
        error_code = "You do not have permission to perform this action."
        self.assertEqual(response.data['developer_message'], error_code)  # noqa: PT009

    def test_get_course_metadata_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_get_course_metadata_invalid_course_id(self):
        """
        Test error handling for invalid course ID.
        """
        self.client.force_authenticate(user=self.instructor)
        invalid_course_id = 'invalid-course-id'
        with self.assertRaises(NoReverseMatch):  # noqa: PT027
            self.client.get(self._get_url(course_id=invalid_course_id))

    def test_get_course_metadata_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009
        error_code = "Course not found: course-v1:edX+NonExistent+2024."
        self.assertEqual(response.data['developer_message'], error_code)  # noqa: PT009

    def test_instructor_permissions_reflected(self):
        """
        Test that instructor permissions are correctly reflected in response.
        """
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        permissions_data = response.data['permissions']

        # Instructor should have instructor permission
        self.assertTrue(permissions_data['instructor'])  # noqa: PT009

    def test_learner_and_staff_counts(self):
        """
        Test that learner_count excludes staff/admins and staff_count is the difference.
        """
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data

        total = data['total_enrollment']
        learner_count = data['learner_count']
        staff_count = data['staff_count']

        # Counts must be non-negative and sum to total
        self.assertGreaterEqual(learner_count, 0)  # noqa: PT009
        self.assertGreaterEqual(staff_count, 0)  # noqa: PT009
        self.assertEqual(total, learner_count + staff_count)  # noqa: PT009

        # The student enrolled in setUp is not staff, so learner_count >= 1
        self.assertGreaterEqual(learner_count, 1)  # noqa: PT009

    def test_enrollment_counts_by_mode(self):
        """
        Test that enrollment counts include all configured modes,
        even those with zero enrollments.
        """
        # Configure modes for the course: audit, verified, honor, and professional
        for mode_slug in ('audit', 'verified', 'honor', 'professional'):
            CourseModeFactory.create(course_id=self.course_key, mode_slug=mode_slug)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        enrollment_counts = response.data['enrollment_counts']

        # All configured modes should be present
        self.assertIn('audit', enrollment_counts)  # noqa: PT009
        self.assertIn('verified', enrollment_counts)  # noqa: PT009
        self.assertIn('honor', enrollment_counts)  # noqa: PT009
        self.assertIn('professional', enrollment_counts)  # noqa: PT009
        self.assertIn('total', enrollment_counts)  # noqa: PT009

        # professional has no enrollments but should still appear with 0
        self.assertEqual(enrollment_counts['professional'], 0)  # noqa: PT009

        # Modes with enrollments should have correct counts
        self.assertGreaterEqual(enrollment_counts['audit'], 1)  # noqa: PT009
        self.assertGreaterEqual(enrollment_counts['verified'], 1)  # noqa: PT009
        self.assertGreaterEqual(enrollment_counts['honor'], 1)  # noqa: PT009
        self.assertGreaterEqual(enrollment_counts['total'], 3)  # noqa: PT009

    def test_enrollment_counts_excludes_unconfigured_modes(self):
        """
        Test that enrollment counts only include modes configured for the course,
        not modes that exist on other courses.
        """
        # Only configure audit and honor for this course (not verified)
        CourseModeFactory.create(course_id=self.course_key, mode_slug='audit')
        CourseModeFactory.create(course_id=self.course_key, mode_slug='honor')

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        enrollment_counts = response.data['enrollment_counts']

        # Only configured modes should appear
        self.assertIn('audit', enrollment_counts)  # noqa: PT009
        self.assertIn('honor', enrollment_counts)  # noqa: PT009
        self.assertIn('total', enrollment_counts)  # noqa: PT009

        # verified is not configured, so it should not appear
        # (even though there are verified enrollments from setUp)
        self.assertNotIn('verified', enrollment_counts)  # noqa: PT009

    def _get_tabs_from_response(self, user, course_id=None):
        """Helper to get tabs from API response."""
        self.client.force_authenticate(user=user)
        response = self.client.get(self._get_url(course_id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        return response.data.get('tabs', [])

    def _test_staff_tabs(self, tabs):
        """Helper to test tabs visible to staff users."""
        tab_ids = [tab['tab_id'] for tab in tabs]

        # Staff should see these basic tabs (course_team is restricted to instructor/forum_admin)
        expected_basic_tabs = ['course_info', 'enrollments', 'grading', 'cohorts']
        assert tab_ids == expected_basic_tabs

    def test_staff_sees_basic_tabs(self):
        """
        Test that staff users see the basic set of tabs.
        """
        tabs = self._get_tabs_from_response(self.staff)
        self._test_staff_tabs(tabs)

    def test_instructor_sees_all_basic_tabs(self):
        """
        Test that instructors see all tabs that staff see.
        """
        instructor_tabs = self._get_tabs_from_response(self.instructor)
        tab_ids = [tab['tab_id'] for tab in instructor_tabs]

        expected_tabs = ['course_info', 'enrollments', 'course_team', 'grading', 'cohorts']
        assert tab_ids == expected_tabs

    def test_researcher_sees_all_basic_tabs(self):
        """
        Test that instructors see all tabs that staff see.
        """
        tabs = self._get_tabs_from_response(self.data_researcher)
        tab_ids = [tab['tab_id'] for tab in tabs]
        self.assertEqual(['data_downloads'], tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.is_enabled_for_course')
    def test_date_extensions_tab_when_enabled(self, mock_is_enabled):
        """
        Test that date_extensions tab appears when edx-when is enabled for the course.
        """
        mock_is_enabled.return_value = True

        tabs = self._get_tabs_from_response(self.instructor)
        tab_ids = [tab['tab_id'] for tab in tabs]

        self.assertIn('date_extensions', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.modulestore')
    def test_open_responses_tab_with_openassessment_blocks(self, mock_modulestore):
        """
        Test that open_responses tab appears when course has openassessment blocks.
        """
        # Mock openassessment block
        mock_block = Mock()
        mock_block.parent = Mock()  # Has a parent (not orphaned)
        mock_store = Mock()
        mock_store.get_items.return_value = [mock_block]
        mock_store.get_course_errors.return_value = []
        mock_modulestore.return_value = mock_store

        tabs = self._get_tabs_from_response(self.staff)
        tab_ids = [tab['tab_id'] for tab in tabs]

        self.assertIn('open_responses', tab_ids)  # noqa: PT009

    @patch('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True, 'MAX_ENROLLMENT_INSTR_BUTTONS': 200})
    def test_special_exams_tab_with_proctored_exams_enabled(self):
        """
        Test that special_exams tab appears when course has proctored exams enabled.
        """
        tabs = self._get_tabs_from_response(self.instructor)
        tab_ids = [tab['tab_id'] for tab in tabs]

        self.assertIn('special_exams', tab_ids)  # noqa: PT009

    @patch('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True, 'MAX_ENROLLMENT_INSTR_BUTTONS': 200})
    def test_special_exams_tab_with_timed_exams_enabled(self):
        """
        Test that special_exams tab appears when course has timed exams enabled.
        """
        # Create course with timed exams
        timed_course = CourseFactory.create(
            org='edX',
            number='Timed',
            run='2024',
            enable_timed_exams=True,
        )
        CourseInstructorRole(timed_course.id).add_users(self.instructor)
        tabs = self._get_tabs_from_response(self.instructor, course_id=timed_course.id)
        tab_ids = [tab['tab_id'] for tab in tabs]
        self.assertIn('special_exams', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.CertificateGenerationConfiguration.current')
    @patch('django.conf.settings.FEATURES', {'ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE': True,
                                             'MAX_ENROLLMENT_INSTR_BUTTONS': 200})
    def test_certificates_tab_for_instructor_when_enabled(self, mock_cert_config):
        """
        Test that certificates tab appears for instructors when certificate management is enabled.
        """
        mock_config = Mock()
        mock_config.enabled = True
        mock_cert_config.return_value = mock_config

        tabs = self._get_tabs_from_response(self.instructor)
        tab_ids = [tab['tab_id'] for tab in tabs]
        self.assertIn('certificates', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.CertificateGenerationConfiguration.current')
    def test_certificates_tab_for_admin_visible(self, mock_cert_config):
        """
        Test that certificates tab appears for admin users when certificates are enabled.
        """
        mock_config = Mock()
        mock_config.enabled = True
        mock_cert_config.return_value = mock_config

        tabs = self._get_tabs_from_response(self.admin)
        tab_ids = [tab['tab_id'] for tab in tabs]
        self.assertIn('certificates', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.is_bulk_email_feature_enabled')
    @ddt.data('staff', 'instructor', 'admin')
    def test_bulk_email_tab_when_enabled(self, user_attribute, mock_bulk_email_enabled):
        """
        Test that the bulk_email tab appears for all staff-level users when is_bulk_email_feature_enabled is True.
        """
        mock_bulk_email_enabled.return_value = True

        user = getattr(self, user_attribute)
        tabs = self._get_tabs_from_response(user)
        tab_ids = [tab['tab_id'] for tab in tabs]

        self.assertIn('bulk_email', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.is_bulk_email_feature_enabled')
    @ddt.data(
        (False, 'staff'),
        (False, 'instructor'),
        (False, 'admin'),
        (True, 'data_researcher'),
    )
    @ddt.unpack
    def test_bulk_email_tab_not_visible(self, feature_enabled, user_attribute, mock_bulk_email_enabled):
        """
        Test that the bulk_email tab does not appear when is_bulk_email_feature_enabled is False or the user is not
        a user with staff permissions.
        """
        mock_bulk_email_enabled.return_value = feature_enabled

        user = getattr(self, user_attribute)
        tabs = self._get_tabs_from_response(user)
        tab_ids = [tab['tab_id'] for tab in tabs]

        self.assertNotIn('bulk_email', tab_ids)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.is_bulk_email_feature_enabled')
    @override_settings(COMMUNICATIONS_MICROFRONTEND_URL='http://localhost:1984')
    def test_bulk_email_tab_url_uses_communications_mfe(self, mock_bulk_email_enabled):
        """
        Test that the bulk_email tab URL uses COMMUNICATIONS_MICROFRONTEND_URL,
        not INSTRUCTOR_MICROFRONTEND_URL.
        """
        mock_bulk_email_enabled.return_value = True

        tabs = self._get_tabs_from_response(self.staff)
        bulk_email_tab = next((tab for tab in tabs if tab['tab_id'] == 'bulk_email'), None)

        self.assertIsNotNone(bulk_email_tab)  # noqa: PT009
        expected_url = f'http://localhost:1984/courses/{self.course.id}/bulk_email'
        self.assertEqual(bulk_email_tab['url'], expected_url)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.is_bulk_email_feature_enabled')
    @override_settings(COMMUNICATIONS_MICROFRONTEND_URL=None)
    def test_bulk_email_tab_logs_warning_when_communications_mfe_url_not_set(self, mock_bulk_email_enabled):
        """
        Test that a warning is logged when COMMUNICATIONS_MICROFRONTEND_URL is not set,
        and the resulting URL does not contain 'None'.
        """
        mock_bulk_email_enabled.return_value = True

        with self.assertLogs('lms.djangoapps.instructor.views.serializers_v2', level='WARNING') as cm:
            tabs = self._get_tabs_from_response(self.staff)

        self.assertTrue(  # noqa: PT009
            any('COMMUNICATIONS_MICROFRONTEND_URL is not configured' in msg for msg in cm.output)
        )
        bulk_email_tab = next((tab for tab in tabs if tab['tab_id'] == 'bulk_email'), None)
        self.assertIsNotNone(bulk_email_tab)  # noqa: PT009
        self.assertFalse(  # noqa: PT009
            bulk_email_tab['url'].startswith('None'),
            f"Tab URL should not start with 'None': {bulk_email_tab['url']}"
        )

    def test_tabs_have_sort_order(self):
        """
        Test that all tabs include a sort_order field.
        """
        tabs = self._get_tabs_from_response(self.staff)

        for tab in tabs:
            self.assertIn('sort_order', tab)  # noqa: PT009
            self.assertIsInstance(tab['sort_order'], int)  # noqa: PT009

    def test_disable_buttons_false_for_small_course(self):
        """
        Test that disable_buttons is False for courses with <=200 enrollments.
        """
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        # With only 3 enrollments, buttons should not be disabled
        self.assertFalse(response.data['disable_buttons'])  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.modulestore')
    def test_course_errors_from_modulestore(self, mock_modulestore):
        """
        Test that course errors from modulestore are included in response.
        """
        mock_store = Mock()
        mock_store.get_course_errors.return_value = [(Exception("Test error"), '')]
        mock_store.get_items.return_value = []
        mock_modulestore.return_value = mock_store

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('course_errors', response.data)  # noqa: PT009
        self.assertIsInstance(response.data['course_errors'], list)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.serializers_v2.settings.INSTRUCTOR_MICROFRONTEND_URL', None)
    def test_tabs_log_warning_when_mfe_url_not_set(self):
        """
        Test that a warning is logged when INSTRUCTOR_MICROFRONTEND_URL is not set.
        """
        with self.assertLogs('lms.djangoapps.instructor.views.serializers_v2', level='WARNING') as cm:
            tabs = self._get_tabs_from_response(self.staff)

        self.assertTrue(  # noqa: PT009
            any('INSTRUCTOR_MICROFRONTEND_URL is not configured' in msg for msg in cm.output)
        )
        # Tab URLs should use empty string as base, not "None"
        for tab in tabs:
            self.assertFalse(tab['url'].startswith('None'), f"Tab URL should not start with 'None': {tab['url']}")  # noqa: PT009  # pylint: disable=line-too-long
            self.assertTrue(  # noqa: PT009
                tab['url'].startswith(f'/{self.course.id}/'),
                f"Tab URL should start with '/{self.course.id}/': {tab['url']}"
            )

    def test_pacing_self_for_self_paced_course(self):
        """
        Test that pacing is 'self' for self-paced courses.
        """
        # Create a self-paced course
        self_paced_course = CourseFactory.create(
            org='edX',
            number='SelfPaced',
            run='SP1',
            self_paced=True,
        )
        instructor = InstructorFactory.create(course_key=self_paced_course.id)

        self.client.force_authenticate(user=instructor)
        url = reverse('instructor_api_v2:course_metadata', kwargs={'course_id': str(self_paced_course.id)})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['pacing'], 'self')  # noqa: PT009

    def test_masquerade_as_student_role_returns_403(self):
        """
        Test that the endpoint returns 403 when a staff user masquerades as a student role.
        """
        # Use Django's test Client for masquerade (MasqueradeMixin is incompatible with DRF APIClient)
        original_client = self.client
        self.client = DjangoClient()
        self.client.login(username=self.staff.username, password='Password1234')
        self.update_masquerade(course=self.course, role='student')
        response = self.client.get(self._get_url())
        self.client = original_client
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_masquerade_as_specific_student_returns_403(self):
        """
        Test that the endpoint returns 403 when a staff user masquerades as a specific student.
        """
        original_client = self.client
        self.client = DjangoClient()
        self.client.login(username=self.staff.username, password='Password1234')
        self.update_masquerade(course=self.course, username=self.student.username)
        response = self.client.get(self._get_url())
        self.client = original_client
        assert response.status_code == status.HTTP_403_FORBIDDEN


class BuildTabUrlTest(SimpleTestCase):
    """
    Unit tests for CourseInformationSerializerV2._build_tab_url.

    Tests the helper directly to verify URL joining behavior without
    going through the full API stack.
    """

    def _build(self, setting_name, *parts, strip_url=True):
        return CourseInformationSerializerV2._build_tab_url(setting_name, *parts, strip_url=strip_url)  # pylint: disable=protected-access

    @override_settings(INSTRUCTOR_MICROFRONTEND_URL='http://localhost:2003/instructor-dashboard')
    def test_joins_base_and_path_parts(self):
        """Parts are joined with '/' separators."""
        result = self._build('INSTRUCTOR_MICROFRONTEND_URL', 'course-v1:edX+DemoX+Demo', 'grading')
        self.assertEqual(result, '/instructor-dashboard/course-v1:edX+DemoX+Demo/grading')  # noqa: PT009

    @override_settings(INSTRUCTOR_MICROFRONTEND_URL='http://localhost:2003/instructor-dashboard/')
    def test_strips_trailing_slash_from_base(self):
        """A trailing slash on the base URL does not produce a double slash."""
        result = self._build('INSTRUCTOR_MICROFRONTEND_URL', 'course-v1:edX+DemoX+Demo', 'grading')
        self.assertEqual(result, '/instructor-dashboard/course-v1:edX+DemoX+Demo/grading')  # noqa: PT009

    @override_settings(INSTRUCTOR_MICROFRONTEND_URL='http://localhost:2003/instructor-dashboard')
    def test_strips_slashes_from_path_parts(self):
        """Leading and trailing slashes on path parts are stripped before joining."""
        result = self._build('INSTRUCTOR_MICROFRONTEND_URL', '/course-v1:edX+DemoX+Demo/', '/grading/')
        self.assertEqual(result, '/instructor-dashboard/course-v1:edX+DemoX+Demo/grading')  # noqa: PT009

    @override_settings(COMMUNICATIONS_MICROFRONTEND_URL=None)
    def test_logs_warning_and_returns_relative_url_when_setting_is_none(self):
        """When the setting is None, a warning is logged and the URL is relative (no 'None' prefix)."""
        with self.assertLogs('lms.djangoapps.instructor.views.serializers_v2', level='WARNING') as cm:
            result = self._build(
                'COMMUNICATIONS_MICROFRONTEND_URL', 'courses', 'course-v1:edX+DemoX+Demo', 'bulk_email'
            )

        self.assertTrue(any('COMMUNICATIONS_MICROFRONTEND_URL is not configured' in msg for msg in cm.output))  # noqa: PT009  # pylint: disable=line-too-long
        self.assertFalse(result.startswith('None'))  # noqa: PT009
        self.assertEqual(result, '/courses/course-v1:edX+DemoX+Demo/bulk_email')  # noqa: PT009

    def test_logs_warning_when_setting_does_not_exist(self):
        """When the setting name is not defined at all, behavior matches the None case."""
        with self.assertLogs('lms.djangoapps.instructor.views.serializers_v2', level='WARNING') as cm:
            result = self._build('NONEXISTENT_MFE_URL', 'course-v1:edX+DemoX+Demo', 'grading')

        self.assertTrue(any('NONEXISTENT_MFE_URL is not configured' in msg for msg in cm.output))  # noqa: PT009
        self.assertEqual(result, '/course-v1:edX+DemoX+Demo/grading')  # noqa: PT009

    @override_settings(COMMUNICATIONS_MICROFRONTEND_URL='http://localhost:1984/communications/')
    def test_base_with_subpath_and_trailing_slash(self):
        """Base URL with a subpath and trailing slash is joined cleanly."""
        result = self._build(
            "COMMUNICATIONS_MICROFRONTEND_URL", "courses", "course-v1:edX+DemoX+Demo", "bulk_email", strip_url=False
        )
        self.assertEqual(result, 'http://localhost:1984/communications/courses/course-v1:edX+DemoX+Demo/bulk_email')  # noqa: PT009  # pylint: disable=line-too-long


@ddt.ddt
class InstructorTaskListViewTest(SharedModuleStoreTestCase):
    """
    Tests for the InstructorTaskListView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TestX',
            run='Test_Course',
            display_name='Test Course',
        )
        cls.course_key = cls.course.id

        # Create a problem block for testing
        cls.chapter = BlockFactory.create(
            parent=cls.course,
            category='chapter',
            display_name='Test Chapter'
        )
        cls.sequential = BlockFactory.create(
            parent=cls.chapter,
            category='sequential',
            display_name='Test Sequential'
        )
        cls.vertical = BlockFactory.create(
            parent=cls.sequential,
            category='vertical',
            display_name='Test Vertical'
        )
        cls.problem = BlockFactory.create(
            parent=cls.vertical,
            category='problem',
            display_name='Test Problem'
        )
        cls.problem_location = str(cls.problem.location)

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:instructor_tasks', kwargs={'course_id': course_id})

    def test_get_instructor_tasks_as_instructor(self):
        """
        Test that an instructor can retrieve instructor tasks.
        """
        # Create a test task
        task_id = str(uuid4())
        InstructorTaskFactory.create(
            course_id=self.course_key,
            task_type='grade_problems',
            task_state='PROGRESS',
            requester=self.instructor,
            task_id=task_id,
            task_key="dummy key",
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('tasks', response.data)  # noqa: PT009
        self.assertIsInstance(response.data['tasks'], list)  # noqa: PT009

    def test_get_instructor_tasks_unauthorized(self):
        """
        Test that students cannot access instructor tasks endpoint.
        """
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
        self.assertIn('You do not have permission to perform this action.', response.data['developer_message'])  # noqa: PT009  # pylint: disable=line-too-long

    def test_get_instructor_tasks_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_get_instructor_tasks_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009
        self.assertEqual('Course not found: course-v1:edX+NonExistent+2024.', response.data['developer_message'])  # noqa: PT009  # pylint: disable=line-too-long

    def test_filter_by_problem_location(self):
        """
        Test filtering tasks by problem location.
        """
        self.client.force_authenticate(user=self.instructor)
        params = {
            'problem_location_str': self.problem_location,
        }
        url = f"{self._get_url()}?{urlencode(params)}"

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('tasks', response.data)  # noqa: PT009

    def test_filter_requires_problem_location_with_student(self):
        """
        Test that student identifier requires problem location.
        """
        self.client.force_authenticate(user=self.instructor)

        self.client.force_authenticate(user=self.instructor)
        params = {
            'unique_student_identifier': self.student.email,
        }
        url = f"{self._get_url()}?{urlencode(params)}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        self.assertIn('error', response.data)  # noqa: PT009
        self.assertIn('problem_location_str', response.data['error'])  # noqa: PT009

    def test_filter_by_problem_and_student(self):
        """
        Test filtering tasks by both problem location and student identifier.
        """
        # Enroll the student
        CourseEnrollmentFactory.create(
            user=self.student,
            course_id=self.course_key,
            is_active=True
        )

        StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )

        task_id = str(uuid4())
        InstructorTaskFactory.create(
            course_id=self.course_key,
            task_state='PROGRESS',
            requester=self.student,
            task_id=task_id,
            task_key="dummy key",
        )

        self.client.force_authenticate(user=self.instructor)
        params = {
            'problem_location_str': self.problem_location,
            'unique_student_identifier': self.student.email,
        }
        url = f"{self._get_url()}?{urlencode(params)}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('tasks', response.data)  # noqa: PT009

    def test_invalid_student_identifier(self):
        """
        Test error handling for invalid student identifier.
        """
        self.client.force_authenticate(user=self.instructor)
        params = {
            'problem_location_str': self.problem_location,
            'unique_student_identifier': 'nonexistent@example.com',
        }
        url = f"{self._get_url()}?{urlencode(params)}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        self.assertIn('error', response.data)  # noqa: PT009

    def test_invalid_problem_location(self):
        """
        Test error handling for invalid problem location.
        """
        self.client.force_authenticate(user=self.instructor)

        url = f"{self._get_url()}?problem_location_str=invalid-location"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        self.assertIn('error', response.data)  # noqa: PT009
        self.assertIn('Invalid problem location', response.data['error'])  # noqa: PT009

    @ddt.data(
        ('grade_problems', 'PROGRESS'),
        ('rescore_problem', 'SUCCESS'),
        ('reset_student_attempts', 'FAILURE'),
    )
    @ddt.unpack
    def test_various_task_types_and_states(self, task_type, task_state):
        """
        Test that various task types and states are properly returned.
        """
        task_id = str(uuid4())
        InstructorTaskFactory.create(
            course_id=self.course_key,
            task_type=task_type,
            task_state=task_state,
            requester=self.instructor,
            task_id=task_id,
            task_key="dummy key",
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('tasks', response.data)  # noqa: PT009
        if task_state == 'PROGRESS':
            self.assertEqual(task_id, response.data['tasks'][0]['task_id'])  # noqa: PT009
            self.assertEqual(task_type, response.data['tasks'][0]['task_type'])  # noqa: PT009
            self.assertEqual(task_state, response.data['tasks'][0]['task_state'])  # noqa: PT009

    def test_task_data_structure(self):
        """
        Test that task data contains expected fields from extract_task_features.
        """
        task_id = str(uuid4())
        InstructorTaskFactory.create(
            course_id=self.course_key,
            task_type='grade_problems',
            task_state='PROGRESS',
            requester=self.instructor,
            task_id=task_id,
            task_key="dummy key",
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        tasks = response.data['tasks']

        if tasks:
            task_data = tasks[0]
            # Verify key fields are present (these come from extract_task_features)
            self.assertIn('task_type', task_data)  # noqa: PT009
            self.assertIn('task_state', task_data)  # noqa: PT009
            self.assertIn('created', task_data)  # noqa: PT009


@ddt.ddt
class GradedSubsectionsViewTest(SharedModuleStoreTestCase):
    """
    Tests for the GradedSubsectionsView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='DemoX',
            run='Demo_Course',
            display_name='Demonstration Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()
        CourseEnrollmentFactory.create(
            user=self.student,
            course_id=self.course_key,
            mode='audit',
            is_active=True
        )

        # Create some subsections with due dates
        self.chapter = BlockFactory.create(
            parent=self.course,
            category='chapter',
            display_name='Test Chapter'
        )
        self.due_date = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)
        self.subsection_with_due_date = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name='Homework 1',
            due=self.due_date
        )
        self.subsection_without_due_date = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name='Reading Material'
        )
        self.problem = BlockFactory.create(
            parent=self.subsection_with_due_date,
            category='problem',
            display_name='Test Problem'
        )

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:graded_subsections', kwargs={'course_id': course_id})

    def test_get_graded_subsections_success(self):
        """
        Test that an instructor can retrieve graded subsections with due dates.
        """
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        response_data = json.loads(response.content)
        self.assertIn('items', response_data)  # noqa: PT009
        self.assertIsInstance(response_data['items'], list)  # noqa: PT009

        # Should include subsection with due date
        items = response_data['items']
        if items:  # Only test if there are items with due dates
            item = items[0]
            self.assertIn('display_name', item)  # noqa: PT009
            self.assertIn('subsection_id', item)  # noqa: PT009
            self.assertIsInstance(item['display_name'], str)  # noqa: PT009
            self.assertIsInstance(item['subsection_id'], str)  # noqa: PT009

    def test_get_graded_subsections_as_staff(self):
        """
        Test that staff can retrieve graded subsections.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        response_data = json.loads(response.content)
        self.assertIn('items', response_data)  # noqa: PT009

    def test_get_graded_subsections_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:NonExistent+Course+2024'
        nonexistent_url = self._get_url(nonexistent_course_id)
        response = self.client.get(nonexistent_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009

    def test_get_graded_subsections_empty_course(self):
        """
        Test graded subsections for course without due dates.
        """
        # Create a completely separate course without any subsections with due dates
        empty_course = CourseFactory.create(
            org='EmptyTest',
            number='EmptyX',
            run='Empty2024',
            display_name='Empty Test Course'
        )
        # Don't add any subsections to this course
        empty_instructor = InstructorFactory.create(course_key=empty_course.id)

        self.client.force_authenticate(user=empty_instructor)
        response = self.client.get(self._get_url(str(empty_course.id)))

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        response_data = json.loads(response.content)
        # An empty course should have no graded subsections with due dates
        self.assertEqual(response_data['items'], [])  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_get_graded_subsections_with_mocked_units(self, mock_get_units):
        """
        Test graded subsections response format with mocked data.
        """
        # Mock a unit with due date
        mock_unit = Mock()
        mock_unit.display_name = 'Mocked Assignment'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@mock')
        mock_get_units.return_value = [mock_unit]

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        response_data = json.loads(response.content)
        items = response_data['items']
        self.assertEqual(len(items), 1)  # noqa: PT009
        self.assertEqual(items[0]['display_name'], 'Mocked Assignment')  # noqa: PT009
        self.assertEqual(items[0]['subsection_id'], 'block-v1:Test+Course+2024+type@sequential+block@mock')  # noqa: PT009  # pylint: disable=line-too-long

    @patch('lms.djangoapps.instructor.views.api_v2.title_or_url')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_get_graded_subsections_title_fallback(self, mock_get_units, mock_title_or_url):
        """
        Test graded subsections when display_name is not available.
        """
        # Mock a unit without display_name
        mock_unit = Mock()
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@fallback')
        mock_get_units.return_value = [mock_unit]
        mock_title_or_url.return_value = 'block-v1:Test+Course+2024+type@sequential+block@fallback'

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        response_data = json.loads(response.content)
        items = response_data['items']
        self.assertEqual(len(items), 1)  # noqa: PT009
        self.assertEqual(items[0]['display_name'], 'block-v1:Test+Course+2024+type@sequential+block@fallback')  # noqa: PT009  # pylint: disable=line-too-long
        self.assertEqual(items[0]['subsection_id'], 'block-v1:Test+Course+2024+type@sequential+block@fallback')  # noqa: PT009  # pylint: disable=line-too-long

    def test_get_graded_subsections_response_format(self):
        """
        Test that the response has the correct format.
        """
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

        response_data = json.loads(response.content)
        # Verify top-level structure
        self.assertIn('items', response_data)  # noqa: PT009
        self.assertIsInstance(response_data['items'], list)  # noqa: PT009

        # Verify each item has required fields
        for item in response_data['items']:
            self.assertIn('display_name', item)  # noqa: PT009
            self.assertIn('subsection_id', item)  # noqa: PT009
            self.assertIsInstance(item['display_name'], str)  # noqa: PT009
            self.assertIsInstance(item['subsection_id'], str)  # noqa: PT009


class ORABaseViewsTest(SharedModuleStoreTestCase, APITestCase):
    """
    Base class for ORA view tests.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.course = CourseFactory.create()
        cls.course_key = cls.course.location.course_key

        cls.ora_block = BlockFactory.create(
            category="openassessment",
            parent_location=cls.course.location,
            display_name="test",
        )
        cls.ora_usage_key = str(cls.ora_block.location)

        cls.password = "password"
        cls.staff = StaffFactory(course_key=cls.course_key, password=cls.password)

    def log_in(self):
        """Log in as staff by default."""
        self.client.login(username=self.staff.username, password=self.password)


class ORAViewTest(ORABaseViewsTest):
    """
    Tests for the ORAAssessmentsView API endpoints.
    """

    view_name = "instructor_api_v2:ora_assessments"

    def setUp(self):
        super().setUp()
        self.log_in()

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse(self.view_name, kwargs={'course_id': course_id})

    def test_get_assessment_list(self):
        """Test retrieving the list of ORA assessments."""
        response = self.client.get(
            self._get_url()
        )

        assert response.status_code == 200
        data = response.data['results']
        assert len(data) == 1
        ora_data = data[0]
        assert ora_data['block_id'] == self.ora_usage_key
        assert ora_data['unit_name'].startswith("Run")
        assert ora_data['display_name'] == "test"
        assert ora_data['total_responses'] == 0
        assert ora_data['training'] == 0
        assert ora_data['peer'] == 0
        assert ora_data['self'] == 0
        assert ora_data['waiting'] == 0
        assert ora_data['staff'] == 0
        assert ora_data['final_grade_received'] == 0
        assert ora_data['staff_ora_grading_url'] is None

    @patch("lms.djangoapps.instructor.ora.modulestore")
    def test_get_assessment_list_includes_staff_ora_grading_url_for_non_team_assignment(
        self, mock_modulestore
    ):
        """
        Retrieve ORA assessments and ensure staff grading URL is included
        for non-team assignments with staff assessment enabled.
        """
        mock_store = Mock()

        mock_assessment_block = Mock(
            location=self.ora_block.location,
            parent=Mock(),
            teams_enabled=False,
            assessment_steps=["staff-assessment"],
        )

        mock_store.get_items.return_value = [mock_assessment_block]
        mock_modulestore.return_value = mock_store

        response = self.client.get(self._get_url())

        assert response.status_code == 200

        results = response.data["results"]
        assert len(results) == 1

        ora_data = results[0]

        assert "staff_ora_grading_url" in ora_data
        assert ora_data["staff_ora_grading_url"]

    @patch("lms.djangoapps.instructor.ora.modulestore")
    def test_get_assessment_list_includes_staff_ora_grading_url_for_team_assignment(
        self, mock_modulestore
    ):
        """
        Retrieve ORA assessments and ensure staff grading URL is included
        for team assignments with staff assessment enabled.
        """
        mock_store = Mock()

        mock_assessment_block = Mock(
            location=self.ora_block.location,
            parent=Mock(),
            teams_enabled=True,
            display_name="Team Assignment",
            assessment_steps=["staff-assessment"],
        )

        mock_store.get_items.return_value = [mock_assessment_block]
        mock_modulestore.return_value = mock_store

        response = self.client.get(self._get_url())

        assert response.status_code == 200

        results = response.data["results"]
        assert len(results) == 1

        ora_data = results[0]

        assert "staff_ora_grading_url" in ora_data
        assert ora_data["staff_ora_grading_url"] is None

    def test_invalid_course_id(self):
        """Test error handling for invalid course ID."""
        invalid_course_id = 'invalid-course-id'
        url = self._get_url()
        response = self.client.get(url.replace(str(self.course_key), invalid_course_id))
        assert response.status_code == 404

    def test_permission_denied_for_non_staff(self):
        """Test that non-staff users cannot access the endpoint."""
        # Log out staff
        self.client.logout()

        # Create a non-staff user and enroll them in the course
        user = UserFactory(password="password")
        CourseEnrollment.enroll(user, self.course_key)

        # Log in as the non-staff user
        self.client.login(username=user.username, password="password")

        response = self.client.get(self._get_url())
        assert response.status_code == 403

    def test_permission_allowed_for_instructor(self):
        """Test that instructor users can access the endpoint."""
        # Log out staff user
        self.client.logout()

        # Create instructor for this course
        instructor = InstructorFactory(course_key=self.course_key, password="password")

        # Log in as instructor
        self.client.login(username=instructor.username, password="password")

        # Access the endpoint
        response = self.client.get(self._get_url())
        assert response.status_code == 200

    def test_pagination_of_assessments(self):
        """Test pagination works correctly."""
        # Create additional ORA blocks to test pagination
        for i in range(15):
            BlockFactory.create(
                category="openassessment",
                parent_location=self.course.location,
                display_name=f"test_{i}",
            )

        response = self.client.get(self._get_url(), {'page_size': 10})
        assert response.status_code == 200
        data = response.data
        assert data['count'] == 16  # 1 original + 15 new
        assert len(data['results']) == 10  # Page size

        # Get second page
        response = self.client.get(self._get_url(), {'page_size': 10, 'page': 2})
        assert response.status_code == 200
        data = response.data
        assert len(data['results']) == 6  # Remaining items

    def test_no_assessments(self):
        """Test response when there are no ORA assessments."""
        # Create a new course with no ORA blocks
        empty_course = CourseFactory.create()
        empty_course_key = empty_course.location.course_key
        empty_staff = StaffFactory(course_key=empty_course_key, password="password")

        # Log in as staff for the empty course
        self.client.logout()
        self.client.login(username=empty_staff.username, password="password")

        response = self.client.get(
            reverse(self.view_name, kwargs={'course_id': str(empty_course_key)})
        )

        assert response.status_code == 200
        data = response.data['results']
        assert len(data) == 0


class ORASummaryViewTest(ORABaseViewsTest):
    """
    Tests for the ORASummaryView API endpoints.
    """

    view_name = "instructor_api_v2:ora_summary"

    def setUp(self):
        super().setUp()
        self.log_in()

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse(self.view_name, kwargs={'course_id': course_id})

    @patch('openassessment.data.OraAggregateData.collect_ora2_responses')
    def test_get_ora_summary_with_final_grades(self, mock_get_responses):
        """Test retrieving the ORA summary with final grades."""

        mock_get_responses.return_value = {
            self.ora_usage_key: {
                "done": 3,
                "total": 2,
                "total_responses": 0,
                "training": 0,
                "peer": 0,
                "self": 0,
                "waiting": 0,
                "staff": 0,
            }
        }

        response = self.client.get(
            self._get_url()
        )

        assert response.status_code == 200
        data = response.data

        assert data['final_grade_received'] == 3

    def test_get_ora_summary(self):
        """Test retrieving the ORA summary."""

        BlockFactory.create(
            category="openassessment",
            parent_location=self.course.location,
            display_name="test2",
        )

        response = self.client.get(
            self._get_url()
        )

        assert response.status_code == 200
        data = response.data
        assert 'total_units' in data
        assert 'total_assessments' in data
        assert 'total_responses' in data
        assert 'training' in data
        assert 'peer' in data
        assert 'self' in data
        assert 'waiting' in data
        assert 'staff' in data
        assert 'final_grade_received' in data

        assert data['total_units'] == 2
        assert data['total_assessments'] == 2
        assert data['total_responses'] == 0
        assert data['training'] == 0
        assert data['peer'] == 0
        assert data['self'] == 0
        assert data['waiting'] == 0
        assert data['staff'] == 0
        assert data['final_grade_received'] == 0

    def test_invalid_course_id(self):
        """Test error handling for invalid course ID."""
        invalid_course_id = 'invalid-course-id'
        url = self._get_url()
        response = self.client.get(url.replace(str(self.course_key), invalid_course_id))
        assert response.status_code == 404

    def test_permission_denied_for_non_staff(self):
        """Test that non-staff users cannot access the endpoint."""
        # Log out staff
        self.client.logout()

        # Create a non-staff user and enroll them in the course
        user = UserFactory(password="password")
        CourseEnrollment.enroll(user, self.course_key)

        # Log in as the non-staff user
        self.client.login(username=user.username, password="password")

        response = self.client.get(self._get_url())
        assert response.status_code == 403

    def test_permission_allowed_for_instructor(self):
        """Test that instructor users can access the endpoint."""
        # Log out staff user
        self.client.logout()

        # Create instructor for this course
        instructor = InstructorFactory(course_key=self.course_key, password="password")

        # Log in as instructor
        self.client.login(username=instructor.username, password="password")

        # Access the endpoint
        response = self.client.get(self._get_url())
        assert response.status_code == 200


@ddt.ddt
class UnitExtensionsViewTest(SharedModuleStoreTestCase):
    """
    Tests for the UnitExtensionsView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TestX',
            run='Test_Course',
            display_name='Test Course',
        )
        cls.course_key = cls.course.id

        # Create course structure
        cls.chapter = BlockFactory.create(
            parent=cls.course,
            category='chapter',
            display_name='Test Chapter'
        )
        cls.subsection = BlockFactory.create(
            parent=cls.chapter,
            category='sequential',
            display_name='Homework 1'
        )
        cls.vertical = BlockFactory.create(
            parent=cls.subsection,
            category='vertical',
            display_name='Test Vertical'
        )
        cls.problem = BlockFactory.create(
            parent=cls.vertical,
            category='problem',
            display_name='Test Problem'
        )

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.student1 = UserFactory.create(username='student1', email='student1@example.com')
        self.student2 = UserFactory.create(username='student2', email='student2@example.com')

        # Enroll students
        CourseEnrollmentFactory.create(
            user=self.student1,
            course_id=self.course_key,
            is_active=True
        )
        CourseEnrollmentFactory.create(
            user=self.student2,
            course_id=self.course_key,
            is_active=True
        )

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:unit_extensions', kwargs={'course_id': course_id})

    def test_get_unit_extensions_as_staff(self):
        """
        Test that staff can retrieve unit extensions.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_get_unit_extensions_unauthorized(self):
        """
        Test that students cannot access unit extensions endpoint.
        """
        self.client.force_authenticate(user=self.student1)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_get_unit_extensions_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_get_unit_extensions_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009

    def test_get_unit_extensions(self):
        """
        Test retrieving unit extensions.
        """

        # Set up due dates
        date1 = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)
        date2 = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)
        date3 = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)

        items = [
            (self.subsection.location, {'due': date1}),  # Homework 1
            (self.vertical.location, {'due': date2}),  # Test Vertical (Should be ignored)
            (self.problem.location, {'due': date3}),  # Test Problem (Should be ignored)
        ]
        set_dates_for_course(self.course_key, items)

        # Set up overrides
        override1 = datetime(2025, 10, 31, 23, 59, 59, tzinfo=UTC)
        override2 = datetime(2025, 11, 30, 23, 59, 59, tzinfo=UTC)
        override3 = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
        # Single override per user
        # Only return the top-level override per user, in this case the subsection level
        set_date_for_block(self.course_key, self.subsection.location, 'due', override1, user=self.student1)
        set_date_for_block(self.course_key, self.subsection.location, 'due', override2, user=self.student2)
        # Multiple overrides per user
        set_date_for_block(self.course_key, self.subsection.location, 'due', override3, user=self.student2)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        results = data['results']

        self.assertEqual(len(results), 2)  # noqa: PT009

        # Student 1's extension
        extension = results[0]
        self.assertEqual(extension['username'], 'student1')  # noqa: PT009
        self.assertIn('Robot', extension['full_name'])  # noqa: PT009
        self.assertEqual(extension['email'], 'student1@example.com')  # noqa: PT009
        self.assertEqual(extension['unit_title'], 'Homework 1')  # Should be the top-level unit  # noqa: PT009
        self.assertEqual(extension['unit_location'], 'block-v1:edX+TestX+Test_Course+type@sequential+block@Homework_1')  # noqa: PT009  # pylint: disable=line-too-long
        self.assertEqual(extension['extended_due_date'], '2025-10-31T23:59:59Z')  # noqa: PT009

        # Student 2's extension
        extension = results[1]
        self.assertEqual(extension['username'], 'student2')  # noqa: PT009
        self.assertIn('Robot', extension['full_name'])  # noqa: PT009
        self.assertEqual(extension['email'], 'student2@example.com')  # noqa: PT009
        self.assertEqual(extension['unit_title'], 'Homework 1')  # Should be the top-level unit  # noqa: PT009
        self.assertEqual(extension['unit_location'], 'block-v1:edX+TestX+Test_Course+type@sequential+block@Homework_1')  # noqa: PT009  # pylint: disable=line-too-long
        self.assertEqual(extension['extended_due_date'], '2025-12-31T23:59:59Z')  # noqa: PT009

    @ddt.data(
        ('student1', True),
        ('jane@example.com', False),
        ('STUDENT1', True),  # Test case insensitive
        ('JANE@EXAMPLE.COM', False),  # Test case insensitive
    )
    @ddt.unpack
    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_course')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_filter_by_email_or_username(self, filter_value, is_username, mock_get_units, mock_get_overrides):
        """
        Test filtering unit extensions by email or username.
        """
        # Mock units with due dates
        mock_unit = Mock()
        mock_unit.display_name = 'Homework 1'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')
        mock_get_units.return_value = [mock_unit]

        # Mock location for dictionary lookup
        mock_location = Mock()
        mock_location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')

        # Mock course overrides data
        extended_date = datetime(2025, 1, 15, 23, 59, 59, tzinfo=UTC)
        mock_get_overrides.return_value = [
            ('student1', 'John Doe', 'john@example.com', mock_location, extended_date),
            ('student2', 'Jane Smith', 'jane@example.com', mock_location, extended_date),
        ]

        self.client.force_authenticate(user=self.instructor)

        # Test filter by username
        params = {'email_or_username': filter_value}
        response = self.client.get(self._get_url(), params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        results = data['results']

        self.assertEqual(len(results), 1)  # noqa: PT009

        # Check that the filter value is in the appropriate field
        if is_username:
            self.assertIn(filter_value.lower(), results[0]['username'].lower())  # noqa: PT009
        else:
            self.assertIn(filter_value.lower(), results[0]['email'].lower())  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_block')
    @patch('lms.djangoapps.instructor.views.api_v2.find_unit')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_filter_by_block_id(self, mock_get_units, mock_find_unit, mock_get_overrides_block):
        """
        Test filtering unit extensions by specific block_id.
        """
        # Mock unit
        mock_unit = Mock()
        mock_unit.display_name = 'Homework 1'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')

        mock_find_unit.return_value = mock_unit
        mock_get_units.return_value = [mock_unit]

        # Mock block-specific overrides data (username, full_name, email, location, due_date)
        extended_date = datetime(2025, 1, 15, 23, 59, 59, tzinfo=UTC)
        mock_get_overrides_block.return_value = [
            ('student1', 'John Doe', extended_date, 'john@example.com', mock_unit.location),
        ]

        self.client.force_authenticate(user=self.instructor)
        params = {'block_id': 'block-v1:Test+Course+2024+type@sequential+block@hw1'}
        response = self.client.get(self._get_url(), params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

        data = response.data
        results = data['results']

        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertEqual(len(results), 1)  # noqa: PT009

        data = results[0]
        self.assertEqual(data['username'], 'student1')  # noqa: PT009
        self.assertEqual(data['full_name'], 'John Doe')  # noqa: PT009
        self.assertEqual(data['email'], 'john@example.com')  # noqa: PT009
        self.assertEqual(data['unit_title'], 'Homework 1')  # noqa: PT009
        self.assertEqual(data['unit_location'], 'block-v1:Test+Course+2024+type@sequential+block@hw1')  # noqa: PT009
        self.assertEqual(data['extended_due_date'], extended_date.strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.find_unit')
    def test_filter_by_invalid_block_id(self, mock_find_unit):
        """
        Test filtering by invalid block_id returns empty list.
        """
        # Make find_unit raise an exception
        mock_find_unit.side_effect = InvalidKeyError('Invalid block', 'invalid-block-id')

        self.client.force_authenticate(user=self.instructor)
        params = {'block_id': 'invalid-block-id'}
        response = self.client.get(self._get_url(), params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertEqual(data['count'], 0)  # noqa: PT009
        self.assertEqual(data['results'], [])  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_block')
    @patch('lms.djangoapps.instructor.views.api_v2.find_unit')
    def test_combined_filters(self, mock_find_unit, mock_get_overrides_block):
        """
        Test combining block_id and email_or_username filters.
        """
        # Mock unit
        mock_unit = Mock()
        mock_unit.display_name = 'Homework 1'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')

        mock_find_unit.return_value = mock_unit

        # Mock block-specific overrides data
        extended_date = datetime(2025, 1, 15, 23, 59, 59, tzinfo=UTC)
        mock_get_overrides_block.return_value = [
            ('student1', 'John Doe', extended_date, 'john@example.com', mock_unit.location),
            ('student2', 'Jane Smith', extended_date, 'jane@example.com', mock_unit.location),
        ]

        self.client.force_authenticate(user=self.instructor)
        params = {
            'block_id': 'block-v1:Test+Course+2024+type@sequential+block@hw1',
            'email_or_username': 'student1'
        }
        response = self.client.get(self._get_url(), params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

        data = response.data
        results = data['results']

        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertEqual(len(results), 1)  # noqa: PT009

        data = results[0]
        # Match only the filtered student1
        self.assertEqual(data['username'], 'student1')  # noqa: PT009
        self.assertEqual(data['full_name'], 'John Doe')  # noqa: PT009
        self.assertEqual(data['email'], 'john@example.com')  # noqa: PT009
        self.assertEqual(data['unit_title'], 'Homework 1')  # noqa: PT009
        self.assertEqual(data['unit_location'], 'block-v1:Test+Course+2024+type@sequential+block@hw1')  # noqa: PT009
        self.assertEqual(data['extended_due_date'], extended_date.strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_course')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_pagination_parameters(self, mock_get_units, mock_get_overrides):
        """
        Test that pagination parameters work correctly.
        """
        # Mock units with due dates
        mock_unit = Mock()
        mock_unit.display_name = 'Homework 1'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')
        mock_get_units.return_value = [mock_unit]

        # Mock location for dictionary lookup
        mock_location = Mock()
        mock_location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')

        # Mock course overrides data
        extended_date = datetime(2025, 1, 15, 23, 59, 59, tzinfo=UTC)
        mock_get_overrides.return_value = [
            ('student1', 'John Doe', 'john@example.com', mock_location, extended_date),
            ('student2', 'Jane Smith', 'jane@example.com', mock_location, extended_date),
        ]
        self.client.force_authenticate(user=self.instructor)

        # Test page parameter
        params = {'page': '1', 'page_size': '1'}
        response = self.client.get(self._get_url(), params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertIn('count', data)  # noqa: PT009
        self.assertIn('next', data)  # noqa: PT009
        self.assertIn('previous', data)  # noqa: PT009
        self.assertIn('results', data)  # noqa: PT009

        self.assertEqual(data['count'], 2)  # noqa: PT009
        self.assertIsNotNone(data['next'])  # noqa: PT009
        self.assertIsNone(data['previous'])  # noqa: PT009
        self.assertEqual(len(data['results']), 1)  # noqa: PT009

        # Test second page
        params = {'page': '2', 'page_size': '1'}
        response = self.client.get(self._get_url(), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertIsNone(data['next'])  # noqa: PT009
        self.assertIsNotNone(data['previous'])  # noqa: PT009
        self.assertEqual(len(data['results']), 1)  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_course')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    def test_empty_results(self, mock_get_units, mock_get_overrides):
        """
        Test endpoint with no extension data.
        """
        # Mock empty data
        mock_get_units.return_value = []
        mock_get_overrides.return_value = []

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertEqual(data['count'], 0)  # noqa: PT009
        self.assertEqual(data['results'], [])  # noqa: PT009

    @patch('lms.djangoapps.instructor.views.api_v2.edx_when_api.get_overrides_for_course')
    @patch('lms.djangoapps.instructor.views.api_v2.get_units_with_due_date')
    @patch('lms.djangoapps.instructor.views.api_v2.title_or_url')
    def test_extension_data_structure(self, mock_title_or_url, mock_get_units, mock_get_overrides):
        """
        Test that extension data has the correct structure.
        """
        # Mock units with due dates
        mock_unit = Mock()
        mock_unit.display_name = 'Homework 1'
        mock_unit.location = Mock()
        mock_unit.location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')
        mock_get_units.return_value = [mock_unit]
        mock_title_or_url.return_value = 'Homework 1'

        # Mock location for dictionary lookup
        mock_location = Mock()
        mock_location.__str__ = Mock(return_value='block-v1:Test+Course+2024+type@sequential+block@hw1')

        # Mock course overrides data
        extended_date = datetime(2025, 1, 15, 23, 59, 59, tzinfo=UTC)
        mock_get_overrides.return_value = [
            ('student1', 'John Doe', 'john@example.com', mock_location, extended_date),
        ]

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertEqual(data['count'], 1)  # noqa: PT009

        extension = data['results'][0]

        # Verify all required fields are present
        required_fields = [
            'username', 'full_name', 'email',
            'unit_title', 'unit_location', 'extended_due_date'
        ]
        for field in required_fields:
            self.assertIn(field, extension)  # noqa: PT009

        # Verify data types
        self.assertIsInstance(extension['username'], str)  # noqa: PT009
        self.assertIsInstance(extension['full_name'], str)  # noqa: PT009
        self.assertIsInstance(extension['email'], str)  # noqa: PT009
        self.assertIsInstance(extension['unit_title'], str)  # noqa: PT009
        self.assertIsInstance(extension['unit_location'], str)  # noqa: PT009

    def test_reset_extension_with_none_date_excluded(self):
        """
        Test that extensions reset via set_date_for_block(None) are excluded from results.
        When an extension is reset, edx-when creates a UserDate with abs_date=None and rel_date=None,
        causing actual_date to fall back to the original block due date. These reverted overrides
        should not appear as granted extensions.
        """
        original_due = datetime.now(UTC).replace(microsecond=0)
        extended = original_due + timedelta(days=60)
        set_dates_for_course(self.course_key, [(self.subsection.location, {'due': original_due})])

        # Grant extension to student1, then reset it by passing None
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student1)
        set_date_for_block(self.course_key, self.subsection.location, 'due', None, user=self.student1)

        # Grant a real extension to student2
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student2)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        assert response.status_code == 200
        results = response.data['results']
        assert len(results) == 1
        assert results[0]['username'] == 'student2'
        assert results[0]['extended_due_date'] == extended.strftime('%Y-%m-%dT%H:%M:%SZ')

    def test_reset_extension_matching_original_date_excluded(self):
        """
        Test that extensions whose override date matches the original due date are excluded.
        When an extension is reset, the override reverts to the original subsection date,
        making it appear as if there's an active extension when there isn't one.
        """
        original_due = datetime.now(UTC).replace(microsecond=0)
        extended = original_due + timedelta(days=60)
        set_dates_for_course(self.course_key, [(self.subsection.location, {'due': original_due})])

        # Grant extension to student1, then "reset" it by setting it back to the original date
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student1)
        set_date_for_block(self.course_key, self.subsection.location, 'due', original_due, user=self.student1)

        # Grant a real extension to student2
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student2)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        assert response.status_code == 200
        results = response.data['results']
        assert len(results) == 1
        assert results[0]['username'] == 'student2'
        assert results[0]['extended_due_date'] == extended.strftime('%Y-%m-%dT%H:%M:%SZ')

    def test_reset_extension_excluded_with_block_id_filter(self):
        """
        Test that reset extensions are also excluded when filtering by block_id.
        """
        original_due = datetime.now(UTC).replace(microsecond=0)
        extended = original_due + timedelta(days=60)
        set_dates_for_course(self.course_key, [(self.subsection.location, {'due': original_due})])

        # Grant extension to student1, then reset it
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student1)
        set_date_for_block(self.course_key, self.subsection.location, 'due', None, user=self.student1)

        # Grant a real extension to student2
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended, user=self.student2)

        self.client.force_authenticate(user=self.instructor)
        params = {'block_id': str(self.subsection.location)}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == 200
        results = response.data['results']
        assert len(results) == 1
        assert results[0]['username'] == 'student2'
        assert results[0]['extended_due_date'] == extended.strftime('%Y-%m-%dT%H:%M:%SZ')

    def test_active_extensions_still_returned(self):
        """
        Test that legitimate extensions (date differs from original) are still returned.
        """
        original_due = datetime.now(UTC).replace(microsecond=0)
        extended1 = original_due + timedelta(days=30)
        extended2 = original_due + timedelta(days=60)
        set_dates_for_course(self.course_key, [(self.subsection.location, {'due': original_due})])

        set_date_for_block(self.course_key, self.subsection.location, 'due', extended1, user=self.student1)
        set_date_for_block(self.course_key, self.subsection.location, 'due', extended2, user=self.student2)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        assert response.status_code == 200
        results = response.data['results']
        assert len(results) == 2
        results_by_username = {r['username']: r for r in results}
        assert results_by_username['student1']['extended_due_date'] == extended1.strftime('%Y-%m-%dT%H:%M:%SZ')
        assert results_by_username['student2']['extended_due_date'] == extended2.strftime('%Y-%m-%dT%H:%M:%SZ')


@ddt.ddt
class IssuedCertificatesViewTest(SharedModuleStoreTestCase):
    """
    Tests for the IssuedCertificatesView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TestX',
            run='Test_Course',
            display_name='Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.student1 = UserFactory.create(username='student1', email='student1@example.com')
        self.student2 = UserFactory.create(username='student2', email='student2@example.com')

        # Enroll students
        CourseEnrollmentFactory.create(
            user=self.student1,
            course_id=self.course_key,
            mode='verified',
            is_active=True
        )
        CourseEnrollmentFactory.create(
            user=self.student2,
            course_id=self.course_key,
            mode='audit',
            is_active=True
        )

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:issued_certificates', kwargs={'course_id': course_id})

    def test_get_issued_certificates_as_staff(self):
        """
        Test that staff can retrieve issued certificates.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data
        assert 'count' in response.data

    def test_get_issued_certificates_unauthorized(self):
        """
        Test that students cannot access issued certificates endpoint.
        """
        self.client.force_authenticate(user=self.student1)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_issued_certificates_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_issued_certificates_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_search_filter(self):
        """
        Test filtering certificates by search term.
        """
        # Create a certificate for student1
        GeneratedCertificateFactory.create(
            user=self.student1,
            course_id=self.course_key,
            status=CertificateStatuses.downloadable
        )
        # Create a certificate for student2
        GeneratedCertificateFactory.create(
            user=self.student2,
            course_id=self.course_key,
            status=CertificateStatuses.downloadable
        )

        self.client.force_authenticate(user=self.instructor)
        params = {'search': 'student1'}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == status.HTTP_200_OK
        # Verify only student1's certificate is returned
        assert response.data['count'] == 1
        assert response.data['results'][0]['username'] == 'student1'

    @ddt.data(
        'received',
        'not_received',
        'audit_passing',
        'audit_not_passing',
        'error',
        'granted_exceptions',
        'invalidated',
    )
    def test_filter_types(self, filter_type):
        """
        Test various filter types for certificates.
        """
        self.client.force_authenticate(user=self.instructor)
        params = {'filter': filter_type}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data

    def test_pagination(self):
        """
        Test pagination parameters work correctly.
        """
        self.client.force_authenticate(user=self.instructor)
        params = {'page': '1', 'page_size': '10'}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == status.HTTP_200_OK
        assert 'count' in response.data
        assert 'next' in response.data
        assert 'previous' in response.data
        assert 'results' in response.data

    def test_granted_exceptions_without_certificates(self):
        """
        Test that granted_exceptions filter shows allowlisted users
        even if they don't have GeneratedCertificate records yet.
        """
        # Add student1 to allowlist (has verified enrollment)
        CertificateAllowlist.objects.create(
            user=self.student1,
            course_id=self.course_key,
            allowlist=True,
            notes='Medical emergency'
        )

        # Add student2 to allowlist (has audit enrollment, no certificate)
        CertificateAllowlist.objects.create(
            user=self.student2,
            course_id=self.course_key,
            allowlist=True,
            notes='Special case'
        )

        # Create certificate only for student1
        GeneratedCertificateFactory.create(
            user=self.student1,
            course_id=self.course_key,
            status=CertificateStatuses.downloadable
        )

        self.client.force_authenticate(user=self.instructor)
        params = {'filter': 'granted_exceptions'}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 2  # Both students should appear

        results = {r['username']: r for r in response.data['results']}

        # Verify student1 (has certificate)
        assert 'student1' in results
        assert results['student1']['enrollment_track'] == 'verified'
        assert results['student1']['certificate_status'] == 'downloadable'
        assert results['student1']['special_case'] == 'Exception'
        assert results['student1']['exception_notes'] == 'Medical emergency'

        # Verify student2 (no certificate, but should appear with enrollment data)
        assert 'student2' in results
        assert results['student2']['enrollment_track'] == 'audit'
        assert results['student2']['certificate_status'] == 'audit_notpassing'
        assert results['student2']['special_case'] == 'Exception'
        assert results['student2']['exception_notes'] == 'Special case'


@ddt.ddt
class CertificateGenerationHistoryViewTest(SharedModuleStoreTestCase):
    """
    Tests for the CertificateGenerationHistoryView API endpoint.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TestX',
            run='Test_Course',
            display_name='Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:certificate_generation_history', kwargs={'course_id': course_id})

    def test_get_generation_history_as_staff(self):
        """
        Test that staff can retrieve certificate generation history.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data
        assert 'count' in response.data

    def test_get_generation_history_unauthorized(self):
        """
        Test that students cannot access generation history endpoint.
        """
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_generation_history_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_generation_history_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_pagination(self):
        """
        Test pagination parameters work correctly.
        """
        self.client.force_authenticate(user=self.instructor)
        params = {'page': '1', 'page_size': '10'}
        response = self.client.get(self._get_url(), params)

        assert response.status_code == status.HTTP_200_OK
        assert 'count' in response.data
        assert 'next' in response.data
        assert 'previous' in response.data
        assert 'results' in response.data

    def test_history_entry_structure(self):
        """
        Test that history entries have the correct structure.
        """
        # Create a real certificate generation history entry
        task = InstructorTaskFactory.create(
            course_id=self.course_key,
            task_type='generate_certificates',
            task_key=str(self.course_key),
            task_id=str(uuid4()),
            task_input='{}',
            requester=self.instructor,
        )
        CertificateGenerationHistory.objects.create(
            course_id=self.course_key,
            generated_by=self.instructor,
            instructor_task=task,
            is_regeneration=True,
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 1

        entry = response.data['results'][0]
        # Verify all required fields are present (snake_case from serializer)
        assert entry['task_name'] == 'Regenerated'
        assert 'date' in entry
        assert entry['details'] == 'All Learners'

        # Verify data types
        assert isinstance(entry['task_name'], str)
        assert isinstance(entry['date'], str)
        assert isinstance(entry['details'], str)


class CourseEnrollmentsViewTest(SharedModuleStoreTestCase):
    """Tests for the CourseEnrollmentsView v2 GET endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.url = reverse(
            'instructor_api_v2:course_enrollments',
            kwargs={'course_id': str(self.course.id)}
        )

        self.enrolled_users = []
        for i in range(30):
            user = UserFactory(
                username=f'student_{i}',
                email=f'student{i}@example.com',
                first_name=f'Student{i}',
                last_name=f'Learner{i}'
            )
            CourseEnrollmentFactory(
                user=user,
                course_id=self.course.id,
                is_active=True
            )
            self.enrolled_users.append(user)

        # Inactive enrollments should not appear
        for i in range(5):
            user = UserFactory(
                username=f'inactive_{i}',
                email=f'inactive{i}@example.com'
            )
            CourseEnrollmentFactory(
                user=user,
                course_id=self.course.id,
                is_active=False
            )

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_student_returns_403(self):
        student = UserFactory()
        self.client.force_authenticate(user=student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_default_pagination(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.data
        self.assertEqual(data['course_id'], str(self.course.id))  # noqa: PT009
        self.assertEqual(data['count'], 30)  # noqa: PT009
        self.assertEqual(data['num_pages'], 3)  # noqa: PT009
        self.assertEqual(data['current_page'], 1)  # noqa: PT009
        self.assertIn('next', data)  # noqa: PT009
        self.assertIsNone(data['previous'])  # noqa: PT009
        self.assertIn('results', data)  # noqa: PT009
        # DefaultPagination page_size=10
        self.assertEqual(len(data['results']), 10)  # noqa: PT009

    def test_custom_pagination(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page': 1, 'page_size': 15})
        data = response.data
        self.assertEqual(data['count'], 30)  # noqa: PT009
        self.assertEqual(data['num_pages'], 2)  # noqa: PT009
        self.assertEqual(data['current_page'], 1)  # noqa: PT009
        self.assertEqual(len(data['results']), 15)  # noqa: PT009

    def test_second_page(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page': 2, 'page_size': 10})
        data = response.data
        self.assertEqual(data['current_page'], 2)  # noqa: PT009
        self.assertEqual(len(data['results']), 10)  # noqa: PT009
        self.assertIsNotNone(data['previous'])  # noqa: PT009

    def test_last_page_partial(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page': 3, 'page_size': 10})
        data = response.data
        self.assertEqual(data['current_page'], 3)  # noqa: PT009
        self.assertEqual(len(data['results']), 10)  # noqa: PT009
        self.assertIsNone(data['next'])  # noqa: PT009

    def test_search_by_username(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': 'student_2', 'page_size': 100})
        data = response.data
        # Matches student_2, student_20..student_29 = 11
        self.assertEqual(data['count'], 11)  # noqa: PT009
        for user in data['results']:
            self.assertIn('student_2', user['username'])  # noqa: PT009

    def test_search_by_email(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': 'student7@example.com'})
        data = response.data
        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertEqual(data['results'][0]['email'], 'student7@example.com')  # noqa: PT009

    def test_search_case_insensitive(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': 'STUDENT_5'})
        data = response.data
        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertEqual(data['results'][0]['username'], 'student_5')  # noqa: PT009

    def test_search_no_results(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': 'nonexistent'})
        data = response.data
        self.assertEqual(data['count'], 0)  # noqa: PT009
        self.assertEqual(len(data['results']), 0)  # noqa: PT009

    def test_excludes_inactive_enrollments(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': 'inactive'})
        data = response.data
        self.assertEqual(data['count'], 0)  # noqa: PT009

    def test_invalid_page_returns_404(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page': 999})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009

    def test_ordered_by_username(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page_size': 5})
        data = response.data
        usernames = [u['username'] for u in data['results']]
        self.assertEqual(usernames, sorted(usernames))  # noqa: PT009

    def test_staff_can_access(self):
        staff = StaffFactory(course_key=self.course.id)
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_includes_mode_field(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page_size': 1})
        enrollment = response.data['results'][0]
        self.assertIn('mode', enrollment)  # noqa: PT009

    def test_includes_full_name_field(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page_size': 1})
        enrollment = response.data['results'][0]
        self.assertIn('full_name', enrollment)  # noqa: PT009

    def test_includes_is_beta_tester_field(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'page_size': 100})
        for enrollment in response.data['results']:
            self.assertIn('is_beta_tester', enrollment)  # noqa: PT009
            self.assertFalse(enrollment['is_beta_tester'])  # noqa: PT009

    def test_beta_tester_flag_true(self):
        beta_role = CourseBetaTesterRole(self.course.id)
        target_user = self.enrolled_users[0]
        beta_role.add_users(target_user)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'search': target_user.username})
        data = response.data
        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertTrue(data['results'][0]['is_beta_tester'])  # noqa: PT009

    def test_filter_beta_testers_only(self):
        beta_role = CourseBetaTesterRole(self.course.id)
        beta_users = self.enrolled_users[:3]
        for user in beta_users:
            beta_role.add_users(user)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'is_beta_tester': 'true', 'page_size': 100})
        data = response.data
        self.assertEqual(data['count'], 3)  # noqa: PT009
        for enrollment in data['results']:
            self.assertTrue(enrollment['is_beta_tester'])  # noqa: PT009

    def test_filter_non_beta_testers_only(self):
        beta_role = CourseBetaTesterRole(self.course.id)
        beta_users = self.enrolled_users[:3]
        for user in beta_users:
            beta_role.add_users(user)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'is_beta_tester': 'false', 'page_size': 100})
        data = response.data
        self.assertEqual(data['count'], 27)  # noqa: PT009
        for enrollment in data['results']:
            self.assertFalse(enrollment['is_beta_tester'])  # noqa: PT009

    def test_filter_beta_testers_with_search(self):
        beta_role = CourseBetaTesterRole(self.course.id)
        beta_users = self.enrolled_users[:5]
        for user in beta_users:
            beta_role.add_users(user)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {
            'is_beta_tester': 'true',
            'search': self.enrolled_users[0].username,
        })
        data = response.data
        self.assertEqual(data['count'], 1)  # noqa: PT009
        self.assertTrue(data['results'][0]['is_beta_tester'])  # noqa: PT009


class EnrollmentModifyViewTest(SharedModuleStoreTestCase):
    """Tests for the EnrollmentModifyView v2 bulk POST endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.url = reverse(
            'instructor_api_v2:enrollment_modify',
            kwargs={'course_id': str(self.course.id)}
        )

    def _enroll(self, identifiers, **extra):
        return self.client.post(
            self.url,
            {'identifier': identifiers, 'action': 'enroll', **extra},
            format='json',
        )

    def _unenroll(self, identifiers, **extra):
        return self.client.post(
            self.url,
            {'identifier': identifiers, 'action': 'unenroll', **extra},
            format='json',
        )

    def test_unauthenticated_returns_401(self):
        response = self._enroll(['test@example.com'])
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_student_returns_403(self):
        self.client.force_authenticate(user=UserFactory())
        response = self._enroll(['test@example.com'])
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_fields_returns_400(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_identifier_list_returns_400(self):
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_action_returns_400(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'identifier': ['a@b.com'], 'action': 'bogus'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_enroll_existing_user(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.email])
        assert response.status_code == status.HTTP_200_OK
        assert response.data['action'] == 'enroll'
        assert response.data['auto_enroll'] is False
        result = response.data['results'][0]
        assert result['identifier'] == learner.email
        assert result['before'] == {'user': True, 'enrollment': False, 'allowed': False, 'auto_enroll': False}
        assert result['after']['enrollment'] is True
        assert result['after']['user'] is True
        assert result['after']['allowed'] is False
        assert CourseEnrollment.is_enrolled(learner, self.course.id)

    def test_enroll_by_username(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.username])
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'][0]['after']['enrollment'] is True
        assert CourseEnrollment.is_enrolled(learner, self.course.id)

    def test_enroll_nonexistent_user_creates_cea(self):
        email = 'newlearner@example.com'
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([email])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['after']['user'] is False
        assert result['after']['enrollment'] is False
        assert result['after']['allowed'] is True
        assert CourseEnrollmentAllowed.objects.filter(email=email, course_id=self.course.id).exists()

    def test_enroll_invalid_email_returns_invalid_identifier(self):
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll(['not-an-email'])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result.get('invalid_identifier') is True
        assert 'before' not in result
        assert 'after' not in result

    def test_enroll_already_enrolled_is_idempotent(self):
        learner = UserFactory()
        CourseEnrollmentFactory(user=learner, course_id=self.course.id, is_active=True)
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.email])
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'][0]['after']['enrollment'] is True

    def test_enroll_creates_audit_record(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.email], reason='Manual enrollment')
        assert response.status_code == status.HTTP_200_OK

        enrollment = CourseEnrollment.get_enrollment(learner, self.course.id)
        audit = ManualEnrollmentAudit.objects.filter(enrollment=enrollment).first()
        assert audit is not None
        assert audit.reason == 'Manual enrollment'

    def test_enroll_ambiguous_identifier_returns_error(self):
        user_a = UserFactory(username='enroll_ambig@example.com')
        UserFactory(email='enroll_ambig@example.com')
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([user_a.username])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result.get('error') is True

    def test_enroll_auto_enroll_reflected_top_level(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.email], auto_enroll=True)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['auto_enroll'] is True

    def test_enroll_mixed_success_and_failure(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._enroll([learner.email, 'not-an-email'])
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) == 2
        assert results[0]['identifier'] == learner.email
        assert 'after' in results[0]
        assert results[1]['identifier'] == 'not-an-email'
        assert results[1].get('invalid_identifier') is True

    def test_unenroll_existing_user(self):
        learner = UserFactory()
        CourseEnrollmentFactory(user=learner, course_id=self.course.id, is_active=True)
        self.client.force_authenticate(user=self.instructor)
        response = self._unenroll([learner.email])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['before']['enrollment'] is True
        assert result['after']['enrollment'] is False
        assert not CourseEnrollment.is_enrolled(learner, self.course.id)

    def test_unenroll_not_enrolled_returns_200(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._unenroll([learner.email])
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'][0]['after']['enrollment'] is False

    def test_unenroll_creates_audit_record(self):
        learner = UserFactory()
        CourseEnrollmentFactory(user=learner, course_id=self.course.id, is_active=True)
        self.client.force_authenticate(user=self.instructor)
        response = self._unenroll([learner.email], reason='Manual unenrollment')
        assert response.status_code == status.HTTP_200_OK

        audits = ManualEnrollmentAudit.objects.filter(enrolled_email=learner.email)
        assert audits.exists()

    def test_staff_can_access(self):
        staff = StaffFactory(course_key=self.course.id)
        learner = UserFactory()
        self.client.force_authenticate(user=staff)
        response = self._enroll([learner.email])
        assert response.status_code == status.HTTP_200_OK


class BetaTesterModifyViewTest(SharedModuleStoreTestCase):
    """Tests for the BetaTesterModifyView v2 bulk POST endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.url = reverse(
            'instructor_api_v2:beta_tester_modify',
            kwargs={'course_id': str(self.course.id)}
        )

    def _add(self, identifiers, **extra):
        return self.client.post(
            self.url,
            {'identifier': identifiers, 'action': 'add', **extra},
            format='json',
        )

    def _remove(self, identifiers, **extra):
        return self.client.post(
            self.url,
            {'identifier': identifiers, 'action': 'remove', **extra},
            format='json',
        )

    def test_unauthenticated_returns_401(self):
        response = self._add(['test'])
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_student_returns_403(self):
        self.client.force_authenticate(user=UserFactory())
        response = self._add(['test'])
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_fields_returns_400(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_action_returns_400(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'identifier': ['someone'], 'action': 'bogus'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_beta_tester(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._add([learner.email])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['identifier'] == learner.email
        assert result['error'] is False
        assert CourseBetaTesterRole(self.course.id).has_user(learner)

    def test_add_beta_tester_with_auto_enroll(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._add([learner.email], auto_enroll=True)
        assert response.status_code == status.HTTP_200_OK
        assert CourseEnrollment.is_enrolled(learner, self.course.id)

    def test_add_beta_tester_auto_enroll_already_enrolled(self):
        learner = UserFactory()
        CourseEnrollmentFactory(user=learner, course_id=self.course.id, is_active=True)
        self.client.force_authenticate(user=self.instructor)
        response = self._add([learner.email], auto_enroll=True)
        assert response.status_code == status.HTTP_200_OK
        assert CourseEnrollment.objects.filter(user=learner, course_id=self.course.id).count() == 1

    def test_add_nonexistent_user_returns_per_user_error(self):
        self.client.force_authenticate(user=self.instructor)
        response = self._add(['nobody@example.com'])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['error'] is True
        assert result['user_does_not_exist'] is True

    def test_add_ambiguous_identifier_returns_per_user_error(self):
        user_a = UserFactory(username='beta_ambig@example.com')
        UserFactory(email='beta_ambig@example.com')
        self.client.force_authenticate(user=self.instructor)
        response = self._add([user_a.username])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['error'] is True
        assert result['user_does_not_exist'] is False

    def test_remove_beta_tester(self):
        learner = UserFactory()
        CourseBetaTesterRole(self.course.id).add_users(learner)
        self.client.force_authenticate(user=self.instructor)
        response = self._remove([learner.email])
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'][0]['error'] is False
        assert not CourseBetaTesterRole(self.course.id).has_user(learner)

    def test_remove_nonexistent_user_returns_per_user_error(self):
        self.client.force_authenticate(user=self.instructor)
        response = self._remove(['nobody@example.com'])
        assert response.status_code == status.HTTP_200_OK
        result = response.data['results'][0]
        assert result['error'] is True
        assert result['user_does_not_exist'] is True

    def test_add_mixed_success_and_failure(self):
        learner = UserFactory()
        self.client.force_authenticate(user=self.instructor)
        response = self._add([learner.email, 'nobody@example.com'])
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) == 2
        assert results[0]['error'] is False
        assert results[1]['error'] is True


class CourseTeamRolesViewTest(SharedModuleStoreTestCase):
    """Tests for CourseTeamRolesView (GET available roles) endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='RolesX',
            run='2024',
            display_name='Roles Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()
        self.url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(self.course_key)})

    def test_list_roles_without_ccx(self):
        """Returns roles excluding ccx_coach when CCX is not enabled; includes forum roles."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['course_id'] == str(self.course_key)
        returned_roles = [r['role'] for r in response.data['results']]
        assert 'ccx_coach' not in returned_roles
        for expected in ['beta', 'data_researcher', 'instructor', 'limited_staff', 'staff']:
            assert expected in returned_roles
        for expected in ['Administrator', 'Moderator', 'Group Moderator', 'Community TA']:
            assert expected in returned_roles

    @override_settings(FEATURES={**settings.FEATURES, 'CUSTOM_COURSES_EDX': True})
    def test_list_roles_with_ccx_enabled(self):
        """Returns all roles including ccx_coach when CCX is enabled for the course."""
        ccx_course = CourseFactory.create(
            org='edX',
            number='CcxX',
            run='2024',
            display_name='CCX Test Course',
            enable_ccx=True,
        )
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(ccx_course.id)})
        instructor = InstructorFactory.create(course_key=ccx_course.id)
        self.client.force_authenticate(user=instructor)
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        returned_roles = [r['role'] for r in response.data['results']]
        assert 'ccx_coach' in returned_roles
        ccx_entry = next(r for r in response.data['results'] if r['role'] == 'ccx_coach')
        assert ccx_entry['display_name'] == 'CCX Coach'

    @override_settings(FEATURES={**settings.FEATURES, 'CUSTOM_COURSES_EDX': True})
    def test_roles_sort_order(self):
        """Roles are returned in the expected display order, with ccx_coach last."""
        ccx_course = CourseFactory.create(
            org='edX',
            number='SortX',
            run='2024',
            display_name='Sort Order Test Course',
            enable_ccx=True,
        )
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(ccx_course.id)})
        instructor = InstructorFactory.create(course_key=ccx_course.id)
        self.client.force_authenticate(user=instructor)
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        returned_roles = [r['role'] for r in response.data['results']]
        assert returned_roles == [
            'staff', 'limited_staff', 'instructor', 'beta', 'data_researcher',
            'Administrator', 'Moderator', 'Group Moderator', 'Community TA',
            'ccx_coach',
        ]

    def test_list_roles_unauthenticated(self):
        """Unauthenticated request returns 401."""
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_roles_no_permission(self):
        """Student without instructor access gets 403."""
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@ddt.ddt
class CourseTeamViewTest(SharedModuleStoreTestCase):
    """Tests for CourseTeamView (GET list and POST grant) endpoints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TeamX',
            run='2024',
            display_name='Team Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff_user = StaffFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()
        self.url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})

    # ---- GET tests ----

    def test_list_staff_members(self):
        """Instructors can list users with a given role."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'staff'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['course_id'] == str(self.course_key)
        assert response.data['role'] == 'staff'
        usernames = [m['username'] for m in response.data['results']]
        assert self.staff_user.username in usernames

    def test_list_instructors(self):
        """List instructor role members."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'instructor'})

        assert response.status_code == status.HTTP_200_OK
        usernames = [m['username'] for m in response.data['results']]
        assert self.instructor.username in usernames

    def test_list_all_roles_when_no_role_param(self):
        """GET without role param returns all team members aggregated per user."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['role'] is None
        usernames = [m['username'] for m in response.data['results']]
        assert self.instructor.username in usernames
        assert self.staff_user.username in usernames
        for member in response.data['results']:
            assert 'roles' in member
            assert isinstance(member['roles'], list)
            for role_entry in member['roles']:
                assert 'role' in role_entry
                assert 'display_name' in role_entry

    def test_list_aggregates_user_with_multiple_roles(self):
        """A user with multiple roles appears as a single record with all roles."""
        multi_role_user = UserFactory.create(username='multirole')
        CourseStaffRole(self.course_key).add_users(multi_role_user)
        CourseBetaTesterRole(self.course_key).add_users(multi_role_user)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        matches = [m for m in response.data['results'] if m['username'] == 'multirole']
        assert len(matches) == 1
        role_names = {r['role'] for r in matches[0]['roles']}
        assert {'staff', 'beta'}.issubset(role_names)
        for role_entry in matches[0]['roles']:
            assert role_entry['display_name'] == str(ROLE_DISPLAY_NAMES[role_entry['role']])

    def test_list_invalid_role(self):
        """GET with invalid role returns 400."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'nonexistent'})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_unauthenticated(self):
        """Unauthenticated request returns 401."""
        response = self.client.get(self.url, {'role': 'staff'})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_no_permission(self):
        """Student without instructor access gets 403."""
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url, {'role': 'staff'})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_response_fields(self):
        """Verify response contains expected user fields and roles array."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'staff'})

        assert response.status_code == status.HTTP_200_OK
        for member in response.data['results']:
            assert 'username' in member
            assert 'email' in member
            assert 'first_name' in member
            assert 'last_name' in member
            assert 'roles' in member
            assert any(r['role'] == 'staff' for r in member['roles'])

    @ddt.data('instructor', 'staff', 'limited_staff', 'beta', 'ccx_coach', 'data_researcher')
    def test_list_all_valid_roles(self, role):
        """GET with any valid role returns 200."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': role})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['role'] == role
        assert 'results' in response.data

    # ---- POST grant tests ----

    def test_grant_staff_role(self):
        """Grant staff role to one or more users via array."""
        new_user = UserFactory.create()
        other_user = UserFactory.create()
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [new_user.username, other_user.email],
            'role': 'staff',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['action'] == 'allow'
        assert response.data['role'] == 'staff'
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[new_user.username]['error'] is False
        assert results_by_id[new_user.username]['userDoesNotExist'] is False
        assert results_by_id[new_user.username]['is_active'] is True
        assert results_by_id[other_user.email]['error'] is False
        assert CourseStaffRole(self.course_key).has_user(new_user)
        assert CourseStaffRole(self.course_key).has_user(other_user)

    def test_grant_role_auto_enrolls(self):
        """Granting a role also enrolls the user if not already enrolled."""
        new_user = UserFactory.create()
        self.client.force_authenticate(user=self.instructor)
        self.client.post(self.url, {
            'identifiers': [new_user.username],
            'role': 'staff',
            'action': 'allow',
        }, format='json')

        assert CourseEnrollment.is_enrolled(new_user, self.course_key)

    def test_grant_role_user_not_found(self):
        """Granting a role to a non-existent user reports per-identifier error."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': ['nonexistent_user_12345'],
            'role': 'staff',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id['nonexistent_user_12345']['error'] is True
        assert results_by_id['nonexistent_user_12345']['userDoesNotExist'] is True
        assert results_by_id['nonexistent_user_12345']['is_active'] is None

    def test_grant_role_invalid_role(self):
        """Granting an invalid role returns 400."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [self.student.username],
            'role': 'nonexistent',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_grant_role_inactive_user(self):
        """Granting a role to an inactive user reports per-identifier error."""
        inactive_user = UserFactory.create(is_active=False)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [inactive_user.username],
            'role': 'staff',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[inactive_user.username]['error'] is True
        assert results_by_id[inactive_user.username]['userDoesNotExist'] is False
        assert results_by_id[inactive_user.username]['is_active'] is False
        assert not CourseStaffRole(self.course_key).has_user(inactive_user)

    def test_grant_role_empty_identifiers(self):
        """POST with empty identifiers array returns 400."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [],
            'role': 'staff',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_missing_action_returns_400(self):
        """POST without action field returns 400."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [self.student.username],
            'role': 'staff',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # ---- POST revoke tests ----

    def test_revoke_staff_role(self):
        """Revoke staff role from one or more users via array."""
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        CourseStaffRole(self.course_key).add_users(user_a, user_b)
        assert CourseStaffRole(self.course_key).has_user(user_a)
        assert CourseStaffRole(self.course_key).has_user(user_b)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [user_a.username, user_b.username],
            'role': 'staff',
            'action': 'revoke',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['action'] == 'revoke'
        assert response.data['role'] == 'staff'
        for result in response.data['results']:
            assert result['error'] is False
        fresh_a = get_user_model().objects.get(pk=user_a.pk)
        fresh_b = get_user_model().objects.get(pk=user_b.pk)
        assert not CourseStaffRole(self.course_key).has_user(fresh_a)
        assert not CourseStaffRole(self.course_key).has_user(fresh_b)

    def test_revoke_own_instructor_role_errors(self):
        """Instructors cannot revoke their own instructor access."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [self.instructor.username],
            'role': 'instructor',
            'action': 'revoke',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[self.instructor.username]['error'] is True
        assert CourseInstructorRole(self.course_key).has_user(self.instructor)

    def test_revoke_role_user_not_found(self):
        """Revoking a role from a non-existent user reports per-identifier error."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': ['ghost_user_xyz'],
            'role': 'staff',
            'action': 'revoke',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id['ghost_user_xyz']['error'] is True
        assert results_by_id['ghost_user_xyz']['userDoesNotExist'] is True

    def test_revoke_inactive_user_errors(self):
        """Revoking a role from an inactive user reports per-identifier error."""
        inactive_user = UserFactory.create(is_active=False)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [inactive_user.username],
            'role': 'staff',
            'action': 'revoke',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[inactive_user.username]['error'] is True
        assert results_by_id[inactive_user.username]['is_active'] is False

    # ---- email_or_username filter tests ----

    def test_list_email_or_username_filters_by_username(self):
        """email_or_username filters by username substring (case-insensitive)."""
        target = UserFactory.create(username='alicelookup', email='alice@example.com')
        CourseStaffRole(self.course_key).add_users(target)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'email_or_username': 'ALICELOOK'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['email_or_username'] == 'ALICELOOK'
        usernames = [m['username'] for m in response.data['results']]
        assert target.username in usernames
        assert self.staff_user.username not in usernames

    def test_list_email_or_username_filters_by_email(self):
        """email_or_username filters by email substring."""
        target = UserFactory.create(email='needle@example.com')
        CourseStaffRole(self.course_key).add_users(target)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'email_or_username': 'needle'})

        assert response.status_code == status.HTTP_200_OK
        emails = [m['email'] for m in response.data['results']]
        assert target.email in emails

    def test_list_email_or_username_no_match_returns_empty(self):
        """email_or_username that matches nothing returns an empty results list."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'email_or_username': 'zzz_no_match_zzz'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'] == []
        assert response.data['count'] == 0

    def test_list_email_or_username_null_when_omitted(self):
        """email_or_username is null in response when no filter is provided."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['email_or_username'] is None

    # ---- Pagination tests ----

    def test_list_pagination_fields_present(self):
        """Paginated response includes DRF pagination fields."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        for field in ('count', 'num_pages', 'current_page', 'next', 'previous', 'results'):
            assert field in response.data

    def test_list_page_size_limits_results(self):
        """page_size limits the number of returned results per page."""
        for i in range(5):
            user = UserFactory.create(username=f'extra_staff_{i}')
            CourseStaffRole(self.course_key).add_users(user)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'staff', 'page_size': 2})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 2
        assert response.data['count'] >= 5
        assert response.data['num_pages'] >= 3

    def test_list_page_navigation(self):
        """Second page returns different results than the first."""
        for i in range(5):
            user = UserFactory.create(username=f'paged_staff_{i:02d}')
            CourseStaffRole(self.course_key).add_users(user)
        self.client.force_authenticate(user=self.instructor)

        page1 = self.client.get(self.url, {'role': 'staff', 'page_size': 2, 'page': 1})
        page2 = self.client.get(self.url, {'role': 'staff', 'page_size': 2, 'page': 2})

        assert page1.status_code == status.HTTP_200_OK
        assert page2.status_code == status.HTTP_200_OK
        page1_users = {m['username'] for m in page1.data['results']}
        page2_users = {m['username'] for m in page2.data['results']}
        assert page1_users.isdisjoint(page2_users)

    # ---- Forum role tests ----

    def test_grant_forum_role(self):
        """POST with a forum role grants the role via the forum role system."""
        seed_permissions_roles(self.course_key)

        new_user = UserFactory.create()
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [new_user.username],
            'role': 'Moderator',
            'action': 'allow',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['role'] == 'Moderator'
        assert response.data['action'] == 'allow'
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[new_user.username]['error'] is False
        role = Role.objects.get(course_id=self.course_key, name='Moderator')
        assert role.users.filter(pk=new_user.pk).exists()

    def test_list_forum_role(self):
        """GET with a forum role query lists forum role holders."""
        seed_permissions_roles(self.course_key)

        target = UserFactory.create()
        role = Role.objects.get(course_id=self.course_key, name='Community TA')
        role.users.add(target)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self.url, {'role': 'Community TA'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['role'] == 'Community TA'
        usernames = [m['username'] for m in response.data['results']]
        assert target.username in usernames
        for member in response.data['results']:
            assert any(r['role'] == 'Community TA' for r in member['roles'])

    def test_revoke_forum_role(self):
        """POST with action=revoke removes a forum role."""
        seed_permissions_roles(self.course_key)
        target = UserFactory.create()
        role = Role.objects.get(course_id=self.course_key, name='Moderator')
        role.users.add(target)
        assert role.users.filter(pk=target.pk).exists()

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {
            'identifiers': [target.username],
            'role': 'Moderator',
            'action': 'revoke',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['action'] == 'revoke'
        results_by_id = {r['identifier']: r for r in response.data['results']}
        assert results_by_id[target.username]['error'] is False
        assert not role.users.filter(pk=target.pk).exists()


class InstructorPermissionInvalidKeyTest(SimpleTestCase):
    """InstructorPermission must translate InvalidKeyError into Http404."""

    def test_invalid_course_key_raises_http404(self):
        """A malformed course_id on the view kwargs raises Http404, not 500."""
        permission = InstructorPermission()
        request = APIRequestFactory().get('/irrelevant')
        view = Mock(kwargs={'course_id': 'this-is-not-a-course-key'})

        try:
            permission.has_permission(request, view)
        except Http404:
            return
        raise AssertionError('Expected Http404 for invalid course key')


class CourseTeamMemberViewTest(SharedModuleStoreTestCase):
    """Tests for CourseTeamMemberView (DELETE revoke) endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TeamX',
            run='2024_revoke',
            display_name='Team Revoke Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff_user = StaffFactory.create(course_key=self.course_key)
        self.student = UserFactory.create()

    def _get_url(self, email_or_username):
        """Build URL for course team member endpoint."""
        return reverse(
            'instructor_api_v2:course_team_member',
            kwargs={'course_id': str(self.course_key), 'email_or_username': email_or_username}
        )

    def test_revoke_staff_role(self):
        """Revoke staff role from a user."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(self._get_url(self.staff_user.username), {'roles': ['staff']}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['success']
        assert response.data['action'] == 'revoke'
        assert response.data['roles'] == ['staff']
        assert not CourseStaffRole(self.course_key).has_user(self.staff_user)

    def test_revoke_multiple_roles(self):
        """Revoke multiple roles from a user in one request."""
        target = UserFactory.create()
        CourseStaffRole(self.course_key).add_users(target)
        CourseBetaTesterRole(self.course_key).add_users(target)
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(self._get_url(target.username), {'roles': ['staff', 'beta']}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['roles'] == ['staff', 'beta']
        assert not CourseStaffRole(self.course_key).has_user(target)
        assert not CourseBetaTesterRole(self.course_key).has_user(target)

    def test_revoke_self_instructor_blocked(self):
        """Instructors cannot revoke their own instructor access."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self._get_url(self.instructor.username), {'roles': ['instructor']}, format='json'
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_revoke_missing_role_param(self):
        """DELETE without role returns 400."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(self._get_url(self.staff_user.username), format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_revoke_user_not_found(self):
        """Revoking from non-existent user returns 404."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self._get_url('nonexistent_user_12345'), {'roles': ['staff']}, format='json'
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_revoke_unauthenticated(self):
        """Unauthenticated request returns 401."""
        response = self.client.delete(self._get_url(self.staff_user.username), {'roles': ['staff']}, format='json')

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_revoke_no_permission(self):
        """Student without instructor access gets 403."""
        self.client.force_authenticate(user=self.student)
        response = self.client.delete(self._get_url(self.staff_user.username), {'roles': ['staff']}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_revoke_forum_role(self):
        """DELETE with a forum role revokes the role via the forum role system."""
        seed_permissions_roles(self.course_key)
        target = UserFactory.create()
        role = Role.objects.get(course_id=self.course_key, name='Moderator')
        role.users.add(target)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(self._get_url(target.username), {'roles': ['Moderator']}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['roles'] == ['Moderator']
        assert response.data['action'] == 'revoke'
        assert not role.users.filter(pk=target.pk).exists()


class CourseTeamTabVisibilityTest(SharedModuleStoreTestCase):
    """
    Tests that the course_team tab is only visible to Admin (instructor role)
    and Discussion Admin (forum Administrator role).

    See: https://github.com/openedx/openedx-platform/issues/38439
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='TabVis',
            run='2024',
            display_name='Tab Visibility Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse('instructor_api_v2:course_metadata', kwargs={'course_id': str(self.course_key)})

        # Instructor (Admin) — should see course_team tab
        self.instructor = InstructorFactory.create(course_key=self.course_key)

        # Discussion Admin (forum Administrator) — should see course_team tab
        self.forum_admin = StaffFactory.create(course_key=self.course_key)
        seed_permissions_roles(self.course_key)
        admin_role = Role.objects.get(course_id=self.course_key, name='Administrator')
        admin_role.users.add(self.forum_admin)

        # Staff — should NOT see course_team tab
        self.staff_user = StaffFactory.create(course_key=self.course_key)

    def _get_tab_ids(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        return [tab['tab_id'] for tab in response.data.get('tabs', [])]

    def test_instructor_sees_course_team_tab(self):
        """Admin (instructor role) should see the course_team tab."""
        tab_ids = self._get_tab_ids(self.instructor)
        assert 'course_team' in tab_ids

    def test_forum_admin_sees_course_team_tab(self):
        """Discussion Admin (forum Administrator role) should see the course_team tab."""
        tab_ids = self._get_tab_ids(self.forum_admin)
        assert 'course_team' in tab_ids

    def test_staff_does_not_see_course_team_tab(self):
        """Staff without instructor or forum admin role should NOT see the course_team tab."""
        tab_ids = self._get_tab_ids(self.staff_user)
        assert 'course_team' not in tab_ids


class CourseTeamEndpointForumAdminAccessTest(SharedModuleStoreTestCase):
    """
    Tests that Discussion Admin (forum Administrator role) can access
    course team endpoints, not just the instructor role.

    See: https://github.com/openedx/openedx-platform/issues/38439
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edX',
            number='ForumAccess',
            run='2024',
            display_name='Forum Admin Access Test Course',
        )
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        # Discussion Admin: staff + forum Administrator role
        self.forum_admin = StaffFactory.create(course_key=self.course_key)
        seed_permissions_roles(self.course_key)
        admin_role = Role.objects.get(course_id=self.course_key, name='Administrator')
        admin_role.users.add(self.forum_admin)

        # Plain staff user (no forum admin, no instructor) — should be denied
        self.staff_user = StaffFactory.create(course_key=self.course_key)

    def test_forum_admin_can_list_team_roles(self):
        """Discussion Admin should be able to GET /team/roles."""
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_forum_admin_can_list_team_members(self):
        """Discussion Admin should be able to GET /team."""
        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_forum_admin_can_grant_forum_role(self):
        """Discussion Admin should be able to grant a forum role."""
        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        target = UserFactory.create()
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.post(url, {
            'identifiers': [target.username],
            'role': 'Moderator',
            'action': 'allow',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK

    def test_forum_admin_can_revoke_forum_role(self):
        """Discussion Admin should be able to revoke a forum role."""
        target = UserFactory.create()
        role = Role.objects.get(course_id=self.course_key, name='Moderator')
        role.users.add(target)
        url = reverse(
            'instructor_api_v2:course_team_member',
            kwargs={'course_id': str(self.course_key), 'email_or_username': target.username},
        )
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.delete(url, {'roles': ['Moderator']}, format='json')
        assert response.status_code == status.HTTP_200_OK

    def test_forum_admin_cannot_grant_course_role(self):
        """Discussion Admin should not be able to grant a non-forum course role like staff."""
        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        target = UserFactory.create()
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.post(url, {
            'identifiers': [target.username],
            'role': 'staff',
            'action': 'allow',
        }, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'You do not have permissions to change this role' in response.data['error']

    def test_forum_admin_cannot_revoke_course_role(self):
        """Discussion Admin should not be able to revoke a non-forum course role like staff."""
        target = StaffFactory.create(course_key=self.course_key)
        url = reverse(
            'instructor_api_v2:course_team_member',
            kwargs={'course_id': str(self.course_key), 'email_or_username': target.username},
        )
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.delete(url, {'roles': ['staff']}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'You do not have permissions to change the requested roles' in response.data['error']

    def test_plain_staff_cannot_access_team_endpoints(self):
        """Staff without instructor or forum admin role should get 403."""
        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_non_staff_forum_admin_cannot_access_team_endpoints(self):
        """Non-staff user with only forum Administrator role should get 403."""
        non_staff_forum_admin = UserFactory.create()
        admin_role = Role.objects.get(course_id=self.course_key, name='Administrator')
        admin_role.users.add(non_staff_forum_admin)

        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=non_staff_forum_admin)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_forum_admin_cannot_grant_instructor_role(self):
        """Discussion Admin should not be able to grant the instructor role (privilege escalation)."""
        url = reverse('instructor_api_v2:course_team', kwargs={'course_id': str(self.course_key)})
        target = UserFactory.create()
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.post(url, {
            'identifiers': [target.username],
            'role': 'instructor',
            'action': 'allow',
        }, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'You do not have permissions to change this role' in response.data['error']

    def test_forum_admin_cannot_revoke_instructor_role(self):
        """Discussion Admin should not be able to revoke the instructor role."""
        instructor = InstructorFactory.create(course_key=self.course_key)
        url = reverse(
            'instructor_api_v2:course_team_member',
            kwargs={'course_id': str(self.course_key), 'email_or_username': instructor.username},
        )
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.delete(url, {'roles': ['instructor']}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'You do not have permissions to change the requested roles' in response.data['error']

    def test_roles_editable_param_filters_for_forum_admin(self):
        """GET /team/roles?editable=true returns only forum roles for Discussion Admin."""
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.get(url, {'editable': 'true'})
        assert response.status_code == status.HTTP_200_OK
        returned_roles = {r['role'] for r in response.data['results']}
        assert returned_roles == {'Administrator', 'Moderator', 'Group Moderator', 'Community TA'}

    def test_roles_editable_param_returns_all_for_instructor(self):
        """GET /team/roles?editable=true returns all roles for an instructor."""
        instructor = InstructorFactory.create(course_key=self.course_key)
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=instructor)
        response = self.client.get(url, {'editable': 'true'})
        assert response.status_code == status.HTTP_200_OK
        returned_roles = {r['role'] for r in response.data['results']}
        # Instructor should see both course roles and forum roles
        assert 'instructor' in returned_roles
        assert 'staff' in returned_roles
        assert 'Administrator' in returned_roles

    def test_roles_without_editable_param_returns_all(self):
        """GET /team/roles without editable param returns all roles regardless of user."""
        url = reverse('instructor_api_v2:course_team_roles', kwargs={'course_id': str(self.course_key)})
        self.client.force_authenticate(user=self.forum_admin)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        returned_roles = {r['role'] for r in response.data['results']}
        # Without editable param, all roles are returned
        assert 'instructor' in returned_roles
        assert 'staff' in returned_roles
        assert 'Administrator' in returned_roles
