"""Tests for contents"""


import unittest
from unittest.mock import Mock, patch

import ddt
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import AssetLocator, CourseLocator

from xmodule.contentstore.content import ContentStore, StaticContent, StaticContentStream

SAMPLE_STRING = """
This is a sample string with more than 1024 bytes, the default STREAM_DATA_CHUNK_SIZE

Lorem Ipsum is simply dummy text of the printing and typesetting industry.
Lorem Ipsum has been the industry's standard dummy text ever since the 1500s,
when an unknown printer took a galley of type and scrambled it to make a type
specimen book. It has survived not only five centuries, but also the leap into
electronic typesetting, remaining essentially unchanged. It was popularised in
the 1960s with the release of Letraset sheets containing Lorem Ipsum passages,
nd more recently with desktop publishing software like Aldus PageMaker including
versions of Lorem Ipsum.

It is a long established fact that a reader will be distracted by the readable
content of a page when looking at its layout. The point of using Lorem Ipsum is
that it has a more-or-less normal distribution of letters, as opposed to using
'Content here, content here', making it look like readable English. Many desktop
ublishing packages and web page editors now use Lorem Ipsum as their default model
text, and a search for 'lorem ipsum' will uncover many web sites still in their infancy.
Various versions have evolved over the years, sometimes by accident, sometimes on purpose
injected humour and the like).

Lorem Ipsum is simply dummy text of the printing and typesetting industry.
Lorem Ipsum has been the industry's standard dummy text ever since the 1500s,
when an unknown printer took a galley of type and scrambled it to make a type
specimen book. It has survived not only five centuries, but also the leap into
electronic typesetting, remaining essentially unchanged. It was popularised in
the 1960s with the release of Letraset sheets containing Lorem Ipsum passages,
nd more recently with desktop publishing software like Aldus PageMaker including
versions of Lorem Ipsum.

It is a long established fact that a reader will be distracted by the readable
content of a page when looking at its layout. The point of using Lorem Ipsum is
that it has a more-or-less normal distribution of letters, as opposed to using
'Content here, content here', making it look like readable English. Many desktop
ublishing packages and web page editors now use Lorem Ipsum as their default model
text, and a search for 'lorem ipsum' will uncover many web sites still in their infancy.
Various versions have evolved over the years, sometimes by accident, sometimes on purpose
injected humour and the like).
"""


class Content:
    """
    A class with location and content_type members
    """
    def __init__(self, location, content_type):
        self.location = location
        self.content_type = content_type
        self.data = None


class FakeGridFsItem:
    """
    This class provides the basic methods to get data from a GridFS item
    """
    def __init__(self, string_data):
        self.cursor = 0
        self.data = string_data
        self.length = len(string_data)

    def seek(self, position):
        """
        Set the cursor at "position"
        """
        self.cursor = position

    def read(self, chunk_size):
        """
        Read "chunk_size" bytes of data at position cursor and move the cursor
        """
        chunk = self.data[self.cursor:(self.cursor + chunk_size)]
        self.cursor += chunk_size
        return chunk


class MockImage(Mock):
    """
    This class pretends to be PIL.Image for purposes of thumbnails testing.
    """
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@ddt.ddt
class ContentTest(unittest.TestCase):  # pylint: disable=missing-class-docstring

    def test_thumbnail_none(self):
        # We had a bug where a thumbnail location of None was getting transformed into a Location tuple, with
        # all elements being None. It is important that the location be just None for rendering.
        content = StaticContent('loc', 'name', 'content_type', 'data', None, None, None)
        assert content.thumbnail_location is None

        content = StaticContent('loc', 'name', 'content_type', 'data')
        assert content.thumbnail_location is None

    @ddt.data(
        ("monsters__.jpg", "monsters__.jpg"),
        ("monsters__.png", "monsters__-png.jpg"),
        ("dots.in.name.jpg", "dots.in.name.jpg"),
        ("dots.in.name.png", "dots.in.name-png.jpg"),
    )
    @ddt.unpack
    def test_generate_thumbnail_image(self, original_filename, thumbnail_filename):
        content_store = ContentStore()
        content = Content(AssetLocator(CourseLocator('mitX', '800', 'ignore_run'), 'asset', original_filename),
                          None)
        (thumbnail_content, thumbnail_file_location) = content_store.generate_thumbnail(content)
        assert thumbnail_content is None
        assert AssetLocator(CourseLocator('mitX', '800', 'ignore_run'), 'thumbnail', thumbnail_filename) ==\
               thumbnail_file_location

    @patch('xmodule.contentstore.content.Image')
    def test_image_is_closed_when_generating_thumbnail(self, image_class_mock):
        # We used to keep the Image's file descriptor open when generating a thumbnail.
        # It should be closed after being used.
        mock_image = MockImage()
        image_class_mock.open.return_value = mock_image

        content_store = ContentStore()
        content = Content(AssetLocator(CourseLocator('mitX', '800', 'ignore_run'), 'asset', "monsters.jpg"),
                          "image/jpeg")
        content.data = b'mock data'
        content_store.generate_thumbnail(content)
        assert image_class_mock.open.called, 'Image.open not called'
        assert mock_image.close.called, 'mock_image.close not called'

    def test_store_svg_as_thumbnail(self):
        # We had a bug that caused generate_thumbnail to attempt to pass SVG to PIL to generate a thumbnail.
        # SVG files should be stored in original form for thumbnail purposes.
        content_store = ContentStore()
        content_store.save = Mock()
        thumbnail_filename = 'test.svg'
        content = Content(AssetLocator(CourseLocator('mitX', '800', 'ignore_run'), 'asset', 'test.svg'),
                          'image/svg+xml')
        content.data = b'mock svg file'
        (thumbnail_content, thumbnail_file_location) = content_store.generate_thumbnail(content)
        assert thumbnail_content.data.read() == b'mock svg file'
        assert AssetLocator(CourseLocator('mitX', '800', 'ignore_run'), 'thumbnail', thumbnail_filename) ==\
               thumbnail_file_location

    def test_compute_location(self):
        # We had a bug that __ got converted into a single _. Make sure that substitution of INVALID_CHARS (like space)
        # still happen.
        asset_location = StaticContent.compute_location(
            CourseKey.from_string('mitX/400/ignore'), 'subs__1eo_jXvZnE .srt.sjson'
        )
        assert AssetLocator(CourseLocator('mitX', '400', 'ignore', deprecated=True),
                            'asset', 'subs__1eo_jXvZnE_.srt.sjson') == asset_location

    def test_get_location_from_path(self):
        asset_location = StaticContent.get_location_from_path('/c4x/a/b/asset/images_course_image.jpg')
        assert AssetLocator(CourseLocator('a', 'b', None, deprecated=True),
                            'asset', 'images_course_image.jpg', deprecated=True) == asset_location

    def test_static_content_stream_stream_data(self):
        """
        Test StaticContentStream stream_data function, asserts that we get all the bytes
        """
        data = SAMPLE_STRING
        item = FakeGridFsItem(data)
        static_content_stream = StaticContentStream('loc', 'name', 'type', item, length=item.length)

        total_length = 0
        stream = static_content_stream.stream_data()
        for chunck in stream:
            total_length += len(chunck)

        assert total_length == static_content_stream.length

    def test_static_content_stream_stream_data_in_range(self):
        """
        Test StaticContentStream stream_data_in_range function,
        asserts that we get the requested number of bytes
        first_byte and last_byte are chosen to be simple but non trivial values
        and to have total_length > STREAM_DATA_CHUNK_SIZE (1024)
        """
        data = SAMPLE_STRING
        item = FakeGridFsItem(data)
        static_content_stream = StaticContentStream('loc', 'name', 'type', item, length=item.length)

        first_byte = 100
        last_byte = 1500

        total_length = 0
        stream = static_content_stream.stream_data_in_range(first_byte, last_byte)
        for chunck in stream:
            total_length += len(chunck)

        assert total_length == ((last_byte - first_byte) + 1)
