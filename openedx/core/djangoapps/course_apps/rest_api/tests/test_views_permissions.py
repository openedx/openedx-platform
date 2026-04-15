"""
Integration tests verifying authz permissions for CourseAppsView.
"""
import contextlib
from unittest import mock

from django.urls import reverse
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ...tests.utils import make_test_course_app


@skip_unless_cms
class CourseAppsAuthzTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    Integration tests for CourseAppsView authz permissions.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)

    def setUp(self):
        super().setUp()
        self.url = reverse('course_apps_api:v1:course_apps', kwargs={'course_id': self.course.id})

    @contextlib.contextmanager
    def _setup_plugin_mock(self):
        """Patch get_available_plugins to return a test plugin."""
        patcher = mock.patch('openedx.core.djangoapps.course_apps.plugins.PluginManager.get_available_plugins')
        mock_plugins = patcher.start()
        mock_plugins.return_value = {
            'app1': make_test_course_app(app_id='app1', name='App One', is_available=True),
        }
        yield
        patcher.stop()

    # --- GET - requires courses.view_pages_and_resources ---

    def test_staff_can_list_apps(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        with self._setup_plugin_mock():
            resp = self.authorized_client.get(self.url)
        assert resp.status_code == 200

    def test_auditor_can_list_apps(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        with self._setup_plugin_mock():
            resp = self.authorized_client.get(self.url)
        assert resp.status_code == 200

    def test_limited_staff_cannot_list_apps(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_list_apps(self):
        resp = self.unauthorized_client.get(self.url)
        assert resp.status_code == 403

    # --- PATCH - requires courses.manage_pages_and_resources ---

    def test_staff_can_toggle_app(self):
        """Asserts not-403 rather than 200 because the test plugin may not fully process the toggle."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        with self._setup_plugin_mock():
            resp = self.authorized_client.patch(
                self.url, data={'id': 'app1', 'enabled': True}, format='json',
            )
        assert resp.status_code != 403

    def test_auditor_cannot_toggle_app(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.patch(
            self.url, data={'id': 'app1', 'enabled': True}, format='json',
        )
        assert resp.status_code == 403

    def test_unauthorized_cannot_toggle_app(self):
        resp = self.unauthorized_client.patch(
            self.url, data={'id': 'app1', 'enabled': True}, format='json',
        )
        assert resp.status_code == 403

    # --- Superuser bypass ---

    def test_superuser_can_list_apps(self):
        superuser = UserFactory(is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        with self._setup_plugin_mock():
            resp = client.get(self.url)
        assert resp.status_code == 200
