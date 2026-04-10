"""
Shallow tests for `./manage.py cms reset_course_content COURSE_KEY VERSION_GUID`
"""
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.mixed import MixedModuleStore


class TestCommand(TestCase):
    """
    Shallow test for CMS `reset_course_content` management command.

    The underlying implementation (`DraftVersioningModulestore.reset_course_to_version`)
    is tested within the modulestore.
    """

    def test_bad_course_id(self):
        with self.assertRaises(InvalidKeyError):  # noqa: PT027
            call_command("reset_course_content", "not_a_course_id", "0123456789abcdef01234567")

    def test_wrong_length_version_guid(self):
        with self.assertRaises(CommandError):  # noqa: PT027
            call_command("reset_course_content", "course-v1:a+b+c", "0123456789abcdef")

    def test_non_hex_version_guid(self):
        with self.assertRaises(CommandError):  # noqa: PT027
            call_command("reset_course_content", "course-v1:a+b+c", "0123456789abcdefghijklmn")

    @mock.patch.object(MixedModuleStore, "reset_course_to_version")
    def test_good_arguments(self, mock_reset_course_to_version):
        call_command("reset_course_content", "course-v1:a+b+c", "0123456789abcdef01234567")
        mock_reset_course_to_version.assert_called_once_with(
            CourseKey.from_string("course-v1:a+b+c"),
            "0123456789abcdef01234567",
            ModuleStoreEnum.UserID.mgmt_command,
        )
