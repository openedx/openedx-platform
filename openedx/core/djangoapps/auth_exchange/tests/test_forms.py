# pylint: disable=no-member
"""
Tests for OAuth token exchange forms
"""


import unittest

import httpretty
import social_django.utils as social_utils
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import TestCase
from django.test.client import RequestFactory
from social_django.models import Partial

from common.djangoapps.third_party_auth.tests.utils import (  # lint-amnesty, pylint: disable=line-too-long
    ThirdPartyOAuthTestMixinFacebook,
    ThirdPartyOAuthTestMixinGoogle,
)

from ..forms import AccessTokenExchangeForm
from .mixins import DOTAdapterMixin
from .utils import TPA_FEATURE_ENABLED, TPA_FEATURES_KEY, AccessTokenExchangeTestMixin


class AccessTokenExchangeFormTest(AccessTokenExchangeTestMixin):
    """
    Mixin that defines test cases for AccessTokenExchangeForm
    """
    def setUp(self):
        super().setUp()
        self.request = RequestFactory().post("dummy_url")
        redirect_uri = 'dummy_redirect_url'
        SessionMiddleware(get_response=lambda request: None).process_request(self.request)
        self.request.social_strategy = social_utils.load_strategy(self.request)
        # pylint: disable=no-member
        self.request.backend = social_utils.load_backend(self.request.social_strategy, self.BACKEND, redirect_uri)

    def tearDown(self):
        super().tearDown()
        Partial.objects.all().delete()

    def _assert_error(self, data, expected_error, expected_error_description):  # lint-amnesty, pylint: disable=arguments-differ
        form = AccessTokenExchangeForm(request=self.request, oauth2_adapter=self.oauth2_adapter, data=data)
        assert form.errors == {'error': expected_error, 'error_description': expected_error_description}

    def _assert_success(self, data, expected_scopes, expected_logged_in_user=None):
        form = AccessTokenExchangeForm(request=self.request, oauth2_adapter=self.oauth2_adapter, data=data)
        assert form.is_valid()
        assert form.cleaned_data['user'] == self.user
        assert form.cleaned_data['client'] == self.oauth_client
        assert set(form.cleaned_data['scope']) == set(expected_scopes)


# This is necessary because cms does not implement third party auth
@unittest.skipUnless(TPA_FEATURE_ENABLED, TPA_FEATURES_KEY + " not enabled")
@httpretty.activate
class DOTAccessTokenExchangeFormTestFacebook(
        DOTAdapterMixin,
        AccessTokenExchangeFormTest,
        ThirdPartyOAuthTestMixinFacebook,
        TestCase,
):
    """
    Tests for AccessTokenExchangeForm used with Facebook, tested against
    django-oauth-toolkit (DOT).
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


# This is necessary because cms does not implement third party auth
@unittest.skipUnless(TPA_FEATURE_ENABLED, TPA_FEATURES_KEY + " not enabled")
@httpretty.activate
class DOTAccessTokenExchangeFormTestGoogle(
        DOTAdapterMixin,
        AccessTokenExchangeFormTest,
        ThirdPartyOAuthTestMixinGoogle,
        TestCase,
):
    """
    Tests for AccessTokenExchangeForm used with Google, tested against
    django-oauth-toolkit (DOT).
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass
