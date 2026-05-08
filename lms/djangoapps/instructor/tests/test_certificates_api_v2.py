"""
Unit tests for instructor API v2 certificate management endpoints.
"""
from io import BytesIO
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    InstructorFactory,
    UserFactory,
)
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import CertificateAllowlist, CertificateInvalidation
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class ToggleCertificateGenerationViewTest(SharedModuleStoreTestCase):
    """Tests for ToggleCertificateGenerationView."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory.create()
        self.url = reverse(
            'instructor_api_v2:toggle_certificate_generation',
            kwargs={'course_id': str(self.course.id)}
        )

    def test_permission_required(self):
        """Test that only instructors can toggle certificate generation."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, {'enabled': True}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_course_not_found(self):
        """Test 404 when course doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        url = reverse(
            'instructor_api_v2:toggle_certificate_generation',
            kwargs={'course_id': 'course-v1:edX+Invalid+2024'}
        )
        response = self.client.post(url, {'enabled': True}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_missing_enabled_field(self):
        """Test validation error when enabled field is missing."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'enabled' in response.data

    def test_invalid_enabled_field_type(self):
        """Test validation error when enabled is not boolean."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {'enabled': 'invalid'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_successful_toggle(self):
        """Test successful certificate generation toggle."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {'enabled': False}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'enabled': False}


class CertificateExceptionsViewTest(SharedModuleStoreTestCase):
    """Tests for CertificateExceptionsView."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory.create()
        self.enrolled_student = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.enrolled_student, course_id=self.course.id)
        self.url = reverse(
            'instructor_api_v2:certificate_exceptions',
            kwargs={'course_id': str(self.course.id)}
        )

    def test_post_permission_required(self):
        """Test that only instructors can grant exceptions."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_course_not_found(self):
        """Test 404 when course doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        url = reverse(
            'instructor_api_v2:certificate_exceptions',
            kwargs={'course_id': 'course-v1:edX+Invalid+2024'}
        )
        response = self.client.post(
            url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_post_no_learners(self):
        """Test error when no learners provided."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {'learners': [], 'notes': 'Test'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_user_not_found(self):
        """Test error for non-existent user."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': ['nonexistent'], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['errors']) == 1
        # The actual error message from get_user_by_username_or_email
        assert 'does not exist' in response.data['errors'][0]['message']

    def test_post_user_not_enrolled(self):
        """Test error when user is not enrolled."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['errors']) == 1
        assert 'not enrolled' in response.data['errors'][0]['message']

    def test_post_user_has_invalidation(self):
        """Test error when user has active invalidation."""
        cert = GeneratedCertificateFactory.create(
            user=self.enrolled_student,
            course_id=self.course.id,
            status=CertificateStatuses.unavailable
        )
        CertificateInvalidation.objects.create(
            generated_certificate=cert,
            invalidated_by=self.instructor,
            notes='Test invalidation',
            active=True
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['errors']) == 1
        assert 'invalidation' in response.data['errors'][0]['message']

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_post_successful_single(self, mock_create):
        """Test successful exception grant for single learner."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test exception'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        assert self.enrolled_student.username in response.data['success']
        mock_create.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_post_successful_bulk(self, mock_create):
        """Test successful bulk exception grant."""
        student2 = UserFactory.create()
        CourseEnrollmentFactory.create(user=student2, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {
                'learners': [self.enrolled_student.username, student2.username],
                'notes': 'Bulk test'
            },
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 2
        assert mock_create.call_count == 2

    def test_delete_permission_required(self):
        """Test that only instructors can remove exceptions."""
        self.client.force_authenticate(user=self.student)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_no_username(self):
        """Test error when username not provided."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(self.url, {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_user_not_found(self):
        """Test error when user doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': 'nonexistent'},
            format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.get_allowlist_entry')
    def test_delete_no_exception(self, mock_get_entry):
        """Test 404 when no exception exists."""
        mock_get_entry.return_value = None
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.remove_allowlist_entry')
    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.get_allowlist_entry')
    def test_delete_successful(self, mock_get_entry, mock_remove):
        """Test successful exception removal."""
        mock_entry = CertificateAllowlist(
            user=self.enrolled_student,
            course_id=self.course.id,
            allowlist=True,
            notes='Test'
        )
        mock_get_entry.return_value = mock_entry

        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        mock_remove.assert_called_once_with(self.enrolled_student, self.course.id)


class BulkCertificateExceptionsViewTest(SharedModuleStoreTestCase):
    """Tests for BulkCertificateExceptionsView."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory.create()
        self.url = reverse(
            'instructor_api_v2:bulk_certificate_exceptions',
            kwargs={'course_id': str(self.course.id)}
        )

    def _create_csv_file(self, content):
        """Helper to create a CSV file upload."""
        csv_file = BytesIO(content.encode('utf-8'))
        csv_file.name = 'test.csv'
        return csv_file

    def test_permission_required(self):
        """Test that only instructors can upload bulk exceptions."""
        self.client.force_authenticate(user=self.student)
        csv_file = self._create_csv_file('user1,notes1')
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_no_file_uploaded(self):
        """Test error when no file is uploaded."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self.url, {}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'No file uploaded' in response.data['message']

    def test_non_csv_file_type(self):
        """Test error when uploaded file is not CSV."""
        self.client.force_authenticate(user=self.instructor)
        txt_file = BytesIO(b'user1,notes1')
        txt_file.name = 'test.txt'
        response = self.client.post(self.url, {'file': txt_file}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'CSV format' in response.data['message']

    def test_empty_csv(self):
        """Test error when CSV file is empty."""
        self.client.force_authenticate(user=self.instructor)
        csv_file = self._create_csv_file('')
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'empty' in response.data['message']

    def test_csv_with_only_empty_rows(self):
        """Test error when CSV contains only empty rows."""
        self.client.force_authenticate(user=self.instructor)
        csv_file = self._create_csv_file('\n\n  \n')
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'empty' in response.data['message']

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_happy_path_csv(self, mock_create):
        """Test successful bulk upload with valid CSV."""
        student1 = UserFactory.create(username='student1')
        student2 = UserFactory.create(username='student2', email='student2@example.com')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=student2, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'student1,First student notes\nstudent2@example.com,Second student notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 2
        assert 'student1' in response.data['success']
        assert 'student2@example.com' in response.data['success']
        assert len(response.data['errors']) == 0
        assert mock_create.call_count == 2

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_csv_without_notes_column(self, mock_create):
        """Test CSV with only username column (no notes)."""
        student1 = UserFactory.create(username='student1')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'student1'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        # Verify empty notes were passed
        call_args = mock_create.call_args
        assert call_args[0][2] == ''  # notes parameter

    def test_unresolvable_learners(self):
        """Test error handling for users that don't exist."""
        self.client.force_authenticate(user=self.instructor)
        csv_content = 'nonexistent1,notes1\nnonexistent2,notes2'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 0
        assert len(response.data['errors']) == 2
        assert any('nonexistent1' in str(err) for err in response.data['errors'])
        assert any('nonexistent2' in str(err) for err in response.data['errors'])

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_partial_success(self, mock_create):
        """Test mix of valid and invalid learners in CSV."""
        student1 = UserFactory.create(username='valid_user')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'valid_user,Valid notes\ninvalid_user,Invalid notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        assert 'valid_user' in response.data['success']
        assert len(response.data['errors']) == 1
        assert any('invalid_user' in str(err) for err in response.data['errors'])
        mock_create.assert_called_once()

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_duplicate_csv_identifiers(self, mock_create):
        """Test that duplicate identifiers use last occurrence's notes."""
        student1 = UserFactory.create(username='student1')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        # Same identifier twice with different notes
        csv_content = 'student1,First notes\nstudent1,Last notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        # Verify the last notes value was used (dict behavior)
        call_args = mock_create.call_args
        assert call_args[0][2] == 'Last notes'

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_csv_with_empty_notes(self, mock_create):
        """Test CSV rows with empty notes column."""
        student1 = UserFactory.create(username='student1')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'student1,'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        call_args = mock_create.call_args
        assert call_args[0][2] == ''

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_unenrolled_learner(self, mock_create):
        """Test error when learner exists but is not enrolled in course."""
        UserFactory.create(username='unenrolled')
        # Don't enroll the student

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'unenrolled,notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 0
        assert len(response.data['errors']) == 1
        assert 'not enrolled' in response.data['errors'][0]['message']
        mock_create.assert_not_called()

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_learner_with_active_invalidation(self, mock_create):
        """Test error when learner has an active certificate invalidation."""
        student1 = UserFactory.create(username='invalidated')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)
        cert = GeneratedCertificateFactory.create(
            user=student1,
            course_id=self.course.id,
            status=CertificateStatuses.unavailable
        )
        CertificateInvalidation.objects.create(
            generated_certificate=cert,
            invalidated_by=self.instructor,
            notes='Test invalidation',
            active=True
        )

        self.client.force_authenticate(user=self.instructor)
        csv_content = 'invalidated,notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 0
        assert len(response.data['errors']) == 1
        assert 'invalidation' in response.data['errors'][0]['message']
        mock_create.assert_not_called()

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_or_update_certificate_allowlist_entry')
    def test_csv_with_utf8_bom(self, mock_create):
        """Test CSV file with UTF-8 BOM is handled correctly."""
        student1 = UserFactory.create(username='student1')
        CourseEnrollmentFactory.create(user=student1, course_id=self.course.id)

        self.client.force_authenticate(user=self.instructor)
        # UTF-8 BOM + CSV content
        csv_content = '\ufeffstudent1,notes'
        csv_file = self._create_csv_file(csv_content)
        response = self.client.post(self.url, {'file': csv_file}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        mock_create.assert_called_once()


class CertificateInvalidationsViewTest(SharedModuleStoreTestCase):
    """Tests for CertificateInvalidationsView."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory.create()
        self.enrolled_student = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.enrolled_student, course_id=self.course.id)
        self.url = reverse(
            'instructor_api_v2:certificate_invalidations',
            kwargs={'course_id': str(self.course.id)}
        )

    def test_post_permission_required(self):
        """Test that only instructors can invalidate certificates."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_course_not_found(self):
        """Test 404 when course doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        url = reverse(
            'instructor_api_v2:certificate_invalidations',
            kwargs={'course_id': 'course-v1:edX+Invalid+2024'}
        )
        response = self.client.post(
            url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_post_no_certificate(self):
        """Test error when certificate doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['errors']) == 1
        assert 'not found' in response.data['errors'][0]['message']

    def test_post_certificate_already_invalid(self):
        """Test error when certificate is already invalid."""
        GeneratedCertificateFactory.create(
            user=self.enrolled_student,
            course_id=self.course.id,
            status=CertificateStatuses.unavailable
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['errors']) == 1
        assert 'invalid' in response.data['errors'][0]['message']

    @patch('lms.djangoapps.instructor.views.api_v2.certs_api.create_certificate_invalidation_entry')
    def test_post_successful(self, mock_create):
        """Test successful certificate invalidation."""
        GeneratedCertificateFactory.create(
            user=self.enrolled_student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(
            self.url,
            {'learners': [self.enrolled_student.username], 'notes': 'Test invalidation'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['success']) == 1
        mock_create.assert_called_once()

    def test_delete_permission_required(self):
        """Test that only instructors can re-validate certificates."""
        self.client.force_authenticate(user=self.student)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_no_certificate(self):
        """Test 404 when certificate doesn't exist."""
        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch('lms.djangoapps.instructor_task.api.generate_certificates_for_students')
    def test_delete_no_invalidation(self, mock_generate):
        """Test 404 when no active invalidation exists."""
        GeneratedCertificateFactory.create(
            user=self.enrolled_student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_generate.assert_not_called()

    @patch('lms.djangoapps.instructor_task.api.generate_certificates_for_students')
    def test_delete_successful_regeneration(self, mock_generate):
        """Test successful re-validation triggers certificate regeneration."""
        cert = GeneratedCertificateFactory.create(
            user=self.enrolled_student,
            course_id=self.course.id,
            status=CertificateStatuses.unavailable
        )
        CertificateInvalidation.objects.create(
            generated_certificate=cert,
            invalidated_by=self.instructor,
            notes='Test invalidation',
            active=True
        )

        self.client.force_authenticate(user=self.instructor)
        response = self.client.delete(
            self.url,
            {'username': self.enrolled_student.username},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify certificate regeneration was triggered
        mock_generate.assert_called_once()
        call_args = mock_generate.call_args
        assert call_args.kwargs['student_set'] == 'specific_student'
        assert call_args.kwargs['specific_student_id'] == self.enrolled_student.id
