""" Utilities for views in the course home api"""
from django.contrib.auth import get_user_model
from django.http.response import Http404
from rest_framework.exceptions import PermissionDenied

from lms.djangoapps.ccx.custom_exception import CCXLocatorValidationException
from lms.djangoapps.courseware.access import has_ccx_coach_role
from lms.djangoapps.courseware.courses import get_course_with_access as base_get_course_with_access
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.courseware.masquerade import setup_masquerade

User = get_user_model()


def get_course_or_403(*args, **kwargs):
    """
    When we make requests to the various Learner Home API endpoints, we do not want to return the actual redirects,
    Instead we should return an error code. The redirect info is returned from the course metadata endpoint and the
    URL can be followed by whatever client is calling.

    Raises:
     - 404 if course is not found
     - 403 if the requesting user does not have access to the course
    """
    try:
        return base_get_course_with_access(*args, **kwargs)
    except CourseAccessRedirect as e:
        raise PermissionDenied(
            detail=e.access_error.user_message,
            code=e.access_error.error_code
        ) from e


def get_student_user(request, course_key, student_id, is_staff):
    """Gets the student User object, either from coaching, masquerading, or normal actual request"""
    if student_id:
        try:
            student_id = int(student_id)
        except ValueError as e:
            raise Http404 from e

    if student_id is None or student_id == request.user.id:
        _, student = setup_masquerade(
            request,
            course_key,
            staff_access=is_staff,
            reset_masquerade_data=True
        )
        return student

    # When a student_id is passed in, we display the progress page for the user
    # with the provided user id, rather than the requesting user
    try:
        coach_access = has_ccx_coach_role(request.user, course_key)
    except CCXLocatorValidationException:
        coach_access = False

    has_access_on_students_profiles = is_staff or coach_access
    # Requesting access to a different student's profile
    if not has_access_on_students_profiles:
        raise Http404

    try:
        return User.objects.get(id=student_id)
    except User.DoesNotExist as exc:
        raise Http404 from exc
