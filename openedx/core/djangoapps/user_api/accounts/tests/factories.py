"""
Model Factories for testing purposes of User Accounts
"""
from factory import SubFactory
from factory.django import DjangoModelFactory

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus


class RetirementStateFactory(DjangoModelFactory):
    """
    Simple factory class for storing retirement state.
    """

    class Meta:
        model = RetirementState


class UserRetirementStatusFactory(DjangoModelFactory):
    """
    Simple factory class for storing user retirement status.
    """

    class Meta:
        model = UserRetirementStatus

    user = SubFactory(UserFactory)
    current_state = SubFactory(RetirementStateFactory)
    last_state = SubFactory(RetirementStateFactory)
