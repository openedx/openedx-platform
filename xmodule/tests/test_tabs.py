"""
Tests for CourseTabsListTestCase.
"""
from unittest import TestCase

import ddt

from xmodule.tabs import CourseTabList, InvalidTabsException


@ddt.ddt
class CourseTabsListTestCase(TestCase):
    """
    Class containing CourseTabsListTestCase tests.
    """

    @ddt.data(
        [
            [],
            []
        ],
        [
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Courseware'},
                {'type': 'course_info', 'course_staff_only': False, 'name': 'Course Info'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
        ],
        [
            [
                {'type': 'course_info', 'course_staff_only': False, 'name': 'Home'},
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
        ]
    )
    @ddt.unpack
    def test_upgrade_tabs(self, tabs, expected_result):
        CourseTabList.upgrade_tabs(tabs)
        self.assertEqual(tabs, expected_result)

    @ddt.data(
        [
            [],
            True
        ],
        [
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
            ],
            True
        ],
        [
            [
                {'type': 'course_info', 'course_staff_only': False, 'name': 'Home'},
            ],
            False
        ],
        [
            [
                {'type': 'course_info', 'course_staff_only': False, 'name': 'Home'},
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
            False
        ],
        [
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
            False
        ],
        [
            [
                {'type': 'courseware', 'course_staff_only': False, 'name': 'Course'},
                {'type': 'discussion', 'course_staff_only': False, 'name': 'Discussion'},
                {'type': 'wiki', 'course_staff_only': False, 'name': 'Wiki'},
                {'type': 'textbooks', 'course_staff_only': False, 'name': 'Textbooks'},
                {'type': 'progress', 'course_staff_only': False, 'name': 'Progress'}
            ],
            True
        ]
    )
    @ddt.unpack
    def test_validate_tabs(self, tabs, expected_success):
        if not expected_success:
            with self.assertRaises(InvalidTabsException):
                CourseTabList.validate_tabs(tabs)
        else:
            CourseTabList.validate_tabs(tabs)
