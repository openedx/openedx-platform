"""
Unit tests for helpers.py.
"""

from unittest.mock import Mock, patch
from urllib.parse import quote

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    LibraryFactory,
)

from ...helpers import xblock_embed_lms_url, xblock_lms_url, xblock_studio_url, xblock_type_display_name


class HelpersTestCase(CourseTestCase):
    """
    Unit tests for helpers.py.
    """

    def test_xblock_studio_url(self):

        # Verify course URL
        course_url = f'/course/{str(self.course.id)}'
        self.assertEqual(xblock_studio_url(self.course), course_url)

        # Verify chapter URL
        chapter = BlockFactory.create(parent_location=self.course.location, category='chapter',
                                      display_name="Week 1")
        self.assertEqual(
            xblock_studio_url(chapter),
            f'{course_url}?show={quote(str(chapter.location).encode())}'
        )

        # Verify sequential URL
        sequential = BlockFactory.create(parent_location=chapter.location, category='sequential',
                                         display_name="Lesson 1")
        self.assertEqual(
            xblock_studio_url(sequential),
            f'{course_url}?show={quote(str(sequential.location).encode())}'
        )

        # Verify unit URL
        vertical = BlockFactory.create(parent_location=sequential.location, category='vertical',
                                       display_name='Unit')
        self.assertEqual(xblock_studio_url(vertical), f'/container/{vertical.location}')

        # Verify child vertical URL
        child_vertical = BlockFactory.create(parent_location=vertical.location, category='vertical',
                                             display_name='Child Vertical')
        self.assertEqual(xblock_studio_url(child_vertical), f'/container/{child_vertical.location}')

        # Verify video URL
        video = BlockFactory.create(parent_location=child_vertical.location, category="video",
                                    display_name="My Video")
        self.assertIsNone(xblock_studio_url(video))
        # Verify video URL with find_parent=True
        self.assertEqual(xblock_studio_url(video, find_parent=True), f'/container/{child_vertical.location}')

        # Verify library URL
        library = LibraryFactory.create()
        expected_url = f'/library/{str(library.location.library_key)}'
        self.assertEqual(xblock_studio_url(library), expected_url)

    @patch('cms.djangoapps.contentstore.helpers.configuration_helpers.get_value')
    def test_xblock_lms_url(self, mock_get_value: Mock):
        mock_get_value.return_value = 'lms.example.com'

        # Verify chapter URL
        chapter = BlockFactory.create(
            parent_location=self.course.location, category='chapter', display_name="Week 1"
        )
        self.assertEqual(
            xblock_lms_url(chapter),
            f"lms.example.com/courses/{chapter.location.course_key}/jump_to/{chapter.location}"
        )

        # Verify sequential URL
        sequential = BlockFactory.create(
            parent_location=chapter.location, category='sequential', display_name="Lesson 1"
        )
        self.assertEqual(
            xblock_lms_url(sequential),
            f"lms.example.com/courses/{sequential.location.course_key}/jump_to/{sequential.location}"
        )

    @patch('cms.djangoapps.contentstore.helpers.configuration_helpers.get_value')
    def test_xblock_embed_lms_url(self, mock_get_value: Mock):
        mock_get_value.return_value = 'lms.example.com'

        # Verify chapter URL
        chapter = BlockFactory.create(
            parent_location=self.course.location, category='chapter', display_name="Week 1"
        )
        self.assertEqual(xblock_embed_lms_url(chapter), f"lms.example.com/xblock/{chapter.location}")

        # Verify sequential URL
        sequential = BlockFactory.create(
            parent_location=chapter.location, category='sequential', display_name="Lesson 1"
        )
        self.assertEqual(xblock_embed_lms_url(sequential), f"lms.example.com/xblock/{sequential.location}")

    def test_xblock_type_display_name(self):

        # Verify chapter type display name
        chapter = BlockFactory.create(parent_location=self.course.location, category='chapter')
        self.assertEqual(xblock_type_display_name(chapter), 'Section')
        self.assertEqual(xblock_type_display_name('chapter'), 'Section')

        # Verify sequential type display name
        sequential = BlockFactory.create(parent_location=chapter.location, category='sequential')
        self.assertEqual(xblock_type_display_name(sequential), 'Subsection')
        self.assertEqual(xblock_type_display_name('sequential'), 'Subsection')

        # Verify unit type display names
        vertical = BlockFactory.create(parent_location=sequential.location, category='vertical')
        self.assertEqual(xblock_type_display_name(vertical), 'Unit')
        self.assertEqual(xblock_type_display_name('vertical'), 'Unit')

        # Verify child vertical type display name
        child_vertical = BlockFactory.create(parent_location=vertical.location, category='vertical',
                                             display_name='Child Vertical')
        self.assertEqual(xblock_type_display_name(child_vertical), 'Vertical')

        # Verify video type display names
        video = BlockFactory.create(parent_location=vertical.location, category="video")
        self.assertEqual(xblock_type_display_name(video), 'Video')
        self.assertEqual(xblock_type_display_name('video'), 'Video')

        # Verify split test type display names
        split_test = BlockFactory.create(parent_location=vertical.location, category="split_test")
        self.assertEqual(xblock_type_display_name(split_test), 'Content Experiment')
        self.assertEqual(xblock_type_display_name('split_test'), 'Content Experiment')
