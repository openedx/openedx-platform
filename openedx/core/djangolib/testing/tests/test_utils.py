"""
Test that testing utils do what they say.
"""

from crum import set_current_request
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http.request import HttpRequest
from django.test import TestCase

from ..utils import get_mock_request

USER_MODEL = get_user_model()


class TestGetMockRequest(TestCase):
    """
    Validate the behavior of get_mock_request
    """
    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        self.addCleanup(set_current_request, None)

    def test_mock_request_is_request(self):
        request = get_mock_request(USER_MODEL())
        assert isinstance(request, HttpRequest)

    def test_user_is_attached_to_mock_request(self):
        user = USER_MODEL()
        request = get_mock_request(user)
        assert request.user is user

    def test_mock_request_without_user(self):
        request = get_mock_request()
        assert isinstance(request.user, AnonymousUser)
