"""Provides factories for User API models."""


from factory import Sequence, SubFactory
from factory.django import DjangoModelFactory
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserCourseTag,
    UserOrgTag,
    UserPreference,
    UserRetirementRequest,
    UserRetirementStatus
)


# Factories are self documenting
# pylint: disable=missing-docstring
class UserPreferenceFactory(DjangoModelFactory):
    class Meta:
        model = UserPreference

    user = None
    key = None
    value = "default test value"


class UserCourseTagFactory(DjangoModelFactory):
    class Meta:
        model = UserCourseTag

    user = SubFactory(UserFactory)
    course_id = CourseLocator('org', 'course', 'run')
    key = None
    value = None


class UserOrgTagFactory(DjangoModelFactory):
    """ Simple factory class for generating UserOrgTags """
    class Meta:
        model = UserOrgTag

    user = SubFactory(UserFactory)
    org = 'org'
    key = None
    value = None


class RetirementStateFactory(DjangoModelFactory):
    """
    Factory class for generating RetirementState instances.
    """
    class Meta:
        model = RetirementState

    state_name = Sequence("STEP_{}".format)
    state_execution_order = Sequence(lambda n: n * 10)
    is_dead_end_state = False
    required = False


class UserRetirementStatusFactory(DjangoModelFactory):
    """
    Factory class for generating UserRetirementStatus instances.
    """
    class Meta:
        model = UserRetirementStatus

    user = SubFactory(UserFactory)
    original_username = Sequence('learner_{}'.format)
    original_email = Sequence("learner{}@email.org".format)
    original_name = Sequence("Learner{} Shmearner".format)
    retired_username = Sequence("retired__learner_{}".format)
    retired_email = Sequence("returned__learner{}@retired.invalid".format)
    current_state = None
    last_state = None
    responses = ""


class UserRetirementRequestFactory(DjangoModelFactory):
    """
    Factory class for generating UserRetirementRequest instances.
    """
    class Meta:
        model = UserRetirementRequest

    user = SubFactory(UserFactory)
