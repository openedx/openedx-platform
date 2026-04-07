"""
Tests for third party auth middleware
"""


from unittest import mock

from django.contrib.messages.middleware import MessageMiddleware
from django.http import HttpResponse
from django.test.client import RequestFactory
from requests.exceptions import HTTPError

from common.djangoapps.student.helpers import get_next_url_for_login_page
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
