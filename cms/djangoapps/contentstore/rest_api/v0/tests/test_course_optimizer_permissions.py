"""
Integration tests verifying authz permissions for v0 course optimizer REST API views.
"""
from unittest.mock import patch

from django.urls import reverse
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_EDITOR, COURSE_STAFF

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin

VIEWS_MODULE = 'cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer'


class CourseOptimizerV0AuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Integration tests for v0 course optimizer API authz permissions.

    All endpoints require courses.edit_course_content.
    """

    def setUp(self):
        super().setUp()
        self.link_check_url = reverse(
            'cms.djangoapps.contentstore:v0:link_check',
            kwargs={'course_id': self.course.id},
        )
        self.link_check_status_url = reverse(
            'cms.djangoapps.contentstore:v0:link_check_status',
            kwargs={'course_id': self.course.id},
        )
        self.rerun_link_update_url = reverse(
            'cms.djangoapps.contentstore:v0:rerun_link_update',
            kwargs={'course_id': self.course.id},
        )
        self.rerun_link_update_status_url = reverse(
            'cms.djangoapps.contentstore:v0:rerun_link_update_status',
            kwargs={'course_id': self.course.id},
        )

        prev_run_links_patcher = patch(
            f'{VIEWS_MODULE}.enable_course_optimizer_check_prev_run_links',
            return_value=True,
        )
        prev_run_links_patcher.start()
        self.addCleanup(prev_run_links_patcher.stop)

    # --- LinkCheckView (POST) ---

    @patch(f'{VIEWS_MODULE}.check_broken_links')
    def test_editor_can_start_link_check(self, mock_task):
        """Test that a course editor can start a link check."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.link_check_url)
        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    @patch(f'{VIEWS_MODULE}.check_broken_links')
    def test_staff_can_start_link_check(self, mock_task):
        """Test that course staff can start a link check."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.post(self.link_check_url)
        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    @patch(f'{VIEWS_MODULE}.check_broken_links')
    def test_auditor_cannot_start_link_check(self, mock_task):
        """Test that a course auditor cannot start a link check."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.link_check_url)
        assert resp.status_code == 403
        mock_task.delay.assert_not_called()

    @patch(f'{VIEWS_MODULE}.check_broken_links')
    def test_unauthorized_cannot_start_link_check(self, mock_task):
        """Test that a user without a role in the course cannot start a link check."""
        resp = self.unauthorized_client.post(self.link_check_url)
        assert resp.status_code == 403
        mock_task.delay.assert_not_called()

    # --- LinkCheckStatusView (GET) ---

    def test_editor_can_get_link_check_status(self):
        """Test that a course editor can read the link check status."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.link_check_status_url)
        assert resp.status_code == 200

    def test_auditor_cannot_get_link_check_status(self):
        """Test that a course auditor cannot read the link check status."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.link_check_status_url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_get_link_check_status(self):
        """Test that a user without a role in the course cannot read the link check status."""
        resp = self.unauthorized_client.get(self.link_check_status_url)
        assert resp.status_code == 403

    # --- RerunLinkUpdateView (POST) ---

    @patch(f'{VIEWS_MODULE}.update_course_rerun_links')
    def test_editor_can_start_rerun_link_update(self, mock_task):
        """Test that a course editor can start a rerun link update."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.rerun_link_update_url, data={'action': 'all'}, format='json')
        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    @patch(f'{VIEWS_MODULE}.update_course_rerun_links')
    def test_auditor_cannot_start_rerun_link_update(self, mock_task):
        """Test that a course auditor cannot start a rerun link update."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.post(self.rerun_link_update_url, data={'action': 'all'}, format='json')
        assert resp.status_code == 403
        mock_task.delay.assert_not_called()

    @patch(f'{VIEWS_MODULE}.update_course_rerun_links')
    def test_unauthorized_cannot_start_rerun_link_update(self, mock_task):
        """Test that a user without a role in the course cannot start a rerun link update."""
        resp = self.unauthorized_client.post(self.rerun_link_update_url, data={'action': 'all'}, format='json')
        assert resp.status_code == 403
        mock_task.delay.assert_not_called()

    # --- RerunLinkUpdateStatusView (GET) ---

    def test_editor_can_get_rerun_link_update_status(self):
        """Test that a course editor can read the rerun link update status."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.rerun_link_update_status_url)
        assert resp.status_code == 200

    def test_auditor_cannot_get_rerun_link_update_status(self):
        """Test that a course auditor cannot read the rerun link update status."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.rerun_link_update_status_url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_get_rerun_link_update_status(self):
        """Test that a user without a role in the course cannot read the rerun link update status."""
        resp = self.unauthorized_client.get(self.rerun_link_update_status_url)
        assert resp.status_code == 403

    # --- Superuser bypass ---

    @patch(f'{VIEWS_MODULE}.check_broken_links')
    def test_superuser_can_start_link_check(self, mock_task):
        """Test that a superuser bypasses the permission check and can start a link check."""
        resp = self.super_client.post(self.link_check_url)
        assert resp.status_code == 200
        mock_task.delay.assert_called_once()
