"""Tests for models supporting Credentials-related functionality."""


from django.test import TestCase, override_settings  # noqa: I001

from openedx.core.djangoapps.credentials.models import API_VERSION
from openedx.core.djangoapps.credentials.tests.mixins import CredentialsApiConfigMixin
from openedx.core.djangolib.testing.utils import skip_unless_lms

CREDENTIALS_INTERNAL_SERVICE_URL = 'https://credentials.example.com'
CREDENTIALS_PUBLIC_SERVICE_URL = 'https://credentials.example.com'


@skip_unless_lms
class TestCredentialsApiConfig(CredentialsApiConfigMixin, TestCase):
    """Tests covering the CredentialsApiConfig model."""

    @override_settings(
        CREDENTIALS_INTERNAL_SERVICE_URL=CREDENTIALS_INTERNAL_SERVICE_URL,
        CREDENTIALS_PUBLIC_SERVICE_URL=CREDENTIALS_PUBLIC_SERVICE_URL
    )
    def test_url_construction(self):
        """Verify that URLs returned by the model are constructed correctly."""
        credentials_config = self.create_credentials_config()

        expected = '{root}/api/{version}/'.format(root=CREDENTIALS_INTERNAL_SERVICE_URL.strip('/'), version=API_VERSION)
        assert credentials_config.get_internal_api_url_for_org('nope') == expected

        expected = '{root}/api/{version}/'.format(root=CREDENTIALS_INTERNAL_SERVICE_URL.strip('/'), version=API_VERSION)
        assert credentials_config.internal_api_url == expected

        expected = '{root}/api/{version}/'.format(root=CREDENTIALS_INTERNAL_SERVICE_URL.strip('/'), version=API_VERSION)
        assert credentials_config.get_internal_api_url_for_org('nope') == expected

        expected = '{root}/api/{version}/'.format(root=CREDENTIALS_PUBLIC_SERVICE_URL.strip('/'), version=API_VERSION)
        assert credentials_config.public_api_url == expected

    def test_is_learner_issuance_enabled(self):
        """
        Verify that the property controlling display on the student dashboard is only True
        when configuration is enabled and all required configuration is provided.
        """
        credentials_config = self.create_credentials_config(enabled=False)
        assert not credentials_config.is_learner_issuance_enabled

        credentials_config = self.create_credentials_config(enable_learner_issuance=False)
        assert not credentials_config.is_learner_issuance_enabled

        credentials_config = self.create_credentials_config()
        assert credentials_config.is_learner_issuance_enabled
