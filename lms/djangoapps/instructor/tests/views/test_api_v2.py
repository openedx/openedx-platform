"""
Tests for Instructor API v2 GET endpoints.
"""
import json
from uuid import uuid4

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import InstructorFactory, UserFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor_task.models import InstructorTask
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class LearnerViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/learners/{email_or_username}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(
            username='john_harvard',
            email='john@example.com',
        )
        self.student.profile.name = 'John Harvard'
        self.student.profile.save()
        self.client.force_authenticate(user=self.instructor)

    def test_get_learner_by_username(self):
        """Test retrieving learner info by username"""
        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.username
        })
        response = self.client.get(url)

        expected_progress_url = reverse('student_progress', kwargs={
            'course_id': str(self.course.id),
            'student_id': self.student.id,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['username'], 'john_harvard')  # noqa: PT009
        self.assertEqual(data['email'], 'john@example.com')  # noqa: PT009
        self.assertEqual(data['full_name'], 'John Harvard')  # noqa: PT009
        self.assertEqual(data['progress_url'], expected_progress_url)  # noqa: PT009

    def test_get_learner_by_email(self):
        """Test retrieving learner info by email"""
        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.email
        })
        response = self.client.get(url)

        expected_progress_url = reverse('student_progress', kwargs={
            'course_id': str(self.course.id),
            'student_id': self.student.id,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['username'], 'john_harvard')  # noqa: PT009
        self.assertEqual(data['email'], 'john@example.com')  # noqa: PT009
        self.assertEqual(data['progress_url'], expected_progress_url)  # noqa: PT009

    def test_get_learner_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.username
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])  # noqa: PT009


class ProblemViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/problems/{location}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(display_name='Test Course')
        self.instructor = InstructorFactory.create(course_key=self.course.id)
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

    def test_get_problem_metadata(self):
        """Test retrieving problem metadata"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['id'], str(self.problem.location))  # noqa: PT009
        self.assertEqual(data['name'], 'Sample Problem')  # noqa: PT009
        self.assertIn('breadcrumbs', data)  # noqa: PT009
        self.assertIsInstance(data['breadcrumbs'], list)  # noqa: PT009

    def test_get_problem_with_breadcrumbs(self):
        """Test that breadcrumbs contain the full course hierarchy"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        breadcrumbs = data['breadcrumbs']

        # Should contain: course → chapter → sequential → problem
        self.assertEqual(len(breadcrumbs), 4)  # noqa: PT009
        self.assertEqual(breadcrumbs[0]['display_name'], self.course.display_name)  # noqa: PT009
        self.assertIsNone(breadcrumbs[0]['usage_key'])  # course-level has no usage_key  # noqa: PT009
        self.assertEqual(breadcrumbs[1]['display_name'], 'Week 1')  # noqa: PT009
        self.assertEqual(breadcrumbs[2]['display_name'], 'Homework 1')  # noqa: PT009
        self.assertEqual(breadcrumbs[3]['display_name'], 'Sample Problem')  # noqa: PT009

    def test_get_problem_invalid_location(self):
        """Test 400 with invalid problem location"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': 'invalid-location'
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        self.assertIn('error', response.json())  # noqa: PT009

    def test_get_problem_without_learner_has_null_score_and_attempts(self):
        """Test that current_score and attempts are null when no learner is specified"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertIsNone(data['current_score'])  # noqa: PT009
        self.assertIsNone(data['attempts'])  # noqa: PT009

    def test_get_problem_with_learner_returns_score_and_attempts(self):
        """Test that current_score and attempts are returned when learner has a StudentModule"""
        student = UserFactory()
        StudentModule.objects.create(
            student=student,
            course_id=self.course.id,
            module_state_key=self.problem.location,
            module_type='problem',
            grade=7.0,
            max_grade=10.0,
            state=json.dumps({'attempts': 3}),
        )

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url, {'email_or_username': student.username})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['current_score']['score'], 7.0)  # noqa: PT009
        self.assertEqual(data['current_score']['total'], 10.0)  # noqa: PT009
        self.assertEqual(data['attempts']['current'], 3)  # noqa: PT009

    def test_get_problem_with_learner_no_submission_returns_nulls(self):
        """Test that current_score and attempts are null when learner has no StudentModule"""
        student = UserFactory()
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url, {'email_or_username': student.username})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertIsNone(data['current_score'])  # noqa: PT009
        self.assertIsNone(data['attempts'])  # noqa: PT009

    def test_get_problem_with_unknown_learner_returns_404(self):
        """Test that a 404 is returned when learner does not exist"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url, {'email_or_username': 'nonexistent_user'})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009

    def test_get_problem_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])  # noqa: PT009


class TaskStatusViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/tasks/{task_id}
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)

    def test_get_task_status_completed(self):
        """Test retrieving completed task status"""
        # Create a completed task
        task_id = str(uuid4())
        task_output = json.dumps({
            'current': 150,
            'total': 150,
            'message': 'Reset attempts for 150 learners'
        })
        InstructorTask.objects.create(
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

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['task_id'], task_id)  # noqa: PT009
        self.assertEqual(data['state'], 'completed')  # noqa: PT009
        self.assertIn('progress', data)  # noqa: PT009
        self.assertEqual(data['progress']['current'], 150)  # noqa: PT009
        self.assertEqual(data['progress']['total'], 150)  # noqa: PT009
        self.assertIn('result', data)  # noqa: PT009
        self.assertTrue(data['result']['success'])  # noqa: PT009

    def test_get_task_status_running(self):
        """Test retrieving running task status"""
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

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['state'], 'running')  # noqa: PT009
        self.assertIn('progress', data)  # noqa: PT009
        self.assertEqual(data['progress']['current'], 75)  # noqa: PT009
        self.assertEqual(data['progress']['total'], 150)  # noqa: PT009

    def test_get_task_status_failed(self):
        """Test retrieving failed task status"""
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

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertEqual(data['state'], 'failed')  # noqa: PT009
        self.assertIn('error', data)  # noqa: PT009
        self.assertIn('code', data['error'])  # noqa: PT009
        self.assertIn('message', data['error'])  # noqa: PT009

    def test_get_task_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:task_status', kwargs={
            'course_id': str(self.course.id),
            'task_id': 'some-task-id'
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])  # noqa: PT009


class GradingConfigViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/instructor/v2/courses/{course_key}/grading-config
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)

    def test_get_grading_config(self):
        """Test retrieving grading configuration returns graders and grade cutoffs"""
        url = reverse('instructor_api_v2:grading_config', kwargs={
            'course_id': str(self.course.id),
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        self.assertIn('graders', data)  # noqa: PT009
        self.assertIn('grade_cutoffs', data)  # noqa: PT009
        self.assertIsInstance(data['graders'], list)  # noqa: PT009
        self.assertIsInstance(data['grade_cutoffs'], dict)  # noqa: PT009

    def test_get_grading_config_grader_fields(self):
        """Test that each grader entry has the expected fields"""
        url = reverse('instructor_api_v2:grading_config', kwargs={
            'course_id': str(self.course.id),
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        data = response.json()
        for grader in data['graders']:
            self.assertIn('type', grader)  # noqa: PT009
            self.assertIn('min_count', grader)  # noqa: PT009
            self.assertIn('drop_count', grader)  # noqa: PT009
            self.assertIn('weight', grader)  # noqa: PT009

    def test_get_grading_config_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:grading_config', kwargs={
            'course_id': str(self.course.id),
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])  # noqa: PT009
