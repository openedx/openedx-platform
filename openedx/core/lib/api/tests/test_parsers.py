"""
TestCases verifying proper behavior of custom DRF request parsers.
"""

from collections import namedtuple
from io import BytesIO

import pytest
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory, APITestCase

from openedx.core.lib.api import parsers


class TestTypedFileUploadParser(APITestCase):
    """
    Tests that verify the behavior of TypedFileUploadParser
    """

    def setUp(self):
        super().setUp()
        self.parser = parsers.TypedFileUploadParser()
        self.request_factory = APIRequestFactory()
        upload_media_types = {'image/png', 'image/jpeg', 'application/octet-stream'}
        self.view = namedtuple('view', ('upload_media_types',))(upload_media_types)

    def test_parse_supported_type(self):
        """
        Test that TypedFileUploadParser returns empty data and content stored in
        files['file'].
        """
        request = self.request_factory.post(
            '/',
            content_type='image/png',
            HTTP_CONTENT_DISPOSITION='attachment; filename="file.PNG"',
        )
        context = {'view': self.view, 'request': request}
        result = self.parser.parse(stream=BytesIO(b'abcdefgh'), media_type='image/png', parser_context=context)
        assert result.data == {}
        assert 'file' in result.files
        assert result.files['file'].read() == b'abcdefgh'

    def test_parse_unsupported_type(self):
        """
        Test that TypedFileUploadParser raises an exception when parsing an
        unsupported image format.
        """
        request = self.request_factory.post(
            '/',
            content_type='image/tiff',
            HTTP_CONTENT_DISPOSITION='attachment; filename="file.tiff"',
        )
        context = {'view': self.view, 'request': request}
        with pytest.raises(exceptions.UnsupportedMediaType):
            self.parser.parse(stream=BytesIO(b'abcdefgh'), media_type='image/tiff', parser_context=context)

    def test_parse_unconstrained_type(self):
        """
        Test that TypedFileUploader allows any extension for mimetypes without
        specified extensions
        """
        request = self.request_factory.post(
            '/',
            content_type='application/octet-stream',
            HTTP_CONTENT_DISPOSITION='attachment; filename="VIRUS.EXE',
        )
        context = {'view': self.view, 'request': request}
        result = self.parser.parse(
            stream=BytesIO(b'abcdefgh'), media_type='application/octet-stream', parser_context=context
        )
        assert result.data == {}
        assert 'file' in result.files
        assert result.files['file'].read() == b'abcdefgh'

    def test_parse_mismatched_filename_and_mimetype(self):
        """
        Test that TypedFileUploadParser raises an exception when the specified
        content-type doesn't match the filename extension in the
        content-disposition header.
        """
        request = self.request_factory.post(
            '/',
            content_type='image/png',
            HTTP_CONTENT_DISPOSITION='attachment; filename="file.jpg"',
        )
        context = {'view': self.view, 'request': request}
        with pytest.raises(exceptions.ParseError) as err:  # noqa: PT012
            self.parser.parse(stream=BytesIO(b'abcdefgh'), media_type='image/png', parser_context=context)
            assert 'developer_message' in err.detail
            # pylint: disable=no-member
            assert 'user_message' not in err.detail
            # pylint: disable=no-member

    def test_no_acceptable_types(self):
        """
        If the view doesn't specify supported types, the parser rejects
        everything.
        """
        view = object()
        assert not hasattr(view, 'upload_media_types')

        request = self.request_factory.post(
            '/',
            content_type='image/png',
            HTTP_CONTENT_DISPOSITION='attachment; filename="file.png"',
        )
        context = {'view': view, 'request': request}
        with pytest.raises(exceptions.UnsupportedMediaType) as err:  # noqa: PT012
            self.parser.parse(stream=BytesIO(b'abcdefgh'), media_type='image/png', parser_context=context)
            assert 'developer_message' in err.detail
            # pylint: disable=no-member
            assert 'user_message' in err.detail
            # pylint: disable=no-member
