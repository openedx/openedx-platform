"""
Tests for the Salesforce API functionality
"""
import logging
from contextlib import contextmanager
from unittest import mock

import pytest
from simple_salesforce import SalesforceError

from scripts.user_retirement.utils.thirdparty_apis import salesforce_api


@pytest.fixture
def test_learner():
    return {'original_email': 'foo@bar.com'}


def make_api():
    """
    Helper function to create salesforce api object
    """
    return salesforce_api.SalesforceApi("user", "pass", "key", "domain", "user")


@contextmanager
def mock_get_user():
    """
    Context manager method to mock getting the assignee user id when the api object is created
    """
    with mock.patch(
        'scripts.user_retirement.utils.thirdparty_apis.salesforce_api.SalesforceApi.get_user_id'
    ) as getuser:
        getuser.return_value = "userid"
        yield


def test_no_assignee_email():
    with mock.patch(
        'scripts.user_retirement.utils.thirdparty_apis.salesforce_api.SalesforceApi.get_user_id'
    ) as getuser:
        getuser.return_value = None
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            with pytest.raises(Exception) as exc:  # noqa: PT011
                make_api()
            print(str(exc))
            assert 'Could not find Salesforce user with username user' in str(exc)


def test_retire_no_email():
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            with pytest.raises(TypeError) as exc:
                make_api().retire_learner({})
            assert 'Expected an email address for user to delete, but received None.' in str(exc)


def test_retire_get_id_error(test_learner):  # pylint: disable=redefined-outer-name
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            api._sf.query.side_effect = SalesforceError("", "", "", "")  # pylint: disable=protected-access
            with pytest.raises(SalesforceError):
                api.retire_learner(test_learner)


# pylint: disable=protected-access
def test_escape_email():
    with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
        api = make_api()
        mock_response = {'totalSize': 0, 'records': []}
        api._sf.query.return_value = mock_response
        api.get_lead_ids_by_email("Robert'); DROP TABLE students;--")
        api._sf.query.assert_called_with(
            "SELECT Id FROM Lead WHERE Email = 'Robert\\'); DROP TABLE students;--'"
        )


# pylint: disable=protected-access
def test_escape_username():
    with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
        api = make_api()
        mock_response = {'totalSize': 0, 'records': []}
        api._sf.query.return_value = mock_response
        api.get_user_id("Robert'); DROP TABLE students;--")
        api._sf.query.assert_called_with(
            "SELECT Id FROM User WHERE Username = 'Robert\\'); DROP TABLE students;--'"
        )


def test_retire_learner_not_found(test_learner, caplog):  # pylint: disable=redefined-outer-name
    caplog.set_level(logging.INFO)
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            mock_response = {'totalSize': 0, 'records': []}
            api._sf.query.return_value = mock_response  # pylint: disable=protected-access
            api.retire_learner(test_learner)
            assert not api._sf.Task.create.called  # pylint: disable=protected-access
            assert 'No action taken because no lead was found in Salesforce.' in caplog.text


def test_retire_task_error(test_learner, caplog):  # pylint: disable=redefined-outer-name
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            mock_query_response = {'totalSize': 1, 'records': [{'Id': 1}]}
            api._sf.query.return_value = mock_query_response  # pylint: disable=protected-access
            mock_task_response = {'success': False, 'errors': ["This is an error!"]}
            api._sf.Task.create.return_value = mock_task_response  # pylint: disable=protected-access
            with pytest.raises(Exception) as exc:  # noqa: PT011
                api.retire_learner(test_learner)
            assert "Errors while creating task:" in caplog.text
            assert "This is an error!" in caplog.text
            assert "Unable to create retirement task for email foo@bar.com" in str(exc)


def test_retire_task_exception(test_learner):  # pylint: disable=redefined-outer-name
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            mock_query_response = {'totalSize': 1, 'records': [{'Id': 1}]}
            api._sf.query.return_value = mock_query_response  # pylint: disable=protected-access
            api._sf.Task.create.side_effect = SalesforceError("", "", "", "")  # pylint: disable=protected-access
            with pytest.raises(SalesforceError):
                api.retire_learner(test_learner)


def test_retire_success(test_learner, caplog):  # pylint: disable=redefined-outer-name
    caplog.set_level(logging.INFO)
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            mock_query_response = {'totalSize': 1, 'records': [{'Id': 1}]}
            api._sf.query.return_value = mock_query_response  # pylint: disable=protected-access
            mock_task_response = {'success': True, 'id': 'task-id'}
            api._sf.Task.create.return_value = mock_task_response  # pylint: disable=protected-access
            api.retire_learner(test_learner)
            assert "Successfully salesforce task created task task-id" in caplog.text


def test_retire_multiple_learners(test_learner, caplog):  # pylint: disable=redefined-outer-name
    caplog.set_level(logging.INFO)
    with mock_get_user():
        with mock.patch('scripts.user_retirement.utils.thirdparty_apis.salesforce_api.Salesforce'):
            api = make_api()
            mock_response = {'totalSize': 2, 'records': [{'Id': 1}, {'Id': 2}]}
            api._sf.query.return_value = mock_response  # pylint: disable=protected-access
            mock_task_response = {'success': True, 'id': 'task-id'}
            api._sf.Task.create.return_value = mock_task_response  # pylint: disable=protected-access
            api.retire_learner(test_learner)
            assert "Multiple Ids returned for Lead with email foo@bar.com" in caplog.text
            assert "Successfully salesforce task created task task-id" in caplog.text
            note = "Notice: Multiple leads were identified with the same email. Please retire all following leads:"
            assert note in api._sf.Task.create.call_args[0][0]['Description']  # pylint: disable=protected-access
