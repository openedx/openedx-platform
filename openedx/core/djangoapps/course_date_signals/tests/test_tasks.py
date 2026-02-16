from unittest.mock import patch
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey, UsageKey

from edx_when.api import Assignment, update_or_create_assignments_due_dates
from edx_when.models import ContentDate, DatePolicy

from openedx.core.djangoapps.course_date_signals.tasks import update_assignment_dates_for_course

User = get_user_model()


class TestUpdateAssignmentDatesForCourse(TestCase):

    def setUp(self):
        self.course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        self.course_key_str = str(self.course_key)
        self.staff_user = User.objects.create_user(
            username='staff_user',
            email='staff@example.com',
            is_staff=True
        )
        self.block_key = UsageKey.from_string(
            'block-v1:edX+DemoX+Demo_Course+type@sequential+block@test1'
        )
        self.due_date = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    def _assignment(self, title='Test Assignment', date=None, block_key=None, assignment_type='Homework',
                    subsection_name=''):
        """Build an Assignment DTO as accepted by edx_when.api.update_or_create_assignments_due_dates."""
        return Assignment(
            title=title,
            date=date or self.due_date,
            block_key=block_key or self.block_key,
            assignment_type=assignment_type,
            subsection_name=subsection_name,
        )

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_update_assignment_dates_new_records(self, mock_get_assignments):
        """
        Test inserting new records when missing.
        """
        mock_get_assignments.return_value = [self._assignment()]

        update_assignment_dates_for_course(self.course_key_str)

        content_date = ContentDate.objects.get(
            course_id=self.course_key,
            location=self.block_key
        )
        self.assertEqual(content_date.assignment_title, 'Test Assignment')
        self.assertEqual(content_date.block_type, 'Homework')
        self.assertEqual(content_date.policy.abs_date, self.due_date)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_update_assignment_dates_existing_records(self, mock_get_assignments):
        """
        Test updating existing records when values differ.
        """
        existing_policy = DatePolicy.objects.create(
            abs_date=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        ContentDate.objects.create(
            course_id=self.course_key,
            location=self.block_key,
            field='due',
            block_type='Homework',
            policy=existing_policy,
            assignment_title='Old Title',
            course_name=self.course_key.course,
            subsection_name='Old Title'
        )

        mock_get_assignments.return_value = [
            self._assignment(title='Updated Assignment', subsection_name='Updated Subsection')
        ]

        update_assignment_dates_for_course(self.course_key_str)

        content_date = ContentDate.objects.get(
            course_id=self.course_key,
            location=self.block_key
        )
        self.assertEqual(content_date.assignment_title, 'Updated Assignment')
        self.assertEqual(content_date.policy.abs_date, self.due_date)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_missing_staff_user(self, mock_get_assignments):
        """
        Test that task raises when no staff user exists.
        """
        User.objects.filter(is_staff=True).delete()

        with self.assertRaises(RuntimeError) as ctx:
            update_assignment_dates_for_course(self.course_key_str)

        self.assertIn("No staff user found", str(ctx.exception))
        mock_get_assignments.assert_not_called()

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_assignment_with_null_date(self, mock_get_assignments):
        """
        Test handling assignments with null dates.
        """
        mock_get_assignments.return_value = [
            self._assignment(title='No Due Date Assignment', date=None)
        ]

        update_assignment_dates_for_course(self.course_key_str)

        content_date_exists = ContentDate.objects.filter(
            course_id=self.course_key,
            location=self.block_key
        ).exists()
        self.assertFalse(content_date_exists)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_assignment_with_missing_metadata(self, mock_get_assignments):
        """
        Test handling assignments with missing metadata (no date or title -> skipped by API).
        """
        mock_get_assignments.return_value = [
            self._assignment(title='', date=None, assignment_type='')
        ]

        update_assignment_dates_for_course(self.course_key_str)

        content_date_exists = ContentDate.objects.filter(
            course_id=self.course_key,
            location=self.block_key
        ).exists()
        self.assertFalse(content_date_exists)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_multiple_assignments(self, mock_get_assignments):
        """
        Test processing multiple assignments.
        """
        block_key2 = UsageKey.from_string(
            'block-v1:edX+DemoX+Demo_Course+type@sequential+block@test2'
        )
        mock_get_assignments.return_value = [
            self._assignment(title='Assignment 1', assignment_type='Gradeable'),
            self._assignment(
                title='Assignment 2',
                date=datetime(2025, 1, 15, tzinfo=timezone.utc),
                block_key=block_key2,
                assignment_type='Homework',
            ),
        ]

        update_assignment_dates_for_course(self.course_key_str)

        self.assertEqual(ContentDate.objects.count(), 2)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_invalid_course_key(self, mock_get_assignments):
        """
        Test handling invalid course key.
        """
        with self.assertRaises(Exception):
            update_assignment_dates_for_course('invalid-course-key')

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_get_course_assignments_exception(self, mock_get_assignments):
        """
        Test handling exception from get_course_assignments.
        """
        mock_get_assignments.side_effect = Exception('API Error')

        with self.assertRaises(Exception):
            update_assignment_dates_for_course(self.course_key_str)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    def test_empty_assignments_list(self, mock_get_assignments):
        """
        Test handling empty assignments list.
        """
        mock_get_assignments.return_value = []

        update_assignment_dates_for_course(self.course_key_str)

        self.assertEqual(ContentDate.objects.count(), 0)

    @patch('openedx.core.djangoapps.course_date_signals.tasks.get_course_assignments')
    @patch('edx_when.models.DatePolicy.objects.get_or_create')
    def test_date_policy_creation_exception(self, mock_policy_create, mock_get_assignments):
        """
        Test handling exception during DatePolicy creation.
        """
        mock_get_assignments.return_value = [
            self._assignment(assignment_type='problem')
        ]
        mock_policy_create.side_effect = Exception('Database Error')

        with self.assertRaises(Exception):
            update_assignment_dates_for_course(self.course_key_str)
