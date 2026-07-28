"""
Various helpful utilities for ESG
"""
import json
from functools import wraps

from opaque_keys.edx.keys import UsageKey
from rest_framework.request import clone_request

from lms.djangoapps.courseware.block_render import handle_xblock_callback_noauth
from lms.djangoapps.ora_staff_grader.errors import MissingParamResponse


def require_params(param_names):
    """
    Adds the required query params to the view function. Returns 404 if param(s) missing.

    Params:
    - param_name (string): the query param to unpack

    Raises:
    - MissingParamResponse (HTTP 400)
    """

    def decorator(function):
        @wraps(function)
        def wrapped_function(
            self, request, *args, **kwargs
        ):  # pylint: disable=unused-argument
            passed_parameters = []

            for param_name in param_names:
                param = request.query_params.get(param_name)

                if not param:
                    return MissingParamResponse()

                passed_parameters.append(param)
            return function(self, request, *passed_parameters, *args, **kwargs)

        return wrapped_function

    return decorator


def is_json(input_string):
    """Quick True/False check to see if a value is JSON"""
    try:
        json.loads(input_string)
    except ValueError:
        return False
    return True


def call_xblock_json_handler(request, usage_id, handler_name, data, auth=False):
    """
    WARN: Tested only for use in ESG. Consult before use outside of ESG.

    Create an internally-routed XBlock.json_handler request. The internal auth code/param unpacking requires a POST
    request with payload in the body. Ideally, we would be able to call functions on XBlocks without this sort of
    hacky request proxying but this is what we have to work with right now.

    params:
        request (HttpRequest): Originating web request, we're going to borrow auth headers/cookies from this
        usage_id (str): Usage ID of the XBlock for running the handler
        handler_name (str): the name of the XBlock handler method
        data (dict): Data to be encoded and sent as the body of the POST request
    returns:
        response (HttpResponse): get response data with json.loads(response.content)
    """
    # XBlock.json_handler operates through a POST request
    proxy_request = clone_request(request, "POST")
    proxy_request.META["REQUEST_METHOD"] = "POST"

    # The body is an encoded JSON blob
    proxy_request.body = json.dumps(data).encode()

    # Course ID can be retrieved from the usage_id
    usage_key = UsageKey.from_string(usage_id)
    course_id = str(usage_key.course_key)

    # Send the request and return the HTTP response from the XBlock
    return handle_xblock_callback_noauth(
        proxy_request, course_id, usage_id, handler_name
    )
