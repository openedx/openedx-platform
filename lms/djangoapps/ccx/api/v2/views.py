"""
CCX Coach API v2 views.

Endpoints consumed by the Instructor Dashboard MFE (CCX Coach experience):

* `GET  /api/ccx_coach/v2/courses/{course_id|ccx_course_id}/metadata`
* `POST /api/ccx_coach/v2/courses/{course_id}/create_ccx`

Both follow the Instructor Dashboard v2 conventions (DRF `APIView` +
`DeveloperErrorViewMixin`, JWT/session auth) and reuse existing CCX logic.
"""

import logging

from ccx_keys.locator import CCXLocator
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
from lms.djangoapps.ccx.api.v2.serializers import CCXCoachMetadataSerializer, CreateCCXRequestSerializer
from lms.djangoapps.ccx.utils import create_ccx_course, get_ccx_for_coach
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin

log = logging.getLogger(__name__)


def _error_response(error_code, http_status):
    """Return a standard DRF error response with a machine-readable code."""
    return Response({'error_code': error_code}, status=http_status)


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
        request_serializer.is_valid(raise_exception=True)
        name = request_serializer.validated_data['name']

        ccx = create_ccx_course(master_course, request.user, name)
        ccx_course_key = CCXLocator.from_course_locator(master_course_key, str(ccx.id))

        data = {'master_course_key': master_course_key, 'ccx_course_key': ccx_course_key}
        return Response(CCXCoachMetadataSerializer(data).data, status=status.HTTP_201_CREATED)
