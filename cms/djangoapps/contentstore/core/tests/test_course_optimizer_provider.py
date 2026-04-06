"""
Tests for course optimizer
"""

from unittest import mock
from unittest.mock import Mock

from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.core.course_optimizer_provider import (
    _create_dto_recursive,
    _update_node_tree_and_dictionary,
    generate_broken_links_descriptor,
    sort_course_sections,
)
from cms.djangoapps.contentstore.tasks import LinkState, extract_content_URLs_from_course
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import contains_course_reference
from xmodule.tabs import StaticTab


class TestLinkCheckProvider(CourseTestCase):
    """
    Tests for functions that generate a json structure of locked and broken links
    to send to the frontend.
    """
    def setUp(self):
        """Setup course blocks for tests"""
        super().setUp()
        self.mock_course = Mock()
        self.mock_section = Mock(
            location=Mock(block_id='chapter_1'),
            display_name='Section Name',
            category='chapter'
        )
        self.mock_subsection = Mock(
            location=Mock(block_id='sequential_1'),
            display_name='Subsection Name',
            category='sequential'
        )
        self.mock_unit = Mock(
            location=Mock(block_id='vertical_1'),
            display_name='Unit Name',
            category='vertical'
        )
        self.mock_block = Mock(
            location=Mock(block_id='block_1'),
            display_name='Block Name',
            course_id=self.course.id,
            category='html'
        )
        self.mock_course.get_parent.return_value = None
        self.mock_section.get_parent.return_value = self.mock_course
        self.mock_subsection.get_parent.return_value = self.mock_section
        self.mock_unit.get_parent.return_value = self.mock_subsection
        self.mock_block.get_parent.return_value = self.mock_unit

    def test_update_node_tree_and_dictionary_returns_node_tree(self):
        """
        Verify _update_node_tree_and_dictionary creates a node tree structure
        when passed a block level xblock.
        """
        expected_tree = {
            self.mock_section.location: {
                self.mock_subsection.location: {
                    self.mock_unit.location: {
                        self.mock_block.location: {}
                    }
                }
            }
        }
        result_tree, result_dictionary = _update_node_tree_and_dictionary(
            self.mock_block, 'example_link', LinkState.LOCKED, {}, {}
        )

        self.assertEqual(expected_tree, result_tree)

    def test_update_node_tree_and_dictionary_returns_dictionary(self):
        """
        Verify _update_node_tree_and_dictionary creates a dictionary of parent xblock entries
        when passed a block level xblock.
        """
        expected_dictionary = {
            self.mock_section.location: {
                'display_name': 'Section Name',
                'category': 'chapter'
            },
            self.mock_subsection.location: {
                'display_name': 'Subsection Name',
                'category': 'sequential'
            },
            self.mock_unit.location: {
                'display_name': 'Unit Name',
                'category': 'vertical'
            },
            self.mock_block.location: {
                'display_name': 'Block Name',
                'category': 'html',
                'url': f'/course/{self.course.id}/editor/html/{self.mock_block.location}',
                'locked_links': ['example_link']
            }
        }
        result_tree, result_dictionary = _update_node_tree_and_dictionary(
            self.mock_block, 'example_link', LinkState.LOCKED, {}, {}
        )

        self.assertEqual(expected_dictionary, result_dictionary)

    def test_create_dto_recursive_returns_for_empty_node(self):
        """
        Test _create_dto_recursive behavior at the end of recursion.
        Function should return None when given empty node tree and empty dictionary.
        """
        expected = _create_dto_recursive({}, {})
        self.assertEqual(None, expected)

    def test_create_dto_recursive_returns_for_leaf_node(self):
        """
        Test _create_dto_recursive behavior at the step before the end of recursion.
        When evaluating a leaf node in the node tree, the function should return broken links
        and locked links data from the leaf node.
        """
        expected_result = {
            'blocks': [
                {
                    'id': 'block_1',
                    'displayName': 'Block Name',
                    'url': '/block/1',
                    'brokenLinks': ['broken_link_1', 'broken_link_2'],
                    'lockedLinks': ['locked_link'],
                    'externalForbiddenLinks': ['forbidden_link_1'],
                    'previousRunLinks': [],
                }
            ]
        }

        mock_node_tree = {
            'block_1': {}
        }
        mock_dictionary = {
            'chapter_1': {
                'display_name': 'Section Name',
                'category': 'chapter'
            },
            'sequential_1': {
                'display_name': 'Subsection Name',
                'category': 'sequential'
            },
            'vertical_1': {
                'display_name': 'Unit Name',
                'category': 'vertical'
            },
            'block_1': {
                'display_name': 'Block Name',
                'url': '/block/1',
                'broken_links': ['broken_link_1', 'broken_link_2'],
                'locked_links': ['locked_link'],
                'external_forbidden_links': ['forbidden_link_1'],
            }
        }
        expected = _create_dto_recursive(mock_node_tree, mock_dictionary)
        self.assertEqual(expected_result, expected)

    def test_create_dto_recursive_returns_for_full_tree(self):
        """
        Test _create_dto_recursive behavior when recursing many times.
        When evaluating a fully mocked node tree and dictionary, the function should return
        a full json DTO prepared for frontend.
        """
        expected_result = {
            'sections': [
                {
                    'id': 'chapter_1',
                    'displayName': 'Section Name',
                    'subsections': [
                        {
                            'id': 'sequential_1',
                            'displayName': 'Subsection Name',
                            'units': [
                                {
                                    'id': 'vertical_1',
                                    'displayName': 'Unit Name',
                                    'blocks': [
                                        {
                                            'id': 'block_1',
                                            'displayName': 'Block Name',
                                            'url': '/block/1',
                                            'brokenLinks': ['broken_link_1', 'broken_link_2'],
                                            'lockedLinks': ['locked_link'],
                                            'externalForbiddenLinks': ['forbidden_link_1'],
                                            'previousRunLinks': [],
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        mock_node_tree = {
            'chapter_1': {
                'sequential_1': {
                    'vertical_1': {
                        'block_1': {}
                    }
                }
            }
        }
        mock_dictionary = {
            'chapter_1': {
                'display_name': 'Section Name',
                'category': 'chapter'
            },
            'sequential_1': {
                'display_name': 'Subsection Name',
                'category': 'sequential'
            },
            'vertical_1': {
                'display_name': 'Unit Name',
                'category': 'vertical'
            },
            'block_1': {
                'display_name': 'Block Name',
                'url': '/block/1',
                'broken_links': ['broken_link_1', 'broken_link_2'],
                'locked_links': ['locked_link'],
                'external_forbidden_links': ['forbidden_link_1'],
            }
        }
        expected = _create_dto_recursive(mock_node_tree, mock_dictionary)

        self.assertEqual(expected_result, expected)

    @mock.patch('cms.djangoapps.contentstore.core.course_optimizer_provider.modulestore', autospec=True)
    def test_returns_unchanged_data_if_no_course_blocks(self, mock_modulestore):
        """Test that the function returns unchanged data if no course blocks exist."""
        mock_modulestore_instance = Mock()
        mock_modulestore.return_value = mock_modulestore_instance
        mock_modulestore_instance.get_items.return_value = []

        data = {}
        result = sort_course_sections("course-v1:Test+Course", data)
        assert result == data  # Should return the original data

    @mock.patch('cms.djangoapps.contentstore.core.course_optimizer_provider.modulestore', autospec=True)
    def test_returns_unchanged_data_if_linkcheckoutput_missing(self, mock_modulestore):
        """Test that the function returns unchanged data if 'LinkCheckOutput' is missing."""

        mock_modulestore_instance = Mock()
        mock_modulestore.return_value = mock_modulestore_instance

        data = {'LinkCheckStatus': 'Uninitiated'}  # No 'LinkCheckOutput'
        mock_modulestore_instance.get_items.return_value = data

        result = sort_course_sections("course-v1:Test+Course", data)
        assert result == data

    @mock.patch('cms.djangoapps.contentstore.core.course_optimizer_provider.modulestore', autospec=True)
    def test_returns_unchanged_data_if_sections_missing(self, mock_modulestore):
        """Test that the function returns unchanged data if 'sections' is missing."""

        mock_modulestore_instance = Mock()
        mock_modulestore.return_value = mock_modulestore_instance

        data = {'LinkCheckStatus': 'Success', 'LinkCheckOutput': {}}  # No 'LinkCheckOutput'
        mock_modulestore_instance.get_items.return_value = data

        result = sort_course_sections("course-v1:Test+Course", data)
        assert result == data

    @mock.patch('cms.djangoapps.contentstore.core.course_optimizer_provider.modulestore', autospec=True)
    def test_sorts_sections_correctly(self, mock_modulestore):
        """Test that the function correctly sorts sections based on published course structure."""

        # Create mock location objects that will match the section IDs in data
        mock_location2 = "section2"
        mock_location3 = "section3"
        mock_location1 = "section1"

        mock_course_block = Mock()
        mock_course_block.get_children.return_value = [
            Mock(location=mock_location2),
            Mock(location=mock_location3),
            Mock(location=mock_location1),
        ]

        mock_modulestore_instance = Mock()
        mock_modulestore.return_value = mock_modulestore_instance
        mock_modulestore_instance.get_items.return_value = [mock_course_block]

        data = {
            "LinkCheckOutput": {
                "sections": [
                    {"id": "section1", "name": "Intro"},
                    {"id": "section2", "name": "Advanced"},
                    {"id": "section3", "name": "Bonus"},  # Not in course structure
                ]
            }
        }

        result = sort_course_sections("course-v1:Test+Course", data)
        expected_sections = [
            {"id": "section2", "name": "Advanced"},
            {"id": "section3", "name": "Bonus"},
            {"id": "section1", "name": "Intro"},
        ]
        self.assertEqual(result["LinkCheckOutput"]["sections"], expected_sections)

    def test_prev_run_link_detection(self):
        """Test the core logic of separating previous run links from regular links."""

        previous_course_key = CourseKey.from_string(
            "course-v1:edX+DemoX+Demo_Course_2023"
        )

        test_cases = [
            (f"/courses/{previous_course_key}/info", True),
            (f"/courses/{previous_course_key}/courseware", True),
            (f"/courses/{str(previous_course_key).upper()}/page", True),
            # Should NOT match
            ("/courses/course-v1:edX+DemoX+Demo_Course_2024/info", False),
            ("/static/image.png", False),
            ("/assets/courseware/file.pdf", False),
            ("", False),
            ("   ", False),
        ]

        for url, expected_match in test_cases:
            with self.subTest(url=url, expected=expected_match):
                result = contains_course_reference(url, previous_course_key)
                self.assertEqual(
                    result,
                    expected_match,
                    f"URL '{url}' should {'match' if expected_match else 'not match'} previous course",
                )

    def test_enhanced_url_detection_edge_cases(self):
        """Test edge cases for enhanced URL detection."""

        test_cases = [
            ("", []),  # Empty content
            ("No URLs here", []),  # Content without URLs
            (
                "Visit https://example.com today!",
                ["https://example.com"],
            ),  # URL in text
            ('href="#anchor"', []),  # Should exclude fragments
            ('src="data:image/png;base64,123"', []),  # Should exclude data URLs
            (
                "Multiple URLs: http://site1.com and https://site2.com",
                ["http://site1.com", "https://site2.com"],
            ),  # Multiple URLs
            (
                "URL with params: https://example.com/page?param=value&other=123",
                ["https://example.com/page?param=value&other=123"],
            ),  # URL with parameters
        ]

        for content, expected_urls in test_cases:
            with self.subTest(content=content):
                urls = extract_content_URLs_from_course(content)
                for expected_url in expected_urls:
                    self.assertIn(
                        expected_url,
                        urls,
                        f"Should find '{expected_url}' in content: {content}",
                    )

    def test_course_updates_and_custom_pages_structure(self):
        """Test that course_updates and custom_pages are properly structured in the response."""
        course_key = self.course.id

        # Test data that represents the broken links JSON structure
        json_content = [
            [
                str(self.mock_block.location),
                "http://content-link.com",
                LinkState.BROKEN,
            ],
            [
                str(self.mock_unit.location),
                "http://unit-link.com",
                LinkState.LOCKED,
            ],
            # Course updates
            [
                f"{course_key}+type@course_info+block@updates",
                "http://update1.com",
                LinkState.BROKEN,
            ],
            [
                f"{course_key}+type@course_info+block@updates",
                "http://update2.com",
                LinkState.LOCKED,
            ],
            # Handouts (should be merged into course_updates)
            [
                f"{course_key}+type@course_info+block@handouts",
                "http://handout.com",
                LinkState.BROKEN,
            ],
            # Custom pages (static tabs)
            [
                f"{course_key}+type@static_tab+block@page1",
                "http://page1.com",
                LinkState.BROKEN,
            ],
            [
                f"{course_key}+type@static_tab+block@page2",
                "http://page2.com",
                LinkState.EXTERNAL_FORBIDDEN,
            ],
        ]

        with mock.patch(
            "cms.djangoapps.contentstore.core.course_optimizer_provider._generate_links_descriptor_for_content"
        ) as mock_content, mock.patch(
            "cms.djangoapps.contentstore.core.course_optimizer_provider.modulestore"
        ) as mock_modulestore, mock.patch(
            "cms.djangoapps.contentstore.core.course_optimizer_provider.create_course_info_usage_key"
        ) as mock_create_usage_key, mock.patch(
            "cms.djangoapps.contentstore.core.course_optimizer_provider.get_course_update_items"
        ) as mock_get_update_items, mock.patch(
            "cms.djangoapps.contentstore.core.course_optimizer_provider.extract_content_URLs_from_course"
        ) as mock_extract_urls:

            mock_content.return_value = {"sections": []}
            mock_course = self.mock_course
            mock_tab1 = StaticTab(name="Test Page 1", url_slug="page1")
            mock_tab2 = StaticTab(name="Test Page 2", url_slug="page2")
            mock_course.tabs = [mock_tab1, mock_tab2]
            mock_course.id = course_key
            mock_modulestore.return_value.get_course.return_value = mock_course
            mock_updates_usage_key = Mock()
            mock_handouts_usage_key = Mock()
            mock_create_usage_key.side_effect = lambda course, info_type: (
                mock_updates_usage_key if info_type == "updates" else mock_handouts_usage_key
            )
            mock_updates_block = Mock()
            mock_updates_block.data = "Check out <a href='http://update1.com'>this update</a>"
            mock_handouts_block = Mock()
            mock_handouts_block.data = "Download <a href='http://handout.com'>handout</a>"
            mock_get_item_mapping = {
                mock_updates_usage_key: mock_updates_block,
                mock_handouts_usage_key: mock_handouts_block,
            }
            mock_modulestore.return_value.get_item.side_effect = (
                lambda usage_key: mock_get_item_mapping.get(usage_key, Mock())
            )
            mock_get_update_items.return_value = [
                {"id": "update1", "date": "2024-01-01", "content": "Update content 1", "status": "visible"},
                {"id": "update2", "date": "2024-01-02", "content": "Update content 2", "status": "visible"}
            ]
            mock_extract_urls.return_value = ["http://update1.com", "http://update2.com"]
            result = generate_broken_links_descriptor(
                json_content, self.user, course_key
            )

            # Verify top-level structure
            self.assertIn("sections", result)
            self.assertIn("course_updates", result)
            self.assertIn("custom_pages", result)
            self.assertNotIn("handouts", result)

            # Course updates should include both updates and handouts
            self.assertGreaterEqual(
                len(result["course_updates"]),
                1,
                "Should have course updates/handouts",
            )

            # Custom pages should have custom pages data
            self.assertGreaterEqual(
                len(result["custom_pages"]), 1, "Should have custom pages"
            )
