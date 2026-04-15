"""
Test the retirement_bulk_status_update.py script
"""
from unittest.mock import DEFAULT, patch

from click.testing import CliRunner

from scripts.user_retirement.retirement_bulk_status_update import (
    ERR_BAD_CONFIG,
    ERR_FETCHING,
    ERR_NO_CONFIG,
    ERR_SETUP_FAILED,
    ERR_UPDATING,
    update_statuses,
)
from scripts.user_retirement.tests.retirement_helpers import fake_config_file, get_fake_user_retirement


def _call_script(initial_state='COMPLETE', new_state='PENDING', start_date='2018-01-01', end_date='2018-01-15',
                 rewind_state=False):
    """
    Call the bulk update statuses script with the given params and a generic config file.
    Returns the CliRunner.invoke results
    """
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open('test_config.yml', 'w') as f:
            fake_config_file(f)
        args = [
            '--config_file', 'test_config.yml',
            '--initial_state', initial_state,
            '--start_date', start_date,
            '--end_date', end_date
        ]
        args.extend(['--new_state', new_state]) if new_state else None
        args.append('--rewind-state') if rewind_state else None
        result = runner.invoke(
            update_statuses,
            args=args
        )
    print(result)
    print(result.output)
    return result


def fake_learners_to_retire(**overrides):
    """
    A simple hard-coded list of fake learners with the only piece of
    information this script cares about.
    """

    return [
        get_fake_user_retirement(**{"original_username": "user1", **overrides}),
        get_fake_user_retirement(**{"original_username": "user2", **overrides}),
        get_fake_user_retirement(**{"original_username": "user3", **overrides}),
    ]


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    update_learner_retirement_state=DEFAULT
)
def test_successful_update(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']
    mock_update_learner_state = kwargs['update_learner_retirement_state']

    mock_get_learners.return_value = fake_learners_to_retire()

    result = _call_script()

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()
    assert mock_update_learner_state.call_count == 3

    assert result.exit_code == 0
    assert 'Bulk update complete' in result.output


def test_no_config():
    runner = CliRunner()
    result = runner.invoke(
        update_statuses,
        args=[
            '--initial_state', 'COMPLETE',
            '--new_state', 'PENDING',
            '--start_date', '2018-01-01',
            '--end_date', '2018-01-15'
        ]
    )
    assert result.exit_code == ERR_NO_CONFIG
    assert 'No config file passed in.' in result.output


def test_bad_config():
    runner = CliRunner()
    result = runner.invoke(
        update_statuses,
        args=[
            '--config_file', 'does_not_exist.yml',
            '--initial_state', 'COMPLETE',
            '--new_state', 'PENDING',
            '--start_date', '2018-01-01',
            '--end_date', '2018-01-15'
        ]
    )
    assert result.exit_code == ERR_BAD_CONFIG
    assert 'does_not_exist.yml' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    update_learner_retirement_state=DEFAULT
)
def test_successful_rewind(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']
    mock_update_learner_state = kwargs['update_learner_retirement_state']

    mock_get_learners.return_value = fake_learners_to_retire(current_state_name='ERRORED')

    result = _call_script(new_state=None, rewind_state=True)

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()
    assert mock_update_learner_state.call_count == 3

    assert result.exit_code == 0
    assert 'Bulk update complete' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    update_learner_retirement_state=DEFAULT
)
def test_rewind_bad_args(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']

    mock_get_learners.return_value = fake_learners_to_retire(current_state_name='ERRORED')

    result = _call_script(rewind_state=True)

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()

    assert result.exit_code == ERR_BAD_CONFIG
    assert 'boolean rewind_state or a new state to set learners to' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.__init__', side_effect=Exception)
def test_setup_failed(*_):
    result = _call_script()
    assert result.exit_code == ERR_SETUP_FAILED


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.get_learners_by_date_and_status', side_effect=Exception)
def test_bad_fetch(*_):
    result = _call_script()
    assert result.exit_code == ERR_FETCHING
    assert 'Unexpected error occurred fetching users to update!' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.get_learners_by_date_and_status',
       return_value=fake_learners_to_retire())
@patch('scripts.user_retirement.utils.edx_api.LmsApi.update_learner_retirement_state', side_effect=Exception)
def test_bad_update(*_):
    result = _call_script()
    assert result.exit_code == ERR_UPDATING
    assert 'Unexpected error occurred updating users!' in result.output
