"""
Integration tests verifying authz permissions for discussions views.
"""
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class DiscussionsAuthzTest(CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """
    Integration tests for discussions views authz permissions.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.settings_url = reverse(
            'discussions-settings',
            kwargs={'course_key_string': str(self.course.id)},
        )
        self.providers_url = reverse(
            'discussions-providers',
            kwargs={'course_key_string': str(self.course.id)},
        )

    # --- GET settings - requires courses.view_pages_and_resources ---

    def test_staff_can_get_settings(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.settings_url)
        assert resp.status_code == 200

    def test_auditor_can_get_settings(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.settings_url)
        assert resp.status_code == 200

    def test_limited_staff_cannot_get_settings(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.settings_url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_get_settings(self):
        resp = self.unauthorized_client.get(self.settings_url)
        assert resp.status_code == 403

    # --- POST settings - requires courses.manage_pages_and_resources ---

    def test_staff_can_post_settings(self):
        """Asserts not-403 rather than 200 because the empty payload may fail validation."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.post(self.settings_url, data={}, format='json')
        assert resp.status_code != 403

    def test_auditor_cannot_post_settings(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.settings_url, data={}, format='json')
        assert resp.status_code == 403

    def test_unauthorized_cannot_post_settings(self):
        resp = self.unauthorized_client.post(self.settings_url, data={}, format='json')
        assert resp.status_code == 403

    # --- GET providers - requires courses.view_pages_and_resources ---

    def test_staff_can_get_providers(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.providers_url)
        assert resp.status_code == 200

    def test_auditor_can_get_providers(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.providers_url)
        assert resp.status_code == 200

    def test_unauthorized_cannot_get_providers(self):
        resp = self.unauthorized_client.get(self.providers_url)
        assert resp.status_code == 403

    # --- Superuser bypass ---

    def test_superuser_can_get_settings(self):
        superuser = UserFactory(is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        resp = client.get(self.settings_url)
        assert resp.status_code == 200
