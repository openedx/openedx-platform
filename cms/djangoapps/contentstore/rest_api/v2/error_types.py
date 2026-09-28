"""Error types of the v2 authoring endpoints that the shared error catalog lacks."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import MethodNotAllowed, NotAcceptable


def register_error_types():
    """
    Register the request errors these endpoints can raise but the shared catalog does not classify.

    Without them, a wrong verb or an unsatisfiable ``Accept`` header would be
    reported to the client as an internal server error at a 4xx status.
    """
    register_error_type(MethodNotAllowed, "method-not-allowed", "Method Not Allowed")
    register_error_type(NotAcceptable, "not-acceptable", "Not Acceptable")
