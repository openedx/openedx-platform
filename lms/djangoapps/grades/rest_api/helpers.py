"""
Helpers shared by the versions of the Grades REST API.

Grades v1 keeps its own copies of these, so that the deprecated version stays
unchanged until it is removed; removing it then leaves the newer versions
untouched.
"""

from django.conf import settings
from django.http import Http404

from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
from lms.djangoapps.ccx.permissions import VIEW_CCX_COACH_DASHBOARD
from openedx.core.lib.courses import get_course_by_id


def has_ccx_gradebook_access(user, course_key):
    """
    Return True if ``user`` may use the gradebook of the CCX course ``course_key``.

    Applies the same platform-level and course-level guards as
    ``lms.djangoapps.ccx.api.v2.permissions.IsCCXCoach``:

    * ``CUSTOM_COURSES_EDX`` must be enabled.
    * The master course must have ``enable_ccx = True``.
    * The user must either have the staff permission on the master course
      (``VIEW_CCX_COACH_DASHBOARD``), hold the CCX coach role on it, or be site
      staff or hold a staff or instructor role on the CCX itself (which
      ``create_ccx_course`` grants to the coach).
    """
    if not settings.CUSTOM_COURSES_EDX:
        return False
    master_course_key = course_key.to_course_locator()
    try:
        master_course = get_course_by_id(master_course_key)
    except Http404:
        return False
    if not master_course.enable_ccx:
        return False
    return (
        user.is_staff
        or user.has_perm(VIEW_CCX_COACH_DASHBOARD, master_course)
        or CourseCcxCoachRole(master_course_key).has_user(user)
        or CourseStaffRole(course_key).has_user(user)
        or CourseInstructorRole(course_key).has_user(user)
    )


def get_problem_blocks(course):
    """
    Return the scored problem blocks of ``course``, in course order.

    Only problems placed directly in a unit are found: problems inside legacy
    library content blocks or content libraries are not included.
    """
    blocks = []
    for section in course.get_children():
        for subsection in section.get_children():
            for vertical in subsection.get_children():
                for block in vertical.get_children():
                    if block.category == 'problem' and getattr(block, 'has_score', False):
                        blocks.append(block)
    return blocks
