"""
Unit tests for instructor API v2 certificate management endpoints.
"""
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
