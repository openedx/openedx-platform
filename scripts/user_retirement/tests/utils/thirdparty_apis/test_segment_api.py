"""
Tests for the Segment API functionality
"""
import json
from unittest import mock

import pytest
import requests
from six import text_type

from scripts.user_retirement.tests.retirement_helpers import get_fake_user_retirement
from scripts.user_retirement.utils.thirdparty_apis.segment_api import BULK_REGULATE_URL, SegmentApi

FAKE_AUTH_TOKEN = 'FakeToken'
TEST_SEGMENT_CONFIG = {
    'projects_to_retire': ['project_1', 'project_2'],
    'learner': [get_fake_user_retirement(), ],
    'fake_base_url': 'https://segment.invalid/',
    'fake_auth_token': FAKE_AUTH_TOKEN,
    'fake_workspace': 'FakeEdx',
    'headers': {"Authorization": "Bearer {}".format(FAKE_AUTH_TOKEN), "Content-Type": "application/json"}  # noqa: UP032
}


class FakeResponse:
    """
    Fakes out requests.post response
    """

    def json(self):
        """
        Returns fake Segment retirement response data in the correct format
        """
        return {'regulate_id': 1}

    def raise_for_status(self):
        pass


class FakeErrorResponse:
    """
    Fakes an error response
    """
    status_code = 500
    text = "{'error': 'Test error message'}"

    def json(self):
        """
        Returns fake Segment retirement response error in the correct format
        """
        return json.loads(self.text)

    def raise_for_status(self):
        raise requests.exceptions.HTTPError("", response=self)


@pytest.fixture
def setup_regulation_api():
    """
    Fixture to setup common bulk delete items.
    """
    with mock.patch('requests.post') as mock_post:
        segment = SegmentApi(
            *[TEST_SEGMENT_CONFIG[key] for key in [
                'fake_base_url', 'fake_auth_token', 'fake_workspace'
            ]]
        )

        yield mock_post, segment


def test_bulk_delete_success(setup_regulation_api):  # pylint: disable=redefined-outer-name
    """
    Test simple success case
    """
    mock_post, segment = setup_regulation_api
    mock_post.return_value = FakeResponse()

    learner = TEST_SEGMENT_CONFIG['learner']
    segment.delete_and_suppress_learners(learner, 1000)

    assert mock_post.call_count == 1

    expected_learner = get_fake_user_retirement()
    learners_vals = [
        text_type(expected_learner['user']['id']),
        expected_learner['original_username'],
        expected_learner['ecommerce_segment_id'],
    ]

    fake_json = {
        "regulation_type": "Suppress_With_Delete",
        "attributes": {
            "name": "userId",
            "values": learners_vals
        }
    }

    url = TEST_SEGMENT_CONFIG['fake_base_url'] + BULK_REGULATE_URL.format(TEST_SEGMENT_CONFIG['fake_workspace'])
    mock_post.assert_any_call(
        url, json=fake_json, headers=TEST_SEGMENT_CONFIG['headers']
    )


def test_bulk_delete_error(setup_regulation_api, caplog):  # pylint: disable=redefined-outer-name
    """
    Test simple error case
    """
    mock_post, segment = setup_regulation_api
    mock_post.return_value = FakeErrorResponse()

    learner = TEST_SEGMENT_CONFIG['learner']
    with pytest.raises(Exception):  # noqa: B017, PT011
        segment.delete_and_suppress_learners(learner, 1000)

    assert mock_post.call_count == 4
    assert "Error was encountered for params:" in caplog.text
    assert "9009" in caplog.text
    assert "foo_username" in caplog.text
    assert "ecommerce-90" in caplog.text
    assert "Suppress_With_Delete" in caplog.text
    assert "Test error message" in caplog.text


def test_bulk_unsuppress_success(setup_regulation_api):  # pylint: disable=redefined-outer-name
    """
    Test simple success case
    """
    mock_post, segment = setup_regulation_api
    mock_post.return_value = FakeResponse()

    learner = TEST_SEGMENT_CONFIG['learner']
    segment.unsuppress_learners_by_key('original_username', learner, 100)

    assert mock_post.call_count == 1

    expected_learner = get_fake_user_retirement()

    fake_json = {
        "regulation_type": "Unsuppress",
        "attributes": {
            "name": "userId",
            "values": [expected_learner['original_username'], ]
        }
    }

    url = TEST_SEGMENT_CONFIG['fake_base_url'] + BULK_REGULATE_URL.format(TEST_SEGMENT_CONFIG['fake_workspace'])
    mock_post.assert_any_call(
        url, json=fake_json, headers=TEST_SEGMENT_CONFIG['headers']
    )


def test_bulk_unsuppress_error(setup_regulation_api, caplog):  # pylint: disable=redefined-outer-name
    """
    Test simple error case
    """
    mock_post, segment = setup_regulation_api
    mock_post.return_value = FakeErrorResponse()

    learner = TEST_SEGMENT_CONFIG['learner']
    with pytest.raises(Exception):  # noqa: B017, PT011
        segment.unsuppress_learners_by_key('original_username', learner, 100)

    assert mock_post.call_count == 4
    assert "Error was encountered for params:" in caplog.text
    assert "9009" not in caplog.text
    assert "foo_username" in caplog.text
    assert "ecommerce-90" not in caplog.text
    assert "Unsuppress" in caplog.text
    assert "Test error message" in caplog.text
