"""
Tests for the sync course runs management command.
"""
from unittest import mock

import ddt
from django.core.management import call_command

from openedx.core.djangoapps.catalog.management.commands.sync_course_runs import Command as sync_command
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

COMMAND_MODULE = 'openedx.core.djangoapps.catalog.management.commands.sync_course_runs'


@ddt.ddt
@mock.patch(COMMAND_MODULE + '.get_course_runs')
class TestSyncCourseRunsCommand(ModuleStoreTestCase):
    """
    Test for the sync course runs management command.
    """
    def setUp(self):
        super().setUp()
        # create mongo course
        self.course = CourseFactory.create()
        # load this course into course overview
        self.course_overview = CourseOverview.get_from_id(self.course.id)
        # create a catalog course run with the same course id.
        self.catalog_course_run = CourseRunFactory(
            key=str(self.course.id),
            marketing_url='test_marketing_url',
            eligible_for_financial_aid=False
        )

    def test_course_run_sync(self, mock_catalog_course_runs):
        """
        Verify on executing management command course overview data is updated
        with course run data from course discovery.
        """
        mock_catalog_course_runs.return_value = [self.catalog_course_run]

        call_command('sync_course_runs')
        updated_course_overview = CourseOverview.objects.get(id=self.course.id)

        # assert fields have updated
        for field in sync_command.course_run_fields:
            course_overview_field_name = field.course_overview_name
            catalog_field_name = field.catalog_name

            previous_course_overview_value = getattr(self.course_overview, course_overview_field_name)
            updated_course_overview_value = getattr(updated_course_overview, course_overview_field_name)

            # course overview value matches catalog value
            assert updated_course_overview_value == self.catalog_course_run.get(catalog_field_name)  # pylint: disable=no-member, line-too-long
            # new value doesn't match old value
            assert updated_course_overview_value != previous_course_overview_value

    @mock.patch(COMMAND_MODULE + '.log.info')
    def test_course_overview_does_not_exist(self, mock_log_info, mock_catalog_course_runs):
        """
        Verify no error in case if a course run is not found in course overview.
        """
        nonexistent_course_run = CourseRunFactory()
        mock_catalog_course_runs.return_value = [self.catalog_course_run, nonexistent_course_run]

        call_command('sync_course_runs')

        mock_log_info.assert_any_call(
            '[sync_course_runs] course overview record not found for course run: %s',
            nonexistent_course_run['key'],
        )
        updated_marketing_url = CourseOverview.objects.get(id=self.course.id).marketing_url
        assert updated_marketing_url == 'test_marketing_url'

    @mock.patch(COMMAND_MODULE + '.log.info')
    def test_starting_and_ending_logs(self, mock_log_info, mock_catalog_course_runs):
        """
        Verify logging at start and end of the command.
        """
        def _assert_logs(num_updates):
            mock_log_info.assert_any_call('[sync_course_runs] Fetching course runs from catalog service.')
            mock_log_info.assert_any_call(
                '[sync_course_runs] course runs found in catalog: %d, course runs found in course overview: %d,'
                ' course runs not found in course overview: %d, course overviews updated: %d',
                3,
                1,
                2,
                num_updates,
            )
            mock_log_info.reset_mock()

        mock_catalog_course_runs.return_value = [self.catalog_course_run, CourseRunFactory(), CourseRunFactory()]

        call_command('sync_course_runs')
        _assert_logs(num_updates=1)

        call_command('sync_course_runs')
        _assert_logs(num_updates=0)
