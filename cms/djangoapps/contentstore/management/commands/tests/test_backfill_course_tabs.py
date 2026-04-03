"""
Tests for `backfill_course_outlines` Studio (cms) management command.
"""
from unittest import mock

import ddt
from django.core.management import call_command

from cms.djangoapps.contentstore.models import BackfillCourseTabsConfig
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class BackfillCourseTabsTest(ModuleStoreTestCase):
    """
    Test `backfill_course_tabs`
    """
    @mock.patch('cms.djangoapps.contentstore.management.commands.backfill_course_tabs.logger')
    def test_no_tabs_to_add(self, mock_logger):
        """ Calls command with a course already having all default tabs. """
        course = CourseFactory()
        tabs_before = course.tabs

        call_command('backfill_course_tabs')

        course = self.store.get_course(course.id)
        tabs_after = course.tabs
        assert tabs_before == tabs_after
        # Ensure update_item was never called since there were no changes necessary
        # Using logger as a proxy. First time is number of courses read.
        assert mock_logger.info.call_count == 1

    @mock.patch('cms.djangoapps.contentstore.management.commands.backfill_course_tabs.logger')
    def test_add_one_tab(self, mock_logger):
        """
        Calls command on a course with existing tabs, but not all default ones.
        """
        course = CourseFactory()
        course.tabs = [tab for tab in course.tabs if tab.type != 'dates']
        self.update_course(course, ModuleStoreEnum.UserID.test)
        assert len(course.tabs) == 5
        assert 'dates' not in {tab.type for tab in course.tabs}

        call_command('backfill_course_tabs')

        course = self.store.get_course(course.id)
        assert len(course.tabs) == 6
        assert 'dates' in {tab.type for tab in course.tabs}
        mock_logger.info.assert_any_call(f'Updating tabs for {course.id}.')
        mock_logger.info.assert_any_call(f'Successfully updated tabs for {course.id}.')
        assert mock_logger.info.call_count == 3

        call_command('backfill_course_tabs')
        # Ensure rerunning the command does not require another update on the course.
        # Goes up by one from the courses read log.
        assert mock_logger.info.call_count == 4

    @mock.patch('cms.djangoapps.contentstore.management.commands.backfill_course_tabs.logger')
    def test_multiple_courses_one_update(self, mock_logger):
        """
        Calls command on multiple courses, some that already have all their tabs, and some that need updates.
        """
        CourseFactory()
        CourseFactory()
        CourseFactory()
        course = CourseFactory()
        course.tabs = [tab for tab in course.tabs if tab.type == 'courseware']
        self.update_course(course, ModuleStoreEnum.UserID.test)
        assert len(course.tabs) == 1
        assert 'dates' not in {tab.type for tab in course.tabs}
        assert 'progress' not in {tab.type for tab in course.tabs}

        call_command('backfill_course_tabs')

        course = self.store.get_course(course.id)
        assert len(course.tabs) == 6
        assert 'dates' in {tab.type for tab in course.tabs}
        assert 'progress' in {tab.type for tab in course.tabs}
        mock_logger.info.assert_any_call('4 courses read from modulestore. Processing 0 to 4.')
        mock_logger.info.assert_any_call(f'Updating tabs for {course.id}.')
        mock_logger.info.assert_any_call(f'Successfully updated tabs for {course.id}.')
        assert mock_logger.info.call_count == 3

        call_command('backfill_course_tabs')
        # Ensure rerunning the command does not require another update on the course.
        # Goes up by one from the courses read log.
        assert mock_logger.info.call_count == 4

    @mock.patch('cms.djangoapps.contentstore.management.commands.backfill_course_tabs.logger')
    def test_multiple_courses_all_updated(self, mock_logger):
        """
        Calls command on multiple courses where all of them need updates.
        """
        course_1 = CourseFactory()
        course_1.tabs = [tab for tab in course_1.tabs if tab.type != 'dates']
        self.update_course(course_1, ModuleStoreEnum.UserID.test)
        course_2 = CourseFactory()
        course_2.tabs = [tab for tab in course_2.tabs if tab.type != 'progress']
        self.update_course(course_2, ModuleStoreEnum.UserID.test)
        assert len(course_1.tabs) == 5
        assert len(course_2.tabs) == 5
        assert 'dates' not in {tab.type for tab in course_1.tabs}
        assert 'progress' not in {tab.type for tab in course_2.tabs}

        call_command('backfill_course_tabs')

        course_1 = self.store.get_course(course_1.id)
        course_2 = self.store.get_course(course_2.id)
        assert len(course_1.tabs) == 6
        assert len(course_2.tabs) == 6
        assert 'dates' in {tab.type for tab in course_1.tabs}
        assert 'progress' in {tab.type for tab in course_2.tabs}
        mock_logger.info.assert_any_call('2 courses read from modulestore. Processing 0 to 2.')
        mock_logger.info.assert_any_call(f'Updating tabs for {course_1.id}.')
        mock_logger.info.assert_any_call(f'Successfully updated tabs for {course_1.id}.')
        mock_logger.info.assert_any_call(f'Updating tabs for {course_2.id}.')
        mock_logger.info.assert_any_call(f'Successfully updated tabs for {course_2.id}.')
        assert mock_logger.info.call_count == 5

        call_command('backfill_course_tabs')
        # Ensure rerunning the command does not require another update on any courses.
        # Goes up by one from the courses read log.
        assert mock_logger.info.call_count == 6

    @mock.patch('cms.djangoapps.contentstore.management.commands.backfill_course_tabs.logger')
    def test_command_logs_exception_on_error(self, mock_logger):
        """
        The command should make it through all the courses regardless of Exceptions and will log any
        encountered.

        Command is only manually run and should be monitored.
        """
        error_course = CourseFactory()
        error_course.tabs = [tab for tab in error_course.tabs if tab.type != 'dates']
        self.update_course(error_course, ModuleStoreEnum.UserID.test)
        error_course_tabs_before = error_course.tabs

        updated_course = CourseFactory()
        updated_course.tabs = [tab for tab in updated_course.tabs if tab.type != 'dates']
        self.update_course(updated_course, ModuleStoreEnum.UserID.test)
        updated_course_tabs_before = updated_course.tabs

        with mock.patch(
            'lms.djangoapps.ccx.modulestore.CCXModulestoreWrapper.update_item', side_effect=[ValueError, None]
        ):
            call_command('backfill_course_tabs')

        error_course = self.store.get_course(error_course.id)
        error_course_tabs_after = error_course.tabs
        # Course wasn't updated due to the ValueError
        assert error_course_tabs_before == error_course_tabs_after

        mock_logger.info.assert_any_call('2 courses read from modulestore. Processing 0 to 2.')
        mock_logger.info.assert_any_call(f'Successfully updated tabs for {updated_course.id}.')
        mock_logger.exception.assert_called()
        mock_logger.error.assert_called_once_with(
            f'Course {error_course.id} encountered an Exception while trying to update.'
        )

    @ddt.data(
        (1, 2, [False, True, True, False]),
        (1, 0, [False, True, True, True]),
        (-1, -1, [True, True, True, True]),
    )
    @ddt.unpack
    def test_arguments_batching(self, start, count, expected_tabs_modified):
        courses = CourseFactory.create_batch(4)
        for course in courses:
            course.tabs = [tab for tab in course.tabs if tab.type == 'courseware']
            course = self.update_course(course, ModuleStoreEnum.UserID.test)
            assert len(course.tabs) == 1

        BackfillCourseTabsConfig.objects.create(enabled=True, start_index=start, count=count)
        call_command('backfill_course_tabs')

        for i, course in enumerate(courses):
            course = self.store.get_course(course.id)
            assert len(course.tabs) == (6 if expected_tabs_modified[i] else 1), f'Wrong tabs for course index {i}'
