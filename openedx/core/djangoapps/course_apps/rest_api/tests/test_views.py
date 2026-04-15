"""
Tests for the rest api for course apps.
"""
import contextlib
import json
from unittest import mock

import ddt
from django.test import Client
from django.urls import reverse

from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ...tests.utils import make_test_course_app


@skip_unless_cms
@ddt.ddt
class CourseAppsRestApiTest(SharedModuleStoreTestCase):
    """
    Tests for the rest api for course apps.
    """

    def setUp(self):
        super().setUp()
        store = ModuleStoreEnum.Type.split
        self.course = CourseFactory.create(default_store=store)
        self.instructor = UserFactory()
        self.user = UserFactory()
        self.client = Client()
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.url = reverse("course_apps_api:v1:course_apps", kwargs=dict(course_id=self.course.id))
        CourseStaffRole(self.course.id).add_users(self.instructor)

    @contextlib.contextmanager
    def _setup_plugin_mock(self):
        """
        Context manager that patches get_available_plugins to return test plugins.
        """
        patcher = mock.patch("openedx.core.djangoapps.course_apps.plugins.PluginManager.get_available_plugins")
        mock_get_available_plugins = patcher.start()
        mock_get_available_plugins.return_value = {
            "app1": make_test_course_app(app_id="app1", name="App One", is_available=True),
            "app2": make_test_course_app(app_id="app2", name="App Two", is_available=True),
            "app3": make_test_course_app(app_id="app3", name="App Three", is_available=False),
            "app4": make_test_course_app(app_id="app4", name="App Four", is_available=True),
        }
        yield
        patcher.stop()

    def test_only_show_available_apps(self):
        """
        Tests that only available apps show up in the API response.
        """
        with self._setup_plugin_mock():
            response = self.client.get(self.url)
        data = json.loads(response.content.decode("utf-8"))
        # Make sure that "app3" doesn't show up since it isn't available.
        assert len(data) == 3
        assert all(app["id"] != "app3" for app in data)

    @ddt.data(True, False)
    def test_update_status_success(self, enabled):
        """
        Tests successful update response
        """
        with self._setup_plugin_mock():
            response = self.client.patch(self.url, {"id": "app1", "enabled": enabled}, content_type="application/json")
        data = json.loads(response.content.decode("utf-8"))
        assert "enabled" in data
        assert data["enabled"] == enabled
        assert data["id"] == "app1"

    def test_update_invalid_enabled(self):
        """
        Tests that an invalid or missing enabled value raises an error response.
        """
        with self._setup_plugin_mock():
            response = self.client.patch(self.url, {"id": "app1"}, content_type="application/json")
        assert response.status_code == 400
        data = json.loads(response.content.decode("utf-8"))
        assert "developer_message" in data
        # Check that there is an issue with the enabled field
        assert "enabled" in data["developer_message"]

    @ddt.data("non-app", None, "app3")
    def test_update_invalid_appid(self, app_id):
        """
        Tests that an invalid appid raises an error response"""
        with self._setup_plugin_mock():
            response = self.client.patch(self.url, {"id": app_id, "enabled": True}, content_type="application/json")
        assert response.status_code == 400
        data = json.loads(response.content.decode("utf-8"))
        assert "developer_message" in data
        # Check that there is an issue with the ID field
        assert "id" in data["developer_message"]
