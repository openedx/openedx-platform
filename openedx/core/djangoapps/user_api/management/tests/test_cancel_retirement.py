"""
Test the cancel_user_retirement_request management command
"""


import pytest
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    logged_out_retirement_request,
    setup_retirement_states,
)
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementRequest, UserRetirementStatus

pytestmark = pytest.mark.django_db


def test_successful_cancellation(setup_retirement_states, logged_out_retirement_request, capsys):  # pylint: disable=redefined-outer-name, unused-argument
    """
    Test a successfully cancelled retirement request.
    """
    call_command('cancel_user_retirement_request', logged_out_retirement_request.original_email)
    output = capsys.readouterr().out
    # Confirm that no retirement status exists for the user.
    with pytest.raises(UserRetirementStatus.DoesNotExist):
        UserRetirementStatus.objects.get(original_email=logged_out_retirement_request.user.email)
    # Confirm that no retirement request exists for the user.
    with pytest.raises(UserRetirementRequest.DoesNotExist):
        UserRetirementRequest.objects.get(user=logged_out_retirement_request.user)
    # Ensure user can be retrieved using the original email address.
    user = User.objects.get(email=logged_out_retirement_request.original_email)
    # Ensure the user has a usable password so they can go through the reset flow
    assert not user.password.startswith(UNUSABLE_PASSWORD_PREFIX)
    assert "Successfully cancelled retirement request for user with email address" in output
    assert logged_out_retirement_request.original_email in output


def test_cancellation_in_unrecoverable_state(setup_retirement_states, logged_out_retirement_request):  # pylint: disable=redefined-outer-name, unused-argument
    """
    Test a failed cancellation of a retirement request due to the retirement already beginning.
    """
    retiring_lms_state = RetirementState.objects.get(state_name='RETIRING_LMS')
    logged_out_retirement_request.current_state = retiring_lms_state
    logged_out_retirement_request.save()
    with pytest.raises(CommandError, match=r'Retirement requests can only be cancelled for users in the PENDING state'):
        call_command('cancel_user_retirement_request', logged_out_retirement_request.original_email)


def test_cancellation_unknown_email_address(setup_retirement_states, logged_out_retirement_request):  # pylint: disable=redefined-outer-name, unused-argument
    """
    Test attempting to cancel a non-existent request of a user.
    """
    user = UserFactory()
    with pytest.raises(CommandError, match=r'No retirement request with email address'):
        call_command('cancel_user_retirement_request', user.email)
