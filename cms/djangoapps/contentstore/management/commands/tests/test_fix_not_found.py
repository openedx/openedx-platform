"""
Tests for the fix_not_found management command
"""


from django.core.management import CommandError, call_command

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestFixNotFound(ModuleStoreTestCase):
    """
    Tests for the fix_not_found management command
    """
    def test_no_args(self):
        """
        Test fix_not_found command with no arguments
        """
        msg = "Error: the following arguments are required: course_id"

        with self.assertRaisesRegex(CommandError, msg):
            call_command('fix_not_found')

    def test_fix_not_found(self):
        course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        BlockFactory.create(category='chapter', parent_location=course.location)

        # get course again in order to update its children list
        course = self.store.get_course(course.id)

        # create a dangling usage key that we'll add to the course's children list
        dangling_pointer = course.id.make_usage_key('chapter', 'DanglingPointer')

        course.children.append(dangling_pointer)
        self.store.update_item(course, self.user.id)

        # the course block should now point to two children, one of which
        # doesn't actually exist
        self.assertEqual(len(course.children), 2)
        self.assertIn(dangling_pointer, course.children)

        call_command("fix_not_found", str(course.id))

        # make sure the dangling pointer was removed from
        # the course block's children
        course = self.store.get_course(course.id)
        self.assertEqual(len(course.children), 1)
        self.assertNotIn(dangling_pointer, course.children)
