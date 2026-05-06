"""
Unit tests for instructor_task subtasks.
"""


from unittest.mock import Mock, patch
from uuid import uuid4

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.subtasks import queue_subtasks_for_query
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from lms.djangoapps.instructor_task.tests.test_base import InstructorTaskCourseTestCase


class TestSubtasks(InstructorTaskCourseTestCase):
    """Tests for subtasks."""

    def setUp(self):
        super().setUp()
        self.initialize_course()

    def _enroll_students_in_course(self, course_id, num_students):  # pylint: disable=unused-argument
        """Create and enroll some students in the course."""

        for _ in range(num_students):
            random_id = uuid4().hex[:8]
            self.create_student(username=f'student{random_id}')

    def _queue_subtasks(self, create_subtask_fcn, items_per_task, initial_count, extra_count):
        """Queue subtasks while enrolling more students into course in the middle of the process."""

        task_id = str(uuid4())
        instructor_task = InstructorTaskFactory.create(
            course_id=self.course.id,
            task_id=task_id,
            task_key='dummy_task_key',
            task_type=InstructorTaskTypes.BULK_COURSE_EMAIL,
        )

        self._enroll_students_in_course(self.course.id, initial_count)
        task_querysets = [CourseEnrollment.objects.filter(course_id=self.course.id)]

        def initialize_subtask_info(*args):  # pylint: disable=unused-argument
            """Instead of initializing subtask info enroll some more students into course."""
            self._enroll_students_in_course(self.course.id, extra_count)
            return {}

        with patch('lms.djangoapps.instructor_task.subtasks.initialize_subtask_info') as mock_initialize_subtask_info:
            mock_initialize_subtask_info.side_effect = initialize_subtask_info
            queue_subtasks_for_query(
                entry=instructor_task,
                action_name='action_name',
                create_subtask_fcn=create_subtask_fcn,
                item_querysets=task_querysets,
                item_fields=[],
                items_per_task=items_per_task,
                total_num_items=initial_count,
            )

    def test_queue_subtasks_for_query1(self):
        """Test queue_subtasks_for_query() if the last subtask only needs to accommodate < items_per_tasks items."""

        mock_create_subtask_fcn = Mock()
        self._queue_subtasks(mock_create_subtask_fcn, 3, 7, 1)

        # Check number of items for each subtask
        mock_create_subtask_fcn_args = mock_create_subtask_fcn.call_args_list
        assert len(mock_create_subtask_fcn_args[0][0][0]) == 3
        assert len(mock_create_subtask_fcn_args[1][0][0]) == 3
        assert len(mock_create_subtask_fcn_args[2][0][0]) == 2

    def test_queue_subtasks_for_query2(self):
        """Test queue_subtasks_for_query() if the last subtask needs to accommodate > items_per_task items."""

        mock_create_subtask_fcn = Mock()
        self._queue_subtasks(mock_create_subtask_fcn, 3, 8, 3)

        # Check number of items for each subtask
        mock_create_subtask_fcn_args = mock_create_subtask_fcn.call_args_list
        assert len(mock_create_subtask_fcn_args[0][0][0]) == 3
        assert len(mock_create_subtask_fcn_args[1][0][0]) == 3
        assert len(mock_create_subtask_fcn_args[2][0][0]) == 5
