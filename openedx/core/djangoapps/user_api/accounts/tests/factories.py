"""
Model Factories for testing purposes of User Accounts
"""
from factory import SubFactory  # noqa: I001
from factory.django import DjangoModelFactory
from openedx.core.djangoapps.user_api.models import UserRetirementStatus, RetirementState
from common.djangoapps.student.tests.factories import UserFactory


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
