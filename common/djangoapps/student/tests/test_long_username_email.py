""" Test case for longer user emails."""
import json

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from openedx.core.djangoapps.user_api.accounts import USERNAME_BAD_LENGTH_MSG


@override_settings(RATELIMIT_ENABLE=False)
class TestLongUsernameEmail(TestCase):  # pylint: disable=missing-class-docstring

    def setUp(self):
        super().setUp()
        self.url = reverse('create_account')
        self.url_params = {
            'username': 'username',
            'email': 'foo_bar' + '@bar.com',
            'name': 'foo bar',
            'password': '123',
            'terms_of_service': 'true',
            'honor_code': 'true',
        }

    def test_long_username(self):
        """
        Test username cannot be more than 30 characters long.
        """

        self.url_params['username'] = 'username' * 4
        response = self.client.post(self.url, self.url_params)

        # Status code should be 400.
        assert response.status_code == 400

        obj = json.loads(response.content.decode('utf-8'))
        assert obj['username'][0]['user_message'] == USERNAME_BAD_LENGTH_MSG

    def test_spoffed_name(self):
        """
        Test name cannot contain html.
        """
        self.url_params['name'] = '<p style="font-size:300px; color:green;"></br>Name<input type="text"></br>Content spoof'  # pylint: disable=line-too-long
        response = self.client.post(self.url, self.url_params)
        assert response.status_code == 400

    def test_long_email(self):
        """
        Test email cannot be more than 254 characters long.
        """

        self.url_params['email'] = '{email}@bar.com'.format(email='foo_bar' * 36)
        response = self.client.post(self.url, self.url_params)

        # Assert that we get error when email has more than 254 characters.
        assert len(self.url_params['email']) > 254

        # Status code should be 400.
        assert response.status_code == 400

        obj = json.loads(response.content.decode('utf-8'))
        assert obj['email'][0]['user_message'] == 'Email cannot be more than 254 characters long'
