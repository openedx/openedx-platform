"""
Tests for Instructor API v2 endpoints.
"""
import json
from textwrap import dedent
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, InstructorFactory, UserFactory
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

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['username'] == 'john_harvard'
        assert data['email'] == 'john@example.com'
        assert data['full_name'] == 'John Harvard'
        assert data['progress_url'] == expected_progress_url
        assert not data['is_enrolled']

    def test_get_learner_by_username_enrolled(self):
        """Test that is_enrolled is true for users enrolled in the course"""
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.student
        )
        url = reverse('instructor_api_v2:learner_detail', kwargs={
            'course_id': str(self.course.id),
            'email_or_username': self.student.username
        })
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['is_enrolled']

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
        """Test that a 400 is returned when learner does not exist"""
        url = reverse('instructor_api_v2:problem_detail', kwargs={
            'course_id': str(self.course.id),
            'location': str(self.problem.location)
        })
        response = self.client.get(url, {'email_or_username': 'nonexistent_user'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009

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
        """Test retrieving grading configuration returns HTML summary from dump_grading_context"""
        url = reverse('instructor_api_v2:grading_config', kwargs={
            'course_id': str(self.course.id),
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response['Content-Type'], 'text/html')  # noqa: PT009

        hbar = '-' * 77
        expected_html = (
            f'<pre>{hbar}\n'
            'Course grader:\n'
            '&lt;class &#39;xmodule.graders.WeightedSubsectionsGrader&#39;&gt;\n'
            '\n'
            'Graded sections:\n'
            '  subgrader=&lt;class &#39;xmodule.graders.AssignmentFormatGrader&#39;&gt;,'
            ' type=Homework, category=Homework, weight=0.15\n'
            '  subgrader=&lt;class &#39;xmodule.graders.AssignmentFormatGrader&#39;&gt;,'
            ' type=Lab, category=Lab, weight=0.15\n'
            '  subgrader=&lt;class &#39;xmodule.graders.AssignmentFormatGrader&#39;&gt;,'
            ' type=Midterm Exam, category=Midterm Exam, weight=0.3\n'
            '  subgrader=&lt;class &#39;xmodule.graders.AssignmentFormatGrader&#39;&gt;,'
            ' type=Final Exam, category=Final Exam, weight=0.4\n'
            f'{hbar}\n'
            f'Listing grading context for course {self.course.id}\n'
            'graded sections:\n'
            '[]\n'
            'all graded blocks:\n'
            'length=0\n'
            '</pre>'
        )
        self.assertEqual(response.content.decode(), expected_html)  # noqa: PT009

    def test_get_grading_config_requires_authentication(self):
        """Test that endpoint requires authentication"""
        self.client.force_authenticate(user=None)

        url = reverse('instructor_api_v2:grading_config', kwargs={
            'course_id': str(self.course.id),
        })
        response = self.client.get(url)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])  # noqa: PT009


class GradingEndpointTestBase(ModuleStoreTestCase):
    """
    Base test class for grading endpoints with real course structures,
    real permissions, and real StudentModule records.
    """

    PROBLEM_XML = dedent("""\
        <problem>
          <optionresponse>
            <optioninput options="('Option 1','Option 2')" correct="Option 1" />
          </optionresponse>
        </problem>
    """)

    def setUp(self):
        super().setUp()
        self.client = APIClient()
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
            display_name='Test Problem',
            data=self.PROBLEM_XML,
        )

        # Real instructor with real course permissions
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)

        # Real enrolled student with real module state
        self.student = UserFactory(username='test_student', email='student@example.com')
        CourseEnrollment.enroll(self.student, self.course.id)
        self.student_module = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem.location,
            state=json.dumps({'attempts': 10}),
        )


class ResetAttemptsViewTestCase(GradingEndpointTestBase):
    """
    Tests for POST /api/instructor/v2/courses/{course_key}/{problem}/grading/attempts/reset
    """

    def _get_url(self, problem=None):
        return reverse('instructor_api_v2:reset_attempts', kwargs={
            'course_id': str(self.course.id),
            'problem': problem or str(self.problem.location),
        })

    def test_reset_single_learner(self):
        """Single learner reset zeroes attempt count and returns 200."""
        response = self.client.post(self._get_url() + '?learner=test_student')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['learner'] == 'test_student'
        assert data['message'] == 'Attempts reset successfully'

        # Verify the actual StudentModule was modified
        self.student_module.refresh_from_db()
        assert json.loads(self.student_module.state)['attempts'] == 0

    @patch('lms.djangoapps.instructor_task.tasks.reset_problem_attempts.apply_async')
    def test_reset_all_learners(self, mock_apply):
        """Bulk reset queues a background task and returns 202."""
        response = self.client.post(self._get_url())
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert 'task_id' in data
        assert 'status_url' in data
        assert data['scope']['learners'] == 'all'
        mock_apply.assert_called_once()


class DeleteStateViewTestCase(GradingEndpointTestBase):
    """
    Tests for DELETE /api/instructor/v2/courses/{course_key}/{problem}/grading/state
    """

    def _get_url(self, problem=None):
        return reverse('instructor_api_v2:delete_state', kwargs={
            'course_id': str(self.course.id),
            'problem': problem or str(self.problem.location),
        })

    @patch('lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send')
    def test_delete_state(self, _mock_signal):  # noqa: PT019
        """Delete state removes the StudentModule record and returns 200."""
        response = self.client.delete(self._get_url() + '?learner=test_student')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['learner'] == 'test_student'
        assert data['message'] == 'State deleted successfully'

        # Verify the StudentModule was actually deleted
        assert not StudentModule.objects.filter(pk=self.student_module.pk).exists()

    def test_delete_state_requires_learner_param(self):
        """DELETE without learner query param returns 400."""
        response = self.client.delete(self._get_url())
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send')
    def test_delete_state_learner_in_body(self, _mock_signal):  # noqa: PT019
        """DELETE with learner in request body (form data) also works."""
        response = self.client.delete(self._get_url(), data={'learner': 'test_student'})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['success'] is True


class RescoreViewTestCase(GradingEndpointTestBase):
    """
    Tests for POST /api/instructor/v2/courses/{course_key}/{problem}/grading/scores/rescore
    """

    def _get_url(self, problem=None):
        return reverse('instructor_api_v2:rescore', kwargs={
            'course_id': str(self.course.id),
            'problem': problem or str(self.problem.location),
        })

    @patch('lms.djangoapps.instructor_task.tasks.rescore_problem.apply_async')
    def test_rescore_single_learner(self, mock_apply):
        """Single learner rescore queues a task and returns 202."""
        response = self.client.post(self._get_url() + '?learner=test_student')
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert 'task_id' in data
        assert data['scope']['learners'] == 'test_student'
        mock_apply.assert_called_once()

    @patch('lms.djangoapps.instructor_task.api.submit_rescore_problem_for_student')
    def test_rescore_only_if_higher(self, mock_submit):
        """Rescore with only_if_higher=true passes the flag through."""
        mock_task = MagicMock()
        mock_task.task_id = str(uuid4())
        mock_submit.return_value = mock_task

        response = self.client.post(self._get_url() + '?learner=test_student&only_if_higher=true')
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert mock_submit.call_args[0][3] is True

    @patch('lms.djangoapps.instructor_task.tasks.rescore_problem.apply_async')
    def test_rescore_all_learners(self, mock_apply):
        """Bulk rescore queues a task and returns 202."""
        response = self.client.post(self._get_url())
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data['scope']['learners'] == 'all'
        mock_apply.assert_called_once()


class ScoreOverrideViewTestCase(GradingEndpointTestBase):
    """
    Tests for PUT /api/instructor/v2/courses/{course_key}/{problem}/grading/scores
    """

    def _get_url(self, problem=None):
        return reverse('instructor_api_v2:score_override', kwargs={
            'course_id': str(self.course.id),
            'problem': problem or str(self.problem.location),
        })

    @patch('lms.djangoapps.instructor_task.tasks.override_problem_score.apply_async')
    def test_override_score(self, mock_apply):
        """Score override queues a task and returns 202."""
        response = self.client.put(
            self._get_url() + '?learner=test_student',
            data={'score': 0.5},
            format='json',
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert 'task_id' in data
        assert data['scope']['learners'] == 'test_student'
        mock_apply.assert_called_once()

        task = InstructorTask.objects.get(task_id=data['task_id'])
        assert json.loads(task.task_input)['score'] == 0.5

    @patch('lms.djangoapps.instructor_task.tasks.override_problem_score.apply_async')
    def test_override_score_with_new_score_field(self, mock_apply):
        """Score override also accepts 'new_score' field name (frontend compat)."""
        response = self.client.put(
            self._get_url(),
            data={'new_score': 0.5, 'learner': 'test_student'},
            format='json',
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_apply.assert_called_once()

        task = InstructorTask.objects.get(task_id=response.json()['task_id'])
        assert json.loads(task.task_input)['score'] == 0.5

    @patch('lms.djangoapps.instructor_task.tasks.override_problem_score.apply_async')
    def test_override_score_zero(self, mock_apply):
        """Score of 0 via 'score' field is a valid override."""
        response = self.client.put(
            self._get_url() + '?learner=test_student',
            data={'score': 0},
            format='json',
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_apply.assert_called_once()

        task = InstructorTask.objects.get(task_id=response.json()['task_id'])
        assert json.loads(task.task_input)['score'] == 0

    @patch('lms.djangoapps.instructor_task.tasks.override_problem_score.apply_async')
    def test_override_new_score_zero(self, mock_apply):
        """Score of 0 via 'new_score' field is a valid override."""
        response = self.client.put(
            self._get_url() + '?learner=test_student',
            data={'new_score': 0},
            format='json',
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_apply.assert_called_once()

        task = InstructorTask.objects.get(task_id=response.json()['task_id'])
        assert json.loads(task.task_input)['score'] == 0

    def test_override_requires_learner_param(self):
        """PUT without learner query param returns 400."""
        response = self.client.put(
            self._get_url(),
            data={'score': 8.5},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_override_requires_score_in_body(self):
        """PUT without score in body returns 400."""
        response = self.client.put(
            self._get_url() + '?learner=test_student',
            data={},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_override_rejects_negative_score(self):
        """PUT with negative score returns 400."""
        response = self.client.put(
            self._get_url() + '?learner=test_student',
            data={'score': -1},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
