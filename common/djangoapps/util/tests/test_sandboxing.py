"""
Tests for sandboxing.py in util app
"""


import ddt
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator, LibraryLocator, LibraryLocatorV2

from xmodule.contentstore.django import contentstore
from xmodule.modulestore.tests.django_utils import upload_file_to_course
from xmodule.util.sandboxing import SandboxService


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

    @ddt.data(
        CourseLocator('edX', 'full', '2012_Fall'),
        LibraryLocator('edX', 'test_bank'),
    )
    def test_cannot_execute_unsafe_code(self, context_key):
        sandbox_service = SandboxService(course_id=context_key, contentstore=None)
        assert sandbox_service.can_execute_unsafe_code() is False

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
