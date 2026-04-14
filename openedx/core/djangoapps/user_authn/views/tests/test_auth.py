""" Tests for auth. """


import json
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class getPublicSigningJWKSFunctionTest(TestCase):
    """
    Tests for the auth view.
    """
    def _get_jwks(self, accepts='application/json'):
        """ Get JWKS from the endpoint """
        url = reverse('get_public_signing_jwks')

        return self.client.get(url, HTTP_ACCEPT=accepts)

    @mock.patch.dict(settings.JWT_AUTH, {'JWT_PUBLIC_SIGNING_JWK_SET': ''})
    def test_get_public_signing_jwks_with_no_jwk_set(self):
        """ Test JWT_PUBLIC_SIGNING_JWK_SET is undefined """
        resp = self._get_jwks()
        content = json.loads(resp.content)
        assert resp.status_code == 400
        assert 'JWK set is not found' in content['error']

    @mock.patch.dict(settings.JWT_AUTH, {'JWT_PUBLIC_SIGNING_JWK_SET': '{"keys": []}'})
    def test_get_public_signing_jwks_with_jwk_set(self):
        """ Test JWT_PUBLIC_SIGNING_JWK_SET is defined """
        resp = self._get_jwks()
        content = json.loads(resp.content)
        assert resp.status_code == 200
        assert content['keys'] == []
