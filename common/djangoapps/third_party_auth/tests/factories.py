"""
Provides factories for third_party_auth models.
"""

import factory
from factory import SubFactory
from factory.django import DjangoModelFactory
from faker import Factory as FakerFactory

from common.djangoapps.third_party_auth.models import OAuth2ProviderConfig, SAMLConfiguration, SAMLProviderConfig
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory

FAKER = FakerFactory.create()


class SAMLConfigurationFactory(DjangoModelFactory):
    """
    Factory or SAMLConfiguration model in third_party_auth app.
    """
    class Meta:
        model = SAMLConfiguration

    site = SubFactory(SiteFactory)
    enabled = True


class SAMLProviderConfigFactory(DjangoModelFactory):
    """
    Factory or SAMLProviderConfig model in third_party_auth app.
    """
    class Meta:
        model = SAMLProviderConfig
        django_get_or_create = ('slug', 'metadata_source', "entity_id")

    site = SubFactory(SiteFactory)

    enabled = True
    slug = factory.LazyAttribute(lambda x: FAKER.slug())
    name = factory.LazyAttribute(lambda x: FAKER.company())

    entity_id = factory.LazyAttribute(lambda x: FAKER.uri())
    metadata_source = factory.LazyAttribute(lambda x: FAKER.uri())


class OAuth2ProviderConfigFactory(DjangoModelFactory):
    """
    Factory for OAuth2ProviderConfig model in third_party_auth app.
    """
    class Meta:
        model = OAuth2ProviderConfig

    site = SubFactory(SiteFactory)
    enabled = True
    slug = factory.LazyAttribute(lambda x: FAKER.slug())
    name = factory.LazyAttribute(lambda x: FAKER.company())
