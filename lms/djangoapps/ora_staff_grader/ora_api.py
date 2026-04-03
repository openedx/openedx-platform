"""
Functions used for interacting with ORA

All XBlock handlers are wrapped to return:
- Data, on success
- Exception, on failure

Some XBlock handlers natively return error codes for errors.
These are caught by status code and raise XBlockInternalError by convention.

Other handlers return status OK even for an error, but contain error info in the returned payload.
These are checked (usually by checking for a {"success":false} response) and raise errors, possibly with extra context.
"""
import json
from http import HTTPStatus

from rest_framework.request import Request

from lms.djangoapps.ora_staff_grader.errors import LockContestedError, XBlockInternalError
from lms.djangoapps.ora_staff_grader.utils import call_xblock_json_handler, is_json


def get_submissions(request, usage_id):
    """
    Get a list of submissions from the ORA's 'list_staff_workflows' XBlock.json_handler
    """
    handler_name = "list_staff_workflows"
    response = call_xblock_json_handler(request, usage_id, handler_name, {})

    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})

    try:
        return json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise XBlockInternalError(
            context={"handler": handler_name, "details": response.content}
        ) from exc


def get_assessments(request: Request, usage_id: str, handler_name: str, submission_uuid: str):
    """
    Get a list of assessments according to the handler name from ORA XBlock.json_handler

    * `list_assessments_to` handler
        Lists all assessments given by a user (according to their submissionUUID)
        in an ORA assignment. Assessments can be Self, Peer and Staff.

    * `list_assessments_from` handler
        Lists all assessments received by a user (according to their submissionUUID)
        in an ORA assignment. Assessments can be Self, Peer and Staff.

    Args:
        request (Request): The request object.
        usage_id (str): Usage ID of the XBlock for running the handler
        handler_name (str): The name of the handler to call
        submission_uuid (str): The ORA submission UUID
    """
    data = {
        "item_id": usage_id,
        "submission_uuid": submission_uuid,
    }

    response = call_xblock_json_handler(request, usage_id, handler_name, data)

    if response.status_code != HTTPStatus.OK:
        raise XBlockInternalError(context={"handler": handler_name})

    try:
        return json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise XBlockInternalError(
            context={"handler": handler_name, "details": response.content}
        ) from exc


def get_submission_info(request, usage_id, submission_uuid):
    """
    Get submission content from ORA 'get_submission_info' XBlock.json_handler
    """
    handler_name = "get_submission_info"
    data = {"submission_uuid": submission_uuid}
    response = call_xblock_json_handler(request, usage_id, handler_name, data)

    if response.status_code != 200:
        details = (
            json.loads(response.content).get("error", "")
            if is_json(response.content)
            else ""
        )
        raise XBlockInternalError(context={"handler": handler_name})

    return json.loads(response.content)


def get_assessment_info(request, usage_id, submission_uuid):
    """
    Get assessment data from ORA 'get_assessment_info' XBlock.json_handler
    """
    handler_name = "get_assessment_info"
    data = {"submission_uuid": submission_uuid}
    response = call_xblock_json_handler(request, usage_id, handler_name, data)

    if response.status_code != 200:
        details = (
            json.loads(response.content).get("error", "")
            if is_json(response.content)
            else ""
        )
        raise XBlockInternalError(context={"handler": handler_name, "details": details})

    return json.loads(response.content)


def submit_grade(request, usage_id, grade_data):
    """
    Submit a grade for an assessment.

    Returns: {'success': True/False, 'msg': err_msg}
    """
    handler_name = "submit_staff_assessment"
    response = call_xblock_json_handler(request, usage_id, handler_name, grade_data)

    # Unhandled errors might not be JSON, catch before loading
    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})

    response_data = json.loads(response.content)

    # Handled faillure still returns HTTP 200 but with 'success': False and supplied error message 'msg'
    if not response_data.get("success", False):
        raise XBlockInternalError(
            context={"handler": handler_name, "msg": response_data.get("msg", "")}
        )

    return response_data


def check_submission_lock(request, usage_id, submission_uuid):
    """
    Look up lock info for the given submission by calling the ORA's 'check_submission_lock' XBlock.json_handler
    """
    handler_name = "check_submission_lock"
    data = {"submission_uuid": submission_uuid}
    response = call_xblock_json_handler(request, usage_id, handler_name, data)

    # Unclear that there would every be an error (except network/auth) but good to catch here
    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})

    return json.loads(response.content)


def claim_submission_lock(request, usage_id, submission_uuid):
    """
    Attempt to claim a submission lock for grading.

    Returns:
    - lockStatus (string) - One of ['not-locked', 'locked', 'in-progress']
    """
    handler_name = "claim_submission_lock"
    body = {"submission_uuid": submission_uuid}
    response = call_xblock_json_handler(request, usage_id, handler_name, body)

    # Lock contested returns a 403
    if response.status_code == 403:
        raise LockContestedError()

    # Other errors should raise a blanket exception
    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})

    return json.loads(response.content)


def delete_submission_lock(request, usage_id, submission_uuid):
    """
    Attempt to claim a submission lock for grading.

    Returns:
    - lockStatus (string) - One of ['not-locked', 'locked', 'in-progress']
    """
    handler_name = "delete_submission_lock"
    body = {"submission_uuid": submission_uuid}

    # Return raw response to preserve HTTP status codes for failure states
    response = call_xblock_json_handler(request, usage_id, handler_name, body)

    # Lock contested returns a 403
    if response.status_code == 403:
        raise LockContestedError()

    # Other errors should raise a blanket exception
    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})

    return json.loads(response.content)


def batch_delete_submission_locks(request, usage_id, submission_uuids):
    """
    Batch delete a list of submission locks. Limited only to those in the list the user owns.

    Returns: none
    """
    handler_name = "batch_delete_submission_lock"
    body = {"submission_uuids": submission_uuids}

    response = call_xblock_json_handler(request, usage_id, handler_name, body)

    # Errors should raise a blanket exception. Otherwise body is empty, 200 is implicit success
    if response.status_code != 200:
        raise XBlockInternalError(context={"handler": handler_name})
