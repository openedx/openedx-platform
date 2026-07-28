"""
Unittests for creating a course in an chosen modulestore
"""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestArgParsing(TestCase):
    """
    Tests for parsing arguments for the `create_course` management command
    """
    def setUp(self):  # pylint: disable=useless-super-delegation
        super().setUp()

    def test_no_args(self):
        errstring = "Error: the following arguments are required: modulestore, user, org, number, run"
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('create_course')

    def test_invalid_store(self):
        with self.assertRaises(CommandError):  # noqa: PT027
            call_command('create_course', "foo", "user@foo.org", "org", "course", "run")

    def test_nonexistent_user_id(self):
        errstring = "No user 99 found"
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('create_course', "split", "99", "org", "course", "run")

    def test_nonexistent_user_email(self):
        errstring = "No user fake@example.com found"
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('create_course', "mongo", "fake@example.com", "org", "course", "run")


class TestCreateCourse(ModuleStoreTestCase):
    """
    Unit tests for creating a course in either old mongo or split mongo via command line
    """

    def test_all_stores_user_email(self):
        call_command(
            "create_course",
            ModuleStoreEnum.Type.split,
            str(self.user.email),
            "org", "course", "run", "dummy-course-name"
        )
        new_key = modulestore().make_course_key("org", "course", "run")
        self.assertTrue(  # noqa: PT009
            modulestore().has_course(new_key),
            f"Could not find course in {ModuleStoreEnum.Type.split}"
        )
        # pylint: disable=protected-access
        self.assertEqual(  # noqa: PT009
            ModuleStoreEnum.Type.split,
            modulestore()._get_modulestore_for_courselike(new_key).get_modulestore_type()
        )

    def test_duplicate_course(self):
        """
        Test that creating a duplicate course exception is properly handled
        """
        call_command(
            "create_course",
            "split",
            str(self.user.email),
            "org", "course", "run", "dummy-course-name"
        )

        # create the course again
        out = StringIO()
        call_command(
            "create_course",
            "split",
            str(self.user.email),
            "org", "course", "run", "dummy-course-name",
            stderr=out
        )
        expected = "Course already exists"
        self.assertIn(out.getvalue().strip(), expected)  # noqa: PT009

    def test_get_course_with_different_case(self):
        """
        Tests that course can not be accessed with different case.

        Scenario:
            Create a course with lower case keys inside `bulk_operations` with `ignore_case=True`.
            Verify that course is created.
            Verify that get course from store using same course id but different case is not accessible.
        """
        org = 'org1'
        number = 'course1'
        run = 'run1'
        lowercase_course_id = self.store.make_course_key(org, number, run)
        with self.store.bulk_operations(lowercase_course_id, ignore_case=True):
            # Create course with lowercase key & Verify that store returns course.
            self.store.create_course(
                lowercase_course_id.org,
                lowercase_course_id.course,
                lowercase_course_id.run,
                self.user.id
            )
            course = self.store.get_course(lowercase_course_id)
            self.assertIsNotNone(course, 'Course not found using lowercase course key.')  # noqa: PT009
            self.assertEqual(str(course.id), str(lowercase_course_id))  # noqa: PT009

            # Verify store does not return course with different case.
            uppercase_course_id = self.store.make_course_key(org.upper(), number.upper(), run.upper())
            course = self.store.get_course(uppercase_course_id)
            self.assertIsNone(course, 'Course should not be accessed with uppercase course id.')  # noqa: PT009
