"""
Test module to test the discussion enabled flag.
"""


import json

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_usage_url
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestDiscussionEnabled(CourseTestCase):
    """
    Test discussion enabled flags functionality in a Unit.
    """
    def setUp(self):
        super().setUp()
        self.course = self.get_test_course()
        self.course_usage_key = self.course.id.make_usage_key("course", self.course.id.run)
        self.non_staff_authed_user_client, _ = self.create_non_staff_authed_user_client()

    def get_test_course(self):
        """
        Create and return a test course
        """
        self.course = CourseFactory(
            org="SHIELD",
            number="SH101",
            name="Introduction to Avengers",
            run="2020_T2",
            modulestore=self.store
        )
        self.chapter = BlockFactory(
            parent_location=self.course.location,
            category="chapter",
            display_name="What is SHIELD?",
            modulestore=self.store
        )
        self.sequential = BlockFactory(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="HQ",
            modulestore=self.store
        )
        self.vertical = BlockFactory(
            parent_location=self.sequential.location,
            category="vertical",
            display_name="Triskelion",
            modulestore=self.store
        )
        self.vertical_1 = BlockFactory(
            parent_location=self.sequential.location,
            category="vertical",
            display_name="Helicarrier",
            modulestore=self.store
        )
        self.course.save()
        return self.course

    def _get_discussion_enabled_status(self, usage_key, client=None):
        """
        Issue a GET request to fetch value of discussion_enabled flag of xblock represented by param:usage_key
        """
        client = client if client is not None else self.client
        url = reverse_usage_url("xblock_handler", usage_key)
        resp = client.get(url, HTTP_ACCEPT="application/json")
        return resp

    def get_discussion_enabled_status(self, xblock, client=None):
        """
        Issue a GET request to fetch value of discussion_enabled flag of param:xblock's
        """
        resp = self._get_discussion_enabled_status(xblock.location, client=client)
        content = json.loads(resp.content.decode("utf-8"))
        return content.get("discussion_enabled", None)

    def set_discussion_enabled_status(self, xblock, value, client=None):
        """
        Issue a POST request to update value of discussion_enabled flag of param:xblock's
        """
        client = client if client is not None else self.client
        xblock_location = xblock.location
        url = reverse_usage_url("xblock_handler", xblock_location)
        resp = client.post(
            url,
            HTTP_ACCEPT="application/json",
            data=json.dumps({"metadata": {"discussion_enabled": value}}),
            content_type="application/json",
        )
        return resp

    def test_discussion_enabled_true_initially(self):
        """
        Tests discussion_enabled flag is True initially for vertical
        """
        self.assertTrue(self.get_discussion_enabled_status(self.vertical))
        self.assertTrue(self.get_discussion_enabled_status(self.vertical_1))

    def test_discussion_enabled_toggle(self):
        """
        Tests discussion_enabled can be toggled.
        """
        self.set_discussion_enabled_status(self.vertical, False)
        self.assertFalse(self.get_discussion_enabled_status(self.vertical))
        self.assertTrue(self.get_discussion_enabled_status(self.vertical_1))

    def test_non_course_author_cannot_get_or_set_discussion_enabled_flag(self):
        """
        Test non course author cannot get/set discussion_enabled flag
        """
        resp = self._get_discussion_enabled_status(self.course_usage_key, self.non_staff_authed_user_client)
        self.assertEqual(resp.status_code, 403)
        # Set call to the API with non authorised user should raise a 403
        resp = self.set_discussion_enabled_status(self.vertical, True, self.non_staff_authed_user_client)
        self.assertEqual(resp.status_code, 403)
