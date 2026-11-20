# pylint: disable=missing-module-docstring

import unittest
from unittest.mock import Mock

from xblock.field_data import DictFieldData

from xmodule.html_block import CourseInfoBlock

from . import get_test_system


class CourseInfoBlockTestCase(unittest.TestCase):
    """
    Make sure that CourseInfoBlock renders updates properly.
    """

    def test_updates_render(self):
        """
        Tests that a course info block will render its updates, even if they are malformed.
        """
        sample_update_data = [
            {
                "id": i,
                "date": data,
                "content": "This is a very important update!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            } for i, data in enumerate(
                [
                    'January 1, 1970',
                    'Marchtober 45, -1963',
                    'Welcome!',
                    'Date means "title", right?'
                ]
            )
        ]
        info_block = CourseInfoBlock(
            get_test_system(),
            DictFieldData({'items': sample_update_data, 'data': ""}),
            Mock()
        )

        # Prior to TNL-4115, an exception would be raised when trying to parse invalid dates in this method
        try:
            info_block.get_html()
        except ValueError:
            self.fail("CourseInfoBlock could not parse an invalid date!")

    def test_updates_order(self):
        """
        Tests that a course info block will render its updates in the correct order.
        """
        sample_update_data = [
            {
                "id": 3,
                "date": "March 18, 1982",
                "content": "This is a very important update that was inserted last with an older date!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            },
            {
                "id": 1,
                "date": "January 1, 2012",
                "content": "This is a very important update that was inserted first!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            },
            {
                "id": 2,
                "date": "January 1, 2012",
                "content": "This is a very important update that was inserted second!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            }
        ]
        info_block = CourseInfoBlock(
            Mock(),
            DictFieldData({'items': sample_update_data, 'data': ""}),
            Mock()
        )

        expected_context = {
            'visible_updates': [
                {
                    "id": 2,
                    "date": "January 1, 2012",
                    "content": "This is a very important update that was inserted second!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                },
                {
                    "id": 1,
                    "date": "January 1, 2012",
                    "content": "This is a very important update that was inserted first!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                },
                {
                    "id": 3,
                    "date": "March 18, 1982",
                    "content": "This is a very important update that was inserted last with an older date!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                }
            ],
            'hidden_updates': [],
        }
        template_name = f"{info_block.TEMPLATE_DIR}/course_updates.html"
        info_block.get_html()
        info_block.runtime.service(info_block, 'mako').render_lms_template.assert_called_once_with(
            template_name,
            expected_context
        )
