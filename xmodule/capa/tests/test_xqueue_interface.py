"""Test the XQueue interface."""

import json
from unittest.mock import Mock, patch

import pytest

from xmodule.capa.xqueue_interface import XQueueInterface


@pytest.mark.django_db
@patch("xmodule.capa.xqueue_submission.XQueueInterfaceSubmission.send_to_submission")
def test_send_to_queue_with_flag_enabled(mock_send_to_submission):
    """Test send_to_queue when the waffle flag is enabled."""
    url = "http://example.com/xqueue"
    django_auth = {"username": "user", "password": "pass"}
    block = Mock()  # Mock block for the constructor
    xqueue_interface = XQueueInterface(url, django_auth, block=block, use_submission_service=True)

    header = json.dumps(
        {
            "lms_callback_url": (
                "http://example.com/courses/course-v1:test_org+test_course+test_run/"
                "xqueue/block@item_id/type@problem"
            ),
            "lms_key": "default",
        }
    )
    body = json.dumps(
        {
            "student_info": json.dumps({"anonymous_student_id": "student_id"}),
            "student_response": "student_answer",
        }
    )
    files_to_upload = None

    mock_send_to_submission.return_value = {"submission": "mock_submission"}
    error, msg = xqueue_interface.send_to_queue(header, body, files_to_upload)  # pylint: disable=unused-variable

    mock_send_to_submission.assert_called_once_with(header, body, "default", {})


@pytest.mark.django_db
@patch("xmodule.capa.xqueue_interface.XQueueInterface._http_post")
def test_send_to_queue_with_flag_disabled(mock_http_post):
    """Test send_to_queue when the waffle flag is disabled."""
    url = "http://example.com/xqueue"
    django_auth = {"username": "user", "password": "pass"}
    block = Mock()  # Mock block for the constructor
    xqueue_interface = XQueueInterface(url, django_auth, block=block, use_submission_service=False)

    header = json.dumps(
        {
            "lms_callback_url": (
                "http://example.com/courses/course-v1:test_org+test_course+test_run/"
                "xqueue/block@item_id/type@problem"
            ),
            "lms_key": "default",
        }
    )
    body = json.dumps(
        {
            "student_info": json.dumps({"anonymous_student_id": "student_id"}),
            "student_response": "student_answer",
        }
    )
    files_to_upload = None

    mock_http_post.return_value = (0, "Submission sent successfully")
    error, msg = xqueue_interface.send_to_queue(header, body, files_to_upload)  # pylint: disable=unused-variable

    mock_http_post.assert_called_once_with(
        "http://example.com/xqueue/xqueue/submit/",
        {"xqueue_header": header, "xqueue_body": body},
        files={},
    )
