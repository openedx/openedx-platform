"""Tests for CMS's requests to logs"""


from django.test import TestCase
from django.urls import reverse

from cms.djangoapps.contentstore.views.helpers import event as cms_user_track


class CMSLogTest(TestCase):
    """
    Tests that request to logs from CMS return 204s
    """

    def test_post_answers_to_log(self):
        """
        Checks that student answer requests submitted to cms's "/event" url
        via POST are correctly returned as 204s
        """
        requests = [
            {"event": "my_event", "event_type": "my_event_type", "page": "my_page"},
            {"event": "{'json': 'object'}", "event_type": chr(512), "page": "my_page"}
        ]
        for request_params in requests:
            response = self.client.post(reverse(cms_user_track), request_params)
            self.assertEqual(response.status_code, 204)  # noqa: PT009

    def test_get_answers_to_log(self):
        """
        Checks that student answer requests submitted to cms's "/event" url
        via GET are correctly returned as 204s
        """
        requests = [
            {"event": "my_event", "event_type": "my_event_type", "page": "my_page"},
            {"event": "{'json': 'object'}", "event_type": chr(512), "page": "my_page"}
        ]
        for request_params in requests:
            response = self.client.get(reverse(cms_user_track), request_params)
            self.assertEqual(response.status_code, 204)  # noqa: PT009
