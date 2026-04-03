"""
Tests for sync courses management command
"""
from unittest import mock

from django.core.management import call_command
from opaque_keys.edx.keys import CourseKey
from testfixtures import LogCapture

from cms.djangoapps.contentstore.views.course import create_new_course_in_store
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)

COMMAND_MODULE = 'cms.djangoapps.contentstore.management.commands.sync_courses'


@mock.patch(COMMAND_MODULE + '.get_course_runs')
class TestSyncCoursesCommand(ModuleStoreTestCase):
    """ Test sync_courses command """

    def setUp(self):
        super().setUp()

        self.user = UserFactory(username='test', email='test@example.com')
        self.catalog_course_runs = [
            CourseRunFactory(),
            CourseRunFactory(),
        ]

    def _validate_courses(self):
        for run in self.catalog_course_runs:
            course_key = CourseKey.from_string(run.get('key'))  # lint-amnesty, pylint: disable=no-member
            self.assertTrue(modulestore().has_course(course_key))
            CourseOverview.objects.get(id=run.get('key'))  # lint-amnesty, pylint: disable=no-member

    def test_courses_sync(self, mock_catalog_course_runs):
        mock_catalog_course_runs.return_value = self.catalog_course_runs

        call_command('sync_courses', self.user.email)

        self._validate_courses()

    def test_duplicate_courses_skipped(self, mock_catalog_course_runs):
        mock_catalog_course_runs.return_value = self.catalog_course_runs
        initial_display_name = "Test duplicated course"
        course_run = self.catalog_course_runs[0]
        course_key = CourseKey.from_string(course_run.get('key'))

        create_new_course_in_store(
            ModuleStoreEnum.Type.split,
            self.user,
            course_key.org,
            course_key.course,
            course_key.run,
            {
                "display_name": initial_display_name
            }
        )

        with LogCapture() as capture:
            call_command('sync_courses', self.user.email)
            expected_message = "Course already exists for {}, {}, {}. Skipping".format(
                course_key.org,
                course_key.course,
                course_key.run,
            )
            capture.check_present(
                (COMMAND_MODULE, 'WARNING', expected_message)
            )

        self._validate_courses()

        course = modulestore().get_course(course_key)
        self.assertEqual(course.display_name, initial_display_name)
