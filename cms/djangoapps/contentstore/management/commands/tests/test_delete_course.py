"""
Delete course tests.
"""


from unittest import mock

from django.core.management import CommandError, call_command

from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class DeleteCourseTests(ModuleStoreTestCase):
    """
    Test for course deleting functionality of the 'delete_course' command
    """
    YESNO_PATCH_LOCATION = 'cms.djangoapps.contentstore.management.commands.delete_course.query_yes_no'

    def test_invalid_course_key(self):
        course_run_key = 'foo/TestX/TS01/2015_Q7'
        expected_error_message = 'Invalid course_key: ' + course_run_key
        with self.assertRaisesRegex(CommandError, expected_error_message):
            call_command('delete_course', course_run_key)

    def test_course_not_found(self):
        course_run_key = 'TestX/TS01/2015_Q7'
        expected_error_message = 'Course not found: ' + course_run_key
        with self.assertRaisesRegex(CommandError, expected_error_message):
            call_command('delete_course', course_run_key)

    def test_asset_and_course_deletion(self):
        course_run = CourseFactory()
        self.assertIsNotNone(modulestore().get_course(course_run.id))

        store = contentstore()
        asset_key = course_run.id.make_asset_key('asset', 'test.txt')
        content = StaticContent(asset_key, 'test.txt', 'plain/text', b'test data')
        store.save(content)
        __, asset_count = store.get_all_content_for_course(course_run.id)
        assert asset_count == 1

        with mock.patch(self.YESNO_PATCH_LOCATION) as patched_yes_no:
            patched_yes_no.return_value = True
            call_command('delete_course', str(course_run.id))

        assert modulestore().get_course(course_run.id) is None

        __, asset_count = store.get_all_content_for_course(course_run.id)
        assert asset_count == 1

    def test_keep_instructors(self):
        course_run = CourseFactory()
        instructor = UserFactory()
        CourseInstructorRole(course_run.id).add_users(instructor)

        with mock.patch(self.YESNO_PATCH_LOCATION, return_value=True):
            call_command('delete_course', str(course_run.id), '--keep-instructors')

        assert CourseInstructorRole(course_run.id).has_user(instructor)

    def test_remove_assets(self):
        course_run = CourseFactory()
        store = contentstore()

        asset_key = course_run.id.make_asset_key('asset', 'test.txt')
        content = StaticContent(asset_key, 'test.txt', 'plain/text', b'test data')
        store.save(content)
        __, asset_count = store.get_all_content_for_course(course_run.id)
        assert asset_count == 1

        with mock.patch(self.YESNO_PATCH_LOCATION, return_value=True):
            call_command('delete_course', str(course_run.id), '--remove-assets')

        __, asset_count = store.get_all_content_for_course(course_run.id)
        assert asset_count == 0
