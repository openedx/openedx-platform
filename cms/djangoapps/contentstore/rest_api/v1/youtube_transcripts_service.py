"""Service layer for the v1 YouTube transcript endpoints."""

import json
import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, QueryDict
from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import (
    APIException,
    MethodNotAllowed,
    NotFound,
    ParseError,
    PermissionDenied,
    ValidationError,
)

from cms.djangoapps.contentstore.views.transcripts_ajax import check_transcripts, replace_transcripts
from openedx.core.djangoapps.video_config.transcripts_utils import TranscriptsRequestValidationException
from xmodule.modulestore.exceptions import ItemNotFoundError

log = logging.getLogger(__name__)

# Neither exception is in the library's built-in classification catalog, so
# without these registrations a wrong verb or an unparseable body would be
# reported to the client as an internal server error at a 4xx status.
register_error_type(MethodNotAllowed, "method-not-allowed", "Method Not Allowed")
register_error_type(ParseError, "parse-error", "Malformed Request")

CHECK_FAILED_MESSAGE = "The transcript status for this video could not be determined."
IMPORT_FAILED_MESSAGE = "The YouTube transcript could not be imported for this video."
FORBIDDEN_MESSAGE = "You do not have permission to edit this video."
NOT_FOUND_MESSAGE = "No video block matches the supplied locator."


def _legacy_request(request, payload):
    """
    Return the underlying Django request with ``payload`` installed as the
    ``data`` query parameter the transcript handlers read.

    The handlers accept their input only from the query string. Building the
    query string here keeps them unmodified while letting the API accept a
    JSON request body.
    """
    django_request = getattr(request, "_request", request)
    query = QueryDict(mutable=True)
    query["data"] = json.dumps(payload)
    django_request.GET = query
    return django_request


def _decode(response):
    """Return the ``(body, status_code)`` pair of a legacy JSON response."""
    try:
        body = json.loads(response.content.decode("utf-8") or "{}")
    except ValueError:
        body = {}
    return body, response.status_code


def _translate(exc, course_key, locator, failure_message):
    """
    Re-raise a handler exception as its DRF equivalent.

    The handlers raise Django-native exceptions, which the standardized error
    envelope would otherwise classify as internal server errors. Messages are
    fixed strings so no internal detail reaches the caller.
    """
    if isinstance(exc, DjangoPermissionDenied):
        log.info(
            "Denied YouTube transcript access for locator %s under course %s.",
            locator,
            course_key,
        )
        raise PermissionDenied(FORBIDDEN_MESSAGE) from exc
    if isinstance(exc, (Http404, ItemNotFoundError)):
        log.info(
            "No video block found for locator %s under course %s.",
            locator,
            course_key,
        )
        raise NotFound(NOT_FOUND_MESSAGE) from exc
    if isinstance(exc, TranscriptsRequestValidationException):
        log.warning(
            "Rejected YouTube transcript request for locator %s under course %s: %s",
            locator,
            course_key,
            exc,
        )
        raise ValidationError({"locator": [failure_message]}) from exc
    raise exc


def _run(handler, request, payload, course_key, failure_message):
    """
    Call ``handler`` with ``payload`` and return its decoded success body.

    A handler failure becomes a validation error carrying ``failure_message``;
    the handler's own message is logged rather than returned, because it can
    embed the text of an upstream exception.
    """
    locator = payload.get("locator")
    try:
        response = handler(_legacy_request(request, payload))
    except APIException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        _translate(exc, course_key, locator, failure_message)
        raise

    body, status_code = _decode(response)
    if status_code >= 400:
        log.warning(
            "YouTube transcript operation failed for locator %s under course %s: %s",
            locator,
            course_key,
            body.get("status"),
        )
        raise ValidationError({"locator": [failure_message]})
    return body


def get_transcript_check_result(request, payload, course_key):
    """Return the YouTube transcript status of the video named in ``payload``."""
    return _run(check_transcripts, request, payload, course_key, CHECK_FAILED_MESSAGE)


def import_transcripts(request, payload, course_key):
    """Import the video's YouTube transcript and return the resulting record."""
    return _run(replace_transcripts, request, payload, course_key, IMPORT_FAILED_MESSAGE)
