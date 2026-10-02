"""Permission classes for the Grades API v2."""

from edx_rest_framework_extensions.permissions import (
    JwtHasScope,
    JwtRestrictedApplication,
    NotJwtRestrictedApplication,
)
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission, IsAdminUser, IsAuthenticated

from common.djangoapps.student.auth import has_course_author_access, is_ccx_course
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.grades.api import is_writable_gradebook_enabled
from lms.djangoapps.grades.grade_utils import are_grades_frozen
from lms.djangoapps.grades.rest_api.v1.gradebook_views import _has_ccx_gradebook_access
from lms.djangoapps.grades.rest_api.v2.errors import GradesFrozen, WritableGradebookDisabled
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

#: Endpoint access for course-grade reads: any authenticated caller, and a token
#: issued to a restricted application only when it carries the view's
#: ``required_scopes``. Which rows a caller then sees is decided by the
#: queryset scoping policy, not here.
COURSE_GRADE_READ_ACCESS = IsAuthenticated & (
    NotJwtRestrictedApplication | (JwtRestrictedApplication & JwtHasScope)
)


class HasGradeBreakdownAccess(BasePermission):
    """
    Refuses the grade breakdown to anyone but global staff signed in as themselves.

    The breakdown carries per-subsection scores, including scores the course
    hides from learners, so a learner may not read even their own, and a
    restricted application token may not read it whatever its filters or user.
    Requests that do not ask for the breakdown pass. The view reports whether
    the request asks for it through ``breakdown_requested()``.
    """

    message = 'Only global staff may read the grade breakdown.'

    def has_permission(self, request, view):
        """Return True unless the breakdown is requested by someone other than global staff."""
        if not view.breakdown_requested():
            return True
        return (
            IsAdminUser().has_permission(request, view)
            and NotJwtRestrictedApplication().has_permission(request, view)
        )


class HasGradebookAccess(BasePermission):
    """
    Allows callers who may manage the gradebook of the course the request names.

    For a CCX course that is a CCX coach, a staff member or instructor of the
    CCX, or global staff, provided custom courses are enabled for the platform
    and the master course. For any other course it is a caller with author
    access to the course.

    The view supplies the course through ``get_course_key()``.
    """

    message = 'You do not have access to the gradebook of this course.'

    def has_permission(self, request, view):
        """Return True when the caller may manage the gradebook of the requested course."""
        course_key = view.get_course_key()
        if is_ccx_course(course_key):
            return _has_ccx_gradebook_access(request.user, course_key)
        return has_course_author_access(request.user, course_key)


class HasCourseStaffAccess(BasePermission):
    """
    Allows callers with staff access to the course the request names.

    That is global staff and the staff and instructors of the course or its
    organization, unless they are viewing the course as a learner. The view
    supplies the course through ``get_course_key()``.
    """

    def has_permission(self, request, view):
        """Return True when the caller has staff access to the requested course."""
        return bool(has_access(request.user, 'staff', view.get_course_key()))


#: Endpoint access for reading how a course is graded: its staff, and anyone
#: who may manage its gradebook.
COURSE_GRADING_POLICY_READ_ACCESS = IsAuthenticated & (HasCourseStaffAccess | HasGradebookAccess)


class HasGradebookAccessUnlessMinimal(BasePermission):
    """
    Requires gradebook access for any but the minimal view of a grading policy.

    Course staff may read the grade cutoffs and assignment types, which the
    minimal view returns. The subsections, freeze state and bulk-management
    setting of the full view are for those who may manage the gradebook, so
    course staff who may not, such as the staff of a custom course while
    custom courses are switched off, get the minimal view only.
    """

    message = HasGradebookAccess.message

    def has_permission(self, request, view):
        """Return True for the minimal view, and otherwise when the caller may manage the gradebook."""
        if view.is_minimal_view_requested(request):
            return True
        return HasGradebookAccess().has_permission(request, view)


class CourseExists(BasePermission):
    """
    Refuses, as not found, requests that name a course which does not exist.

    Place it after the access check, so a caller without access cannot use it
    to learn whether a course exists.
    """

    def has_permission(self, request, view):
        """Return True when the requested course exists."""
        if not CourseOverview.course_exists(view.get_course_key()):
            raise NotFound()
        return True


class WritableGradebookEnabled(BasePermission):
    """
    Refuses requests for a course whose writable gradebook is switched off.

    Place it after the access check, so a caller without access cannot use it
    to learn how the course is configured.
    """

    def has_permission(self, request, view):
        """Return True when the writable gradebook is enabled for the requested course."""
        if not is_writable_gradebook_enabled(view.get_course_key()):
            raise WritableGradebookDisabled()
        return True


class GradesNotFrozen(BasePermission):
    """
    Refuses requests to change the grades of a course whose grades are frozen.

    Place it last, after the access, existence and feature checks.
    """

    def has_permission(self, request, view):
        """Return True when the grades of the requested course may still change."""
        if are_grades_frozen(view.get_course_key()):
            raise GradesFrozen()
        return True
