"""
Tests for exporting courseware to the desired path
"""


import shutil
import unittest
from tempfile import mkdtemp

from django.core.management import CommandError, call_command

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestArgParsingCourseExport(unittest.TestCase):
    """
    Tests for parsing arguments for the `export` management command
    """
    def test_no_args(self):
        """
        Test export command with no arguments
        """
        errstring = "Error: the following arguments are required: course_id, output_path"
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('export')


class TestCourseExport(ModuleStoreTestCase):
    """
    Test exporting a course
    """
    def setUp(self):
        super().setUp()

        # Temp directories (temp_dir_1: relative path, temp_dir_2: absolute path)
        self.temp_dir_1 = mkdtemp()
        self.temp_dir_2 = mkdtemp(dir="")

        # Clean temp directories
        self.addCleanup(shutil.rmtree, self.temp_dir_1)
        self.addCleanup(shutil.rmtree, self.temp_dir_2)

    def test_export_course_with_directory_name(self):
        """
        Create a new course try exporting in a path specified
        """
        course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        course_id = str(course.id)
        self.assertTrue(  # noqa: PT009
            modulestore().has_course(course.id),
            f"Could not find course in {ModuleStoreEnum.Type.split}"
        )
        # Test `export` management command with invalid course_id
        errstring = "Invalid course_key: 'InvalidCourseID'."
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('export', "InvalidCourseID", self.temp_dir_1)

        # Test `export` management command with correct course_id
        for output_dir in [self.temp_dir_1, self.temp_dir_2]:
            call_command('export', course_id, output_dir)

    def test_course_key_not_found(self):
        """
        Test export command with a valid course key that doesn't exist
        """
        errstring = "Course with x/y/z key not found."
        with self.assertRaisesRegex(CommandError, errstring):  # noqa: PT027
            call_command('export', "x/y/z", self.temp_dir_1)
