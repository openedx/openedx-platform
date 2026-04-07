"""
Unit tests for content_libraries_import command.
"""


from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


@mock.patch('openedx.core.djangoapps.content_libraries.management.commands.content_libraries_import.contentlib_api')
class ContentLibrariesImportTest(TestCase):
    """
    Unit tests for content_libraries_import command.
    """

    library_key_str = 'lib:foo:bar'

    course_key_str = 'course-v1:foo+bar+foobar'

    def call_command(self, *args, **kwargs):
        """
        Call command with default test paramters.
        """
        out = StringIO()
        kwargs['stdout'] = out
        library_key = kwargs.pop('library_key', self.library_key_str)
        course_key = kwargs.pop('course_key', self.course_key_str)
        call_command('content_libraries_import', library_key, course_key,
                     'api',
                     '--oauth-creds', 'fake-key', 'fake-secret',
                     *args, **kwargs)
        return out

    def test_call_without_library(self, api_mock):
        """
        Given library does not exists
        Then raises command error
        """
        from openedx.core.djangoapps.content_libraries.api import ContentLibraryNotFound
        api_mock.ContentLibraryNotFound = ContentLibraryNotFound
        api_mock.get_library.side_effect = ContentLibraryNotFound
        with self.assertRaises(CommandError):
            self.call_command()
