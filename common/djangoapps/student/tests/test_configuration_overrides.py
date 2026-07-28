"""
Test for user creation from sites with configuration overrides.
"""


import json
from unittest import mock

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.models import UserSignupSource

FAKE_SITE = {
    "SITE_NAME": "openedx.localhost",
    "university": "fakeuniversity",
    "course_org_filter": "fakeorg",
    "platform_name": "Fake University",
    "PLATFORM_NAME": "Fake University",
    "email_from_address": "no-reply@fakeuniversity.com",
    "REGISTRATION_EXTRA_FIELDS": {
        "address1": "required",
        "city": "required",
        "state": "required",
        "country": "required",
        "company": "required",
        "title": "required"
    },
    "extended_profile_fields": [
        "address1", "state", "company", "title"
    ]
}


def fake_site_name(name, default=None):
    """
    Method for getting site name for a fake site.
    """
    if name == 'SITE_NAME':
        return 'openedx.localhost'
    else:
        return default


def fake_get_value(name, default=None):
    """
    Method for getting configuration override values for a fake site.
    """
    return FAKE_SITE.get(name, default)


# HIBP settings are only defined in lms envs but needed for common tests.
@override_settings(ENABLE_AUTHN_REGISTER_HIBP_POLICY=False)
class TestSite(TestCase):
    """Test for Account Creation from white labeled Sites"""
    def setUp(self):
        super().setUp()
        self.username = "test_user"
        self.url = reverse("create_account")
        self.params = {
            "username": self.username,
            "email": "test@example.org",
            "password": "testpass",
            "name": "Test User",
            "honor_code": "true",
            "terms_of_service": "true",
        }
        self.extended_params = dict(list(self.params.items()) + list({
            "address1": "foo",
            "city": "foo",
            "state": "foo",
            "country": "US",
            "company": "foo",
            "title": "foo"
        }.items()))

    @mock.patch("openedx.core.djangoapps.site_configuration.helpers.get_value", fake_site_name)
    def test_user_signup_source(self):
        """
        Test to create a user from a site with configuration overrides and see that its record has been
        saved in the UserSignupSource Table.
        """
        response = self.client.post(self.url, self.params)
        assert response.status_code == 200
        assert len(UserSignupSource.objects.filter(site='openedx.localhost')) > 0

    def test_user_signup_from_non_configured_site(self):
        """
        Test to create a user from a site without any configuration override. The record should not be saved
        in UserSignupSource Table.
        """
        response = self.client.post(self.url, self.params)
        assert response.status_code == 200
        assert len(UserSignupSource.objects.filter(site='openedx.localhost')) == 0

    @mock.patch("openedx.core.djangoapps.site_configuration.helpers.get_value", fake_get_value)
    def test_user_signup_missing_enhanced_profile(self):
        """
        Test to create a user from a site with configuration overrides but don't provide any overrides for
        profile information.
        """
        response = self.client.post(self.url, self.params)
        assert response.status_code == 400

    @mock.patch("openedx.core.djangoapps.site_configuration.helpers.get_value", fake_get_value)
    def test_user_signup_including_enhanced_profile(self):
        """
        Test to create a user from a site with configuration overrides with overrides for
        profile information.
        """
        response = self.client.post(self.url, self.extended_params)
        assert response.status_code == 200
        user = User.objects.get(username=self.username)
        meta = json.loads(user.profile.meta)
        assert meta['address1'] == 'foo'
        assert meta['state'] == 'foo'
        assert meta['company'] == 'foo'
        assert meta['title'] == 'foo'
