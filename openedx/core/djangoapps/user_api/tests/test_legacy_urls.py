"""
Tests for account_settings_redirect_view in legacy_urls.py.
"""
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from openedx.core.djangoapps.user_api.legacy_urls import account_settings_redirect_view
from openedx.core.djangolib.testing.utils import skip_unless_lms


@override_settings(ACCOUNT_MICROFRONTEND_URL='https://account.example.com')
class AccountSettingsRedirectViewTests(TestCase):
    """
    Tests for the view that replaces the legacy /account/settings
    RedirectView.

    Any third-party-auth error message is intentionally *not* forwarded here:
    it's read by the Account MFE from ThirdPartyAuthErrorMessageView instead
    (see openedx/core/djangoapps/user_api/views.py), so this view is a plain,
    site-aware redirect.
    """

    def test_redirects_to_account_mfe(self):
        """Redirect to the configured Account MFE URL, with no query params."""
        request = RequestFactory().get('/account/settings')

        response = account_settings_redirect_view(request)

        assert response.status_code == 302
        assert response.url == 'https://account.example.com'

    @override_settings(ACCOUNT_MICROFRONTEND_URL='https://fallback.example.com')
    def test_uses_site_configuration_value_over_django_settings(self):
        """The Account MFE URL is re-evaluated per request, honoring site configuration."""
        request = RequestFactory().get('/account/settings')

        with_site_override = 'openedx.core.djangoapps.site_configuration.helpers.get_value'
        with mock.patch(with_site_override, return_value='https://site-specific.example.com'):
            response = account_settings_redirect_view(request)

        assert response.url == 'https://site-specific.example.com'

    @skip_unless_lms
    def test_account_and_account_settings_urls_route_here(self):
        """
        The legacy /account and /account/settings paths (with or without a
        trailing slash) reach this view. LMS-only: legacy_urls.py is wired
        into lms/urls.py, not cms/urls.py -- CMS has no account settings page.
        """
        for path in ('/account', '/account/', '/account/settings', '/account/settings/'):
            match = resolve(path)
            assert match.func == account_settings_redirect_view  # pylint: disable=comparison-with-callable
