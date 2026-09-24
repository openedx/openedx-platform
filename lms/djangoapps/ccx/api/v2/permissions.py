"""
Permissions for the CCX Coach API v2.
"""

import logging

from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.http import Http404
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.permissions import BasePermission

from common.djangoapps.student.roles import CourseCcxCoachRole
from lms.djangoapps.ccx.permissions import VIEW_CCX_COACH_DASHBOARD
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)


class IsCCXCoach(BasePermission):
    """
    Grant access to CCX Coach v2 endpoints.

    Similar to the access rules of the legacy `coach_dashboard` view decorator:

    * the `CUSTOM_COURSES_EDX` platform feature must be enabled, and the
      target (master) course must have CCX enabled (`course.enable_ccx`);
    * the user must either hold a role that can view the coach dashboard
      (staff/instructor, via `VIEW_CCX_COACH_DASHBOARD`) or have the CCX
      Coach role on the master course.

    Malformed or unknown course ids are intentionally deferred to the view,
    which returns structured `400`/`404` DRF errors, rather than being
    masked as a `403` here.
    """

    message = 'You must be a CCX Coach to access this resource.'

    def has_permission(self, request, view):
        if not settings.CUSTOM_COURSES_EDX:
            return False

        course_id = view.kwargs.get('course_id')
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            # Let the view validate and return a 400.
            return True

        if isinstance(course_key, CCXLocator):
            master_course_key = course_key.to_course_locator()
        else:
            master_course_key = course_key

        try:
            course = get_course_by_id(master_course_key)
        except Http404:
            # Let the view validate and return a 404.
            return True

        if not course.enable_ccx:
            return False

        if request.user.has_perm(VIEW_CCX_COACH_DASHBOARD, course):
            return True

        return CourseCcxCoachRole(master_course_key).has_user(request.user)
