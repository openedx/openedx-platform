"""
Model factories for unit testing views or models.
"""


from django.contrib.sites.models import Site  # noqa: I001
from factory.django import DjangoModelFactory
from factory import SubFactory, Sequence, SelfAttribute, lazy_attribute

from openedx.core.djangoapps.site_configuration.models import SiteConfiguration


class SiteFactory(DjangoModelFactory):
    """
    Factory class for Site model
    """
    class Meta:
        model = Site
        django_get_or_create = ('domain',)

    domain = Sequence('{}.testserver.fake'.format)
    name = SelfAttribute('domain')


class SiteConfigurationFactory(DjangoModelFactory):
    """
    Factory class for SiteConfiguration model
    """
    class Meta:
        model = SiteConfiguration

    enabled = True
    site = SubFactory(SiteFactory)

    @lazy_attribute
    def site_values(self):
        return {}
