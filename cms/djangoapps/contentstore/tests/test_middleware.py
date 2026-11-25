"""
Tests for Studio's middleware.
"""

import ddt
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from cms.djangoapps.contentstore.middleware import LoginRedirectToUnauthorizedMiddleware
from common.djangoapps.student.tests.factories import UserFactory

JSON_ACCEPT = 'application/json, text/plain, */*'
HTML_ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
USAGE_KEY = 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@abc123'


@ddt.ddt
class LoginRedirectToUnauthorizedMiddlewareTest(TestCase):
    """
    Tests for LoginRedirectToUnauthorizedMiddleware.
    """


    def setUp(self):
        super().setUp()
        self.outline_url = reverse('xblock_outline_handler', kwargs={'usage_key_string': USAGE_KEY})

    @ddt.data(JSON_ACCEPT, 'application/json', '*/*')
    def test_anonymous_script_request_gets_401(self, accept):
        response = self.client.get(self.outline_url, HTTP_ACCEPT=accept)

        assert response.status_code == 401
        assert not response.has_header('Location')

    @ddt.data(HTML_ACCEPT, 'text/html')
    def test_anonymous_page_request_is_redirected_to_login(self, accept):
        response = self.client.get(self.outline_url, HTTP_ACCEPT=accept)

        assert response.status_code == 302
        assert response['Location'].startswith('/login/?next=')

    def test_anonymous_script_post_gets_401(self):
        url = reverse('xblock_handler', kwargs={'usage_key_string': USAGE_KEY})

        response = self.client.post(url, data='{}', content_type='application/json', HTTP_ACCEPT=JSON_ACCEPT)

        assert response.status_code == 401

    @ddt.data('https://studio.example.com/login/', 'login')
    def test_login_url_setting_forms_are_recognised(self, login_url):
        with override_settings(LOGIN_URL=login_url):
            response = self.client.get(self.outline_url, HTTP_ACCEPT=JSON_ACCEPT)

        assert response.status_code == 401

    def test_anonymous_request_without_accept_header_is_redirected_to_login(self):
        response = self.client.get(self.outline_url)

        assert response.status_code == 302
        assert response['Location'].startswith('/login/?next=')

    def test_authenticated_request_is_not_replaced(self):
        request = RequestFactory().get('/some/path', HTTP_ACCEPT=JSON_ACCEPT)
        request.user = UserFactory.create()
        view_response = redirect_to_login('/some/path')
        middleware = LoginRedirectToUnauthorizedMiddleware(lambda _request: view_response)

        assert middleware(request) is view_response

    @ddt.data(
        HttpResponseRedirect('/home/'),
        HttpResponseRedirect('https://example.com/login/'),
        HttpResponse(status=403),
        HttpResponse(status=200),
    )
    def test_other_responses_pass_through(self, view_response):
        request = RequestFactory().get('/some/path', HTTP_ACCEPT=JSON_ACCEPT)
        middleware = LoginRedirectToUnauthorizedMiddleware(lambda _request: view_response)

        assert middleware(request) is view_response

    def test_login_redirect_on_request_host_is_replaced(self):
        request = RequestFactory().get('/some/path', HTTP_ACCEPT=JSON_ACCEPT)
        view_response = HttpResponseRedirect(f'http://{request.get_host()}/login/?next=/some/path')
        middleware = LoginRedirectToUnauthorizedMiddleware(lambda _request: view_response)

        assert middleware(request).status_code == 401
