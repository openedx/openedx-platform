"""
Model specific tests for user_api
"""


import pytest

from common.djangoapps.student.models import get_retired_email_by_email, get_retired_username_by_username
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    RetirementStateError,
    UserRetirementRequest,
    UserRetirementStatus,
)

from .retirement_helpers import setup_retirement_states  # pylint: disable=unused-import

# Tell pytest it's ok to use the database
pytestmark = pytest.mark.django_db


def _assert_retirementstatus_is_user(retirement, user):
    """
    Helper function to compare a newly created UserRetirementStatus object to expected values for
    the given user.
    """
    pending = RetirementState.objects.all().order_by('state_execution_order')[0]
    retired_username = get_retired_username_by_username(user.username)
    retired_email = get_retired_email_by_email(user.email)

    assert retirement.user == user
    assert retirement.original_username == user.username
    assert retirement.original_email == user.email
    assert retirement.original_name == user.profile.name
    assert retirement.retired_username == retired_username
    assert retirement.retired_email == retired_email
    assert retirement.current_state == pending
    assert retirement.last_state == pending
    assert pending.state_name in retirement.responses


def test_retirement_create_success(setup_retirement_states):  # pylint: disable=unused-argument, redefined-outer-name
    """
    Basic test to make sure default creation succeeds
    """
    user = UserFactory()
    retirement = UserRetirementStatus.create_retirement(user)
    _assert_retirementstatus_is_user(retirement, user)


def test_retirement_create_no_default_state():
    """
    Confirm that if no states have been loaded we fail with a RetirementStateError
    """
    user = UserFactory()

    with pytest.raises(RetirementStateError):
        UserRetirementStatus.create_retirement(user)


def test_retirement_create_already_retired(setup_retirement_states):  # pylint: disable=unused-argument, redefined-outer-name
    """
    Confirm the correct error bubbles up if the user already has a retirement row
    """
    user = UserFactory()
    retirement = UserRetirementStatus.create_retirement(user)
    _assert_retirementstatus_is_user(retirement, user)

    with pytest.raises(RetirementStateError):
        UserRetirementStatus.create_retirement(user)


def test_retirement_request_create_success():
    """
    Ensure that retirement request record creation succeeds.
    """
    user = UserFactory()
    UserRetirementRequest.create_retirement_request(user)
    assert UserRetirementRequest.has_user_requested_retirement(user)


def test_retirement_request_created_upon_status(setup_retirement_states):  # pylint: disable=unused-argument, redefined-outer-name
    """
    Ensure that retirement request record is created upon retirement status creation.
    """
    user = UserFactory()
    UserRetirementStatus.create_retirement(user)
    assert UserRetirementRequest.has_user_requested_retirement(user)


def test_retirement_request_deleted_upon_pending_status_delete(setup_retirement_states):  # pylint: disable=unused-argument, redefined-outer-name
    """
    Ensure that retirement request record is deleted upon deletion of a PENDING retirement status.
    """
    user = UserFactory()
    retirement_status = UserRetirementStatus.create_retirement(user)
    assert UserRetirementRequest.has_user_requested_retirement(user)
    pending = RetirementState.objects.all().order_by('state_execution_order')[0]
    assert retirement_status.current_state == pending
    retirement_status.delete()
    assert not UserRetirementRequest.has_user_requested_retirement(user)


def test_retirement_request_preserved_upon_non_pending_status_delete(setup_retirement_states):  # pylint: disable=unused-argument, redefined-outer-name
    """
    Ensure that retirement request record is not deleted upon deletion of a non-PENDING retirement status.
    """
    user = UserFactory()
    retirement_status = UserRetirementStatus.create_retirement(user)
    assert UserRetirementRequest.has_user_requested_retirement(user)
    non_pending = RetirementState.objects.all().order_by('state_execution_order')[1]
    retirement_status.current_state = non_pending
    retirement_status.delete()
    assert UserRetirementRequest.has_user_requested_retirement(user)
