"""
Tests for DOT Adapter
"""

from datetime import timedelta

import ddt
import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now
from oauth2_provider import models

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

# oauth_dispatch is not in CMS' INSTALLED_APPS so these imports will error during test collection
from ..adapters import DOTAdapter
from ..models import RestrictedApplication
from .constants import DUMMY_REDIRECT_URL, DUMMY_REDIRECT_URL2


@ddt.ddt
@skip_unless_lms
class DOTAdapterTestCase(TestCase):
    """
    Test class for DOTAdapter.
    """
    def setUp(self):
        super().setUp()
        self.adapter = DOTAdapter()
        self.user = UserFactory()
        self.public_client = self.adapter.create_public_client(
            name='public app',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='public-client-id',
        )
        self.confidential_client = self.adapter.create_confidential_client(
            name='confidential app',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='confidential-client-id',
        )
        self.restricted_client = self.adapter.create_confidential_client(
            name='restricted app',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL2,
            client_id='restricted-client-id',
        )
        self.restricted_app = RestrictedApplication.objects.create(application=self.restricted_client)

    def test_restricted_app_unicode(self):
        """
        Make sure unicode representation of RestrictedApplication is correct
        """
        assert str(self.restricted_app) == "<RestrictedApplication '{name}'>"\
            .format(name=self.restricted_client.name)  # noqa: UP032

    @ddt.data(
        ('confidential', models.Application.CLIENT_CONFIDENTIAL),
        ('public', models.Application.CLIENT_PUBLIC),
    )
    @ddt.unpack
    def test_create_client(self, client_name, client_type):
        client = getattr(self, f'{client_name}_client')
        assert isinstance(client, models.Application)
        assert client.client_id == f'{client_name}-client-id'
        assert client.client_type == client_type

    def test_get_client(self):
        """
        Read back one of the confidential clients (there are two)
        and verify that we get back what we expected
        """
        client = self.adapter.get_client(
            redirect_uris=DUMMY_REDIRECT_URL,
            client_type=models.Application.CLIENT_CONFIDENTIAL
        )
        assert isinstance(client, models.Application)
        assert client.client_type == models.Application.CLIENT_CONFIDENTIAL

    def test_get_client_not_found(self):
        with pytest.raises(models.Application.DoesNotExist):
            self.adapter.get_client(client_id='not-found')

    def test_get_client_for_token(self):
        token = models.AccessToken(
            user=self.user,
            application=self.public_client,
        )
        assert self.adapter.get_client_for_token(token) == self.public_client

    def test_get_access_token(self):
        token = self.adapter.create_access_token_for_test(
            'token-id',
            client=self.public_client,
            user=self.user,
            expires=now() + timedelta(days=30),
        )
        assert self.adapter.get_access_token(token_string='token-id') == token

    def test_get_restricted_access_token(self):
        """
        Make sure when generating an access_token for a restricted client
        that the token is immediately expired
        """

        # for this test it requires to call AccessTokenView(_DispatchingView) otherwise it fails
        # to set the expiry.
        self.client.post(reverse('access_token'), {})
        self.adapter.create_access_token_for_test(
            'expired-token-id',
            client=self.restricted_client,
            user=self.user,
            expires=now() + timedelta(days=30),
        )

        readback_token = self.adapter.get_access_token(token_string='expired-token-id')
        assert RestrictedApplication.verify_access_token_as_expired(readback_token)
