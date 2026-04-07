"""
Tests for the force_publish management command
"""


from unittest import mock

from django.core.management import CommandError, call_command

from cms.djangoapps.contentstore.management.commands.force_publish import Command
from cms.djangoapps.contentstore.management.commands.utils import get_course_versions
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class TestForcePublish(SharedModuleStoreTestCase):
    """
    Tests for the force_publish management command
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        cls.test_user_id = ModuleStoreEnum.UserID.test
        cls.command = Command()

    def test_no_args(self):
        """
        Test 'force_publish' command with no arguments
        """
        errstring = "Error: the following arguments are required: course_key"

        with self.assertRaisesRegex(CommandError, errstring):
            call_command('force_publish')

    def test_invalid_course_key(self):
        """
        Test 'force_publish' command with invalid course key
        """
        errstring = "Invalid course key."
        with self.assertRaisesRegex(CommandError, errstring):
            call_command('force_publish', 'TestX/TS01')

    def test_too_many_arguments(self):
        """
        Test 'force_publish' command with more than 2 arguments
        """
        errstring = "Error: unrecognized arguments: invalid-arg"
        with self.assertRaisesRegex(CommandError, errstring):
            call_command('force_publish', str(self.course.id), '--commit', 'invalid-arg')

    def test_course_key_not_found(self):
        """
        Test 'force_publish' command with non-existing course key
        """
        errstring = "Course not found."
        with self.assertRaisesRegex(CommandError, errstring):
            call_command('force_publish', 'course-v1:org+course+run')


class TestForcePublishModifications(ModuleStoreTestCase):
    """
    Tests for the force_publish management command that modify the courseware
    during the test.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.test_user_id = ModuleStoreEnum.UserID.test
        self.command = Command()

    def test_force_publish(self):
        """
        Test 'force_publish' command
        """
        # Add some changes to course
        chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        self.store.create_child(
            self.test_user_id,
            chapter.location,
            'html',
            block_id='html_component'
        )

        # verify that course has changes.
        self.assertTrue(self.store.has_changes(self.store.get_item(self.course.location)))

        # get draft and publish branch versions
        versions = get_course_versions(str(self.course.id))
        draft_version = versions['draft-branch']
        published_version = versions['published-branch']

        # verify that draft and publish point to different versions
        self.assertNotEqual(draft_version, published_version)

        with mock.patch('cms.djangoapps.contentstore.management.commands.force_publish.query_yes_no') as patched_yes_no:
            patched_yes_no.return_value = True

            # force publish course
            call_command('force_publish', str(self.course.id), '--commit')

            # verify that course has no changes
            self.assertFalse(self.store.has_changes(self.store.get_item(self.course.location)))

            # get new draft and publish branch versions
            versions = get_course_versions(str(self.course.id))
            new_draft_version = versions['draft-branch']
            new_published_version = versions['published-branch']

            # verify that the draft branch didn't change while the published branch did
            self.assertEqual(draft_version, new_draft_version)
            self.assertNotEqual(published_version, new_published_version)

            # verify that draft and publish point to same versions now
            self.assertEqual(new_draft_version, new_published_version)
