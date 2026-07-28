"""
Utilities for view tests.
"""


import json

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from xmodule.modulestore.tests.factories import BlockFactory  # pylint: disable=wrong-import-order

from ...helpers import xblock_studio_url


class StudioPageTestCase(CourseTestCase):
    """
    Base class for all tests of Studio pages.
    """

    def setUp(self):
        super().setUp()
        self.chapter = BlockFactory.create(parent=self.course,
                                           category='chapter', display_name="Week 1")
        self.sequential = BlockFactory.create(parent=self.chapter,
                                              category='sequential', display_name="Lesson 1")

    def get_page_html(self, xblock):
        """
        Returns the HTML for the page representing the xblock.
        """
        url = xblock_studio_url(xblock)
        self.assertIsNotNone(url)  # noqa: PT009
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        return resp.content.decode(resp.charset)

    def get_preview_html(self, xblock, view_name):
        """
        Returns the HTML for the xblock when shown within a unit or container page.
        """
        preview_url = f'/xblock/{xblock.location}/{view_name}'
        resp = self.client.get_json(preview_url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        resp_content = json.loads(resp.content.decode('utf-8'))
        return resp_content['html']

    def validate_preview_html(self, xblock, view_name, in_unit=False, can_add=True, can_reorder=True, can_move=True,
                              can_edit=True, can_duplicate=True, can_delete=True):
        """
        Verify that the specified xblock's preview has the expected HTML elements.
        """
        html = self.get_preview_html(xblock, view_name)
        self.validate_html_for_action_button(
            html,
            '<div class="add-xblock-component new-component-item adding"></div>',
            can_add
        )
        self.validate_html_for_action_button(
            html,
            '<span data-tooltip="Drag to reorder" class="drag-handle action"></span>',
            can_reorder
        )

        if in_unit:
            move_action_html = '<button data-tooltip="Move" class="btn-default move-button action-button">'
            delete_action_html = '<button data-tooltip="Delete" class="btn-default delete-button action-button">'
            duplicate_action_html = \
                '<button data-tooltip="Duplicate" class="btn-default duplicate-button action-button">'
        else:
            move_action_html = '<a class="move-button" href="#" role="button">Move</a>'
            delete_action_html = '<a class="delete-button" href="#" role="button">Delete</a>'
            duplicate_action_html = '<a class="duplicate-button" href="#" role="button">Duplicate</a>'

        self.validate_html_for_action_button(
            html,
            move_action_html,
            can_move
        )
        self.validate_html_for_action_button(
            html,
            'button class="btn-default edit-button action-button"',
            can_edit
        )

        self.validate_html_for_action_button(
            html,
            delete_action_html,
            can_delete
        )

        self.validate_html_for_action_button(
            html,
            duplicate_action_html,
            can_duplicate
        )

    def validate_html_for_action_button(self, html, expected_html, can_action=True):
        """
        Validate that the specified HTML has specific action..
        """
        if can_action:
            self.assertIn(expected_html, html)  # noqa: PT009
        else:
            self.assertNotIn(expected_html, html)  # noqa: PT009
