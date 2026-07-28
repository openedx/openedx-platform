"""
Integration tests verifying authz permissions for v0 tabs REST API views.
"""
from urllib.parse import urlencode

from django.urls import reverse
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin


class TabsV0AuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Integration tests for v0 tabs API authz permissions.
    """

    def setUp(self):
        super().setUp()
        self.list_url = reverse(
            'cms.djangoapps.contentstore:v0:course_tab_list',
            kwargs={'course_id': self.course.id},
        )
        self.settings_url = reverse(
            'cms.djangoapps.contentstore:v0:course_tab_settings',
            kwargs={'course_id': self.course.id},
        )
        self.reorder_url = reverse(
            'cms.djangoapps.contentstore:v0:course_tab_reorder',
            kwargs={'course_id': self.course.id},
        )

    # --- CourseTabListView (GET) - requires courses.view_pages_and_resources ---

    def test_staff_can_list_tabs(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.list_url)
        assert resp.status_code == 200

    def test_auditor_can_list_tabs(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.list_url)
        assert resp.status_code == 200

    def test_limited_staff_cannot_list_tabs(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.list_url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_list_tabs(self):
        resp = self.unauthorized_client.get(self.list_url)
        assert resp.status_code == 403

    # --- CourseTabSettingsView (POST) - requires courses.manage_pages_and_resources ---

    def test_staff_can_update_tab_settings(self):
        """Asserts not-403 rather than 200 because the minimal payload may fail validation."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.post(
            f'{self.settings_url}?{urlencode({"tab_id": "wiki"})}',
            data={'is_hidden': True},
            format='json',
        )
        assert resp.status_code != 403

    def test_auditor_cannot_update_tab_settings(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(
            f'{self.settings_url}?{urlencode({"tab_id": "wiki"})}',
            data={'is_hidden': True},
            format='json',
        )
        assert resp.status_code == 403

    def test_unauthorized_cannot_update_tab_settings(self):
        resp = self.unauthorized_client.post(
            f'{self.settings_url}?{urlencode({"tab_id": "wiki"})}',
            data={'is_hidden': True},
            format='json',
        )
        assert resp.status_code == 403

    # --- CourseTabReorderView (POST) - requires courses.manage_pages_and_resources ---

    def test_staff_can_reorder_tabs(self):
        """Asserts not-403 rather than 200 because the empty tab list may fail validation."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.post(self.reorder_url, data=[], format='json')
        assert resp.status_code != 403

    def test_auditor_cannot_reorder_tabs(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.reorder_url, data=[], format='json')
        assert resp.status_code == 403

    def test_unauthorized_cannot_reorder_tabs(self):
        resp = self.unauthorized_client.post(self.reorder_url, data=[], format='json')
        assert resp.status_code == 403

    # --- Superuser bypass ---

    def test_superuser_can_list_tabs(self):
        superuser = UserFactory(is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        resp = client.get(self.list_url)
        assert resp.status_code == 200
