"""
Tests for the CORS CSRF middleware
"""

from unittest import mock
from unittest.mock import patch, Mock, call
import ddt
import pytest
from django.test import TestCase
from django.test.utils import override_settings
from django.core.exceptions import MiddlewareNotUsed, ImproperlyConfigured
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware

from ..middleware import CorsCSRFMiddleware, CsrfCrossDomainCookieMiddleware


SENTINEL = object()


@ddt.ddt
class TestCorsMiddlewareProcessRequest(TestCase):
    """
    Test processing a request through the middleware
    """
    def get_request(self, is_secure, http_referer):
        """
        Build a test request
        """
        request = Mock()
        request.META = {'HTTP_REFERER': http_referer}
        request.is_secure = lambda: is_secure
        request.get_host.return_value = 'foo.com'
        return request

    @override_settings(
        FEATURES={'ENABLE_CORS_HEADERS': True},
        CORS_CSRF_DETAIL_MONITORING=True
    )
    def setUp(self):
        super().setUp()
        self.middleware = CorsCSRFMiddleware(get_response=lambda request: None)

    def test_check_enable_cors_csrf_detail_monitoring(self):
        """
        Check that the CORS CSRF detail monitoring is enabled
        """
        assert self.middleware.enable_cors_csrf_detail_monitoring

    @ddt.data(True, False)
    def test_toggle_enable_cors_csrf_detail_monitoring(self, toggle_value):
        """
        Check that the CORS CSRF detail monitoring can be toggled
        """
        with override_settings(
            FEATURES={'ENABLE_CORS_HEADERS': True},
            CORS_CSRF_DETAIL_MONITORING=toggle_value
        ):
            middleware = CorsCSRFMiddleware(get_response=lambda request: None)
            assert middleware.enable_cors_csrf_detail_monitoring == toggle_value

    def check_not_enabled(self, request):
        """
        Check that the middleware does NOT process the provided request
        """
        with patch.object(CsrfViewMiddleware, 'process_view') as mock_method:
            res = self.middleware.process_view(request, None, None, None)

        assert res is None
        assert not mock_method.called

    def check_enabled(self, request):
        """
        Check that the middleware does process the provided request
        """
        def cb_check_req_is_secure_false(request, callback, args, kwargs):
            """
            Check that the request doesn't pass (yet) the `is_secure()` test
            """
            assert not request.is_secure()
            return SENTINEL

        with patch.object(CsrfViewMiddleware, 'process_view') as mock_method:
            mock_method.side_effect = cb_check_req_is_secure_false
            res = self.middleware.process_view(request, None, None, None)

        assert res is SENTINEL
        assert request.is_secure()

    @override_settings(CORS_ORIGIN_WHITELIST=[
        'https://foo.com', 'https://www.foo.com', 'https://learning.edge.foo.bar']
    )
    @ddt.data(
        'https://foo.com', 'https://foo.com/',
        'https://foo.com/bar/', 'https://foo.com/bar/baz/', 'https://www.foo.com/bar/baz/',
        'https://learning.edge.foo.bar', 'https://learning.edge.foo.bar/foo'
    )
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_enabled(self, http_referer, mock_set_custom_attribute):
        request = self.get_request(is_secure=True, http_referer=http_referer)
        self.check_enabled(request)
        assert mock_set_custom_attribute.call_count == 3

    @override_settings(
        FEATURES={'ENABLE_CORS_HEADERS': False},
        CORS_ORIGIN_WHITELIST=['https://foo.com']
    )
    def test_disabled_no_cors_headers(self):
        with pytest.raises(MiddlewareNotUsed):
            CorsCSRFMiddleware(get_response=lambda request: None)

    @override_settings(CORS_ORIGIN_WHITELIST=['https://bar.com'])
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_disabled_wrong_cors_domain(self, mock_set_custom_attribute):
        request = self.get_request(is_secure=True, http_referer='https://foo.com/bar')
        self.check_not_enabled(request)
        expected_calls = [
            call('tmp_cors_csrf.referer', 'https://foo.com/bar'),
            call('tmp_cors_csrf.host', 'foo.com'),
            call('tmp_cors_csrf.is_allowed', False)
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)
        assert mock_set_custom_attribute.call_count == 3

    @override_settings(CORS_ORIGIN_WHITELIST=['https://foo.com'])
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_disabled_wrong_cors_domain_reversed(self, mock_set_custom_attribute):
        request = self.get_request(is_secure=True, http_referer='https://bar.com/bar')
        self.check_not_enabled(request)

        # Check monitoring calls
        expected_calls = [
            call('tmp_cors_csrf.referer', 'https://bar.com/bar'),
            call('tmp_cors_csrf.host', 'foo.com'),
            call('tmp_cors_csrf.is_allowed', False)
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)
        assert mock_set_custom_attribute.call_count == 3

    @override_settings(CORS_ORIGIN_WHITELIST=['https://foo.com'])
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_disabled_http_request(self, mock_set_custom_attribute):
        request = self.get_request(is_secure=False, http_referer='https://foo.com/bar')
        self.check_not_enabled(request)

        # Check monitoring calls
        expected_calls = [
            call('tmp_cors_csrf.referer', 'https://foo.com/bar'),
            call('tmp_cors_csrf.host', 'foo.com'),
            call('tmp_cors_csrf.is_allowed', False)
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)
        assert mock_set_custom_attribute.call_count == 3

    @override_settings(CORS_ORIGIN_WHITELIST=['https://foo.com'])
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_cors_is_allowed(self, mock_set_custom_attribute):
        request = self.get_request(is_secure=True, http_referer='https://foo.com/bar')
        with patch.object(CsrfViewMiddleware, 'process_view') as mock_method:
            res = self.middleware.process_view(request, None, None, None)

        assert res is not None
        assert mock_method.called

        # Check monitoring calls
        expected_calls = [
            call('tmp_cors_csrf.referer', 'https://foo.com/bar'),
            call('tmp_cors_csrf.host', 'foo.com'),
            call('tmp_cors_csrf.is_allowed', True)
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)
        assert mock_set_custom_attribute.call_count == 3

    @override_settings(CORS_ORIGIN_WHITELIST=['https://foo.com'])
    @mock.patch('openedx.core.djangoapps.cors_csrf.middleware.set_custom_attribute')
    def test_disabled_http_referer(self, mock_set_custom_attribute):
        request = self.get_request(is_secure=True, http_referer='http://foo.com/bar')
        self.check_not_enabled(request)

        # Check monitoring calls
        expected_calls = [
            call('tmp_cors_csrf.referer', 'http://foo.com/bar'),
            call('tmp_cors_csrf.host', 'foo.com'),
            call('tmp_cors_csrf.is_allowed', False)
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)
        assert mock_set_custom_attribute.call_count == 3


@ddt.ddt
class TestCsrfCrossDomainCookieMiddleware(TestCase):
    """Tests for `CsrfCrossDomainCookieMiddleware`. """

    REFERER = 'https://www.example.com'
    COOKIE_NAME = 'shared-csrftoken'
    COOKIE_VALUE = 'abcd123'
    COOKIE_DOMAIN = '.edx.org'

    @override_settings(
        FEATURES={'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': True},
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_CSRF_DETAIL_MONITORING=True
    )
    def setUp(self):
        super().setUp()
        self.middleware = CsrfCrossDomainCookieMiddleware(get_response=lambda request: None)

    def test_check_enable_cors_csrf_detail_monitoring(self):
        """
        Check that the CORS CSRF detail monitoring is enabled
        """
        assert self.middleware.enable_cors_csrf_detail_monitoring

    @override_settings(FEATURES={'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': False})
    def test_disabled_by_feature_flag(self):
        with pytest.raises(MiddlewareNotUsed):
            CsrfCrossDomainCookieMiddleware(get_response=lambda request: None)

    @ddt.data('CROSS_DOMAIN_CSRF_COOKIE_NAME', 'CROSS_DOMAIN_CSRF_COOKIE_DOMAIN')
    def test_improperly_configured(self, missing_setting):
        settings = {
            'FEATURES': {'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': True},
            'CROSS_DOMAIN_CSRF_COOKIE_NAME': self.COOKIE_NAME,
            'CROSS_DOMAIN_CSRF_COOKIE_DOMAIN': self.COOKIE_DOMAIN
        }
        del settings[missing_setting]

        with override_settings(**settings):
            with pytest.raises(ImproperlyConfigured):
                CsrfCrossDomainCookieMiddleware(get_response=lambda request: None)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_not_secure(self):
        response = self._get_response(is_secure=False)
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_not_sending_csrf_token(self):
        response = self._get_response(csrf_cookie_used=False)
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_not_cross_domain_decorator(self):
        response = self._get_response(cross_domain_decorator=False)
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_WHITELIST=['other.example.com']
    )
    def test_skip_if_referer_not_whitelisted(self):
        response = self._get_response()
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN
    )
    def test_skip_if_not_cross_domain(self):
        response = self._get_response(
            referer="https://courses.edx.org/foo",
            host="courses.edx.org"
        )
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_no_referer(self):
        response = self._get_response(delete_referer=True)
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_referer_not_https(self):
        response = self._get_response(referer="http://www.example.com")
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_ALLOW_ALL=True
    )
    def test_skip_if_referer_no_protocol(self):
        response = self._get_response(referer="example.com")
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ALLOW_INSECURE=True
    )
    def test_skip_if_no_referer_insecure(self):
        response = self._get_response(delete_referer=True)
        self._assert_cookie_sent(response, False)

    @override_settings(
        CROSS_DOMAIN_CSRF_COOKIE_NAME=COOKIE_NAME,
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=COOKIE_DOMAIN,
        CORS_ORIGIN_WHITELIST=['https://www.example.com']
    )
    def test_set_cross_domain_cookie(self):
        response = self._get_response()
        self._assert_cookie_sent(response, True)

    def _get_response(self,
                      is_secure=True,
                      csrf_cookie_used=True,
                      cross_domain_decorator=True,
                      referer=None,
                      host=None,
                      delete_referer=False):
        """Process a request using the middleware. """
        request = Mock()
        request.META = {
            'HTTP_REFERER': (
                referer if referer is not None
                else self.REFERER
            )
        }
        request.is_secure = lambda: is_secure

        if host is not None:
            request.get_host = lambda: host

        if delete_referer:
            del request.META['HTTP_REFERER']

        if csrf_cookie_used:
            request.META['CSRF_COOKIE'] = self.COOKIE_VALUE

        if cross_domain_decorator:
            request.META['CROSS_DOMAIN_CSRF_COOKIE_USED'] = True

        return self.middleware.process_response(request, HttpResponse())

    def _assert_cookie_sent(self, response, is_set):
        """Check that the cross-domain CSRF cookie was sent. """
        if is_set:
            assert self.COOKIE_NAME in response.cookies
            cookie_header = str(response.cookies[self.COOKIE_NAME])

            expected = 'Set-Cookie: {name}={value}; Domain={domain};'.format(
                name=self.COOKIE_NAME,
                value=self.COOKIE_VALUE,
                domain=self.COOKIE_DOMAIN
            )
            assert expected in cookie_header
            assert 'Max-Age=31449600; Path=/; Secure' in cookie_header
        else:
            assert self.COOKIE_NAME not in response.cookies
