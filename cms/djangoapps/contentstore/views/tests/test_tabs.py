""" Tests for tab functions (just primitive). """


import json
import random

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from cms.djangoapps.contentstore.views import tabs
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)
from xmodule.tabs import CourseTabList  # pylint: disable=wrong-import-order
from xmodule.x_module import STUDENT_VIEW  # pylint: disable=wrong-import-order


class TabsPageTests(CourseTestCase):
    """Test cases for Tabs (a.k.a Pages) page"""

    def setUp(self):
        """Common setup for tests"""

        # call super class to setup course, etc.
        super().setUp()

        # Set the URL for tests
        self.url = reverse_course_url('tabs_handler', self.course.id)

        # add a static tab to the course, for code coverage
        self.test_tab = BlockFactory.create(
            parent_location=self.course.location,
            category="static_tab",
            display_name="Static_1"
        )
        self.reload_course()

    def check_invalid_tab_id_response(self, resp):
        """Verify response is an error listing the invalid_tab_id"""

        assert resp.status_code == 400
        resp_content = json.loads(resp.content.decode('utf-8'))
        assert "error" in resp_content

    def test_not_implemented(self):
        """Verify not implemented errors"""

        # JSON GET request not supported
        with self.assertRaises(NotImplementedError):  # noqa: PT027
            self.client.get(self.url)

        # JSON POST request not supported
        with self.assertRaises(NotImplementedError):  # noqa: PT027
            self.client.ajax_post(
                self.url,
                data=json.dumps({
                    'tab_id_locator': {'tab_id': 'courseware'},
                    'unsupported_request': None,
                }),
            )

        # invalid JSON POST request
        with self.assertRaises(NotImplementedError):  # noqa: PT027
            self.client.ajax_post(
                self.url,
                data={'invalid_request': None},
            )

    def test_view_index(self):
        """Basic check that the Pages page responds correctly"""

        resp = self.client.get_html(self.url)
        assert resp.status_code == 302

    def test_reorder_tabs(self):
        """Test re-ordering of tabs"""

        # get the original tabs
        course_tabs = list(self.course.tabs)
        num_orig_tabs = len(self.course.tabs)

        # make sure we have enough tabs to play around with
        assert num_orig_tabs >= 5

        # Randomise the order of static tabs, leave the rest intact
        course_tabs.sort(key=lambda tab: (100 + random.random()) if tab.type == 'static_tab' else tab.priority)

        tabs_data = [
            {'tab_locator': str(self.course.id.make_usage_key("static_tab", tab.url_slug))}
            for tab in course_tabs
            if tab.type == 'static_tab'
        ]
        # Remove one tab randomly. This shouldn't delete the tab.
        tabs_data.pop()

        # post the request
        resp = self.client.ajax_post(
            self.url,
            data={
                'tabs': tabs_data
            },
        )
        assert resp.status_code == 204

        # reload the course and verify the new tab order
        self.reload_course()
        reordered_tab_ids = [tab.tab_id for tab in course_tabs]
        new_tab_ids = [tab.tab_id for tab in self.course.tabs]
        assert new_tab_ids == reordered_tab_ids

    def test_reorder_tabs_invalid_tab(self):
        """Test re-ordering of tabs with invalid tab"""

        invalid_tab_ids = ['courseware', 'invalid_tab_id']

        # post the request
        resp = self.client.ajax_post(
            self.url,
            data={'tabs': [{'tab_id': tab_id} for tab_id in invalid_tab_ids]},
        )
        self.check_invalid_tab_id_response(resp)

    def check_toggle_tab_visiblity(self, tab_type, new_is_hidden_setting):
        """Helper method to check changes in tab visibility"""

        # find the tab
        old_tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)

        # visibility should be different from new setting
        assert old_tab.is_hidden != new_is_hidden_setting

        # post the request
        resp = self.client.ajax_post(
            self.url,
            data=json.dumps({
                'tab_id_locator': {'tab_id': old_tab.tab_id},
                'is_hidden': new_is_hidden_setting,
            }),
        )
        assert resp.status_code == 204

        # reload the course and verify the new visibility setting
        self.reload_course()
        new_tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)
        assert new_tab.is_hidden == new_is_hidden_setting

    def test_toggle_tab_visibility(self):
        """Test toggling of tab visibility"""
        self.check_toggle_tab_visiblity('wiki', False)
        self.check_toggle_tab_visiblity('wiki', True)

    def test_toggle_invalid_tab_visibility(self):
        """Test toggling visibility of an invalid tab"""

        # post the request
        resp = self.client.ajax_post(
            self.url,
            data=json.dumps({
                'tab_id_locator': {'tab_id': 'invalid_tab_id'}
            }),
        )
        self.check_invalid_tab_id_response(resp)

    def test_tab_preview_html(self):
        """
        Verify that the static tab renders itself with the correct HTML
        """
        preview_url = f'/xblock/{self.test_tab.location}/{STUDENT_VIEW}'

        resp = self.client.get(preview_url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        resp_content = json.loads(resp.content.decode('utf-8'))
        html = resp_content['html']

        # Verify that the HTML contains the expected elements
        assert '<span class="action-button-text">Edit</span>' in html
        assert '<span class="sr">Duplicate this component</span>' in html
        assert '<span class="sr">Delete this component</span>' in html
        assert '<span data-tooltip="Drag to reorder" class="drag-handle action"></span>' in html


class PrimitiveTabEdit(ModuleStoreTestCase):
    """Tests for the primitive tab edit data manipulations"""

    def test_delete(self):
        """Test primitive tab deletion."""
        course = CourseFactory.create()
        with self.assertRaises(ValueError):  # noqa: PT027
            tabs.primitive_delete(course, 0)
        with self.assertRaises(IndexError):  # noqa: PT027
            tabs.primitive_delete(course, 6)

        assert course.tabs[1] != {'type': 'dates', 'name': 'Dates'}
        tabs.primitive_delete(course, 1)
        assert {'type': 'progress'} not in course.tabs
        # Check that dates has shifted up
        assert course.tabs[1] == {'type': 'dates', 'name': 'Dates'}

    def test_insert(self):
        """Test primitive tab insertion."""
        course = CourseFactory.create()
        tabs.primitive_insert(course, 2, 'pdf_textbooks', 'aname')
        assert course.tabs[2] == {'type': 'pdf_textbooks', 'name': 'aname'}
        with self.assertRaises(ValueError):  # noqa: PT027
            tabs.primitive_insert(course, 0, 'pdf_textbooks', 'aname')
        with self.assertRaises(ValueError):  # noqa: PT027
            tabs.primitive_insert(course, 3, 'static_tab', 'aname')

    def test_save(self):
        """Test course saving."""
        course = CourseFactory.create()
        tabs.primitive_insert(course, 3, 'pdf_textbooks', 'aname')
        course2 = modulestore().get_course(course.id)
        assert course2.tabs[3] == {'type': 'pdf_textbooks', 'name': 'aname'}
