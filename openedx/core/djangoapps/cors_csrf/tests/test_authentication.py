"""Tests for the CORS CSRF version of Django Rest Framework's SessionAuthentication."""


from unittest.mock import patch

from django.conf import settings
from django.middleware.csrf import get_token
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from rest_framework.exceptions import PermissionDenied

from ..authentication import SessionAuthenticationCrossDomainCsrf


# A class to pass into django.middleware.csrf.get_token() so we can easily get a valid CSRF token to use.
class FakeRequest:
    META = {}


class CrossDomainAuthTest(TestCase):
    """Tests for the CORS CSRF version of Django Rest Framework's SessionAuthentication. """

    URL = "/dummy_url"
    REFERER = "https://www.edx.org"

    def setUp(self):
        super().setUp()
        self.auth = SessionAuthenticationCrossDomainCsrf()
        self.csrf_token = get_token(FakeRequest())

    def test_perform_csrf_referer_check(self):
        request = self._fake_request()
        with self.assertRaisesRegex(PermissionDenied, 'CSRF'):
            self.auth.enforce_csrf(request)

    @patch.dict(settings.FEATURES, {
        'ENABLE_CORS_HEADERS': True,
        'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': True
    })
    @override_settings(
        CORS_ORIGIN_WHITELIST=["https://www.edx.org"],
        CROSS_DOMAIN_CSRF_COOKIE_NAME="prod-edx-csrftoken",
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=".edx.org"
    )
    def test_skip_csrf_referer_check(self):
        request = self._fake_request()
        result = self.auth.enforce_csrf(request)
        assert result is None
        assert request.is_secure()

    def _fake_request(self):
        """Construct a fake request with a referer and CSRF token over a secure connection. """
        factory = RequestFactory()
        factory.cookies[settings.CSRF_COOKIE_NAME] = self.csrf_token
        request = factory.post(
            self.URL,
            HTTP_REFERER=self.REFERER,
            HTTP_X_CSRFTOKEN=self.csrf_token
        )
        request.is_secure = lambda: True

        # The way we're testing this skips django.middleware.csrf's process_request, which copies this from the cookie
        request.META['CSRF_COOKIE'] = self.csrf_token
        return request
