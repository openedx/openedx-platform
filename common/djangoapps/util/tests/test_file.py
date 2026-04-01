"""
Tests for file.py
"""


import os
from datetime import datetime
from io import StringIO
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import ddt
from django.core import exceptions
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locations import CourseLocator

from ccx_keys.locator import CCXLocator

import common.djangoapps.util.file
from common.djangoapps.util.file import (
    FileValidationException,
    UniversalNewlineIterator,
    course_and_time_based_filename_generator,
    course_filename_prefix_generator,
    store_uploaded_file
)


@ddt.ddt
class FilenamePrefixGeneratorTestCase(TestCase):
    """
    Tests for course_filename_prefix_generator
    """
    @ddt.data(
        CourseLocator(org='foo', course='bar', run='baz'),
        CourseKey.from_string('foo/bar/baz'),
        CCXLocator.from_course_locator(CourseLocator(org='foo', course='bar', run='baz'), '1'),
    )
    def test_locators(self, course_key):
        """
        Test filename prefix genaration from multiple course key formats.

        Test that the filename prefix is generated from a CCX course locator or a course key. If the
        filename is generated for a CCX course but the related 'ENABLE_COURSE_FILENAME_CCX_SUFFIX'
        feature is not turned on, the generated filename shouldn't contain the CCX course ID.
        """
        assert course_filename_prefix_generator(course_key) == 'foo_bar_baz'

    @ddt.data(
        [CourseLocator(org='foo', course='bar', run='baz'), 'foo_bar_baz'],
        [CourseKey.from_string('foo/bar/baz'), 'foo_bar_baz'],
        [CCXLocator.from_course_locator(CourseLocator(org='foo', course='bar', run='baz'), '1'), 'foo_bar_baz_ccx_1'],
    )
    @ddt.unpack
    @override_settings(FEATURES={'ENABLE_COURSE_FILENAME_CCX_SUFFIX': True})
    def test_include_ccx_id(self, course_key, expected_filename):
        """
        Test filename prefix genaration from multiple course key formats.

        Test that the filename prefix is generated from a CCX course locator or a course key. If the
        filename is generated for a CCX course but the related 'ENABLE_COURSE_FILENAME_CCX_SUFFIX'
        feature is not turned on, the generated filename shouldn't contain the CCX course ID.
        """
        assert course_filename_prefix_generator(course_key) == expected_filename

    @ddt.data(CourseLocator(org='foo', course='bar', run='baz'), CourseKey.from_string('foo/bar/baz'))
    def test_custom_separator(self, course_key):
        """
        Test filename prefix is generated with a custom separator.

        The filename should be build up from the course locator separated by a custom separator.
        """
        assert course_filename_prefix_generator(course_key, separator='-') == 'foo-bar-baz'

    @ddt.data(
        [CourseLocator(org='foo', course='bar', run='baz'), 'foo-bar-baz'],
        [CourseKey.from_string('foo/bar/baz'), 'foo-bar-baz'],
        [CCXLocator.from_course_locator(CourseLocator(org='foo', course='bar', run='baz'), '1'), 'foo-bar-baz-ccx-1'],
    )
    @ddt.unpack
    @override_settings(FEATURES={'ENABLE_COURSE_FILENAME_CCX_SUFFIX': True})
    def test_custom_separator_including_ccx_id(self, course_key, expected_filename):
        """
        Test filename prefix is generated with a custom separator.

        The filename should be build up from the course locator separated by a custom separator
        including the CCX ID if the related 'ENABLE_COURSE_FILENAME_CCX_SUFFIX' is turned on.
        """
        assert course_filename_prefix_generator(course_key, separator='-') == expected_filename


@ddt.ddt
class FilenameGeneratorTestCase(TestCase):
    """
    Tests for course_and_time_based_filename_generator
    """
    NOW = datetime.strptime('1974-06-22T01:02:03', '%Y-%m-%dT%H:%M:%S').replace(tzinfo=ZoneInfo("UTC"))

    def setUp(self):
        super().setUp()
        datetime_patcher = patch.object(
            common.djangoapps.util.file, 'datetime',
            Mock(wraps=datetime)
        )
        mocked_datetime = datetime_patcher.start()
        mocked_datetime.now.return_value = self.NOW
        self.addCleanup(datetime_patcher.stop)

    @ddt.data(CourseLocator(org='foo', course='bar', run='baz'), CourseKey.from_string('foo/bar/baz'))
    def test_filename_generator(self, course_key):
        """
        Tests that the generator creates names based on course_id, base name, and date.
        """
        assert 'foo_bar_baz_file_1974-06-22-010203' == course_and_time_based_filename_generator(course_key, 'file')

        assert 'foo_bar_baz_base_name_ø_1974-06-22-010203' ==\
               course_and_time_based_filename_generator(course_key, ' base` name ø ')


class StoreUploadedFileTestCase(TestCase):
    """
    Tests for store_uploaded_file.
    """

    def setUp(self):
        super().setUp()
        self.request = Mock(spec=HttpRequest)
        self.file_content = b"test file content"
        self.stored_file_name = None
        self.file_storage = None
        self.default_max_size = 2000000

    def tearDown(self):
        super().tearDown()
        if self.file_storage and self.stored_file_name:
            self.file_storage.delete(self.stored_file_name)

    def verify_exception(self, expected_message, error):
        """
        Helper method to verify exception text.
        """
        assert expected_message == str(error.value)

    def test_error_conditions(self):
        """
        Verifies that exceptions are thrown in the expected cases.
        """
        with pytest.raises(ValueError) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(self.request, "wrong_key", [".txt", ".csv"], "stored_file", self.default_max_size)
        self.verify_exception("No file uploaded with key 'wrong_key'.", error)

        with pytest.raises(exceptions.PermissionDenied) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(self.request, "uploaded_file", [], "stored_file", self.default_max_size)
        self.verify_exception("The file must end with one of the following extensions: ''.", error)

        with pytest.raises(exceptions.PermissionDenied) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(self.request, "uploaded_file", [".bar"], "stored_file", self.default_max_size)
        self.verify_exception("The file must end with the extension '.bar'.", error)

        with pytest.raises(exceptions.PermissionDenied) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(self.request, "uploaded_file", [".xxx", ".bar"], "stored_file", self.default_max_size)
        self.verify_exception("The file must end with one of the following extensions: '.xxx', '.bar'.", error)

        with pytest.raises(exceptions.PermissionDenied) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(self.request, "uploaded_file", [".csv"], "stored_file", 2)
        self.verify_exception("Maximum upload file size is 2 bytes.", error)

    def test_validator(self):
        """
        Verify that a validator function can throw an exception.
        """
        validator_data = {}

        def verify_file_presence(should_exist):
            """ Verify whether or not the stored file, passed to the validator, exists. """
            assert should_exist == validator_data['storage'].exists(validator_data['filename'])

        def store_file_data(storage, filename):
            """ Stores file validator data for testing after validation is complete. """
            validator_data["storage"] = storage
            validator_data["filename"] = filename
            verify_file_presence(True)

        def exception_validator(storage, filename):
            """ Validation test function that throws an exception """
            assert 'error_file.csv' == os.path.basename(filename)
            with storage.open(filename, 'rb') as f:
                assert self.file_content == f.read()
            store_file_data(storage, filename)
            raise FileValidationException("validation failed")

        def success_validator(storage, filename):
            """ Validation test function that is a no-op """
            assert 'success_file' in os.path.basename(filename)
            store_file_data(storage, filename)

        with pytest.raises(FileValidationException) as error:
            self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
            store_uploaded_file(
                self.request, "uploaded_file", [".csv"], "error_file",
                self.default_max_size, validator=exception_validator
            )
        self.verify_exception("validation failed", error)
        # Verify the file was deleted.
        verify_file_presence(False)

        self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
        store_uploaded_file(
            self.request, "uploaded_file", [".csv"], "success_file", self.default_max_size, validator=success_validator
        )
        # Verify the file still exists
        verify_file_presence(True)

    def test_file_upload_lower_case_extension(self):
        """
        Tests uploading a file with lower case extension. Verifies that the stored file contents are correct.
        """
        self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.csv", self.file_content)}
        file_storage, stored_file_name = store_uploaded_file(
            self.request, "uploaded_file", [".csv"], "stored_file", self.default_max_size
        )
        self._verify_successful_upload(file_storage, stored_file_name, self.file_content)

    def test_file_upload_upper_case_extension(self):
        """
        Tests uploading a file with upper case extension. Verifies that the stored file contents are correct.
        """
        file_content = b"uppercase"
        self.request.FILES = {"uploaded_file": SimpleUploadedFile("tempfile.CSV", file_content)}
        file_storage, stored_file_name = store_uploaded_file(
            self.request, "uploaded_file", [".gif", ".csv"], "second_stored_file", self.default_max_size
        )
        self._verify_successful_upload(file_storage, stored_file_name, file_content)

    def test_unique_filenames(self):
        """
        Test that the file storage method will create a unique filename if the file already exists.
        """
        requested_file_name = "nonunique_store"
        file_content = b"copy"

        self.request.FILES = {"nonunique_file": SimpleUploadedFile("nonunique.txt", file_content)}
        _, first_stored_file_name = store_uploaded_file(
            self.request, "nonunique_file", [".txt"], requested_file_name, self.default_max_size
        )

        self.request.FILES = {"nonunique_file": SimpleUploadedFile("nonunique.txt", file_content)}
        file_storage, second_stored_file_name = store_uploaded_file(
            self.request, "nonunique_file", [".txt"], requested_file_name, self.default_max_size
        )
        assert first_stored_file_name != second_stored_file_name
        assert requested_file_name in second_stored_file_name
        self._verify_successful_upload(file_storage, second_stored_file_name, file_content)

    def _verify_successful_upload(self, storage, file_name, expected_content):
        """ Helper method that checks that the stored version of the uploaded file has the correct content """
        assert storage.exists(file_name)
        with storage.open(file_name, 'rb') as f:
            assert expected_content == f.read()


@ddt.ddt
class TestUniversalNewlineIterator(TestCase):
    """
    Tests for the UniversalNewlineIterator class.
    """
    @ddt.data(1, 2, 999)
    def test_line_feeds(self, buffer_size):
        assert [thing.decode('utf-8') for thing
                in UniversalNewlineIterator(StringIO('foo\nbar\n'), buffer_size=buffer_size)] == ['foo\n', 'bar\n']

    @ddt.data(1, 2, 999)
    def test_carriage_returns(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO('foo\rbar\r'), buffer_size=buffer_size)] == ['foo\n', 'bar\n']

    @ddt.data(1, 2, 999)
    def test_carriage_returns_and_line_feeds(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO('foo\r\nbar\r\n'), buffer_size=buffer_size)] == ['foo\n', 'bar\n']

    @ddt.data(1, 2, 999)
    def test_no_trailing_newline(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO('foo\nbar'), buffer_size=buffer_size)] == ['foo\n', 'bar']

    @ddt.data(1, 2, 999)
    def test_only_one_line(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO('foo\n'), buffer_size=buffer_size)] == ['foo\n']

    @ddt.data(1, 2, 999)
    def test_only_one_line_no_trailing_newline(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO('foo'), buffer_size=buffer_size)] == ['foo']

    @ddt.data(1, 2, 999)
    def test_empty_file(self, buffer_size):
        assert [thing.decode('utf-8') for thing in
                UniversalNewlineIterator(StringIO(''), buffer_size=buffer_size)] == []

    @ddt.data(1, 2, 999)
    def test_unicode_data(self, buffer_size):
        assert [thing.decode('utf-8') for thing
                in UniversalNewlineIterator(StringIO('héllø wo®ld'), buffer_size=buffer_size)] == ['héllø wo®ld']
