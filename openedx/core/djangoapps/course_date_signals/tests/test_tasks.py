"""
Tests for the ``update_assignment_dates_for_course`` Celery task.

The task reads the published course from the modulestore and writes its graded
subsections into edx-when. Tests build real courses so the block tree, the published
branch and the dates stored by ``extract_dates`` are all exercised.
"""
from datetime import UTC, datetime

import pytest
from edx_when.api import set_dates_for_course
from edx_when.models import ContentDate
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.course_date_signals.handlers import extract_dates_from_course
from openedx.core.djangoapps.course_date_signals.models import SelfPacedRelativeDatesConfig
from openedx.core.djangoapps.course_date_signals.tasks import update_assignment_dates_for_course
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestUpdateAssignmentDatesForCourse(ModuleStoreTestCase):
    """
    Tests for update_assignment_dates_for_course on instructor-paced and self-paced courses.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.due_date = datetime(2030, 12, 31, 23, 59, 59, tzinfo=UTC)
        self.course = CourseFactory.create()
        self.chapter = BlockFactory.create(category='chapter', parent=self.course)

    def _subsection(self, **kwargs):
        """
        Create a subsection under the test chapter.
        """
        kwargs.setdefault('display_name', 'Homework 1')
        kwargs.setdefault('graded', True)
        return BlockFactory.create(category='sequential', parent_location=self.chapter.location, **kwargs)

    def _content_date(self, location):
        """
        Return the ``due`` ContentDate stored for the given block of the test course.
        """
        return ContentDate.objects.get(course_id=self.course.id, location=location, field='due')

    def _extract_dates(self, course_key):
        """
        Store the course dates the way the ``extract_dates`` receiver does on publish.
        """
        course = self.store.get_course(course_key)
        set_dates_for_course(course_key, extract_dates_from_course(course))

    def test_creates_record_for_graded_subsection(self):
        """
        Test that a graded subsection with a due date is written with its metadata.
        """
        subsection = self._subsection(due=self.due_date)

        update_assignment_dates_for_course(str(self.course.id))

        content_date = self._content_date(subsection.location)
        assert content_date.assignment_title == 'Homework 1'
        assert content_date.subsection_name == 'Homework 1'
        assert content_date.block_type == 'sequential'
        assert content_date.policy.abs_date == self.due_date

    def test_keeps_date_policy_stored_by_extract_dates(self):
        """
        Test that metadata is added to the row stored by ``extract_dates`` without changing its date.
        """
        subsection = self._subsection(due=self.due_date)
        self._extract_dates(self.course.id)
        content_date = self._content_date(subsection.location)
        policy_id = content_date.policy_id
        assert content_date.assignment_title == ''

        update_assignment_dates_for_course(str(self.course.id))

        content_date.refresh_from_db()
        assert content_date.assignment_title == 'Homework 1'
        assert content_date.policy_id == policy_id
        assert content_date.policy.abs_date == self.due_date
        assert content_date.policy.rel_date is None
        assert ContentDate.objects.filter(location=subsection.location).count() == 1

    def test_updates_title_of_existing_record(self):
        """
        Test that renaming a subsection updates the existing row instead of adding one.
        """
        subsection = self._subsection(due=self.due_date)
        update_assignment_dates_for_course(str(self.course.id))

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            draft_subsection = self.store.get_item(subsection.location)
            draft_subsection.display_name = 'Renamed Homework'
            self.store.update_item(draft_subsection, self.user.id)
            self.store.publish(subsection.location, self.user.id)
        update_assignment_dates_for_course(str(self.course.id))

        content_date = self._content_date(subsection.location)
        assert content_date.assignment_title == 'Renamed Homework'
        assert ContentDate.objects.filter(location=subsection.location).count() == 1

    def test_skips_subsections_without_explicit_due_or_grading(self):
        """
        Test that ungraded subsections and subsections without their own due date are skipped.
        """
        self._subsection(display_name='Ungraded', graded=False, due=self.due_date)
        self._subsection(display_name='No due date')
        inherited_chapter = BlockFactory.create(category='chapter', parent=self.course, due=self.due_date)
        BlockFactory.create(category='sequential', parent=inherited_chapter, graded=True)

        update_assignment_dates_for_course(str(self.course.id))

        assert not ContentDate.objects.filter(course_id=self.course.id).exists()

    def test_multiple_subsections(self):
        """
        Test that every graded subsection with a due date is written.
        """
        second_due_date = datetime(2031, 1, 15, tzinfo=UTC)
        first = self._subsection(due=self.due_date)
        second = self._subsection(display_name='Homework 2', due=second_due_date)

        update_assignment_dates_for_course(str(self.course.id))

        assert self._content_date(first.location).policy.abs_date == self.due_date
        assert self._content_date(second.location).policy.abs_date == second_due_date
        assert self._content_date(second.location).assignment_title == 'Homework 2'

    def test_skips_self_paced_course(self):
        """
        Test that the relative (PLS) dates of a self-paced course are left unchanged.
        """
        SelfPacedRelativeDatesConfig.objects.create(enabled=True)
        course = CourseFactory.create(self_paced=True)
        chapter = BlockFactory.create(category='chapter', parent=course)
        subsection = BlockFactory.create(
            category='sequential', parent=chapter, graded=True, due=self.due_date,
        )
        vertical = BlockFactory.create(category='vertical', parent=subsection)
        BlockFactory.create(category='problem', parent=vertical, graded=True)
        self._extract_dates(course.id)
        content_date = ContentDate.objects.get(course_id=course.id, location=subsection.location)
        assert content_date.policy.rel_date is not None

        update_assignment_dates_for_course(str(course.id))

        content_date.refresh_from_db()
        assert content_date.policy.rel_date is not None
        assert content_date.policy.abs_date is None
        assert content_date.assignment_title == ''

    def test_missing_course(self):
        """
        Test that a course missing from the modulestore is a no-op.
        """
        update_assignment_dates_for_course('course-v1:edX+Missing+Course')

        assert not ContentDate.objects.filter(
            course_id=CourseKey.from_string('course-v1:edX+Missing+Course')
        ).exists()

    def test_invalid_course_key(self):
        """
        Test handling invalid course key.
        """
        with pytest.raises(InvalidKeyError):
            update_assignment_dates_for_course('invalid-course-key')
