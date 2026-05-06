"""
Test Factory classes for ExternalUserIds
"""

from uuid import uuid4

import factory
from factory.fuzzy import FuzzyChoice, FuzzyText

from openedx.core.djangoapps.external_user_ids.models import ExternalId, ExternalIdType


class ExternalIDTypeFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = ExternalIdType

    name = FuzzyChoice([ExternalIdType.CALIPER])
    description = FuzzyText()


class ExternalIdFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = ExternalId

    external_user_id = factory.LazyFunction(uuid4)
    external_id_type = factory.SubFactory(ExternalIDTypeFactory)
