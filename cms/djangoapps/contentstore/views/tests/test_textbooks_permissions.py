"""
Integration tests verifying authz permissions for legacy textbook handler views.
"""
import json

from django.test import Client
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin


class TextbooksListHandlerAuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Integration tests for textbooks_list_handler authz permissions.

    Uses Django test Client (not DRF APIClient) because textbooks_list_handler
    is a function-based view with @login_required.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse_course_url('textbooks_list_handler', self.course.id)

        self.staff_client = Client()
        self.staff_client.login(username=self.authorized_user.username, password=self.password)

        self.unauth_client = Client()
        self.unauth_client.login(username=self.unauthorized_user.username, password=self.password)

    # --- GET (JSON) - requires courses.view_pages_and_resources ---

    def test_staff_can_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200

    def test_auditor_can_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200

    def test_limited_staff_cannot_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403

    def test_unauthorized_cannot_get(self):
        resp = self.unauth_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403

    # --- POST - requires courses.manage_pages_and_resources ---

    def test_staff_can_post(self):
        """Asserts not-403 rather than 200 because minimal payload may fail validation."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.staff_client.post(
            self.url,
            data=json.dumps({"tab_title": "Test", "chapters": []}),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        assert resp.status_code != 403

    def test_auditor_cannot_post(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.staff_client.post(
            self.url,
            data=json.dumps({"tab_title": "Test", "chapters": []}),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        assert resp.status_code == 403

    def test_unauthorized_cannot_post(self):
        resp = self.unauth_client.post(
            self.url,
            data=json.dumps({"tab_title": "Test", "chapters": []}),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        assert resp.status_code == 403

    # --- Superuser bypass ---

    def test_superuser_can_get(self):
        superuser = UserFactory(is_superuser=True, password=self.password)
        client = Client()
        client.login(username=superuser.username, password=self.password)
        resp = client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200


class TextbooksDetailHandlerAuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Integration tests for textbooks_detail_handler authz permissions.
    """

    def setUp(self):
        super().setUp()
        self.course.pdf_textbooks = [{"tab_title": "Test", "chapters": [], "id": "1test"}]
        self.save_course()
        self.url = f'/textbooks/{self.course.id}/1test'

        self.staff_client = Client()
        self.staff_client.login(username=self.authorized_user.username, password=self.password)

        self.unauth_client = Client()
        self.unauth_client.login(username=self.unauthorized_user.username, password=self.password)

    # --- GET - requires courses.view_pages_and_resources ---

    def test_staff_can_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200

    def test_auditor_can_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 200

    def test_limited_staff_cannot_get(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.staff_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403

    def test_unauthorized_cannot_get(self):
        resp = self.unauth_client.get(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403

    # --- DELETE - requires courses.manage_pages_and_resources ---

    def test_staff_can_delete(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.staff_client.delete(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 204

    def test_auditor_cannot_delete(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.staff_client.delete(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403

    def test_unauthorized_cannot_delete(self):
        resp = self.unauth_client.delete(self.url, HTTP_ACCEPT='application/json')
        assert resp.status_code == 403
