# coding=utf-8
"""
Test the retire_one_learner.py script
"""

import csv
import os
import time
import unicodedata
from datetime import date
from unittest.mock import DEFAULT, patch

from click.testing import CliRunner
from six import PY2, itervalues

from scripts.user_retirement.retirement_partner_report import (
    DEFAULT_FIELD_HEADINGS,
    ERR_BAD_CONFIG,
    ERR_BAD_SECRETS,
    ERR_CLEANUP,
    ERR_DRIVE_LISTING,
    ERR_FETCHING_LEARNERS,
    ERR_NO_CONFIG,
    ERR_NO_OUTPUT_DIR,
    ERR_NO_SECRETS,
    ERR_REPORTING,
    ERR_SETUP_FAILED,
    ERR_UNKNOWN_ORG,
    LEARNER_CREATED_KEY,
    LEARNER_ORIGINAL_USERNAME_KEY,
    ORGS_CONFIG_FIELD_HEADINGS_KEY,
    ORGS_CONFIG_KEY,
    ORGS_CONFIG_LEARNERS_KEY,
    ORGS_CONFIG_ORG_KEY,
    ORGS_KEY,
    REPORTING_FILENAME_PREFIX,
    SETUP_LMS_OR_EXIT,
    _generate_report_files_or_exit,  # pylint: disable=protected-access
    _get_orgs_and_learners_or_exit,  # pylint: disable=protected-access
    generate_report,
)
from scripts.user_retirement.tests.retirement_helpers import (
    FAKE_ORGS,
    TEST_PLATFORM_NAME,
    fake_config_file,
    fake_google_secrets_file,
    flatten_partner_list,
)

TEST_CONFIG_YML_NAME = 'test_config.yml'
TEST_GOOGLE_SECRETS_FILENAME = 'test_google_secrets.json'
DELETION_TIME = time.strftime("%Y-%m-%dT%H:%M:%S")
UNICODE_NAME_CONSTANT = '阿碧'
USER_ID = '12345'
TEST_ORGS_CONFIG = [
    {
        ORGS_CONFIG_ORG_KEY: 'orgCustom',
        ORGS_CONFIG_FIELD_HEADINGS_KEY: ['heading_1', 'heading_2', 'heading_3']
    },
    {
        ORGS_CONFIG_ORG_KEY: 'otherCustomOrg',
        ORGS_CONFIG_FIELD_HEADINGS_KEY: ['unique_id']
    }
]
DEFAULT_FIELD_VALUES = {
    'user_id': USER_ID,
    LEARNER_ORIGINAL_USERNAME_KEY: 'username',
    'original_email': 'invalid',
    'original_name': UNICODE_NAME_CONSTANT,
    'deletion_completed': DELETION_TIME
}


def _call_script(expect_success=True, expected_num_rows=10, config_orgs=None, expected_fields=None):
    """
    Call the retired learner script with the given username and a generic, temporary config file.
    Returns the CliRunner.invoke results
    """
    if expected_fields is None:
        expected_fields = DEFAULT_FIELD_VALUES
    if config_orgs is None:
        config_orgs = FAKE_ORGS

    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(TEST_CONFIG_YML_NAME, 'w') as config_f:
            fake_config_file(config_f, config_orgs)
        with open(TEST_GOOGLE_SECRETS_FILENAME, 'w') as secrets_f:
            fake_google_secrets_file(secrets_f)

        tmp_output_dir = 'test_output_dir'
        os.mkdir(tmp_output_dir)

        result = runner.invoke(
            generate_report,
            args=[
                '--config_file',
                TEST_CONFIG_YML_NAME,
                '--google_secrets_file',
                TEST_GOOGLE_SECRETS_FILENAME,
                '--output_dir',
                tmp_output_dir
            ]
        )

        print(result)
        print(result.output)

        if expect_success:
            assert result.exit_code == 0

            if config_orgs is None:
                # These are the orgs
                config_org_vals = flatten_partner_list(FAKE_ORGS.values())
            else:
                config_org_vals = flatten_partner_list(config_orgs.values())

            # Normalize the unicode as the script does
            if PY2:
                config_org_vals = [org.decode('utf-8') for org in config_org_vals]

            config_org_vals = [unicodedata.normalize('NFKC', org) for org in config_org_vals]

            for org in config_org_vals:
                outfile = os.path.join(tmp_output_dir, '{}_{}_{}_{}.csv'.format(
                    REPORTING_FILENAME_PREFIX, TEST_PLATFORM_NAME, org, date.today().isoformat()
                ))

                with open(outfile, 'r') as csvfile:
                    reader = csv.DictReader(csvfile)
                    rows = []
                    for row in reader:
                        for field_key in expected_fields:
                            field_value = expected_fields[field_key]
                            assert field_value in row[field_key]
                        rows.append(row)

                # Confirm the number of rows
                assert len(rows) == expected_num_rows
    return result


def _fake_retirement_report_user(seed_val, user_orgs=None, user_orgs_config=None):
    """
    Creates unique user to populate a fake report with.
    - seed_val is a number or other unique value for this user, will be formatted into
      user values to make sure they're distinct.
    - user_orgs, if given, should be a list of orgs that will be associated with the user.
    - user_orgs_config, if given, should be a list of dicts mapping orgs to their customized
        field headings. These orgs will also be associated with the user.
    """
    user_info = {
        'user_id': USER_ID,
        LEARNER_ORIGINAL_USERNAME_KEY: 'username_{}'.format(seed_val),
        'original_email': 'user_{}@foo.invalid'.format(seed_val),
        'original_name': '{} {}'.format(UNICODE_NAME_CONSTANT, seed_val),
        LEARNER_CREATED_KEY: DELETION_TIME,
    }

    if user_orgs is not None:
        user_info[ORGS_KEY] = user_orgs

    if user_orgs_config is not None:
        user_info[ORGS_CONFIG_KEY] = user_orgs_config

    return user_info


def _fake_retirement_report(num_users=10, user_orgs=None, user_orgs_config=None):
    """
    Fake the output of a retirement report with unique users
    """
    return [_fake_retirement_report_user(i, user_orgs, user_orgs_config) for i in range(num_users)]


@patch('scripts.user_retirement.utils.edx_api.LmsApi.retirement_partner_report')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
def test_report_generation_multiple_partners(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_retirement_report = args[1]

    org_1_users = [_fake_retirement_report_user(i, user_orgs=['org1']) for i in range(1, 3)]
    org_2_users = [_fake_retirement_report_user(i, user_orgs=['org2']) for i in range(3, 5)]

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_retirement_report.return_value = org_1_users + org_2_users

    config = {
        'client_id': 'bogus id',
        'client_secret': 'supersecret',
        'base_urls': {
            'lms': 'https://stage-edx-edxapp.edx.invalid/',
        },
        'org_partner_mapping': {
            'org1': ['Org1X'],
            'org2': ['Org2X', 'Org2Xb'],
        }
    }
    SETUP_LMS_OR_EXIT(config)
    orgs, usernames = _get_orgs_and_learners_or_exit(config)

    assert usernames == [{'original_username': 'username_{}'.format(username)} for username in range(1, 5)]

    def _get_learner_usernames(org_data):
        return [learner['original_username'] for learner in org_data['learners']]

    assert _get_learner_usernames(orgs['Org1X']) == ['username_1', 'username_2']

    # Org2X and Org2Xb should have the same learners in their report data
    assert _get_learner_usernames(orgs['Org2X']) == _get_learner_usernames(orgs['Org2Xb']) == ['username_3',
                                                                                               'username_4']

    # Org2X and Org2Xb report data should match
    assert orgs['Org2X'] == orgs['Org2Xb']


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_file_in_folder')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.list_permissions_for_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_comments_for_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT,
    retirement_partner_cleanup=DEFAULT
)
def test_successful_report(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_create_comments = args[1]
    mock_list_permissions = args[2]
    mock_walk_files = args[3]
    mock_create_files = args[4]
    mock_driveapi = args[5]
    mock_retirement_report = kwargs['retirement_partner_report']
    mock_retirement_cleanup = kwargs['retirement_partner_cleanup']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_create_comments.return_value = None
    fake_partners = list(itervalues(FAKE_ORGS))
    # Generate the list_permissions return value.
    # The first few have POCs.
    mock_list_permissions.return_value = {
        'folder' + partner: [
            {'emailAddress': 'some.contact@example.com'},  # The POC.
            {'emailAddress': 'another.contact@edx.org'},
        ]
        for partner in flatten_partner_list(fake_partners[:2])
    }
    # The last one does not have any POCs.
    mock_list_permissions.return_value.update({
        'folder' + partner: [
            {'emailAddress': 'another.contact@edx.org'},
        ]
        for partner in fake_partners[2]
    })
    mock_walk_files.return_value = [{'name': partner, 'id': 'folder' + partner} for partner in
                                    flatten_partner_list(FAKE_ORGS.values())]
    mock_create_files.side_effect = ['foo', 'bar', 'baz', 'qux']
    mock_driveapi.return_value = None
    mock_retirement_report.return_value = _fake_retirement_report(user_orgs=list(FAKE_ORGS.keys()))

    result = _call_script()

    # Make sure we're getting the LMS token
    mock_get_access_token.assert_called_once()

    # Make sure that we get the report
    mock_retirement_report.assert_called_once()

    # Make sure we tried to upload the files
    assert mock_create_files.call_count == 4

    # Make sure we tried to add comments to the files
    assert mock_create_comments.call_count == 1
    # First [0] returns all positional args, second [0] gets the first positional arg.
    create_comments_file_ids, create_comments_messages = zip(*mock_create_comments.call_args[0][0])
    assert set(create_comments_file_ids).issubset(set(['foo', 'bar', 'baz', 'qux']))
    assert len(create_comments_file_ids) == 2  # only two comments created, the third didn't have a POC.
    assert all('+some.contact@example.com' in msg for msg in create_comments_messages)
    assert all('+another.contact@edx.org' not in msg for msg in create_comments_messages)
    assert 'WARNING: could not find a POC' in result.output

    # Make sure we tried to remove the users from the queue
    mock_retirement_cleanup.assert_called_with(
        [{'original_username': user[LEARNER_ORIGINAL_USERNAME_KEY]} for user in mock_retirement_report.return_value]
    )

    assert 'All reports completed and uploaded to Google.' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_file_in_folder')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.list_permissions_for_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_comments_for_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT,
    retirement_partner_cleanup=DEFAULT
)
def test_successful_report_org_config(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_create_comments = args[1]
    mock_list_permissions = args[2]
    mock_walk_files = args[3]
    mock_create_files = args[4]
    mock_driveapi = args[5]
    mock_retirement_report = kwargs['retirement_partner_report']
    mock_retirement_cleanup = kwargs['retirement_partner_cleanup']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_create_comments.return_value = None
    fake_custom_orgs = {
        'orgCustom': ['firstBlah']
    }
    fake_partners = list(itervalues(fake_custom_orgs))
    mock_list_permissions.return_value = {
        'folder' + partner: [
            {'emailAddress': 'some.contact@example.com'},  # The POC.
            {'emailAddress': 'another.contact@edx.org'},
        ]
        for partner in flatten_partner_list(fake_partners[:2])
    }
    mock_walk_files.return_value = [{'name': partner, 'id': 'folder' + partner} for partner in
                                    flatten_partner_list(fake_custom_orgs.values())]
    mock_create_files.side_effect = ['foo', 'bar', 'baz']
    mock_driveapi.return_value = None
    expected_num_users = 1

    orgs_config = [
        {
            ORGS_CONFIG_ORG_KEY: 'orgCustom',
            ORGS_CONFIG_FIELD_HEADINGS_KEY: ['heading_1', 'heading_2', 'heading_3']
        }
    ]

    # Input from the LMS
    report_data = [
        {
            'heading_1': 'h1val',
            'heading_2': 'h2val',
            'heading_3': 'h3val',
            LEARNER_ORIGINAL_USERNAME_KEY: 'blah',
            LEARNER_CREATED_KEY: DELETION_TIME,
            ORGS_CONFIG_KEY: orgs_config
        }
    ]

    # Resulting csv file content
    expected_fields = {
        'heading_1': 'h1val',
        'heading_2': 'h2val',
        'heading_3': 'h3val',
    }

    mock_retirement_report.return_value = report_data

    result = _call_script(expected_num_rows=expected_num_users, config_orgs=fake_custom_orgs,
                          expected_fields=expected_fields)

    # Make sure we're getting the LMS token
    mock_get_access_token.assert_called_once()

    # Make sure that we get the report
    mock_retirement_report.assert_called_once()

    # Make sure we tried to remove the users from the queue
    mock_retirement_cleanup.assert_called_with(
        [{'original_username': user[LEARNER_ORIGINAL_USERNAME_KEY]} for user in mock_retirement_report.return_value]
    )

    assert 'All reports completed and uploaded to Google.' in result.output


def test_no_config():
    runner = CliRunner()
    result = runner.invoke(generate_report)
    print(result.output)
    assert result.exit_code == ERR_NO_CONFIG
    assert 'No config file' in result.output


def test_no_secrets():
    runner = CliRunner()
    result = runner.invoke(generate_report, args=['--config_file', 'does_not_exist.yml'])
    print(result.output)
    assert result.exit_code == ERR_NO_SECRETS
    assert 'No secrets file' in result.output


def test_no_output_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(TEST_CONFIG_YML_NAME, 'w') as config_f:
            config_f.write('irrelevant')

        with open(TEST_GOOGLE_SECRETS_FILENAME, 'w') as config_f:
            config_f.write('irrelevant')

        result = runner.invoke(
            generate_report,
            args=[
                '--config_file',
                TEST_CONFIG_YML_NAME,
                '--google_secrets_file',
                TEST_GOOGLE_SECRETS_FILENAME
            ]
        )
    print(result.output)
    assert result.exit_code == ERR_NO_OUTPUT_DIR
    assert 'No output_dir' in result.output


def test_bad_config():
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(TEST_CONFIG_YML_NAME, 'w') as config_f:
            config_f.write(']this is bad yaml')

        with open(TEST_GOOGLE_SECRETS_FILENAME, 'w') as config_f:
            config_f.write('{this is bad json but we should not get to parsing it')

        tmp_output_dir = 'test_output_dir'
        os.mkdir(tmp_output_dir)

        result = runner.invoke(
            generate_report,
            args=[
                '--config_file',
                TEST_CONFIG_YML_NAME,
                '--google_secrets_file',
                TEST_GOOGLE_SECRETS_FILENAME,
                '--output_dir',
                tmp_output_dir
            ]
        )
        print(result.output)
        assert result.exit_code == ERR_BAD_CONFIG
        assert 'Failed to read' in result.output


def test_bad_secrets():
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(TEST_CONFIG_YML_NAME, 'w') as config_f:
            fake_config_file(config_f)

        with open(TEST_GOOGLE_SECRETS_FILENAME, 'w') as config_f:
            config_f.write('{this is bad json')

        tmp_output_dir = 'test_output_dir'
        os.mkdir(tmp_output_dir)

        result = runner.invoke(
            generate_report,
            args=[
                '--config_file',
                TEST_CONFIG_YML_NAME,
                '--google_secrets_file',
                TEST_GOOGLE_SECRETS_FILENAME,
                '--output_dir',
                tmp_output_dir
            ]
        )
        print(result.output)
        assert result.exit_code == ERR_BAD_SECRETS
        assert 'Failed to read' in result.output


def test_bad_output_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(TEST_CONFIG_YML_NAME, 'w') as config_f:
            fake_config_file(config_f)

        with open(TEST_GOOGLE_SECRETS_FILENAME, 'w') as config_f:
            fake_google_secrets_file(config_f)

        result = runner.invoke(
            generate_report,
            args=[
                '--config_file',
                TEST_CONFIG_YML_NAME,
                '--google_secrets_file',
                TEST_GOOGLE_SECRETS_FILENAME,
                '--output_dir',
                'does_not_exist/at_all'
            ]
        )
        print(result.output)
        assert result.exit_code == ERR_NO_OUTPUT_DIR
        assert 'or path does not exist' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
def test_setup_failed(*args):
    mock_get_access_token = args[0]
    mock_get_access_token.side_effect = Exception('boom')

    result = _call_script(expect_success=False)
    mock_get_access_token.assert_called_once()
    assert result.exit_code == ERR_SETUP_FAILED


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT)
def test_fetching_learners_failed(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_walk_files = args[1]
    mock_drive_init = args[2]
    mock_retirement_report = kwargs['retirement_partner_report']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_walk_files.return_value = [{'name': 'dummy_file_name', 'id': 'dummy_file_id'}]
    mock_drive_init.return_value = None
    mock_retirement_report.side_effect = Exception('failed to get learners')

    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_FETCHING_LEARNERS
    assert 'failed to get learners' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
def test_listing_folders_failed(*args):
    mock_get_access_token = args[0]
    mock_walk_files = args[1]
    mock_drive_init = args[2]

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_walk_files.side_effect = [[], Exception()]
    mock_drive_init.return_value = None

    # call it once; this time walk_files will return an empty list.
    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_DRIVE_LISTING
    assert 'Finding partner directories on Drive failed' in result.output

    # call it a second time; this time walk_files will throw an exception.
    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_DRIVE_LISTING
    assert 'Finding partner directories on Drive failed' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT)
def test_unknown_org(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_drive_init = args[2]
    mock_retirement_report = kwargs['retirement_partner_report']

    mock_drive_init.return_value = None
    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)

    orgs = ['orgA', 'orgB']

    mock_retirement_report.return_value = [_fake_retirement_report_user(i, orgs, TEST_ORGS_CONFIG) for i in range(10)]

    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_UNKNOWN_ORG
    assert 'orgA' in result.output
    assert 'orgB' in result.output
    assert 'orgCustom' in result.output
    assert 'otherCustomOrg' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT)
def test_unknown_org_custom(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_drive_init = args[2]
    mock_retirement_report = kwargs['retirement_partner_report']

    mock_drive_init.return_value = None
    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)

    custom_orgs_config = [
        {
            ORGS_CONFIG_ORG_KEY: 'singleCustomOrg',
            ORGS_CONFIG_FIELD_HEADINGS_KEY: ['first_heading', 'second_heading']
        }
    ]

    mock_retirement_report.return_value = [_fake_retirement_report_user(i, None, custom_orgs_config) for i in range(2)]

    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_UNKNOWN_ORG
    assert 'organizations {\'singleCustomOrg\'} do not exist' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch('unicodecsv.DictWriter')
@patch('scripts.user_retirement.utils.edx_api.LmsApi.retirement_partner_report')
def test_reporting_error(*args):
    mock_retirement_report = args[0]
    mock_dictwriter = args[1]
    mock_get_access_token = args[2]
    mock_drive_init = args[4]

    error_msg = 'Fake unable to write csv'

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_dictwriter.side_effect = Exception(error_msg)
    mock_drive_init.return_value = None
    mock_retirement_report.return_value = _fake_retirement_report(user_orgs=list(FAKE_ORGS.keys()))

    result = _call_script(expect_success=False)

    assert result.exit_code == ERR_REPORTING
    assert error_msg in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.list_permissions_for_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_comments_for_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_file_in_folder')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT,
    retirement_partner_cleanup=DEFAULT
)
def test_cleanup_error(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_create_files = args[1]
    mock_driveapi = args[2]
    mock_walk_files = args[3]
    mock_create_comments = args[4]
    mock_list_permissions = args[5]
    mock_retirement_report = kwargs['retirement_partner_report']
    mock_retirement_cleanup = kwargs['retirement_partner_cleanup']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_create_files.return_value = True
    mock_driveapi.return_value = None
    mock_walk_files.return_value = [{'name': partner, 'id': 'folder' + partner} for partner in
                                    flatten_partner_list(FAKE_ORGS.values())]
    fake_partners = list(itervalues(FAKE_ORGS))
    # Generate the list_permissions return value.
    mock_list_permissions.return_value = {
        'folder' + partner: [
            {'emailAddress': 'some.contact@example.com'},  # The POC.
            {'emailAddress': 'another.contact@edx.org'},
            {'emailAddress': 'third@edx.org'}
        ]
        for partner in flatten_partner_list(fake_partners)
    }
    mock_create_comments.return_value = None

    mock_retirement_report.return_value = _fake_retirement_report(user_orgs=list(FAKE_ORGS.keys()))
    mock_retirement_cleanup.side_effect = Exception('Mock cleanup exception')

    result = _call_script(expect_success=False)

    mock_retirement_cleanup.assert_called_with(
        [{'original_username': user[LEARNER_ORIGINAL_USERNAME_KEY]} for user in mock_retirement_report.return_value]
    )

    assert result.exit_code == ERR_CLEANUP
    assert 'Users may be stuck in the processing state!' in result.output


@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.__init__')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_file_in_folder')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.walk_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.list_permissions_for_files')
@patch('scripts.user_retirement.utils.thirdparty_apis.google_api.DriveApi.create_comments_for_files')
@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token')
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    retirement_partner_report=DEFAULT,
    retirement_partner_cleanup=DEFAULT
)
def test_google_unicode_folder_names(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_create_comments = args[1]
    mock_list_permissions = args[2]
    mock_walk_files = args[3]
    mock_create_files = args[4]
    mock_driveapi = args[5]
    mock_retirement_report = kwargs['retirement_partner_report']
    mock_retirement_cleanup = kwargs['retirement_partner_cleanup']

    mock_get_access_token.return_value = ('THIS_IS_A_JWT', None)
    mock_list_permissions.return_value = {
        'folder' + partner: [
            {'emailAddress': 'some.contact@example.com'},
            {'emailAddress': 'another.contact@edx.org'},
        ]
        for partner in [
            unicodedata.normalize('NFKC', u'TéstX'),
            unicodedata.normalize('NFKC', u'TéstX2'),
            unicodedata.normalize('NFKC', u'TéstX3'),
        ]
    }
    mock_walk_files.return_value = [
        {'name': partner, 'id': 'folder' + partner}
        for partner in [
            unicodedata.normalize('NFKC', u'TéstX'),
            unicodedata.normalize('NFKC', u'TéstX2'),
            unicodedata.normalize('NFKC', u'TéstX3'),
        ]
    ]
    mock_create_files.side_effect = ['foo', 'bar', 'baz']
    mock_driveapi.return_value = None
    mock_retirement_report.return_value = _fake_retirement_report(user_orgs=list(FAKE_ORGS.keys()))

    config_orgs = {
        'org1': [unicodedata.normalize('NFKC', u'TéstX')],
        'org2': [unicodedata.normalize('NFD', u'TéstX2')],
        'org3': [unicodedata.normalize('NFKD', u'TéstX3')],
    }

    result = _call_script(config_orgs=config_orgs)

    # Make sure we're getting the LMS token
    mock_get_access_token.assert_called_once()

    # Make sure that we get the report
    mock_retirement_report.assert_called_once()

    # Make sure we tried to upload the files
    assert mock_create_files.call_count == 3

    # Make sure we tried to add comments to the files
    assert mock_create_comments.call_count == 1
    # First [0] returns all positional args, second [0] gets the first positional arg.
    create_comments_file_ids, create_comments_messages = zip(*mock_create_comments.call_args[0][0])
    assert set(create_comments_file_ids) == set(['foo', 'bar', 'baz'])
    assert all('+some.contact@example.com' in msg for msg in create_comments_messages)
    assert all('+another.contact@edx.org' not in msg for msg in create_comments_messages)

    # Make sure we tried to remove the users from the queue
    mock_retirement_cleanup.assert_called_with(
        [{'original_username': user[LEARNER_ORIGINAL_USERNAME_KEY]} for user in mock_retirement_report.return_value]
    )

    assert 'All reports completed and uploaded to Google.' in result.output


def test_file_content_custom_headings():
    runner = CliRunner()
    with runner.isolated_filesystem():
        config = {'partner_report_platform_name': 'fake_platform_name'}
        tmp_output_dir = 'test_output_dir'
        os.mkdir(tmp_output_dir)

        # Custom headings and values
        ch1 = 'special_id'
        ch1v = '134456765432'
        ch2 = 'alternate_heading_for_email'
        ch2v = 'zxcvbvcxz@blah.com'
        custom_field_headings = [ch1, ch2]

        org_name = 'my_delightful_org'
        username = 'unique_user'
        learner_data = [
            {
                ch1: ch1v,
                ch2: ch2v,
                LEARNER_ORIGINAL_USERNAME_KEY: username,
                LEARNER_CREATED_KEY: DELETION_TIME,
            }
        ]
        report_data = {
            org_name: {
                ORGS_CONFIG_FIELD_HEADINGS_KEY: custom_field_headings,
                ORGS_CONFIG_LEARNERS_KEY: learner_data
            }
        }

        partner_filenames = _generate_report_files_or_exit(config, report_data, tmp_output_dir)

        assert len(partner_filenames) == 1
        filename = partner_filenames[org_name]
        with open(filename) as f:
            file_content = f.read()

            # Custom field headings
            for ch in custom_field_headings:
                # Verify custom field headings are present
                assert ch in file_content
            # Verify custom field values are present
            assert ch1v in file_content
            assert ch2v in file_content

            # Default field headings
            for h in DEFAULT_FIELD_HEADINGS:
                # Verify default field headings are not present
                assert h not in file_content
            # Verify default field values are not present
            assert username not in file_content
            assert DELETION_TIME not in file_content
