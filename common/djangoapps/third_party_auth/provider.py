"""
Third-party auth provider configuration API.
"""


from django.contrib.sites.models import Site

from common.djangoapps.third_party_auth.models import (
    _LTI_BACKENDS,
    _PSA_OAUTH2_BACKENDS,
    _PSA_SAML_BACKENDS,
    LTIProviderConfig,
    OAuth2ProviderConfig,
    SAMLConfiguration,
    SAMLProviderConfig
)
from openedx.core.djangoapps.theming.helpers import get_current_request


class Registry:
    """
    API for querying third-party auth ProviderConfig objects.

    Providers must subclass ProviderConfig in order to be usable in the registry.
    """
    @classmethod
    def _enabled_providers(cls):
        """
        Helper method that returns a generator used to iterate over all providers
        of the current site.
        """
        oauth2_backend_names = OAuth2ProviderConfig.key_values('backend_name', flat=True)
        for oauth2_backend_name in oauth2_backend_names:
            provider = OAuth2ProviderConfig.current(oauth2_backend_name)
            if provider.enabled_for_current_site and provider.backend_name in _PSA_OAUTH2_BACKENDS:
                yield provider
        if SAMLConfiguration.is_enabled(Site.objects.get_current(get_current_request()), 'default'):
            idp_slugs = SAMLProviderConfig.key_values('slug', flat=True)
            for idp_slug in idp_slugs:
                provider = SAMLProviderConfig.current(idp_slug)
                if provider.enabled_for_current_site and provider.backend_name in _PSA_SAML_BACKENDS:
                    yield provider
        for consumer_key in LTIProviderConfig.key_values('lti_consumer_key', flat=True):
            provider = LTIProviderConfig.current(consumer_key)
            if provider.enabled_for_current_site and provider.backend_name in _LTI_BACKENDS:
                yield provider

    @classmethod
    def enabled(cls):
        """Returns list of enabled providers."""
        return sorted(cls._enabled_providers(), key=lambda provider: provider.name)

    @classmethod
    def displayed_for_login(cls, tpa_hint=None):
        """
        Args:
            tpa_hint (string): An override used in certain third-party authentication
                scenarios that will cause the specified provider to be included in the
                set along with any providers matching the 'display_for_login' criteria.
                Note that 'provider_id' cannot have a value of None according to the
                current implementation.

        Returns:
            List of ProviderConfig entities
        """
        return [
            provider
            for provider in cls.enabled()
            if provider.display_for_login or provider.provider_id == tpa_hint
        ]

    @classmethod
    def get(cls, provider_id):
        """Gets provider by provider_id string if enabled, else None."""
        if not provider_id:
            return None
        if '-' not in provider_id:  # Check format - see models.py:ProviderConfig
            raise ValueError("Invalid provider_id. Expect something like oa2-google")
        try:
            return next(provider for provider in cls._enabled_providers() if provider.provider_id == provider_id)
        except StopIteration:
            return None

    @classmethod
    def get_from_pipeline(cls, running_pipeline):
        """Gets the provider that is being used for the specified pipeline (or None).

        Args:
            running_pipeline: The python-social-auth pipeline being used to
                authenticate a user.

        Returns:
            An instance of ProviderConfig or None.
        """
        for enabled in cls._enabled_providers():
            if enabled.is_active_for_pipeline(running_pipeline):
                return enabled

    @classmethod
    def get_enabled_by_backend_name(cls, backend_name):
        """Generator returning all enabled providers that use the specified
        backend on the current site.

        Example:
            >>> list(get_enabled_by_backend_name("tpa-saml"))
                [<SAMLProviderConfig>, <SAMLProviderConfig>]

        Args:
            backend_name: The name of a python-social-auth backend used by
                one or more providers.

        Yields:
            Instances of ProviderConfig.
        """
        if backend_name in _PSA_OAUTH2_BACKENDS:
            oauth2_backend_names = OAuth2ProviderConfig.key_values('backend_name', flat=True)
            for oauth2_backend_name in oauth2_backend_names:
                provider = OAuth2ProviderConfig.current(oauth2_backend_name)
                if provider.backend_name == backend_name and provider.enabled_for_current_site:
                    yield provider
        elif backend_name in _PSA_SAML_BACKENDS and SAMLConfiguration.is_enabled(
                Site.objects.get_current(get_current_request()), 'default'):
            idp_names = SAMLProviderConfig.key_values('slug', flat=True)
            for idp_name in idp_names:
                provider = SAMLProviderConfig.current(idp_name)
                if provider.backend_name == backend_name and provider.enabled_for_current_site:
                    yield provider
        elif backend_name in _LTI_BACKENDS:
            for consumer_key in LTIProviderConfig.key_values('lti_consumer_key', flat=True):
                provider = LTIProviderConfig.current(consumer_key)
                if provider.backend_name == backend_name and provider.enabled_for_current_site:
                    yield provider
