"""
Test the release_retired_user_email management command
"""


from unittest.mock import patch

import pytest
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # noqa: F401  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,
)
from openedx.core.djangoapps.user_api.models import RetirementState

pytestmark = pytest.mark.django_db


def _retire_user(user, state_name):
    return create_retirement_status(user, state=RetirementState.objects.get(state_name=state_name))


@patch('openedx.core.djangoapps.user_api.management.commands.release_retired_user_email.logger')
@pytest.mark.usefixtures('setup_retirement_states')
def test_releases_email_by_user_id(mock_logger):
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email == f'retired__uid_{user.id}@retired.invalid'
    mock_logger.info.assert_called_with(f'Successfully released email for user {user.id}.')


def test_requires_user_id():
    with pytest.raises(CommandError, match=r'the following arguments are required: --user_id'):
        call_command('release_retired_user_email')


def test_unknown_user_id():
    with pytest.raises(CommandError, match=r'No user found for the given user_id=999999'):
        call_command('release_retired_user_email', user_id=999999)


@pytest.mark.usefixtures('setup_retirement_states')
def test_blocked_while_retirement_in_progress():
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'RETIRING_LMS')

    with pytest.raises(CommandError, match=r'not COMPLETE'):
        call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert User.objects.get(id=user.id).email == 'retired__user_abc123@retired.invalid'


def test_releases_email_when_status_row_archived():
    """
    No UserRetirementStatus row exists for this user (e.g. it was redacted and
    deleted by the partner-report cleanup endpoint), but the email is still in
    the retired-domain format - the command should fall back to that and succeed.
    """
    user = UserFactory(email='retired__user_abc123@retired.invalid')

    call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email == f'retired__uid_{user.id}@retired.invalid'


def test_raises_when_user_does_not_appear_retired():
    """
    No UserRetirementStatus row and a non-retired-looking email - the command
    should refuse via CommandError rather than release the email.
    """
    user = UserFactory(email='still.active@example.com')

    with pytest.raises(CommandError, match=r'does not appear to be a retired user'):
        call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email == 'still.active@example.com'


@patch('openedx.core.djangoapps.user_api.management.commands.release_retired_user_email.logger')
@pytest.mark.usefixtures('setup_retirement_states')
def test_running_twice_is_idempotent(mock_logger):
    """
    A second run against an already-released user must not error - it should
    hit release_retired_learner_email()'s early return and still report success.
    """
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('release_retired_user_email', user_id=user.id)
    user.refresh_from_db()
    released_email = user.email

    call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email == released_email
    mock_logger.info.assert_called_with(f'Successfully released email for user {user.id}.')
