""" Utilities for views in the course home api"""
from django.contrib.auth import get_user_model
from django.http.response import Http404
from rest_framework.exceptions import PermissionDenied

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.ccx.custom_exception import CCXLocatorValidationException
from lms.djangoapps.courseware.access import has_access, has_ccx_coach_role
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
    """
    Return the user whose data the request may read: the requester, the learner the requester is
    masquerading as, or a named learner.

    ``student_id`` may be a string, an integer, or ``None``. ``None`` or the requester's own id returns
    the requester, or the learner the requester is masquerading as. ``is_staff`` must be the requester's
    staff access computed before this call, because ``has_access`` denies staff under a student-role
    masquerade, which includes masquerading as a specific learner. Staff and CCX coaches may name any
    existing user.

    :raises django.http.Http404: with no message, when ``student_id`` is not an integer, when the
        requester is neither staff nor a CCX coach and names another user, or when no such user exists.
    """
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

    # A named id other than the requester's is served only to staff or a coach.
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


def resolve_course_learner(request, course_key, learner_id):
    """
    Resolve which learner's data the requester may read for a course run (openedx-core ADR 0008).

    ``learner_id`` of ``None`` or the requester's own id means the requester, or the learner the
    requester is masquerading as. Course-run staff and CCX coaches may name any existing user. When a
    staff requester names no other learner, the call clears the session's masquerade field data, the
    same reset the Progress tab and the other course-home GET views run.

    :raises django.http.Http404: with no message, when the named learner does not exist or the
        requester may not see them.
    :raises rest_framework.exceptions.PermissionDenied: when the resolved learner has no active
        enrollment and the requester has no staff access. A CCX coach without staff access on the
        CCX is not exempt, so such a coach can tell an unenrolled learner from an unknown id; that
        is accepted because coaches may see learners.
    """
    is_staff = bool(has_access(request.user, "staff", course_key))
    student = get_student_user(request, course_key, learner_id, is_staff)
    if not is_staff:
        enrollment = CourseEnrollment.get_enrollment(student, course_key)
        if not enrollment or not enrollment.is_active:
            raise PermissionDenied("Learner is not enrolled in this course run.")
    return student
