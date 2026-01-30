"""
Tests for Instructor API v2 GET endpoints.
"""
import json
from unittest.mock import patch
from uuid import uuid4
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor_task.models import InstructorTask
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory


class LearnerViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/learners/{email_or_username}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = UserFactory(is_staff=False)
        self.student = UserFactory(
            username='john_harvard',
            email='john@example.com',
            first_name='John',
            last_name='Harvard'
        )
        self.course = CourseFactory.create()
        self.client.force_authenticate(user=self.instructor)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_learner_by_username(self, mock_perm):
        """Test retrieving learner info by username"""
        mock_perm.return_value = True

        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.username
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['username'], 'john_harvard')
        self.assertEqual(data['email'], 'john@example.com')
        self.assertEqual(data['first_name'], 'John')
        self.assertEqual(data['last_name'], 'Harvard')

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_learner_by_email(self, mock_perm):
        """Test retrieving learner info by email"""
        mock_perm.return_value = True

        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.email
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['username'], 'john_harvard')
        self.assertEqual(data['email'], 'john@example.com')

    def test_get_learner_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.username
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


class ProblemViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/problems/{location}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = UserFactory(is_staff=False)
        self.course = CourseFactory.create(display_name='Test Course')
        self.chapter = BlockFactory.create(
            parent=self.course,
            category='chapter',
            display_name='Week 1'
        )
        self.sequential = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name='Homework 1'
        )
        self.problem = BlockFactory.create(
            parent=self.sequential,
            category='problem',
            display_name='Sample Problem'
        )
        self.client.force_authenticate(user=self.instructor)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_problem_metadata(self, mock_perm):
        """Test retrieving problem metadata"""
        mock_perm.return_value = True

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['id'], str(self.problem.location))
        self.assertEqual(data['name'], 'Sample Problem')
        self.assertIn('breadcrumbs', data)
        self.assertIsInstance(data['breadcrumbs'], list)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_problem_with_breadcrumbs(self, mock_perm):
        """Test that breadcrumbs are included in response"""
        mock_perm.return_value = True

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        breadcrumbs = data['breadcrumbs']

        # Should have at least the problem itself
        self.assertGreater(len(breadcrumbs), 0)
        # Check that breadcrumb items have required fields
        for crumb in breadcrumbs:
            self.assertIn('display_name', crumb)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_problem_invalid_location(self, mock_perm):
        """Test 400 with invalid problem location"""
        mock_perm.return_value = True

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': 'invalid-location'
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.json())

    def test_get_problem_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


class TaskStatusViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/tasks/{task_id}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.instructor = UserFactory(is_staff=False)
        self.course = CourseFactory.create()
        self.client.force_authenticate(user=self.instructor)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_task_status_completed(self, mock_perm):
        """Test retrieving completed task status"""
        mock_perm.return_value = True

        # Create a completed task
        task_id = str(uuid4())
        task_output = json.dumps({
            'current': 150,
            'total': 150,
            'message': 'Reset attempts for 150 learners'
        })
        task = InstructorTask.objects.create(
            course_id=self.course.id,
            task_type='rescore_problem',
            task_key='',
            task_input='{}',
            task_id=task_id,
            task_state='SUCCESS',
            task_output=task_output,
            requester=self.instructor
        )

        url = reverse('instructor_api_v2:task_status', kwargs={
            'course_id': str(self.course.id),
            'task_id': task_id
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['task_id'], task_id)
        self.assertEqual(data['state'], 'completed')
        self.assertIn('progress', data)
        self.assertEqual(data['progress']['current'], 150)
        self.assertEqual(data['progress']['total'], 150)
        self.assertIn('result', data)
        self.assertTrue(data['result']['success'])

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_task_status_running(self, mock_perm):
        """Test retrieving running task status"""
        mock_perm.return_value = True

        # Create a running task
        task_id = str(uuid4())
        task_output = json.dumps({'current': 75, 'total': 150})
        InstructorTask.objects.create(
            course_id=self.course.id,
            task_type='rescore_problem',
            task_key='',
            task_input='{}',
            task_id=task_id,
            task_state='PROGRESS',
            task_output=task_output,
            requester=self.instructor
        )

        url = reverse('instructor_api_v2:task_status', kwargs={
            'course_id': str(self.course.id),
            'task_id': task_id
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['state'], 'running')
        self.assertIn('progress', data)
        self.assertEqual(data['progress']['current'], 75)
        self.assertEqual(data['progress']['total'], 150)

    @patch('lms.djangoapps.instructor.views.api_v2.permissions.InstructorPermission.has_permission')
    def test_get_task_status_failed(self, mock_perm):
        """Test retrieving failed task status"""
        mock_perm.return_value = True

        # Create a failed task
        task_id = str(uuid4())
        InstructorTask.objects.create(
            course_id=self.course.id,
            task_type='rescore_problem',
            task_key='',
            task_input='{}',
            task_id=task_id,
            task_state='FAILURE',
            task_output='Task execution failed',
            requester=self.instructor
        )

        url = reverse('instructor_api_v2:task_status', kwargs={
            'course_id': str(self.course.id),
            'task_id': task_id
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['state'], 'failed')
        self.assertIn('error', data)
        self.assertIn('code', data['error'])
        self.assertIn('message', data['error'])

    def test_get_task_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:task_status', kwargs={
            'course_id': str(self.course.id),
            'task_id': 'some-task-id'
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
