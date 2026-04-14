"""
Tests for Calendar Sync views.
"""


import ddt
from django.test import TestCase
from django.urls import reverse

from openedx.features.calendar_sync.api import SUBSCRIBE, UNSUBSCRIBE
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

TEST_PASSWORD = 'test'


@ddt.ddt
class TestCalendarSyncView(SharedModuleStoreTestCase, TestCase):
    """Tests for the calendar sync view."""
    @classmethod
    def setUpClass(cls):
        """ Set up any course data """
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.user = self.create_user_for_course(self.course)
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.calendar_sync_url = reverse('openedx.calendar_sync', args=[self.course.id])

    @ddt.data(
        # Redirect on successful subscribe
        [{'tool_data': f"{{'toggle_data': '{SUBSCRIBE}'}}"}, 302, ''],
        # Redirect on successful unsubscribe
        [{'tool_data': f"{{'toggle_data': '{UNSUBSCRIBE}'}}"}, 302, ''],
        # 422 on unknown toggle_data
        [{'tool_data': "{{'toggle_data': '{}'}}".format('gibberish')}, 422,
            'Toggle data was not provided or had unknown value.'],
        # 422 on no toggle_data
        [{'tool_data': "{{'random_data': '{}'}}".format('gibberish')}, 422,
            'Toggle data was not provided or had unknown value.'],
        # 422 on no tool_data
        [{'nonsense': "{{'random_data': '{}'}}".format('gibberish')}, 422, 'Tool data was not provided.'],
    )
    @ddt.unpack
    def test_course_dates_fragment(self, data, expected_status_code, contained_text):
        response = self.client.post(self.calendar_sync_url, data)
        assert response.status_code == expected_status_code
        assert contained_text in str(response.content)
