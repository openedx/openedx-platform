# pylint: disable=missing-docstring


from django.test import TestCase
from oauth2_provider.models import AccessToken, Application, RefreshToken

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests import factories


class TestClientFactory(TestCase):
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_client_factory(self):
        actual_application = factories.ApplicationFactory(user=self.user)
        expected_application = Application.objects.get(user=self.user)
        assert actual_application == expected_application


class TestAccessTokenFactory(TestCase):
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_access_token_client_factory(self):
        application = factories.ApplicationFactory(user=self.user)
        actual_access_token = factories.AccessTokenFactory(user=self.user, application=application)
        expected_access_token = AccessToken.objects.get(user=self.user)
        assert actual_access_token == expected_access_token


class TestRefreshTokenFactory(TestCase):
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_refresh_token_factory(self):
        application = factories.ApplicationFactory(user=self.user)
        access_token = factories.AccessTokenFactory(user=self.user, application=application)
        actual_refresh_token = factories.RefreshTokenFactory(
            user=self.user, application=application, access_token=access_token
        )
        expected_refresh_token = RefreshToken.objects.get(user=self.user, access_token=access_token)
        assert actual_refresh_token == expected_refresh_token
