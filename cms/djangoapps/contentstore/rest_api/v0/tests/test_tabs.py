"""
Tests for the course tab API.
"""

import json
import random
from urllib.parse import urlencode

import ddt
from django.urls import reverse

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from xmodule.modulestore.tests.factories import BlockFactory
from xmodule.tabs import CourseTabList


@ddt.ddt
class TabsAPITests(CourseTestCase):
    """
    Test cases for Tabs (a.k.a Pages) page
    """

    def setUp(self):
        """
        Common setup for tests.
        """

        # call super class to setup course, etc.
        super().setUp()

        # Set the URLs for tests
        self.url = reverse(
            "cms.djangoapps.contentstore:v0:course_tab_list",
            kwargs={"course_id": self.course.id},
        )
        self.url_settings = reverse(
            "cms.djangoapps.contentstore:v0:course_tab_settings",
            kwargs={"course_id": self.course.id},
        )
        self.url_reorder = reverse(
            "cms.djangoapps.contentstore:v0:course_tab_reorder",
            kwargs={"course_id": self.course.id},
        )

        # add a static tab to the course, for code coverage
        self.test_tab = BlockFactory.create(
            parent_location=self.course.location,
            category="static_tab",
            display_name="Static_1",
        )
        self.reload_course()

    def check_invalid_response(self, resp):
        """
        Check the response is an error and return the developer message.
        """
        assert resp.status_code, 400
        resp_content = json.loads(resp.content)
        assert "developer_message" in resp_content
        return resp_content["developer_message"]

    def make_reorder_tabs_request(self, data):
        """
        Helper method to make a request for reordering tabs.

        Args:
            data (List): Data to send in the post request

        Returns:
            Response received from API.
        """
        return self.client.post(
            self.url_reorder,
            data=data,
            content_type="application/json",
        )

    def make_update_tab_request(self, tab_id_locator, data):
        """
        Helper method to make a request for hiding/showing tabs.

        Args:
            tab_id_locator (Dict): A dict containing the tab_id/tab_locator to update
            data (Dict): Data to send in the post request

        Returns:
            Response received from API.
        """
        return self.client.post(
            f"{self.url_settings}?{urlencode(tab_id_locator)}",
            data=data,
            content_type="application/json",
        )

    def test_reorder_static_tabs(self):
        """
        Test re-ordering of static tabs in a course.
        """

        # get the original tabs
        course_tabs = list(self.course.tabs)
        num_orig_tabs = len(self.course.tabs)

        # make sure we have enough tabs to play around with
        assert num_orig_tabs >= 5

        # Randomize the order of static tabs, leaving the rest intact
        course_tabs.sort(key=lambda tab: (100 + random.random()) if tab.type == 'static_tab' else tab.priority)

        tabs_data = [
            {'tab_locator': str(self.course.id.make_usage_key("static_tab", tab.url_slug))}
            for tab in course_tabs
            if tab.type == 'static_tab'
        ]
        # Remove one tab randomly. This shouldn't delete the tab.
        tabs_data.pop()

        # post the request with the reordered static tabs only
        resp = self.make_reorder_tabs_request(tabs_data)
        assert resp.status_code == 204

        # Reload the course and verify the new tab order
        self.reload_course()
        reordered_tab_ids = [tab.tab_id for tab in course_tabs]
        new_tab_ids = [tab.tab_id for tab in self.course.tabs]
        assert new_tab_ids == reordered_tab_ids

    def test_reorder_tabs_invalid_tab_ids(self):
        """
        Test re-ordering of tabs with invalid tab.
        """

        invalid_tab_ids = ["courseware", "info", "invalid_tab_id"]

        # post the request
        resp = self.make_reorder_tabs_request([{"tab_id": tab_id} for tab_id in invalid_tab_ids])
        self.check_invalid_response(resp)

    def test_reorder_tabs_invalid_tab_locators(self):
        """
        Test re-ordering of tabs with invalid tab.
        """

        invalid_tab_locators = ["invalid_tab_locator", "block-v1:test+test+test+type@static_tab+block@invalid"]

        # post the request
        resp = self.make_reorder_tabs_request([{"tab_locator": tab_id} for tab_id in invalid_tab_locators])
        self.check_invalid_response(resp)

    def check_toggle_tab_visibility(self, tab_type, new_is_hidden_setting):
        """
        Helper method to check changes in tab visibility.
        """
        old_tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)
        # visibility should be different from new setting
        assert old_tab.is_hidden != new_is_hidden_setting

        resp = self.make_update_tab_request({"tab_id": old_tab.tab_id}, {"is_hidden": new_is_hidden_setting})
        assert resp.status_code == 204, resp.content
        # reload the course and verify the new visibility setting
        self.reload_course()
        new_tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)
        assert new_tab.is_hidden == new_is_hidden_setting

    def test_toggle_tab_visibility(self):
        """
        Test that toggling the visibility via the API works.
        """
        self.check_toggle_tab_visibility("wiki", False)
        self.check_toggle_tab_visibility("wiki", True)

    def test_toggle_tab_visibility_fail(self):
        """
        Test that it isn't possible to toggle visibility of unsupported tabs
        """

        tab_type = "courseware"

        tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)

        assert not tab.is_hideable
        assert not tab.is_hidden

        resp = self.make_update_tab_request({"tab_id": tab.tab_id}, {"is_hidden": True})

        assert resp.status_code == 400
        error = self.check_invalid_response(resp)
        assert error["error"] == f"Tab of type {tab_type} can not be hidden"

        # Make sure the visibility wasn't affected
        self.reload_course()
        updated_tab = CourseTabList.get_tab_by_type(self.course.tabs, tab_type)
        assert not updated_tab.is_hidden

    @ddt.data(
        {"tab_id": "wiki", "tab_locator": "block-v1:test+test+test+type@static_tab+block@invalid"},
        {"tab_id": "invalid_tab_id"},
        {"tab_locator": "invalid_tab_locator"},
        {"tab_locator": "block-v1:test+test+test+type@static_tab+block@invalid"},
        {},
    )
    def test_toggle_invalid_tab_visibility(self, invalid_tab_locator):
        """
        Test toggling visibility of an invalid tab
        """

        # post the request
        resp = self.make_update_tab_request(invalid_tab_locator, {"is_hidden": False})
        self.check_invalid_response(resp)

    @ddt.data(
        dict(is_hidden=None),
        dict(is_hidden="abc"),
        {},
    )
    def test_toggle_tab_invalid_visibility(self, invalid_visibility):
        """
        Test toggling visibility of an invalid tab
        """

        # post the request
        resp = self.make_update_tab_request({"tab_id": "wiki"}, invalid_visibility)
        self.check_invalid_response(resp)
