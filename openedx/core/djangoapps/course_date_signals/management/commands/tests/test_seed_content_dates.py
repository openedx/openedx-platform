"""
Tests for the seed_content_dates management command.
"""

# pylint: disable=missing-function-docstring

from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.courses import _Assignment
from openedx.core.djangoapps.course_date_signals.management.commands.seed_content_dates import (
    Command,
)

_PATCH = (
    'openedx.core.djangoapps.course_date_signals.management.commands.'
    'seed_content_dates'
)

COURSE_ID = 'course-v1:TestOrg+TestCourse+TestRun'


def _make_assignment(block_key, title='Assignment', due=None):
    return _Assignment(
        block_key=block_key,
        title=title,
        url='',
        date=due or datetime(2024, 6, 1, tzinfo=timezone.utc),
        contains_gated_content=False,
        complete=False,
        past_due=False,
        assignment_type='Homework',
        extra_info=None,
        first_component_block_id=None,
    )


@pytest.mark.django_db
class TestSeedContentDatesCommand(TestCase):
    """Tests for the seed_content_dates management command."""

    def setUp(self):  # pylint: disable=invalid-name
        super().setUp()
        self.staff_user = UserFactory.create(username='staff_user', is_staff=True, is_active=True)
        self.command = Command()
        self.stdout = StringIO()

    def _call(self, *extra_args, **extra_kwargs):
        return call_command(
            'seed_content_dates',
            '--username', self.staff_user.username,
            *extra_args,
            stdout=self.stdout,
            **extra_kwargs,
        )

    def test_missing_username_raises(self):
        with pytest.raises((CommandError, SystemExit)):
            call_command('seed_content_dates', '--course-id', COURSE_ID)

    def test_non_staff_username_raises(self):
        regular_user = UserFactory.create(username='regular', is_staff=False, is_active=True)
        with pytest.raises(CommandError, match='No active staff user found'):
            call_command('seed_content_dates', '--username', regular_user.username,
                         '--course-id', COURSE_ID)

    def test_inactive_staff_username_raises(self):
        inactive = UserFactory.create(username='inactive_staff', is_staff=True, is_active=False)
        with pytest.raises(CommandError, match='No active staff user found'):
            call_command('seed_content_dates', '--username', inactive.username,
                         '--course-id', COURSE_ID)

    def test_invalid_course_id_raises(self):
        with pytest.raises(CommandError, match='Invalid course ID format'):
            self._call('--course-id', 'not-a-valid-course-key')

    @patch(_PATCH + '.CourseOverview')
    def test_course_not_found_raises(self, mock_overview):
        mock_overview.objects.filter.return_value.exists.return_value = False
        with pytest.raises(CommandError, match='Course not found'):
            self._call('--course-id', COURSE_ID)

    @patch(_PATCH + '.CourseOverview')
    def test_no_courses_for_org_raises(self, mock_overview):
        mock_overview.objects.all.return_value.filter.return_value.__iter__ = lambda s: iter([])
        mock_overview.objects.all.return_value.filter.return_value.__bool__ = lambda s: False
        # Empty list returned for the org
        mock_overview.objects.all.return_value.filter.return_value = []
        with pytest.raises(CommandError, match="No courses found for org 'UnknownOrg'"):
            self._call('--org', 'UnknownOrg')

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_dry_run_makes_no_db_writes(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_key = MagicMock()
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(block_key)]
        mock_existing.return_value = set()

        self._call('--course-id', COURSE_ID, '--dry-run')

        mock_upsert.assert_not_called()
        output = self.stdout.getvalue()
        assert 'DRY RUN' in output
        assert 'Would process 1 assignments' in output

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_creates_new_content_dates(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_key = MagicMock()
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(block_key, title='HW1')]
        mock_existing.return_value = set()

        self._call('--course-id', COURSE_ID)

        mock_upsert.assert_called_once()
        args = mock_upsert.call_args[0]
        assert len(args[1]) == 1
        output = self.stdout.getvalue()
        assert '1 created' in output

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_skips_existing_without_force_update(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_key = MagicMock()
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(block_key)]
        mock_existing.return_value = {block_key}  # already exists

        self._call('--course-id', COURSE_ID)

        mock_upsert.assert_not_called()
        output = self.stdout.getvalue()
        assert '1 skipped' in output

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_force_update_overwrites_existing(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_key = MagicMock()
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(block_key)]
        mock_existing.return_value = {block_key}  # already exists

        self._call('--course-id', COURSE_ID, '--force-update')

        mock_upsert.assert_called_once()
        output = self.stdout.getvalue()
        assert '1 updated' in output

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_whole_batch_passed_to_upsert(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_keys = [MagicMock() for _ in range(3)]
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [
            _make_assignment(k, title=f'HW{i}') for i, k in enumerate(block_keys)
        ]
        mock_existing.return_value = set()

        self._call('--course-id', COURSE_ID, '--batch-size', '10')

        # One call with all 3 assignments, not 3 individual calls
        assert mock_upsert.call_count == 1
        passed_assignments = mock_upsert.call_args[0][1]
        assert len(passed_assignments) == 3

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_multiple_batches_each_called_once(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_keys = [MagicMock() for _ in range(5)]
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(k) for k in block_keys]
        mock_existing.return_value = set()

        self._call('--course-id', COURSE_ID, '--batch-size', '2')

        # 5 assignments / batch_size 2 = 3 batches (2, 2, 1)
        assert mock_upsert.call_count == 3

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_batch_failure_continues_processing(
        self, mock_overview, mock_store, mock_assignments, mock_existing, mock_upsert
    ):
        block_keys = [MagicMock() for _ in range(4)]
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = [_make_assignment(k) for k in block_keys]
        mock_existing.return_value = set()

        # First batch fails, second should still be attempted
        mock_upsert.side_effect = [Exception("DB error"), None]

        self._call('--course-id', COURSE_ID, '--batch-size', '2')

        assert mock_upsert.call_count == 2

    @patch(_PATCH + '.update_or_create_assignments_due_dates')
    @patch(_PATCH + '.get_existing_due_locations')
    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_modulestore_called_once_for_multiple_courses(
        self, mock_overview, mock_store_fn, mock_assignments, mock_existing, mock_upsert
    ):
        course_overviews = [MagicMock(id=f'course-v1:Org+Course{i}+Run') for i in range(3)]
        mock_overview.objects.all.return_value.filter.return_value = course_overviews
        mock_store_fn.return_value.get_course.return_value = MagicMock()
        mock_assignments.return_value = []
        mock_existing.return_value = set()

        self._call('--org', 'Org')

        mock_store_fn.assert_called_once()

    @patch(_PATCH + '.get_course_assignments')
    @patch(_PATCH + '.modulestore')
    @patch(_PATCH + '.CourseOverview')
    def test_course_not_in_modulestore_returns_zeros(
        self, mock_overview, mock_store, mock_assignments
    ):
        mock_overview.objects.filter.return_value.exists.return_value = True
        mock_store.return_value.get_course.return_value = None

        self._call('--course-id', COURSE_ID)

        mock_assignments.assert_not_called()
        output = self.stdout.getvalue()
        assert '0 assignments processed' in output
