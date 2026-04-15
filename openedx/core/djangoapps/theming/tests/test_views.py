"""
    Tests for comprehensive them
"""
from unittest.mock import patch

from django.conf import settings
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import GlobalStaffFactory, UserFactory
from openedx.core.djangoapps.theming.models import SiteTheme

THEMING_ADMIN_URL = '/theming/admin'
TEST_THEME_NAME = 'test-theme'
TEST_PASSWORD = 'Password1234'


class TestThemingViews(TestCase):
    """
    Test theming views.
    """

    def test_preview_theme_access(self):
        """
        Verify that users have the correct access to preview themes.
        """
        # Anonymous users get redirected to the login page
        response = self.client.get(THEMING_ADMIN_URL)
        # Studio login redirects to LMS login
        expected_target_status_code = 200 if settings.ROOT_URLCONF == 'lms.urls' else 302
        self.assertRedirects(
            response,
            '{login_url}?next={url}'.format(  # noqa: UP032
                login_url=settings.LOGIN_URL,
                url=THEMING_ADMIN_URL,
            ),
            target_status_code=expected_target_status_code
        )

        # Logged in non-global staff get a 404
        non_global_staff_user = UserFactory.create()
        self.client.login(username=non_global_staff_user.username, password=TEST_PASSWORD)
        response = self.client.get(THEMING_ADMIN_URL)
        assert response.status_code == 404

        # Global staff can access the page
        global_staff = GlobalStaffFactory()
        self.client.login(username=global_staff.username, password=TEST_PASSWORD)
        response = self.client.get(THEMING_ADMIN_URL)
        assert response.status_code == 200

    def test_preview_theme(self):
        """
        Verify that preview themes behaves correctly.
        """
        global_staff = GlobalStaffFactory()
        self.client.login(username=global_staff.username, password=TEST_PASSWORD)

        # First request a preview theme
        post_response = self.client.post(
            THEMING_ADMIN_URL,
            {
                'action': 'set_preview_theme',
                'preview_theme': TEST_THEME_NAME,
            }
        )
        self.assertRedirects(post_response, THEMING_ADMIN_URL)

        # Next request a page and verify that the correct theme has been chosen
        response = self.client.get(THEMING_ADMIN_URL)
        assert response.status_code == 200
        self.assertContains(
            response,
            f'<option value="{TEST_THEME_NAME}" selected=selected>'
        )

        # Request to reset the theme
        post_response = self.client.post(
            THEMING_ADMIN_URL,
            {
                'action': 'reset_preview_theme'
            }
        )
        self.assertRedirects(post_response, THEMING_ADMIN_URL)

        # Finally verify that the test theme is no longer selected
        response = self.client.get(THEMING_ADMIN_URL)
        assert response.status_code == 200
        self.assertContains(
            response,
            f'<option value="{TEST_THEME_NAME}">'
        )

    def test_asset_no_theme(self):
        """
        Fetch theme asset when no theme is set.
        """
        response = self.client.get(reverse("theming:openedx.theming.asset", kwargs={"path": "images/logo.png"}))
        assert response.status_code == 302
        assert response.url == "/static/images/logo.png"

    @override_settings(
        STORAGES={
            'staticfiles': {
                'BACKEND': 'openedx.core.storage.DevelopmentStorage'
            }
        }
    )
    def test_asset_with_theme(self):
        """
        Fetch theme asset when a theme is set.
        """
        SiteTheme.objects.create(site=Site.objects.get(), theme_dir_name="red-theme")
        with patch("openedx.core.storage.DevelopmentStorage.themed") as mock_is_themed:
            response = self.client.get(reverse("theming:openedx.theming.asset", kwargs={"path": "images/logo.png"}))
            mock_is_themed.assert_called_once_with("images/logo.png", "red-theme")
        assert response.status_code == 302
        assert response.url == "/static/red-theme/images/logo.png"
