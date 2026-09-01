"""
Tests for the xblock_list_csv management command.
"""

import csv
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.management.commands.xblock_list_csv import generate_xblocks_csv

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
        component.get_children.return_value = []
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

    def _run_generate(self, exclude_core_xblocks=False, courses=None):
        """Helper: run generate_xblocks_csv with mocked modulestore, return parsed CSV rows."""
        output = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [self.MOCK_COURSE]
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE
            generate_xblocks_csv(output, exclude_core_xblocks, courses)
        output.seek(0)
        return list(csv.reader(output))

    def test_header_row(self):
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = []
            output = StringIO()
            generate_xblocks_csv(output, False, None)
            output.seek(0)
            rows = list(csv.reader(output))
        assert rows[0] == [
            "Course ID",
            "Course Name",
            "Section Name",
            "Subsection Name",
            "Unit Name",
            "Component Name",
            "Xblock Type",
            "Full Hierarchy",
        ]

    def test_all_components_included_by_default(self):
        rows = self._run_generate()
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
        assert row[7] == "Section 1 > Subsection 1 > Unit 1 > My HTML"

    def test_exclude_core_xblocks(self):
        rows = self._run_generate(exclude_core_xblocks=True)
        # Only drag-and-drop-v2 survives; html/video/problem are filtered out
        assert len(rows) == 2
        assert rows[1][6] == "drag-and-drop-v2"

    def test_courses_filter_uses_modulestore_get_course(self):
        output = StringIO()
        course_key = CourseKey.from_string(self.COURSE_ID)
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE

            generate_xblocks_csv(output, False, [self.COURSE_ID])

            mock_modulestore.return_value.get_course.assert_called_once_with(course_key)
            mock_modulestore.return_value.get_courses.assert_not_called()

    def test_duplicate_course_id_is_resolved_once(self):
        output = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_course.return_value = self.MOCK_COURSE

            generate_xblocks_csv(output, False, [self.COURSE_ID, self.COURSE_ID])

            mock_modulestore.return_value.get_course.assert_called_once()

        output.seek(0)
        rows = list(csv.reader(output))
        # 1 header + 4 components, not duplicated
        assert len(rows) == 5

    def test_no_courses_filter_uses_get_courses(self):
        output = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [self.MOCK_COURSE]

            generate_xblocks_csv(output, False, None)

            mock_modulestore.return_value.get_courses.assert_called_once()
            mock_modulestore.return_value.get_course.assert_not_called()

    def test_traversal_failure_writes_to_error_file(self):
        output = StringIO()
        errors = StringIO()
        broken_course = self._make_course(self.COURSE_ID, "Broken Course", None)
        broken_course.get_children.side_effect = Exception("boom")
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [broken_course]

            failures = generate_xblocks_csv(output, False, None, errors)

        assert failures == 1

        errors.seek(0)
        assert f"Failed processing course {self.COURSE_ID}" in errors.getvalue()

        output.seek(0)
        rows = list(csv.reader(output))
        # Only the header row is written since traversal failed
        assert len(rows) == 1

    def test_missing_course_id_writes_to_error_file(self):
        output = StringIO()
        errors = StringIO()
        course_key = CourseKey.from_string(self.COURSE_ID)
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_course.return_value = None

            failures = generate_xblocks_csv(output, False, [self.COURSE_ID], errors)

        assert failures == 1

        errors.seek(0)
        assert f"Course not found: {course_key}" in errors.getvalue()

        output.seek(0)
        rows = list(csv.reader(output))
        # Only the header row is written since the course doesn't exist
        assert len(rows) == 1

    def test_invalid_course_id_writes_to_error_file(self):
        output = StringIO()
        errors = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            failures = generate_xblocks_csv(output, False, ["not-a-valid-course-id"], errors)

            mock_modulestore.return_value.get_course.assert_not_called()

        assert failures == 1

        errors.seek(0)
        assert "Invalid course ID: not-a-valid-course-id" in errors.getvalue()

    def test_no_failures_returns_zero(self):
        output = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [self.MOCK_COURSE]
            failures = generate_xblocks_csv(output, False, None)
        assert failures == 0

    def test_handle_raises_command_error_on_failures(self):
        out = StringIO()
        err = StringIO()
        course_key = CourseKey.from_string(self.COURSE_ID)
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_course.return_value = None
            with pytest.raises(CommandError):
                call_command("xblock_list_csv", "-", "--courses", self.COURSE_ID, stdout=out, stderr=err)

        err.seek(0)
        assert f"Course not found: {course_key}" in err.getvalue()

    def test_nested_components_included_with_full_hierarchy(self):
        nested_leaf_a = self._make_block("Nested Video A", "video")
        nested_leaf_b = self._make_block("Nested Video B", "video")
        parent_component = self._make_block("Split Test", "split_test")
        parent_component.get_children.return_value = [nested_leaf_a, nested_leaf_b]

        unit = self._make_container("Unit 1", [parent_component])
        subsection = self._make_container("Subsection 1", [unit])
        section = self._make_container("Section 1", [subsection])
        course = self._make_course(self.COURSE_ID, "Test Course", [section])

        output = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [course]

            generate_xblocks_csv(output, False, None)

        output.seek(0)
        rows = list(csv.reader(output))
        # 1 header + parent component + 2 nested leaves
        assert len(rows) == 4

        parent_row = rows[1]
        assert parent_row[5] == "Split Test"
        assert parent_row[6] == "split_test"
        assert parent_row[7] == "Section 1 > Subsection 1 > Unit 1 > Split Test"

        child_a_row = rows[2]
        assert child_a_row[5] == "Nested Video A"
        assert child_a_row[7] == "Section 1 > Subsection 1 > Unit 1 > Split Test > Nested Video A"

        child_b_row = rows[3]
        assert child_b_row[5] == "Nested Video B"
        assert child_b_row[7] == "Section 1 > Subsection 1 > Unit 1 > Split Test > Nested Video B"

    def test_component_with_no_display_name_does_not_fail_course(self):
        unnamed_component = self._make_block(None, "html")
        named_component = self._make_block("My HTML", "html")
        unit = self._make_container("Unit 1", [unnamed_component, named_component])
        subsection = self._make_container("Subsection 1", [unit])
        section = self._make_container("Section 1", [subsection])
        course = self._make_course(self.COURSE_ID, "Test Course", [section])

        output = StringIO()
        errors = StringIO()
        with patch(MODULESTORE_PATH) as mock_modulestore:
            mock_modulestore.return_value.get_courses.return_value = [course]

            failures = generate_xblocks_csv(output, False, None, errors)

        assert failures == 0
        errors.seek(0)
        assert errors.getvalue() == ""

        output.seek(0)
        rows = list(csv.reader(output))
        # 1 header + 2 components; the course is not skipped
        assert len(rows) == 3

        unnamed_row = rows[1]
        assert unnamed_row[5] == ""
        assert unnamed_row[7] == "Section 1 > Subsection 1 > Unit 1 > "

        named_row = rows[2]
        assert named_row[5] == "My HTML"
        assert named_row[7] == "Section 1 > Subsection 1 > Unit 1 > My HTML"
