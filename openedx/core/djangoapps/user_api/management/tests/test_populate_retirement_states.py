"""
Test the populate_retirement_states management command
"""


import copy

import pytest
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.management.commands.populate_retirement_states import START_STATE
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus

pytestmark = pytest.mark.django_db


def test_successful_create(settings):
    """
    Run the command with default states for a successful initial population
    """
    call_command('populate_retirement_states')
    curr_states = RetirementState.objects.all().values_list('state_name', flat=True)
    assert list(curr_states) == settings.RETIREMENT_STATES


def test_successful_update(settings):
    """
    Run the command with expected inputs for a successful update
    """
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)
    settings.RETIREMENT_STATES.insert(3, 'FOO_START')
    settings.RETIREMENT_STATES.insert(4, 'FOO_COMPLETE')

    call_command('populate_retirement_states')
    curr_states = RetirementState.objects.all().values_list('state_name', flat=True)
    assert list(curr_states) == settings.RETIREMENT_STATES


def test_no_states(settings):
    """
    Test with empty settings.RETIREMENT_STATES
    """
    settings.RETIREMENT_STATES = None
    with pytest.raises(CommandError, match=r'settings.RETIREMENT_STATES does not exist or is empty.'):
        call_command('populate_retirement_states')

    settings.RETIREMENT_STATES = []
    with pytest.raises(CommandError, match=r'settings.RETIREMENT_STATES does not exist or is empty.'):
        call_command('populate_retirement_states')


def test_missing_required_states_start(settings):
    """
    Test with missing PENDING
    """
    # This is used throughout this file to force pytest to actually revert our settings changes.
    # Since we're modifying the list and not directly modifying the settings it doesn't get picked
    # up here:
    # https://github.com/pytest-dev/pytest-django/blob/master/pytest_django/fixtures.py#L254
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)

    # Remove "PENDING" state
    del settings.RETIREMENT_STATES[0]

    with pytest.raises(CommandError, match=r'does not contain all required states'):
        call_command('populate_retirement_states')


def test_missing_required_states_end(settings):
    """
    Test with missing required end states
    """
    # Remove last state, a required dead end state
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)
    del settings.RETIREMENT_STATES[-1]

    with pytest.raises(CommandError, match=r'does not contain all required states'):
        call_command('populate_retirement_states')


def test_out_of_order_start_state(settings):
    """
    Test with PENDING somewhere other than the beginning
    """
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)
    del settings.RETIREMENT_STATES[0]
    settings.RETIREMENT_STATES.insert(4, 'PENDING')

    with pytest.raises(CommandError, match=f'{START_STATE} must be the first state'):
        call_command('populate_retirement_states')


def test_out_of_order_end_states(settings):
    """
    Test with missing PENDING and/or end states
    """
    # Remove last state, a required dead end state
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)
    del settings.RETIREMENT_STATES[-1]
    settings.RETIREMENT_STATES.insert(-2, 'COMPLETE')

    with pytest.raises(CommandError, match=r'in this order'):
        call_command('populate_retirement_states')


def test_end_states_not_at_end(settings):
    """
    Test putting a state after the end states
    """
    settings.RETIREMENT_STATES = copy.deepcopy(settings.RETIREMENT_STATES)
    settings.RETIREMENT_STATES.append('ANOTHER_STATE')
    with pytest.raises(CommandError, match=r'in this order'):
        call_command('populate_retirement_states')


def test_users_in_bad_states():
    """
    Test that having users in the process of retirement cause this to fail
    """
    user = UserFactory()

    # First populate the table
    call_command('populate_retirement_states')

    # Create a UserRetirementStatus in an active state
    retirement = UserRetirementStatus.create_retirement(user)
    retirement.current_state = RetirementState.objects.get(state_name='LOCKING_ACCOUNT')
    retirement.save()

    # Now try to update
    with pytest.raises(CommandError, match=r'Users are currently being processed'):
        call_command('populate_retirement_states')
