"""
Helpers for API tests.
"""

import base64
import json
import re

from django.test import TestCase
from django.test.utils import override_settings

TEST_API_KEY = "test_api_key"


@override_settings(EDX_API_KEY=TEST_API_KEY)
class ApiTestCase(TestCase):
    """
    Parent test case for API workflow coverage
    """

    def basic_auth(self, username, password):
        """
        Returns a dictionary containing the http auth header with encoded username+password
        """
        return {'HTTP_AUTHORIZATION': b'Basic ' + base64.b64encode(b'%s:%s' % (username.encode(), password.encode()))}

    def request_with_auth(self, method, *args, **kwargs):
        """Issue a request to the given URI with the API key header"""
        return getattr(self.client, method)(*args, HTTP_X_EDX_API_KEY=TEST_API_KEY, **kwargs)

    def request_without_auth(self, method, *args, **kwargs):
        """
        Issue a request to the given URI without the API key header. This may be useful if you'll be calling
        an endpoint from javascript code and want to avoid exposing our API key.
        """
        return getattr(self.client, method)(*args, **kwargs)

    def get_json(self, *args, **kwargs):
        """Make a request with the given args and return the parsed JSON response"""
        resp = self.request_with_auth("get", *args, **kwargs)
        self.assertHttpOK(resp)
        assert resp['Content-Type'].startswith('application/json')
        return json.loads(resp.content.decode('utf-8'))

    def assertAllowedMethods(self, uri, expected_methods):
        """Assert that the allowed methods for the given URI match the expected list"""
        resp = self.request_with_auth("options", uri)
        self.assertHttpOK(resp)
        allow_header = resp.get("Allow")
        assert allow_header is not None
        allowed_methods = re.split('[^A-Z]+', allow_header)
        self.assertCountEqual(allowed_methods, expected_methods)  # noqa: PT009

    def assertSelfReferential(self, obj):
        """Assert that accessing the "url" entry in the given object returns the same object"""
        copy = self.get_json(obj["url"])
        assert obj == copy

    def assertHttpOK(self, response):
        """Assert that the given response has the status code 200"""
        assert response.status_code == 200

    def assertHttpCreated(self, response):
        """Assert that the given response has the status code 201"""
        assert response.status_code == 201

    def assertHttpNotAuthorized(self, response):
        """Assert that the given response has the status code 401"""
        assert response.status_code == 401

    def assertHttpForbidden(self, response):
        """Assert that the given response has the status code 403"""
        assert response.status_code == 403

    def assertHttpBadRequest(self, response):
        """Assert that the given response has the status code 400"""
        assert response.status_code == 400

    def assertHttpMethodNotAllowed(self, response):
        """Assert that the given response has the status code 405"""
        assert response.status_code == 405

    def assertAuthDisabled(self, method, uri):
        """
        Assert that the Django rest framework does not interpret basic auth
        headers for views exposed to anonymous users as an attempt to authenticate.

        """
        # Django rest framework interprets basic auth headers
        # as an attempt to authenticate with the API.
        # We don't want this for views available to anonymous users.
        basic_auth_header = "Basic " + base64.b64encode(b'username:password').decode('utf-8')
        response = getattr(self.client, method)(uri, HTTP_AUTHORIZATION=basic_auth_header)
        assert response.status_code != 403
