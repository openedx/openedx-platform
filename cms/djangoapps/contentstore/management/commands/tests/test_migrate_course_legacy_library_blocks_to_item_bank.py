"""
Tests for `migrate_course_legacy_library_blocks_to_item_bank` Studio (cms) management command.
"""
from unittest import mock

import ddt
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


@ddt.ddt
class MigrateCourseLegacyLibraryBlocksToItemBankTests(ModuleStoreTestCase):
    """ Tests for the `migrate_course_legacy_library_blocks_to_item_bank` management command. """
    TASK_PATCH_LOCATION = (
        'cms.djangoapps.contentstore.management.commands.migrate_course_legacy_library_blocks_to_item_bank'
        '.migrate_course_legacy_library_blocks_to_item_bank'
    )

    def setUp(self):
        """ Setup method - create a user and courses to migrate """
        super().setUp()
        self.user = UserFactory()
        self.first_course = CourseFactory.create()
        self.second_course = CourseFactory.create()

    def _call_command(self, **options):
        """ Invoke the command, defaulting `user_id` to a valid user. """
        options.setdefault('user_id', self.user.id)
        call_command('migrate_course_legacy_library_blocks_to_item_bank', **options)

    @ddt.data(
        ({}, 'Either --course-ids or --all-courses argument should be provided.'),
        (
            {'course_ids': 'course-v1:test+course+run', 'all_courses': True},
            'Only one of --course-ids or --all-courses argument should be provided.',
        ),
    )
    @ddt.unpack
    def test_invalid_course_selector_raises_command_error(self, options, expected_message):
        """ Test that specifying neither, or both, of --course-ids/--all-courses raises a CommandError. """
        with self.assertRaisesRegex(CommandError, expected_message):  # noqa: PT027
            self._call_command(**options)

    def test_invalid_user_id_raises_command_error(self):
        """ Test that an unknown --user-id raises a CommandError. """
        invalid_user_id = self.user.id + 1000
        with self.assertRaisesRegex(CommandError, f'No user found with id: {invalid_user_id}'):  # noqa: PT027
            call_command(
                'migrate_course_legacy_library_blocks_to_item_bank',
                all_courses=True,
                user_id=invalid_user_id,
            )

    @ddt.data(
        'invalid_key',
        'course-v1:test+nonexistent+run',
    )
    def test_unparsable_or_nonexistent_course_id_is_skipped(self, bad_course_id):
        """
        Test that an unparsable or nonexistent course key passed via --course-ids is skipped,
        while other, valid, course keys are still dispatched.
        """
        course_ids = f'{bad_course_id},{self.first_course.id}'
        with mock.patch(self.TASK_PATCH_LOCATION) as patched_task:
            self._call_command(course_ids=course_ids)

        patched_task.delay.assert_called_once_with(self.user.id, str(self.first_course.id), False)

    def test_course_ids_dispatches_task_for_each_course(self):
        """ Test that the task is dispatched once per course key passed via --course-ids. """
        course_ids = f'{self.first_course.id},{self.second_course.id}'
        with mock.patch(self.TASK_PATCH_LOCATION) as patched_task:
            self._call_command(course_ids=course_ids)

        expected_calls = [
            mock.call(self.user.id, str(self.first_course.id), False),
            mock.call(self.user.id, str(self.second_course.id), False),
        ]
        self.assertEqual(patched_task.delay.mock_calls, expected_calls)  # noqa: PT009

    def test_all_courses_dispatches_task_for_every_course(self):
        """ Test that --all-courses dispatches the task for every course known to CourseOverview. """
        CourseOverviewFactory(id=self.first_course.id)
        CourseOverviewFactory(id=self.second_course.id)

        with mock.patch(self.TASK_PATCH_LOCATION) as patched_task:
            self._call_command(all_courses=True)

        expected_calls = [
            mock.call(self.user.id, str(self.first_course.id), False),
            mock.call(self.user.id, str(self.second_course.id), False),
        ]
        self.assertCountEqual(patched_task.delay.mock_calls, expected_calls)  # noqa: PT009

    @ddt.data(True, False)
    def test_persist_publish_state_passed_to_task(self, persist_publish_state):
        """ Test that --persist-publish-state is forwarded to the task, defaulting to False. """
        with mock.patch(self.TASK_PATCH_LOCATION) as patched_task:
            self._call_command(
                course_ids=str(self.first_course.id),
                persist_publish_state=persist_publish_state,
            )

        patched_task.delay.assert_called_once_with(self.user.id, str(self.first_course.id), persist_publish_state)
