"""
Tests for third party auth middleware
"""


from unittest import mock

import ddt
from django.contrib.messages.middleware import MessageMiddleware
from django.http import HttpResponse
from django.test.client import RequestFactory
from requests.exceptions import HTTPError
from social_core import exceptions as social_exceptions

from common.djangoapps.student.helpers import get_next_url_for_login_page
from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.middleware import ExceptionMiddleware
from common.djangoapps.third_party_auth.tests.testutil import TestCase
from openedx.core.djangolib.testing.utils import skip_unless_lms


class ThirdPartyAuthMiddlewareTestCase(TestCase):
    """Tests that ExceptionMiddleware is correctly redirected"""

    @skip_unless_lms
    @mock.patch('django.conf.settings.MESSAGE_STORAGE', 'django.contrib.messages.storage.cookie.CookieStorage')
    def test_http_exception_redirection(self):
        """
        Test ExceptionMiddleware is correctly redirected to login page
        when PSA raises HttpError exception.
        """

        request = RequestFactory().get("dummy_url")
        next_url = get_next_url_for_login_page(request)
        login_url = '/login?next=' + next_url
        request.META['HTTP_REFERER'] = 'http://example.com:8000/login'
        exception = HTTPError()
        exception.response = HttpResponse(status=502)

        # Add error message for error in auth pipeline
        MessageMiddleware(get_response=lambda request: None).process_request(request)
        response = ExceptionMiddleware(get_response=lambda request: None).process_exception(
            request, exception
        )
        target_url = response.url

        assert response.status_code == 302
        assert target_url.endswith(login_url)


@ddt.ddt
class ExceptionMiddlewareAccountSettingsDispatchTestCase(TestCase):
    """
    Tests that ExceptionMiddleware.get_redirect_uri() dispatches to the URL
    registered in AUTH_DISPATCH_URLS for the current auth_entry, and that it
    no longer needs to duplicate the error message in a custom session key:
    SocialAuthExceptionMiddleware.process_exception (the parent class)
    already leaves it in the Django messages framework for any
    SocialAuthBaseException, tagged 'social-auth <backend name>'. The
    Account MFE reads that message via
    openedx.core.djangoapps.user_api.views.ThirdPartyAuthErrorMessageView.
    """

    def _build_request(self, auth_entry=pipeline.AUTH_ENTRY_ACCOUNT_SETTINGS):
        """Build a fake request with session and backend for testing TPA error handling."""
        request = RequestFactory().get('/auth/login/tpa-saml/')
        request.session = {}
        request.session[pipeline.AUTH_ENTRY_KEY] = auth_entry

        class FakeBackend:
            name = 'tpa-saml'

        request.backend = FakeBackend()
        request.social_strategy = mock.MagicMock()
        request.social_strategy.setting.return_value = None
        return request

    @ddt.data(
        social_exceptions.AuthAlreadyAssociated,
        social_exceptions.AuthCanceled,
        social_exceptions.AuthFailed,
        social_exceptions.AuthTokenError,
        social_exceptions.AuthStateMissing,
        social_exceptions.AuthStateForbidden,
        social_exceptions.AuthTokenRevoked,
        social_exceptions.AuthUnreachableProvider,
        social_exceptions.InvalidEmail,
    )
    def test_dispatches_to_account_settings_url_for_any_tpa_exception(self, exception_class):
        """The redirect target is /account/settings for the account_settings flow, regardless of exception type."""
        request = self._build_request()

        redirect_uri = ExceptionMiddleware(get_response=lambda r: None).get_redirect_uri(
            request, exception_class('tpa-saml')
        )

        assert redirect_uri == pipeline.AUTH_DISPATCH_URLS[pipeline.AUTH_ENTRY_ACCOUNT_SETTINGS]

    def test_dispatches_elsewhere_outside_account_settings_entry(self):
        """AUTH_DISPATCH_URLS is keyed by auth_entry, not by exception type."""
        request = self._build_request(auth_entry=pipeline.AUTH_ENTRY_LOGIN)

        redirect_uri = ExceptionMiddleware(get_response=lambda r: None).get_redirect_uri(
            request, social_exceptions.AuthAlreadyAssociated('tpa-saml')
        )

        assert redirect_uri == pipeline.AUTH_DISPATCH_URLS[pipeline.AUTH_ENTRY_LOGIN]

    @skip_unless_lms
    @ddt.data(
        social_exceptions.AuthAlreadyAssociated,
        social_exceptions.AuthCanceled,
        social_exceptions.AuthFailed,
        social_exceptions.AuthTokenError,
        social_exceptions.AuthStateMissing,
        social_exceptions.AuthStateForbidden,
        social_exceptions.AuthTokenRevoked,
        social_exceptions.AuthUnreachableProvider,
    )
    def test_process_exception_leaves_social_auth_tagged_message_for_mfe(self, exception_class):
        """
        End to end: process_exception (the parent implementation) must still
        queue a Django message tagged with 'social-auth' for the Account MFE
        to read, since we no longer save it ourselves. Covers every
        recognized third-party-auth exception, not just AuthAlreadyAssociated.
        """
        request = self._build_request()
        MessageMiddleware(get_response=lambda request: None).process_request(request)
        exception = exception_class('tpa-saml')

        ExceptionMiddleware(get_response=lambda r: None).process_exception(request, exception)

        queued_messages = list(request._messages)  # pylint: disable=protected-access
        assert len(queued_messages) == 1
        assert queued_messages[0].extra_tags.split() == ['social-auth', 'tpa-saml']
        assert str(queued_messages[0]) == str(exception)

    @skip_unless_lms
    def test_process_exception_does_not_queue_message_for_unrecognized_exception(self):
        """
        Non-SocialAuthBaseException errors are untouched by
        SocialAuthExceptionMiddleware.process_exception -- confirms we're not
        accidentally tagging unrelated exceptions as TPA errors.
        """
        request = self._build_request()
        MessageMiddleware(get_response=lambda request: None).process_request(request)

        result = ExceptionMiddleware(get_response=lambda r: None).process_exception(request, ValueError('boom'))

        assert result is None
        assert not list(request._messages)  # pylint: disable=protected-access
