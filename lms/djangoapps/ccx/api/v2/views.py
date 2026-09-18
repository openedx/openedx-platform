"""
CCX Coach API v2 views.

Endpoints consumed by the Instructor Dashboard MFE (CCX Coach experience):

* `GET  /api/ccx_coach/v2/courses/{course_id|ccx_course_id}/metadata`
* `POST /api/ccx_coach/v2/courses/{course_id}/create_ccx`

Both follow the Instructor Dashboard v2 conventions (DRF `APIView` +
`DeveloperErrorViewMixin`, JWT/session auth) and reuse existing CCX logic.
"""

import json
import logging

from ccx_keys.locator import CCXLocator
from django.db import transaction
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.ccx.api.v0.views import get_valid_course
from lms.djangoapps.ccx.api.v2.permissions import IsCCXCoach
from lms.djangoapps.ccx.api.v2.serializers import (
    CCXCoachMetadataSerializer,
    CreateCCXRequestSerializer,
    RemoveScheduleRequestSerializer,
)
from lms.djangoapps.ccx.utils import (
    create_ccx_course,
    get_ccx_for_coach,
    get_ccx_schedule,
    remove_block_from_ccx_schedule,
    save_ccx_schedule,
)
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)


def _error_response(error_code, http_status, field_errors=None):
    """
    Return a standard DRF error response with a machine-readable code.

    All error responses from these endpoints share this shape so clients can
    branch on ``error_code``. Field-level validation details, when present, are
    included under ``field_errors``.
    """
    payload = {'error_code': error_code}
    if field_errors:
        payload['field_errors'] = field_errors
    return Response(payload, status=http_status)


def _resolve_ccx_course(course_id):
    """
    Resolve a CCX course id to `(master_course, ccx, error_response)`.

    `master_course` is the master :class:`CourseBlock` (loaded with full
    depth for schedule traversal) and `ccx` is the
    :class:`CustomCourseForEdX`. On failure, `error_response` is a DRF
    `Response` and the first two values are `None`.
    """
    ccx, ccx_key, error_code, http_status = get_valid_course(course_id, is_ccx=True)
    if error_code:
        return None, None, _error_response(error_code, http_status)
    master_course = get_course_by_id(ccx_key.to_course_locator(), depth=None)
    return master_course, ccx, None


class CCXCoachMetadataView(DeveloperErrorViewMixin, APIView):
    """
    Return CCX Coach metadata for a master course or CCX course.

    *Example Request*

        GET /api/ccx_coach/v2/courses/{course_id|ccx_course_id}/metadata

    *Response Values*

        {
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "ccx_course_id": "ccx-v1:edX+DemoX+Demo_Course+ccx@1",
            "tabs": [
                {"tab_id": "enrollments", "title": "Enrollment", "url": "...", "sort_order": 10},
                ...
            ]
        }

    When the id is a master course for which the coach has no CCX yet,
    `ccx_course_id` is an empty string and `tabs` is an empty list (legacy
    behavior; the MFE shows its create/empty state).
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def get(self, request, course_id):
        """Return the metadata payload for the given master or CCX course id."""
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return _error_response('course_id_not_valid', status.HTTP_400_BAD_REQUEST)

        if isinstance(course_key, CCXLocator):
            _ccx, _key, error_code, http_status = get_valid_course(course_id, is_ccx=True)
            if error_code:
                return _error_response(error_code, http_status)
            master_course_key = course_key.to_course_locator()
            ccx_course_key = course_key
        else:
            master_course, master_course_key, error_code, http_status = get_valid_course(course_id)
            if error_code:
                return _error_response(error_code, http_status)
            ccx = get_ccx_for_coach(master_course, request.user)
            ccx_course_key = (
                CCXLocator.from_course_locator(master_course_key, str(ccx.id)) if ccx else None
            )

        data = {'master_course_key': master_course_key, 'ccx_course_key': ccx_course_key}
        return Response(CCXCoachMetadataSerializer(data).data, status=status.HTTP_200_OK)


class CreateCCXView(DeveloperErrorViewMixin, APIView):
    """
    Create a CCX course for a master course and return its metadata payload.

    *Example Request*

        POST /api/ccx_coach/v2/courses/{course_id}/create_ccx
        { "name": "My CCX" }

    Returns `201` with the same payload shape as the metadata endpoint, now
    populated with the new `ccx_course_id` and tabs. The path id must be a
    master course id; a CCX id is rejected with `400`.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def post(self, request, course_id):
        """Create a CCX for `course_id` owned by the requesting user."""
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return _error_response('course_id_not_valid', status.HTTP_400_BAD_REQUEST)

        # A CCX can only be created from a master course id.
        if isinstance(course_key, CCXLocator):
            return _error_response('course_id_not_valid', status.HTTP_400_BAD_REQUEST)

        master_course, master_course_key, error_code, http_status = get_valid_course(
            course_id, advanced_course_check=True
        )
        if error_code:
            return _error_response(error_code, http_status)

        # A CCX can only be created through an external service when a connector
        # url is configured on the master course.
        if getattr(master_course, 'ccx_connector', None):
            return _error_response('ccx_connector_set', status.HTTP_400_BAD_REQUEST)

        request_serializer = CreateCCXRequestSerializer(data=request.data)
        if not request_serializer.is_valid():
            return _error_response(
                'invalid_request', status.HTTP_400_BAD_REQUEST, field_errors=request_serializer.errors
            )
        name = request_serializer.validated_data['name']

        try:
            with transaction.atomic():
                ccx = create_ccx_course(master_course, request.user, name)
        except Exception:  # pylint: disable=broad-except
            # Surface any unexpected failure during CCX creation as a structured
            # JSON error rather than letting it become a 500 HTML response.
            log.exception('Failed to create CCX for course %s', course_id)
            return _error_response('ccx_creation_failed', status.HTTP_500_INTERNAL_SERVER_ERROR)

        ccx_course_key = CCXLocator.from_course_locator(master_course_key, str(ccx.id))

        data = {'master_course_key': master_course_key, 'ccx_course_key': ccx_course_key}
        return Response(CCXCoachMetadataSerializer(data).data, status=status.HTTP_201_CREATED)


class CCXScheduleView(DeveloperErrorViewMixin, APIView):
    """
    Return the CCX schedule for a CCX course.

    *Example Request*

        GET /api/ccx_coach/v2/courses/{ccx_course_id}/schedule

    *Response Values*

        A JSON array of schedule blocks (sections -> subsections -> units), each
        with `location`, `display_name`, `category`, `start`, optional
        `due`, `hidden` and optional `children`. This mirrors the legacy
        `ccx_schedule` output.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def get(self, request, course_id):
        """Return the CCX schedule for the given CCX course id."""
        master_course, ccx, error_response = _resolve_ccx_course(course_id)
        if error_response:
            return error_response
        return Response(get_ccx_schedule(master_course, ccx), status=status.HTTP_200_OK)


class SaveScheduleView(DeveloperErrorViewMixin, APIView):
    """
    Apply an edited schedule tree to a CCX course.

    *Example Request*

        POST /api/ccx_coach/v2/courses/{ccx_course_id}/save_schedule
        [ { "location": "...", "hidden": false, "start": "...", "due": "...", "children": [...] }, ... ]

    *Response Values*

        { "schedule": [...], "grading_policy": "<json string>" }

    Mirrors the legacy `save_ccx` behavior (including automatic grading-policy
    adjustment) but with DRF/JWT auth and JSON in/out.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def post(self, request, course_id):
        """Save the supplied schedule tree to the CCX course."""
        master_course, ccx, error_response = _resolve_ccx_course(course_id)
        if error_response:
            return error_response

        schedule_data = request.data
        if not isinstance(schedule_data, list):
            return _error_response('invalid_schedule_payload', status.HTTP_400_BAD_REQUEST)

        try:
            # Explicit atomic block: the exception is caught below and converted
            # into a response, which would otherwise let the ATOMIC_REQUESTS
            # transaction commit. `save_ccx_schedule` writes overrides as it
            # walks the tree, so a failure part-way through would leave the
            # schedule half-applied. Exiting via the exception rolls it back.
            with transaction.atomic():
                schedule, policy = save_ccx_schedule(master_course, ccx, schedule_data)
        except (KeyError, ValueError, TypeError):
            # Unknown block location, missing required keys, or malformed dates
            # in the payload. Return a structured JSON error rather than a 500.
            return _error_response('invalid_schedule_payload', status.HTTP_400_BAD_REQUEST)

        return Response(
            {'schedule': schedule, 'grading_policy': json.dumps(policy, indent=4)},
            status=status.HTTP_200_OK,
        )


class RemoveScheduleView(DeveloperErrorViewMixin, APIView):
    """
    Remove a block (and its descendants) from a CCX schedule.

    *Example Request*

        POST /api/ccx_coach/v2/courses/{ccx_course_id}/remove_schedule
        { "location": "block-v1:edX+DemoX+Demo_Course+type@chapter+block@week1" }

    Hides the block and its descendants and clears their start/due overrides,
    then returns the updated schedule (same shape as the schedule endpoint) so
    the client can refresh in a single call.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def post(self, request, course_id):
        """Remove the block identified by `location` from the CCX schedule."""
        master_course, ccx, error_response = _resolve_ccx_course(course_id)
        if error_response:
            return error_response

        request_serializer = RemoveScheduleRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        location = request_serializer.validated_data['location']

        try:
            # Explicit atomic block, for the same reason as the save endpoint:
            # the caught exception suppresses the ATOMIC_REQUESTS rollback, and
            # `remove_block_from_ccx_schedule` clears overrides as it walks the
            # block's descendants.
            with transaction.atomic():
                schedule = remove_block_from_ccx_schedule(ccx, master_course, location)
        except ValueError:
            return _error_response('schedule_block_not_found', status.HTTP_400_BAD_REQUEST)

        return Response(schedule, status=status.HTTP_200_OK)
