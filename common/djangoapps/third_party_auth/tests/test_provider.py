"""Unit tests for provider.py."""


import re
from unittest.mock import Mock, patch

from django.contrib.sites.models import Site
from django.db import DEFAULT_DB_ALIAS, connections
from django.test.utils import CaptureQueriesContext

from common.djangoapps.third_party_auth import provider
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth
from openedx.core.djangoapps.site_configuration.tests.test_util import (
    with_site_configuration,
    with_site_configuration_context,
)

SITE_DOMAIN_A = 'professionalx.example.com'
SITE_DOMAIN_B = 'somethingelse.example.com'


@skip_unless_thirdpartyauth()
class RegistryTest(testutil.TestCase):
    """Tests registry discovery and operation."""

    def test_configure_once_adds_gettable_providers(self):
        facebook_provider = self.configure_facebook_provider(enabled=True)
        assert facebook_provider.id == provider.Registry.get(facebook_provider.provider_id).id

    def test_no_providers_by_default(self):
        enabled_providers = provider.Registry.enabled()
        assert len(enabled_providers) == 0, 'By default, no providers are enabled.'

    def test_runtime_configuration(self):
        self.configure_google_provider(enabled=True)
        enabled_providers = provider.Registry.enabled()
        assert len(enabled_providers) == 1
        assert enabled_providers[0].name == 'Google'
        assert enabled_providers[0].get_setting('SECRET') == 'opensesame'

        self.configure_google_provider(enabled=False)
        enabled_providers = provider.Registry.enabled()
        assert len(enabled_providers) == 0

        self.configure_google_provider(enabled=True, secret="alohomora")
        enabled_providers = provider.Registry.enabled()
        assert len(enabled_providers) == 1
        assert enabled_providers[0].get_setting('SECRET') == 'alohomora'

    def test_secure_configuration(self):
        """ Test that some sensitive values can be configured via Django settings """
        self.configure_google_provider(enabled=True, secret="")
        enabled_providers = provider.Registry.enabled()
        assert len(enabled_providers) == 1
        assert enabled_providers[0].name == 'Google'
        assert enabled_providers[0].get_setting('SECRET') == ''
        with self.settings(SOCIAL_AUTH_OAUTH_SECRETS={'google-oauth2': 'secret42'}):
            assert enabled_providers[0].get_setting('SECRET') == 'secret42'

    def test_cannot_load_arbitrary_backends(self):
        """ Test that only backend_names listed in settings.AUTHENTICATION_BACKENDS can be used """
        self.configure_oauth_provider(enabled=True, name="Disallowed", backend_name="disallowed")
        self.enable_saml()
        self.configure_saml_provider(
            enabled=True,
            name="Disallowed",
            slug="test",
            backend_name="disallowed"
        )
        assert len(provider.Registry.enabled()) == 0

    def test_enabled_returns_list_of_enabled_providers_sorted_by_name(self):
        provider_names = ["Stack Overflow", "Google", "LinkedIn", "GitHub"]
        backend_names = []
        for name in provider_names:
            backend_name = name.lower().replace(' ', '')
            backend_names.append(backend_name)
            self.configure_oauth_provider(enabled=True, name=name, backend_name=backend_name)

        with patch('common.djangoapps.third_party_auth.provider._PSA_OAUTH2_BACKENDS', backend_names):
            assert sorted(provider_names) == [prov.name for prov in provider.Registry.enabled()]

    def test_enabled_doesnt_query_site(self):
        """Regression test for 1+N queries for django_site (ARCHBOM-1139)"""
        re_django_site_query = re.compile(r'FROM\s+"django_site"')

        self.enable_saml()
        provider_count = 5
        for i in range(provider_count):
            self.configure_saml_provider(enabled=True, slug=f"saml-slug-{i}", entity_id=f"saml-entity-id-{i}")

        with CaptureQueriesContext(connections[DEFAULT_DB_ALIAS]) as cq:
            enabled_slugs = {p.slug for p in provider.Registry.enabled()}

        assert len(enabled_slugs) == provider_count
        # Should not involve any queries for Site, or at least should not *scale* with number of providers
        all_queries = [q['sql'] for q in cq.captured_queries]
        django_site_queries = list(filter(re_django_site_query.search, all_queries))
        assert len(django_site_queries) == 0
        # previously was == provider_count (1 for each provider)

    def test_providers_displayed_for_login(self):
        """
        Tests to ensure that only providers that we can use to log in are presented
        for rendering in the UI.
        """
        hidden_provider = self.configure_google_provider(visible=False, enabled=True)
        normal_provider = self.configure_facebook_provider(visible=True, enabled=True)
        implicitly_hidden_provider = self.configure_linkedin_provider(enabled=True)
        disabled_provider = self.configure_twitter_provider(visible=True, enabled=False)
        no_log_in_provider = self.configure_lti_provider()
        provider_ids = [idp.provider_id for idp in provider.Registry.displayed_for_login()]
        assert hidden_provider.provider_id not in provider_ids
        assert implicitly_hidden_provider.provider_id not in provider_ids
        assert disabled_provider.provider_id not in provider_ids
        assert no_log_in_provider.provider_id not in provider_ids
        assert normal_provider.provider_id in provider_ids

    def test_tpa_hint_exp_hidden_provider_displayed_for_login(self):
        """
        Test to ensure that an explicitly enabled-but-not-visible provider is presented
        for use in the UI when the "tpa_hint" parameter is specified
        A hidden provider should be accessible with tpa_hint (this is the main case)
        """

        hidden_provider = self.configure_google_provider(visible=False, enabled=True)
        provider_ids = [
            idp.provider_id
            for idp in provider.Registry.displayed_for_login(tpa_hint=hidden_provider.provider_id)
        ]
        assert hidden_provider.provider_id in provider_ids

    def test_tpa_hint_hidden_provider_displayed_for_login(self):
        """
        Tests to ensure that an implicitly enabled-but-not-visible provider is presented
        for use in the UI when the "tpa_hint" parameter is specified.
        New providers are hidden (ie, not flagged as 'visible') by default
        The tpa_hint parameter should work for these providers as well.
        """

        implicitly_hidden_provider = self.configure_linkedin_provider(enabled=True)
        provider_ids = [
            idp.provider_id
            for idp in provider.Registry.displayed_for_login(tpa_hint=implicitly_hidden_provider.provider_id)
        ]
        assert implicitly_hidden_provider.provider_id in provider_ids

    def test_tpa_hint_disabled_hidden_provider_displayed_for_login(self):
        """
        Disabled providers should not be matched in tpa_hint scenarios
        """
        disabled_provider = self.configure_twitter_provider(visible=True, enabled=False)
        provider_ids = [
            idp.provider_id
            for idp in provider.Registry.displayed_for_login(tpa_hint=disabled_provider.provider_id)
        ]
        assert disabled_provider.provider_id not in provider_ids

    def test_tpa_hint_no_log_hidden_provider_displayed_for_login(self):
        """
        Providers not utilized for learner authentication should not match tpa_hint
        """
        no_log_in_provider = self.configure_lti_provider()
        provider_ids = [
            idp.provider_id
            for idp in provider.Registry.displayed_for_login(tpa_hint=no_log_in_provider.provider_id)
        ]
        assert no_log_in_provider.provider_id not in provider_ids

    def test_get_current_site_oauth_provider(self):
        """
        Verify that correct provider for current site is returned even if same backend is used for multiple sites.
        """
        site_a = Site.objects.get_or_create(domain=SITE_DOMAIN_A, name=SITE_DOMAIN_A)[0]
        site_b = Site.objects.get_or_create(domain=SITE_DOMAIN_B, name=SITE_DOMAIN_B)[0]
        site_a_provider = self.configure_google_provider(visible=True, enabled=True, site=site_a)
        site_b_provider = self.configure_google_provider(visible=True, enabled=True, site=site_b)
        with with_site_configuration_context(domain=SITE_DOMAIN_A):
            assert site_a_provider.enabled_for_current_site is True

            # Registry.displayed_for_login gets providers enabled for current site
            provider_ids = provider.Registry.displayed_for_login()
            # Google oauth provider for current site should be displayed
            assert site_a_provider in provider_ids
            assert site_b_provider not in provider_ids

        # Similarly, the other site should only see its own providers
        with with_site_configuration_context(domain=SITE_DOMAIN_B):
            assert site_b_provider.enabled_for_current_site is True
            provider_ids = provider.Registry.displayed_for_login()
            assert site_b_provider in provider_ids
            assert site_a_provider not in provider_ids

    def test_provider_enabled_for_current_site(self):
        """
        Verify that enabled_for_current_site returns True when the provider matches the current site.
        """
        prov = self.configure_google_provider(visible=True, enabled=True, site=Site.objects.get_current())
        assert prov.enabled_for_current_site is True

    @with_site_configuration(SITE_DOMAIN_A)
    def test_provider_disabled_for_mismatching_site(self):
        """
        Verify that enabled_for_current_site returns False when the provider is configured for a different site.
        """
        site_b = Site.objects.get_or_create(domain=SITE_DOMAIN_B, name=SITE_DOMAIN_B)[0]
        prov = self.configure_google_provider(visible=True, enabled=True, site=site_b)
        assert prov.enabled_for_current_site is False

    def test_get_returns_enabled_provider(self):
        google_provider = self.configure_google_provider(enabled=True)
        assert google_provider.id == provider.Registry.get(google_provider.provider_id).id

    def test_oauth2_provider_keyed_by_slug(self):
        """
        Regression test to ensure that the Registry properly fetches OAuth2ProviderConfigs that have a slug
        which doesn't match any of the possible backend_names.
        """
        google_provider = self.configure_google_provider(enabled=True, slug='custom_slug')
        assert google_provider in provider.Registry._enabled_providers()  # pylint: disable=protected-access
        assert google_provider in provider.Registry.get_enabled_by_backend_name('google-oauth2')

    def test_oath2_different_slug_from_backend_name(self):
        """
        Test that an OAuth2 provider can have a slug that differs from the backend name.
        """
        dummy_provider = self.configure_oauth_provider(enabled=True, name="dummy", slug="default", backend_name="dummy")
        assert dummy_provider in provider.Registry.get_enabled_by_backend_name('dummy')

    def test_oauth2_enabled_only_for_supplied_backend(self):
        """
        Test to ensure that Registry.get_enabled_by_backend_name doesn't return OAuth2 providers with incorrect
        backend_names.
        """
        facebook_provider = self.configure_facebook_provider(enabled=True)
        self.configure_google_provider(enabled=True)
        assert facebook_provider not in provider.Registry.get_enabled_by_backend_name('google-oauth2')

    def test_get_returns_none_if_provider_id_is_none(self):
        assert provider.Registry.get(None) is None

    def test_get_returns_none_if_provider_not_enabled_change(self):
        linkedin_provider_id = "oa2-linkedin-oauth2"
        # At this point there should be no configuration entries at all so no providers should be enabled
        assert provider.Registry.enabled() == []
        assert provider.Registry.get(linkedin_provider_id) is None
        # Now explicitly disabled this provider:
        self.configure_linkedin_provider(enabled=False)
        assert provider.Registry.get(linkedin_provider_id) is None

    def test_get_returns_provider_if_provider_enabled(self):
        """
        Test to ensure that Registry gets enabled providers.
        """
        linkedin_provider_id = "oa2-linkedin-oauth2"
        self.configure_linkedin_provider(enabled=True)
        assert provider.Registry.get(linkedin_provider_id).provider_id == linkedin_provider_id

    def test_get_from_pipeline_returns_none_if_provider_not_enabled(self):
        assert provider.Registry.enabled() == [], 'By default, no providers are enabled.'
        assert provider.Registry.get_from_pipeline(Mock()) is None

    def test_get_enabled_by_backend_name_returns_enabled_provider(self):
        google_provider = self.configure_google_provider(enabled=True)
        found = list(provider.Registry.get_enabled_by_backend_name(google_provider.backend_name))
        assert found == [google_provider]

    def test_get_enabled_by_backend_name_returns_none_if_provider_not_enabled(self):
        google_provider = self.configure_google_provider(enabled=False)
        found = list(provider.Registry.get_enabled_by_backend_name(google_provider.backend_name))
        assert not found
