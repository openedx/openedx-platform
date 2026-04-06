"""
Tests for the `api_admin` api module.
"""

import json

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase
from rest_framework.reverse import reverse

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.api_admin.tests import factories
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class ApiAccessRequestViewTests(TestCase):
    """
    Tests for API access request api views.
    """
    password = 'test'

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        """
        Perform operations common to all test cases.
        """
        self.user = UserFactory.create(password=self.password)
        self.client.login(username=self.user.username, password=self.password)

        # Create APIAccessRequest records for testing.
        factories.ApiAccessRequestFactory.create_batch(5)
        factories.ApiAccessRequestFactory.create(user=self.user)

        self.url = reverse('api_admin:api:v1:list_api_access_request')

    def update_user_and_re_login(self, **kwargs):
        """
        Update attributes of currently logged in user.
        """
        self.client.logout()
        User.objects.filter(id=self.user.id).update(**kwargs)
        self.client.login(username=self.user.username, password=self.password)

    def _assert_api_access_request_response(self, api_response, expected_results_count):
        """
        Assert API response on `API Access Request` endpoint.
        """
        json_content = json.loads(api_response.content.decode('utf-8'))
        assert api_response.status_code == 200
        assert json_content['count'] == expected_results_count

    def test_list_view_for_not_authenticated_user(self):
        """
        Make sure API end point 'api_access_request' returns access denied if user is not authenticated.
        """
        self.update_user_and_re_login(is_staff=False)

        response = self.client.get(self.url)
        self._assert_api_access_request_response(api_response=response, expected_results_count=1)

    def test_list_view_for_non_staff_user(self):
        """
        Make sure API end point 'api_access_request' returns api access requests made only by the requesting user.
        """
        self.client.logout()

        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_list_view_for_staff_user(self):
        """
        Make sure API end point 'api_access_request' returns all api access requests to staff user.
        """
        self.update_user_and_re_login(is_staff=True)

        response = self.client.get(self.url)
        self._assert_api_access_request_response(api_response=response, expected_results_count=6)

    def test_filtering_for_staff_user(self):
        """
        Make sure that staff user can filter API Access Requests with username.
        """
        self.update_user_and_re_login(is_staff=True)

        response = self.client.get(self.url + f'?user__username={self.user.username}')
        self._assert_api_access_request_response(api_response=response, expected_results_count=1)

    def test_filtering_for_non_existing_user(self):
        """
        Make sure that 404 is returned if user does not exist against the username
        used for filtering.
        """
        self.update_user_and_re_login(is_staff=True)

        response = self.client.get(self.url + '?user__username={}'.format('non-existing-user-name'))
        assert response.status_code == 200
        self._assert_api_access_request_response(api_response=response, expected_results_count=0)
