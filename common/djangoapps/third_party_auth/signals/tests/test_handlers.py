"""
Tests for SAML configuration signal handlers.
"""

import ddt
from unittest import mock
from unittest.mock import call
from django.test import TestCase
from django.contrib.sites.models import Site
from common.djangoapps.third_party_auth.tests.factories import SAMLConfigurationFactory, SAMLProviderConfigFactory
from common.djangoapps.third_party_auth.models import SAMLProviderConfig


@ddt.ddt
class TestSAMLConfigurationSignalHandlers(TestCase):
    """
    Test effects of SAML configuration signal handlers.
    """
    def setUp(self):
        self.saml_config = SAMLConfigurationFactory(
            slug='test-config',
            entity_id='https://test.example.com',
            org_info_str='{"en-US": {"url": "http://test.com", "displayname": "Test", "name": "test"}}'
        )

        self.site1 = Site.objects.get_or_create(domain='test-site1.com', name='Site 1')[0]
        self.site2 = Site.objects.get_or_create(domain='test-site2.com', name='Site 2')[0]

        # Existing SAML config used by provider update tests
        self.existing_saml_config = SAMLConfigurationFactory(
            site=self.site1,
            slug='slug',
            entity_id='https://existing.example.com'
        )

    @mock.patch('common.djangoapps.third_party_auth.signals.handlers.set_custom_attribute')
    def test_saml_config_signal_handlers_with_error(self, mock_set_custom_attribute):
        """
        Test error handling when signal handlers encounter an exception.

        Verifies that error information is properly captured when provider updates fail.
        """
        error_message = "Test error"
        # Simulate an exception in the provider config update logic
        with mock.patch(
            'common.djangoapps.third_party_auth.models.SAMLProviderConfig.objects.current_set',
            side_effect=Exception(error_message)
        ):
            self.saml_config.entity_id = 'https://updated.example.com'
            self.saml_config.save()

        expected_calls = [
            call('saml_config_signal.new_config_id', self.saml_config.id),
            call('saml_config_signal.slug', 'test-config'),
        ]

        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=False)
        assert mock_set_custom_attribute.call_count == 3

        # Verify error message was logged
        mock_set_custom_attribute.assert_any_call(
            'saml_config_signal.error_message',
            mock.ANY
        )
        error_calls = [
            call for call in mock_set_custom_attribute.mock_calls
            if call[1][0] == 'saml_config_signal.error_message'
        ]
        assert error_message in error_calls[0][1][1], (
            f"Expected '{error_message}' in error message, "
            f"got: {error_calls[0][1][1]}"
        )

    def _get_current_provider(self, slug):
        """
        Helper to get current version of provider by slug.
        """
        return SAMLProviderConfig.objects.current_set().get(slug=slug)

    def _get_site(self, site_id):
        """
        Helper to get site by ID (1 = site1, 2 = site2).
        """
        if site_id == 1:
            return self.site1
        elif site_id == 2:
            return self.site2
        else:
            raise ValueError(f"Unexpected site_id: {site_id}.")

    @ddt.data(
        # Args: provider_site_id, provider_slug, signal_saml_site_id, signal_saml_slug, is_provider_updated
        # All tests: provider's saml_configuration has site_id=1, slug='slug'
        # Signal matches provider's saml config and should update
        (1, 'slug', 1, 'slug', True),                     # Same site, same slug
        (2, 'slug', 1, 'slug', True),                     # Cross-site provider, matching saml config
        (1, 'provider-slug', 1, 'slug', True),            # Different provider slug, matching saml config
        # Signal does not match provider's saml config and should not update
        (1, 'slug', 2, 'slug', False),                    # Different saml config site
        (2, 'slug', 2, 'slug', False),                    # Different saml config site (cross-site)
        (1, 'provider-slug', 1, 'provider-slug', False),  # Different saml config slug
        (2, 'provider-slug', 1, 'provider-slug', False),  # Different saml config slug (cross-site)
    )
    @ddt.unpack
    @mock.patch('common.djangoapps.third_party_auth.signals.handlers.set_custom_attribute')
    def test_saml_provider_config_updates(self, provider_site_id, provider_slug,
                                          signal_saml_site_id, signal_saml_slug, is_provider_updated,
                                          mock_set_custom_attribute):
        """
        Test SAML provider config updates under different scenarios.

        Tests that providers are updated only when the signal's SAML configuration
        matches the provider's existing SAML configuration (by site and slug).
        """
        provider_site = self._get_site(provider_site_id)
        signal_saml_site = self._get_site(signal_saml_site_id)

        provider = SAMLProviderConfigFactory(
            slug=provider_slug,
            site=provider_site,
            saml_configuration=self.existing_saml_config
        )
        original_config_id = provider.saml_configuration_id

        new_saml_config = SAMLConfigurationFactory(
            site=signal_saml_site,
            slug=signal_saml_slug,
            entity_id='https://new.example.com'
        )

        current_provider = self._get_current_provider(provider_slug)

        expected_calls = [
            call('saml_config_signal.new_config_id', new_saml_config.id),
            call('saml_config_signal.slug', signal_saml_slug),
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=False)

        if is_provider_updated:
            mock_set_custom_attribute.assert_any_call('saml_config_signal.updated_count', 1)
            self.assertEqual(current_provider.saml_configuration_id, new_saml_config.id,
                             "Provider should be updated when signal SAML config matches")
        else:
            mock_set_custom_attribute.assert_any_call('saml_config_signal.updated_count', 0)
            self.assertEqual(current_provider.saml_configuration_id, original_config_id,
                             "Provider should NOT be updated when signal SAML config doesn't match")

    @ddt.data(
        # Args: provider_site_id, provider_slug, signal_saml_site_id, signal_saml_slug
        # All tests: provider's saml config is None and should never be updated
        (1, 'slug', 1, 'default'),
        (1, 'default', 1, 'default'),
        (2, 'slug', 1, 'default'),
    )
    @ddt.unpack
    def test_saml_provider_with_null_config_not_updated(self, provider_site_id, provider_slug,
                                                        signal_saml_site_id, signal_saml_slug):
        """
        Test that providers with NULL SAML configuration are never updated by signal handler.

        This is critical for fallback authentication scenarios where providers
        intentionally have no SAML configuration.
        """
        provider_site = self._get_site(provider_site_id)
        signal_saml_site = self._get_site(signal_saml_site_id)

        null_provider = SAMLProviderConfigFactory(
            slug=provider_slug,
            site=provider_site,
            saml_configuration=None
        )

        new_saml_config = SAMLConfigurationFactory(
            site=signal_saml_site,
            slug=signal_saml_slug,
            entity_id='https://new.example.com'
        )

        current_provider = self._get_current_provider(provider_slug)
        self.assertIsNone(current_provider.saml_configuration_id,
                          "Provider with NULL SAML config should never be updated")
