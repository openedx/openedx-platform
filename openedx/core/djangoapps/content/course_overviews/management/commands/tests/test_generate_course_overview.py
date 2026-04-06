"""
Tests that the generate_course_overview management command actually generates course overviews.
"""
from unittest.mock import patch

import pytest
from django.core.management.base import CommandError

from openedx.core.djangoapps.content.course_overviews.management.commands import generate_course_overview
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestGenerateCourseOverview(ModuleStoreTestCase):
    """
    Tests course overview management command.
    """

    def setUp(self):
        """
        Create courses in modulestore.
        """
        super().setUp()
        self.course_key_1 = CourseFactory.create().id
        self.course_key_2 = CourseFactory.create().id
        self.command = generate_course_overview.Command()

    def _assert_courses_not_in_overview(self, *courses):
        """
        Assert that courses doesn't exist in the course overviews.
        """
        course_keys = CourseOverview.get_all_course_keys()
        for expected_course_key in courses:
            assert expected_course_key not in course_keys

    def _assert_courses_in_overview(self, *courses):
        """
        Assert courses exists in course overviews.
        """
        course_keys = CourseOverview.get_all_course_keys()
        for expected_course_key in courses:
            assert expected_course_key in course_keys

    def test_generate_all(self):
        """
        Test that all courses in the modulestore are loaded into course overviews.
        """
        # ensure that the newly created courses aren't in course overviews
        self._assert_courses_not_in_overview(self.course_key_1, self.course_key_2)
        self.command.handle(all_courses=True)

        # CourseOverview will be populated with all courses in the modulestore
        self._assert_courses_in_overview(self.course_key_1, self.course_key_2)

    def test_generate_one(self):
        """
        Test that a specified course is loaded into course overviews.
        """
        self._assert_courses_not_in_overview(self.course_key_1, self.course_key_2)
        self.command.handle(str(self.course_key_1), all_courses=False)
        self._assert_courses_in_overview(self.course_key_1)
        self._assert_courses_not_in_overview(self.course_key_2)

    def test_generate_force_update(self):
        self.command.handle(all_courses=True)

        # update each course
        updated_course_name = 'test_generate_course_overview.course_edit'
        for course_key in (self.course_key_1, self.course_key_2):
            course = self.store.get_course(course_key)
            course.display_name = updated_course_name
            self.store.update_item(course, self.user.id)

        # force_update course_key_1, but not course_key_2
        self.command.handle(str(self.course_key_1), all_courses=False, force_update=True)
        self.command.handle(str(self.course_key_2), all_courses=False, force_update=False)

        assert CourseOverview.get_from_id(self.course_key_1).display_name == updated_course_name
        assert CourseOverview.get_from_id(self.course_key_2).display_name != updated_course_name

    def test_invalid_key(self):
        """
        Test that CommandError is raised for invalid key.
        """
        with pytest.raises(CommandError):
            self.command.handle('not/found', all_courses=False)

    @patch('openedx.core.djangoapps.content.course_overviews.models.log')
    def test_not_found_key(self, mock_log):
        """
        Test keys not found are logged.
        """
        self.command.handle('fake/course/id', all_courses=False)
        assert mock_log.exception.called

    def test_no_params(self):
        """
        Test exception raised when no parameters are specified.
        """
        with pytest.raises(CommandError):
            self.command.handle(all_courses=False)

    @patch('openedx.core.djangoapps.content.course_overviews.tasks.async_course_overview_update')
    def test_routing_key(self, mock_async_task):
        self.command.handle(all_courses=True, force_update=True, routing_key='my-routing-key', chunk_size=10000)

        called_kwargs = mock_async_task.apply_async.call_args_list[0][1]
        assert sorted([str(self.course_key_1), str(self.course_key_2)]) ==\
               sorted(called_kwargs.pop('args'))
        assert {'kwargs': {'force_update': True}, 'routing_key': 'my-routing-key'} == called_kwargs
        assert 1 == mock_async_task.apply_async.call_count
