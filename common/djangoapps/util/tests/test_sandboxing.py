"""
Tests for sandboxing.py in util app
"""


import ddt
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator, LibraryLocator, LibraryLocatorV2

from xmodule.contentstore.django import contentstore
from xmodule.modulestore.tests.django_utils import upload_file_to_course
from xmodule.util.sandboxing import can_execute_unsafe_code, SandboxService


class SandboxingTest(TestCase):
    """
    Test sandbox whitelisting
    """
    @override_settings(COURSES_WITH_UNSAFE_CODE=['edX/full/.*', 'library:v1-edX+.*'])
    def test_sandbox_exclusion(self):
        """
        Test to make sure that a non-match returns false
        """
        assert not can_execute_unsafe_code(CourseLocator('edX', 'notful', 'empty'))
        assert not can_execute_unsafe_code(LibraryLocator('edY', 'test_bank'))

    @override_settings(COURSES_WITH_UNSAFE_CODE=['edX/full/.*'])
    def test_sandbox_inclusion(self):
        """
        Test to make sure that a match works across course runs
        """
        assert can_execute_unsafe_code(CourseKey.from_string('edX/full/2012_Fall'))
        assert can_execute_unsafe_code(CourseKey.from_string('edX/full/2013_Spring'))
        assert not can_execute_unsafe_code(LibraryLocator('edX', 'test_bank'))

    def test_courselikes_with_unsafe_code_default(self):
        """
        Test that the default setting for COURSES_WITH_UNSAFE_CODE is an empty setting, e.g. we don't use @override_settings in these tests  # lint-amnesty, pylint: disable=line-too-long
        """
        assert not can_execute_unsafe_code(CourseLocator('edX', 'full', '2012_Fall'))
        assert not can_execute_unsafe_code(CourseLocator('edX', 'full', '2013_Spring'))
        assert not can_execute_unsafe_code(LibraryLocator('edX', 'test_bank'))


@ddt.ddt
class SandboxServiceTest(TestCase):
    """
    Test SandboxService methods.
    """
    PYTHON_LIB_FILENAME = 'test_python_lib.zip'
    PYTHON_LIB_SOURCE_FILE = './common/test/data/uploads/python_lib.zip'

    @classmethod
    def setUpClass(cls):
        """
        Upload the python lib file to the test course.
        """
        super().setUpClass()

        course_key = CourseLocator('test', 'sandbox_test', '2021_01')
        cls.sandbox_service = SandboxService(course_id=course_key, contentstore=contentstore)
        cls.zipfile = upload_file_to_course(
            course_key=course_key,
            contentstore=cls.sandbox_service.contentstore(),
            source_file=cls.PYTHON_LIB_SOURCE_FILE,
            target_filename=cls.PYTHON_LIB_FILENAME,
        )

    @staticmethod
    def validate_can_execute_unsafe_code(context_key, expected_result):
        sandbox_service = SandboxService(course_id=context_key, contentstore=None)
        assert expected_result == sandbox_service.can_execute_unsafe_code()

    @ddt.data(
        CourseLocator('edX', 'notful', 'empty'),
        LibraryLocator('edY', 'test_bank'),
    )
    @override_settings(COURSES_WITH_UNSAFE_CODE=['edX/full/.*', 'library:v1-edX+.*'])
    def test_sandbox_exclusion(self, context_key):
        """
        Test to make sure that a non-match returns false
        """
        self.validate_can_execute_unsafe_code(context_key, False)

    @ddt.data(
        CourseKey.from_string('edX/full/2012_Fall'),
        CourseKey.from_string('edX/full/2013_Spring'),
    )
    @override_settings(COURSES_WITH_UNSAFE_CODE=['edX/full/.*'])
    def test_sandbox_inclusion(self, context_key):
        """
        Test to make sure that a match works across course runs
        """
        self.validate_can_execute_unsafe_code(context_key, True)
        self.validate_can_execute_unsafe_code(LibraryLocator('edX', 'test_bank'), False)

    @ddt.data(
        CourseLocator('edX', 'full', '2012_Fall'),
        CourseLocator('edX', 'full', '2013_Spring'),
        LibraryLocator('edX', 'test_bank'),
    )
    def test_courselikes_with_unsafe_code_default(self, context_key):
        """
        Test that the default setting for COURSES_WITH_UNSAFE_CODE is an empty setting,
        i.e., we don't use @override_settings in these tests
        """
        self.validate_can_execute_unsafe_code(context_key, False)

    @override_settings(PYTHON_LIB_FILENAME=PYTHON_LIB_FILENAME)
    def test_get_python_lib_zip(self):
        assert self.sandbox_service.get_python_lib_zip() == self.zipfile

    def test_no_python_lib_zip(self):
        assert self.sandbox_service.get_python_lib_zip() is None


class SandboxServiceForLibrariesV2Test(TestCase):
    """
    Test SandboxService methods for V2 Content Libraries.

    (Lacks tests for anything other than python_lib_zip)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        library_key = LibraryLocatorV2('test', 'sandbox_test')
        cls.sandbox_service = SandboxService(course_id=library_key, contentstore=contentstore)

    def test_no_python_lib_zip(self):
        assert self.sandbox_service.get_python_lib_zip() is None
