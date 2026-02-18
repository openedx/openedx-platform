"""
Unit tests for instructor API v2 report endpoints.
"""
from unittest.mock import Mock, patch

import ddt
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.roles import CourseDataResearcherRole
from common.djangoapps.student.tests.factories import (
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class ReportDownloadsViewTest(SharedModuleStoreTestCase):
    """
    Tests for the ReportDownloadsView API endpoint.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='edX',
            number='ReportTestX',
            run='Report_Test_Course',
            display_name='Report Test Course',
        )
        self.course_key = self.course.id
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.data_researcher = UserFactory.create()
        CourseDataResearcherRole(self.course_key).add_users(self.data_researcher)
        self.student = UserFactory.create()

    def _get_url(self, course_id=None):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:report_downloads', kwargs={'course_id': course_id})

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    def test_get_report_downloads_as_instructor(self, mock_report_store):
        """
        Test that an instructor can retrieve report downloads.
        """
        # Mock report store
        mock_store = Mock()
        mock_store.links_for.return_value = [
            (
                'course-v1_edX_TestX_Test_Course_grade_report_2024-01-26-1030.csv',
                '/grades/course-v1:edX+TestX+Test_Course/'
                'course-v1_edX_TestX_Test_Course_grade_report_2024-01-26-1030.csv'
            ),
            (
                'course-v1_edX_TestX_Test_Course_enrolled_students_2024-01-25-0900.csv',
                '/grades/course-v1:edX+TestX+Test_Course/'
                'course-v1_edX_TestX_Test_Course_enrolled_students_2024-01-25-0900.csv'
            ),
        ]
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('downloads', response.data)
        downloads = response.data['downloads']
        self.assertEqual(len(downloads), 2)

        # Verify first report structure
        report = downloads[0]
        self.assertIn('report_name', report)
        self.assertIn('report_url', report)
        self.assertIn('date_generated', report)
        self.assertIn('report_type', report)

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    def test_get_report_downloads_as_staff(self, mock_report_store):
        """
        Test that staff can retrieve report downloads.
        """
        mock_store = Mock()
        mock_store.links_for.return_value = []
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('downloads', response.data)

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    def test_get_report_downloads_as_data_researcher(self, mock_report_store):
        """
        Test that data researchers can retrieve report downloads.
        """
        mock_store = Mock()
        mock_store.links_for.return_value = []
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('downloads', response.data)

    def test_get_report_downloads_unauthorized(self):
        """
        Test that students cannot access report downloads endpoint.
        """
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_report_downloads_unauthenticated(self):
        """
        Test that unauthenticated users cannot access the endpoint.
        """
        response = self.client.get(self._get_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_report_downloads_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.instructor)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.get(self._get_url(course_id=nonexistent_course_id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    @ddt.data(
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_grade_report_2024-01-26-1030.csv',
            'grade',
            '2024-01-26T10:30:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_enrolled_students_2024-01-25-0900.csv',
            'enrolled_students',
            '2024-01-25T09:00:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_problem_grade_report_2024-02-15-1545.csv',
            'problem_grade',
            '2024-02-15T15:45:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_ora2_summary_2024-03-10-2030.csv',
            'ora2_summary',
            '2024-03-10T20:30:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_ora2_data_2024-03-11-1200.csv',
            'ora2_data',
            '2024-03-11T12:00:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_ora2_submission_files_2024-03-12-0800.zip',
            'ora2_submission_files',
            '2024-03-12T08:00:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_certificate_report_2024-04-01-1000.csv',
            'issued_certificates',
            '2024-04-01T10:00:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_problem_responses_2024-05-20-1430.csv',
            'problem_responses',
            '2024-05-20T14:30:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_may_enroll_2024-06-01-0930.csv',
            'pending_enrollments',
            '2024-06-01T09:30:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_inactive_enrolled_2024-07-15-1115.csv',
            'pending_activations',
            '2024-07-15T11:15:00Z'
        ),
        (
            'course-v1_edX_ReportTestX_Report_Test_Course_anon_ids_2024-08-20-1600.csv',
            'anonymized_student_ids',
            '2024-08-20T16:00:00Z'
        ),
    )
    @ddt.unpack
    def test_report_type_detection(self, filename, expected_type, expected_date, mock_report_store):
        """
        Test that report types are correctly detected from filenames.
        """
        mock_store = Mock()
        mock_store.links_for.return_value = [
            (filename, f'/grades/course-v1:edX+ReportTestX+Report_Test_Course/{filename}'),
        ]
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        downloads = response.data['downloads']
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]['report_type'], expected_type)
        self.assertEqual(downloads[0]['date_generated'], expected_date)

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    def test_report_without_date(self, mock_report_store):
        """
        Test handling of report files without date information.
        """
        mock_store = Mock()
        mock_store.links_for.return_value = [
            ('course_report.csv', '/grades/course-v1:edX+ReportTestX+Report_Test_Course/course_report.csv'),
        ]
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        downloads = response.data['downloads']
        self.assertEqual(len(downloads), 1)
        self.assertIsNone(downloads[0]['date_generated'])

    @patch('lms.djangoapps.instructor.views.api_v2.ReportStore.from_config')
    def test_empty_reports_list(self, mock_report_store):
        """
        Test endpoint with no reports available.
        """
        mock_store = Mock()
        mock_store.links_for.return_value = []
        mock_report_store.return_value = mock_store

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['downloads'], [])


@ddt.ddt
class GenerateReportViewTest(SharedModuleStoreTestCase):
    """
    Tests for the GenerateReportView API endpoint.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='edX',
            number='GenReportTestX',
            run='GenReport_Test_Course',
            display_name='Generate Report Test Course',
        )
        self.course_key = self.course.id
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course_key)
        self.staff = StaffFactory.create(course_key=self.course_key)
        self.data_researcher = UserFactory.create()
        CourseDataResearcherRole(self.course_key).add_users(self.data_researcher)
        self.student = UserFactory.create()

    def _get_url(self, course_id=None, report_type='grade'):
        """Helper to get the API URL."""
        if course_id is None:
            course_id = str(self.course_key)
        return reverse('instructor_api_v2:generate_report', kwargs={
            'course_id': course_id,
            'report_type': report_type
        })

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_grades_csv')
    def test_generate_grade_report(self, mock_submit):
        """
        Test generating a grade report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='grade'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.instructor_analytics_basic.get_available_features')
    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_students_features_csv')
    def test_generate_enrolled_students_report(self, mock_submit, mock_get_features):
        """
        Test generating an enrolled students report.
        Verifies that get_available_features is called to support custom attributes.
        """
        mock_submit.return_value = None
        mock_get_features.return_value = ('id', 'username', 'email', 'custom_field')

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='enrolled_students'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_get_features.assert_called_once_with(self.course.id)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_may_enroll_csv')
    def test_generate_pending_enrollments_report(self, mock_submit):
        """
        Test generating a pending enrollments report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='pending_enrollments'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_inactive_enrolled_students_csv')
    def test_generate_pending_activations_report(self, mock_submit):
        """
        Test generating a pending activations report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='pending_activations'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.generate_anonymous_ids')
    def test_generate_anonymized_ids_report(self, mock_submit):
        """
        Test generating an anonymized student IDs report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='anonymized_student_ids'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_problem_grade_report')
    def test_generate_problem_grade_report(self, mock_submit):
        """
        Test generating a problem grade report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='problem_grade'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    def test_generate_problem_responses_report_missing_location(self):
        """
        Test that generating a problem responses report without a problem_location returns 400.
        """
        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='problem_responses'))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_problem_responses_csv')
    @patch('lms.djangoapps.instructor.views.api_v2.modulestore')
    def test_generate_problem_responses_with_location(self, mock_modulestore, mock_submit):
        """
        Test generating a problem responses report with specific problem location.
        """
        # Mock a problem block instead of creating real ones
        mock_problem = Mock()
        mock_problem.location = Mock()

        mock_store = Mock()
        mock_store.get_item.return_value = mock_problem
        mock_store.make_course_usage_key.return_value = self.course.location
        mock_modulestore.return_value = mock_store
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(
            self._get_url(report_type='problem_responses'),
            {'problem_location': 'block-v1:edX+GenReportTestX+GenReport_Test_Course+type@problem+block@test'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_export_ora2_summary')
    def test_generate_ora2_summary_report(self, mock_submit):
        """
        Test generating an ORA2 summary report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='ora2_summary'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_export_ora2_data')
    def test_generate_ora2_data_report(self, mock_submit):
        """
        Test generating an ORA2 data report.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='ora2_data'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_export_ora2_submission_files')
    def test_generate_ora2_submission_files_report(self, mock_submit):
        """
        Test generating an ORA2 submission files archive.
        """
        mock_submit.return_value = None

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='ora2_submission_files'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_submit.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.instructor_analytics_basic.issued_certificates')
    @patch('lms.djangoapps.instructor.views.api_v2.instructor_analytics_csvs.format_dictlist')
    @patch('lms.djangoapps.instructor.views.api_v2.upload_csv_file_to_report_store')
    def test_generate_issued_certificates_report(self, mock_upload, mock_format, mock_issued_certs):
        """
        Test generating an issued certificates report.
        Note: This report uses staff permission instead of CAN_RESEARCH.
        """
        mock_issued_certs.return_value = []
        mock_format.return_value = ([], [])
        mock_upload.return_value = None

        # Use staff user since issued certificates requires staff permission
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(self._get_url(report_type='issued_certificates'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        mock_issued_certs.assert_called_once()
        mock_upload.assert_called_once()

    def test_generate_report_invalid_type(self):
        """
        Test error handling for invalid report type.
        """
        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='invalid_type'))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_grades_csv')
    def test_generate_report_already_running(self, mock_submit):
        """
        Test error handling when a report generation task is already running.
        """
        from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError
        mock_submit.side_effect = AlreadyRunningError('Task already running')

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='grade'))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_grades_csv')
    def test_generate_report_queue_connection_error(self, mock_submit):
        """
        Test error handling for queue connection errors.
        """
        from lms.djangoapps.instructor_task.api_helper import QueueConnectionError
        mock_submit.side_effect = QueueConnectionError('Cannot connect to queue')

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='grade'))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('error', response.data)

    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_problem_responses_csv')
    def test_generate_report_value_error(self, mock_submit):
        """
        Test error handling for ValueError exceptions.
        """
        mock_submit.side_effect = ValueError('Invalid parameter')

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(self._get_url(report_type='problem_responses'))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_generate_report_unauthorized_student(self):
        """
        Test that students cannot generate reports.
        """
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self._get_url(report_type='grade'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_report_unauthenticated(self):
        """
        Test that unauthenticated users cannot generate reports.
        """
        response = self.client.post(self._get_url(report_type='grade'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_generate_report_nonexistent_course(self):
        """
        Test error handling for non-existent course.
        """
        self.client.force_authenticate(user=self.data_researcher)
        nonexistent_course_id = 'course-v1:edX+NonExistent+2024'
        response = self.client.post(self._get_url(course_id=nonexistent_course_id, report_type='grade'))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('lms.djangoapps.instructor.views.api_v2.modulestore')
    @patch('lms.djangoapps.instructor.views.api_v2.task_api.submit_calculate_problem_responses_csv')
    def test_problem_responses_with_invalid_location(self, mock_submit, mock_modulestore):
        """
        Test generating problem responses report with invalid problem location.
        """
        mock_store = Mock()
        mock_store.get_item.side_effect = Exception('Not found')
        mock_modulestore.return_value = mock_store

        self.client.force_authenticate(user=self.data_researcher)
        response = self.client.post(
            self._get_url(report_type='problem_responses'),
            {'problem_location': 'invalid-location'}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
