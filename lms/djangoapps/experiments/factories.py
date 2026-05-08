"""
Experimentation factories
"""


import factory
import factory.fuzzy

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.experiments.models import ExperimentData, ExperimentKeyValue


class ExperimentDataFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = ExperimentData

    user = factory.SubFactory(UserFactory)
    experiment_id = factory.fuzzy.FuzzyInteger(0)
    key = factory.Sequence(lambda n: n)
    value = factory.Faker('word')


class ExperimentKeyValueFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = ExperimentKeyValue

    experiment_id = factory.fuzzy.FuzzyInteger(0)
    key = factory.Sequence(lambda n: n)
    value = factory.Faker('word')
