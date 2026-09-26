"""Error-type catalog entries for request-level refusals."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import (
    MethodNotAllowed,
    NotAcceptable,
    ParseError,
    UnsupportedMediaType,
)

#: Refusals of the request itself - its body, its method, its media type - with
#: the error type and title each is answered with.
_REQUEST_ERROR_TYPES = (
    (ParseError, "validation", "Malformed Request"),
    (MethodNotAllowed, "method-not-allowed", "Method Not Allowed"),
    (NotAcceptable, "not-acceptable", "Not Acceptable"),
    (UnsupportedMediaType, "unsupported-media-type", "Unsupported Media Type"),
)


def register_request_error_types():
    """
    Give each request-level refusal its own error type.

    Uncataloged errors are answered with the type and title of an internal
    server error while keeping their 4xx status, which tells a client that its
    own unparseable body or unsupported method was a fault of the server.
    """
    for exception_class, slug, title in _REQUEST_ERROR_TYPES:
        register_error_type(exception_class, slug, title)
