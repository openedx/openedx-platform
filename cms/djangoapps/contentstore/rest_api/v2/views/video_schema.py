"""Schema parameters and responses shared by the API views of a course's videos."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from edx_rest_framework_extensions.errors import ErrorResponseSerializer

COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, for example course-v1:edX+DemoX+Demo_Course.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

COURSE_NOT_FOUND_RESPONSE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The course does not exist, or the course key is malformed.",
)


def unauthenticated_response(requires_csrf_token):
    """
    Return the 401 response, naming the credentials the platform accepts.

    ``requires_csrf_token`` is true for an operation whose session callers must
    also send the CSRF token, as for any method other than GET, HEAD, OPTIONS
    and TRACE.
    """
    session = "the session cookie of a signed-in Studio user whose account is active"
    if requires_csrf_token:
        session += ", together with the CSRF token in the X-CSRFToken header"
    return OpenApiResponse(
        response=ErrorResponseSerializer,
        description=(
            "The request carries no accepted credentials. Send a JSON Web Token in the Authorization "
            "header as 'JWT <token>' (the OAuth2 access token endpoint issues one when the token request "
            f"sets token_type=jwt), or {session}. OAuth2 access tokens sent as 'Bearer <token>' are not "
            "accepted."
        ),
    )


def forbidden_response(requires_csrf_token):
    """Return the 403 response; ``requires_csrf_token`` as for ``unauthenticated_response``."""
    description = "The requester does not have Studio access to the course"
    if requires_csrf_token:
        description += ", or a request made with a session did not send the CSRF token"
    return OpenApiResponse(response=ErrorResponseSerializer, description=f"{description}.")
