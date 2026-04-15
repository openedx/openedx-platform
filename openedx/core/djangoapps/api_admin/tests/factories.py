"""Factories for API management."""


import factory
from factory.django import DjangoModelFactory
from factory.fuzzy import FuzzyInteger, FuzzyText
from oauth2_provider.models import get_application_model

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest, Catalog
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory

Application = get_application_model()  # pylint: disable=invalid-name


class ApiAccessRequestFactory(DjangoModelFactory):
    """Factory for ApiAccessRequest objects."""
    class Meta:
        model = ApiAccessRequest

    user = factory.SubFactory(UserFactory)
    site = factory.SubFactory(SiteFactory)


class ApplicationFactory(DjangoModelFactory):
    """Factory for OAuth Application objects."""
    class Meta:
        model = Application

    authorization_grant_type = Application.GRANT_CLIENT_CREDENTIALS
    client_type = Application.CLIENT_CONFIDENTIAL


class CatalogFactory(DjangoModelFactory):
    """Factory for Catalog objects."""

    class Meta:
        model = Catalog

    id = FuzzyInteger(0, 999)  # pylint: disable=invalid-name
    query = '*'
    name = FuzzyText(prefix='test-catalog')
