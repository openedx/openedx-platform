"""
Test the get_learners_to_retire.py script
"""
import os
from unittest.mock import DEFAULT, patch

from click.testing import CliRunner
from requests.exceptions import HTTPError

from scripts.user_retirement.get_learners_to_retire import get_learners_to_retire
from scripts.user_retirement.tests.retirement_helpers import fake_config_file, get_fake_user_retirement


def _call_script(expected_user_files, cool_off_days=1, output_dir='test', user_count_error_threshold=200,
                 max_user_batch_size=201):
    """
    Call the retired learner script with the given username and a generic, temporary config file.
    Returns the CliRunner.invoke results
    """
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open('test_config.yml', 'w') as f:
            fake_config_file(f)
        result = runner.invoke(
            get_learners_to_retire,
            args=[
                '--config_file', 'test_config.yml',
                '--cool_off_days', cool_off_days,
                '--output_dir', output_dir,
                '--user_count_error_threshold', user_count_error_threshold,
                '--max_user_batch_size', max_user_batch_size
            ]
        )
        print(result)
        print(result.output)

        # This is the number of users in the mocked call, each should have a file if the number is
        # greater than 0, otherwise a failure is expected and the output dir should not exist
        if expected_user_files:
            assert len(os.listdir(output_dir)) == expected_user_files
        else:
            assert not os.path.exists(output_dir)
    return result


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    learners_to_retire=DEFAULT
)
def test_success(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners_to_retire = kwargs['learners_to_retire']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_get_learners_to_retire.return_value = [
        get_fake_user_retirement(original_username='test_user1'),
        get_fake_user_retirement(original_username='test_user2'),
    ]

    result = _call_script(2)

    # Called once per API we instantiate (LMS, ECommerce, Credentials)
    assert mock_get_access_token.call_count == 1
    mock_get_learners_to_retire.assert_called_once()

    assert result.exit_code == 0


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    learners_to_retire=DEFAULT
)
def test_lms_down(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners_to_retire = kwargs['learners_to_retire']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_get_learners_to_retire.side_effect = HTTPError

    result = _call_script(0)

    # Called once per API we instantiate (LMS, ECommerce, Credentials)
    assert mock_get_access_token.call_count == 1
    mock_get_learners_to_retire.assert_called_once()

    assert result.exit_code == 1


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    learners_to_retire=DEFAULT
)
def test_misconfigured(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners_to_retire = kwargs['learners_to_retire']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_get_learners_to_retire.side_effect = HTTPError

    result = _call_script(0)

    # Called once per API we instantiate (LMS, ECommerce, Credentials)
    assert mock_get_access_token.call_count == 1
    mock_get_learners_to_retire.assert_called_once()

    assert result.exit_code == 1


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    learners_to_retire=DEFAULT
)
def test_too_many_users(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners_to_retire = kwargs['learners_to_retire']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_get_learners_to_retire.return_value = [
        get_fake_user_retirement(original_username='test_user1'),
        get_fake_user_retirement(original_username='test_user2'),
    ]

    result = _call_script(0, user_count_error_threshold=1)

    # Called once per API we instantiate (LMS, ECommerce, Credentials)
    assert mock_get_access_token.call_count == 1
    mock_get_learners_to_retire.assert_called_once()

    assert result.exit_code == -1
    assert 'Too many learners' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    learners_to_retire=DEFAULT
)
def test_users_limit(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners_to_retire = kwargs['learners_to_retire']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_get_learners_to_retire.return_value = [
        get_fake_user_retirement(original_username='test_user1'),
        get_fake_user_retirement(original_username='test_user2'),
    ]

    result = _call_script(1, user_count_error_threshold=200, max_user_batch_size=1)

    # Called once per API we instantiate (LMS, ECommerce, Credentials)
    assert mock_get_access_token.call_count == 1
    mock_get_learners_to_retire.assert_called_once()

    assert result.exit_code == 0
