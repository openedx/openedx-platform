"""
CCX Coach API v2 views.

Endpoints consumed by the Instructor Dashboard MFE (CCX Coach experience):

* `GET  /api/ccx_coach/v2/courses/{course_id|ccx_course_id}/metadata`
* `POST /api/ccx_coach/v2/courses/{course_id}/create_ccx`
* `GET  /api/ccx_coach/v2/courses/{ccx_course_id}/schedule`
* `PUT  /api/ccx_coach/v2/courses/{ccx_course_id}/schedule`
* `POST /api/ccx_coach/v2/courses/{ccx_course_id}/remove_schedule`
* `GET  /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy`
* `PUT  /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy`

These follow the Instructor Dashboard v2 conventions (DRF `APIView` +
`DeveloperErrorViewMixin`, JWT/session auth) and reuse existing CCX logic.
"""

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
    CCXGradingPolicyRequestSerializer,
    CreateCCXRequestSerializer,
    RemoveScheduleRequestSerializer,
)
from lms.djangoapps.ccx.overrides import get_override_for_ccx, override_field_for_ccx
from lms.djangoapps.ccx.utils import (
    create_ccx_course,
    get_ccx_for_coach,
    get_ccx_schedule,
    remove_block_from_ccx_schedule,
    save_ccx_schedule,
)
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import SignalHandler

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


class _CCXResolutionError(Exception):
    """
    Raised when a CCX course id cannot be resolved to a CCX course.

    Carries the machine-readable ``error_code`` and the HTTP status so the
    calling view can translate the failure into this API's standard JSON error
    response, keeping the resolver itself free of HTTP-layer concerns.
    """

    def __init__(self, error_code, http_status):
        super().__init__(error_code)
        self.error_code = error_code
        self.http_status = http_status


def _resolve_ccx_course(course_id):
    """
    Resolve a CCX course id to ``(master_course, ccx)``.

    ``master_course`` is the master :class:`CourseBlock` (loaded with full depth
    for schedule traversal) and ``ccx`` is the :class:`CustomCourseForEdX`.

    Deliberately free of any HTTP-layer dependency: it raises rather than
    returning a DRF ``Response``, so each caller owns the translation to a
    response and this helper stays reusable outside the view layer.

    :raises _CCXResolutionError: if ``course_id`` is not a valid, existing CCX
        course id.
    """
    ccx, ccx_key, error_code, http_status = get_valid_course(course_id, is_ccx=True)
    if error_code:
        raise _CCXResolutionError(error_code, http_status)
    master_course = get_course_by_id(ccx_key.to_course_locator(), depth=None)
    return master_course, ccx


class CCXCoachMetadataView(DeveloperErrorViewMixin, APIView):
    """
    Return CCX Coach metadata for a master course or CCX course.

    **Example Request**

        GET /api/ccx_coach/v2/courses/{course_id|ccx_course_id}/metadata

    **Response Values**

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

    **Example Request**

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
    Read or replace the schedule of a CCX course.

    **Example Requests**

        GET /api/ccx_coach/v2/courses/{ccx_course_id}/schedule

        PUT /api/ccx_coach/v2/courses/{ccx_course_id}/schedule
        [ { "location": "...", "hidden": false, "start": "...", "due": "...", "children": [...] }, ... ]

    **Response Values**

        Both ``GET`` and ``PUT`` return the same payload: a JSON array of
        schedule blocks (sections -> subsections -> units), each with
        ``location``, ``display_name``, ``category``, ``start``, optional
        ``due``, ``hidden`` and optional ``children``. This mirrors the legacy
        ``ccx_schedule`` output.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def get(self, request, course_id):
        """Return the CCX schedule for the given CCX course id."""
        try:
            master_course, ccx = _resolve_ccx_course(course_id)
        except _CCXResolutionError as exc:
            return _error_response(exc.error_code, exc.http_status)

        return Response(get_ccx_schedule(master_course, ccx), status=status.HTTP_200_OK)

    def put(self, request, course_id):
        """Replace the CCX course's schedule with the supplied schedule tree."""
        try:
            master_course, ccx = _resolve_ccx_course(course_id)
        except _CCXResolutionError as exc:
            return _error_response(exc.error_code, exc.http_status)

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
                # save_ccx_schedule() also returns the (possibly adjusted) grading
                # policy The FE can read it from the grading_policy endpoint if necessary.
                schedule, _policy = save_ccx_schedule(master_course, ccx, schedule_data)
        except (KeyError, ValueError, TypeError):
            # Unknown block location, missing required keys, or malformed dates
            # in the payload. Return a structured JSON error rather than a 500.
            return _error_response('invalid_schedule_payload', status.HTTP_400_BAD_REQUEST)

        return Response(schedule, status=status.HTTP_200_OK)


class RemoveScheduleView(DeveloperErrorViewMixin, APIView):
    """
    Remove a block (and its descendants) from a CCX schedule.

    **Example Request**

        POST /api/ccx_coach/v2/courses/{ccx_course_id}/remove_schedule
        { "location": "block-v1:edX+DemoX+Demo_Course+type@chapter+block@week1" }

    Hides the block and its descendants and clears their start/due overrides,
    then returns the updated schedule (same shape as the schedule endpoint) so
    the client can refresh in a single call.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def post(self, request, course_id):
        """Remove the block identified by ``location`` from the CCX schedule."""
        try:
            master_course, ccx = _resolve_ccx_course(course_id)
        except _CCXResolutionError as exc:
            return _error_response(exc.error_code, exc.http_status)

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

class CCXGradingPolicyView(DeveloperErrorViewMixin, APIView):
    """
    Read or replace the grading policy for a CCX course.

    **Example Requests**

        GET /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy

        PUT /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy
        {
            "policy": {
                "GRADER": [...],
                "GRADE_CUTOFFS": {"Pass": 0.5}
            }
        }

    **Response Values**

        {
            "GRADER": [
                {"type": "Homework", "min_count": 12, "drop_count": 2,
                 "short_label": "HW", "weight": 0.15},
                ...
            ],
            "GRADE_CUTOFFS": {"Pass": 0.5}
        }

    GET returns the CCX-specific override when present, otherwise the master
    course grading policy (matching the legacy coach dashboard behavior at
    `lms/djangoapps/ccx/views.py::dashboard`).

    PUT replaces the CCX grading policy override in full and returns the new policy.
    The path id must be a CCX course id; a master course id is rejected with `400`.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsCCXCoach)

    def get(self, request, course_id):
        """Return the effective grading policy for the given CCX course id."""
        try:
            master_course, ccx = _resolve_ccx_course(course_id)
        except _CCXResolutionError as exc:
            return _error_response(exc.error_code, exc.http_status)

        grading_policy = get_override_for_ccx(
            ccx, master_course, 'grading_policy', master_course.grading_policy
        )
        return Response(grading_policy, status=status.HTTP_200_OK)

    def put(self, request, course_id):
        """Replace the CCX grading policy override with the provided policy."""
        try:
            master_course, ccx = _resolve_ccx_course(course_id)
        except _CCXResolutionError as exc:
            return _error_response(exc.error_code, exc.http_status)

        request_serializer = CCXGradingPolicyRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        policy = request_serializer.validated_data['policy']

        override_field_for_ccx(ccx, master_course, 'grading_policy', policy)

        # Match the legacy view: notify listeners so caches are invalidated.
        ccx_course_key = CCXLocator.from_course_locator(master_course.id, str(ccx.id))
        responses = SignalHandler.course_published.send(sender=ccx, course_key=ccx_course_key)
        for rec, response in responses:
            log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

        return Response(policy, status=status.HTTP_200_OK)
