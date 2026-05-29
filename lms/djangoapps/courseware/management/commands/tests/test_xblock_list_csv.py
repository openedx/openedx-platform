"""
Tests for the xblock_list_csv management command.
"""

import csv
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import TestCase

from lms.djangoapps.courseware.management.commands.xblock_list_csv import generate_xblocks_csv

OVERVIEWS_PATH = "lms.djangoapps.courseware.management.commands.xblock_list_csv.CourseOverview.objects"
MODULESTORE_PATH = "lms.djangoapps.courseware.management.commands.xblock_list_csv.modulestore"


class GenerateCSVCommandTestCase(TestCase):
    """
    Test case for the xblock_list_csv management command
    """

    COURSE_ID = "course-v1:edX+Test101+2024"

    @staticmethod
    def _make_block(display_name, block_type):
        """
        Creates a mock block
        """
        component = MagicMock()
        component.display_name = display_name
        component.location.block_type = block_type
        return component

    @staticmethod
    def _make_container(display_name, children):
        """
        Creates a mock container
        """
        block = MagicMock()
        block.display_name = display_name
        block.get_children.return_value = children
        return block

    @staticmethod
    def _make_course(course_id, display_name, sections):
        """
        Creates a mock course
        """
        course = MagicMock()
        course.id = course_id
        course.display_name = display_name
        course.get_children.return_value = sections
        return course

    @staticmethod
    def _make_overview(course_id):
        """
        Creates a mock overview for a course
        """
        overview = MagicMock()
        overview.id = course_id
        return overview

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        HTML_COMPONENT = cls._make_block("My HTML", "html")
        VIDEO_COMPONENT = cls._make_block("My Video", "video")
        PROBLEM_COMPONENT = cls._make_block("My Problem", "problem")
        DRAG_COMPONENT = cls._make_block("My Drag Drop", "drag-and-drop-v2")
        unit = cls._make_container("Unit 1", [HTML_COMPONENT, VIDEO_COMPONENT, PROBLEM_COMPONENT, DRAG_COMPONENT])
        subsection = cls._make_container("Subsection 1", [unit])
        section = cls._make_container("Section 1", [subsection])
        cls.MOCK_COURSE = cls._make_course(cls.COURSE_ID, "Test Course", [section])
        cls.MOCK_OVERVIEW = cls._make_overview(cls.COURSE_ID)

    def _run_generate(self, overviews, exclude_core_xblocks=False, courses=None):
        """Helper: run generate_xblocks_csv with mocked DB/modulestore, return parsed CSV rows."""
        output = StringIO()
        with patch(OVERVIEWS_PATH) as mock_overviews, patch(MODULESTORE_PATH) as mock_modulestore:
            mock_overviews.all.return_value.order_by.return_value = overviews
            mock_overviews.filter.return_value.order_by.return_value = overviews
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE
            generate_xblocks_csv(output, exclude_core_xblocks, courses)
        output.seek(0)
        return list(csv.reader(output))

    def test_header_row(self):
        rows = self._run_generate([])
        assert rows[0] == [
            "Course ID",
            "Course Name",
            "Section Name",
            "Subsection Name",
            "Unit Name",
            "Component Name",
            "Xblock Type",
        ]

    def test_all_components_included_by_default(self):
        rows = self._run_generate([self.MOCK_OVERVIEW])
        # 1 header + 4 components
        assert len(rows) == 5

        # Checking data in the first row
        row = rows[1]
        assert row[0] == str(self.COURSE_ID)
        assert row[1] == "Test Course"
        assert row[2] == "Section 1"
        assert row[3] == "Subsection 1"
        assert row[4] == "Unit 1"
        assert row[5] == "My HTML"
        assert row[6] == "html"

    def test_exclude_core_xblocks(self):
        rows = self._run_generate([self.MOCK_OVERVIEW], exclude_core_xblocks=True)
        # Only drag-and-drop-v2 survives; html/video/problem are filtered out
        assert len(rows) == 2
        assert rows[1][6] == "drag-and-drop-v2"

    def test_courses_filter_uses_filter_queryset(self):
        output = StringIO()
        with patch(OVERVIEWS_PATH) as mock_overviews, patch(MODULESTORE_PATH) as mock_modulestore:
            mock_overviews.filter.return_value.order_by.return_value = []
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE

            generate_xblocks_csv(output, False, [self.COURSE_ID])

            mock_overviews.filter.assert_called_once_with(id__in=[self.COURSE_ID])
            mock_overviews.all.assert_not_called()

    def test_no_courses_filter_uses_all_queryset(self):
        output = StringIO()
        with patch(OVERVIEWS_PATH) as mock_overviews, patch(MODULESTORE_PATH) as mock_modulestore:
            mock_overviews.all.return_value.order_by.return_value = []
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE

            generate_xblocks_csv(output, False, None)

            mock_overviews.all.assert_called_once()
            mock_overviews.filter.assert_not_called()
