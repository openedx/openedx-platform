"""
Legacy (pre-AuthZ) checks of the Instructor Dashboard AuthZ permissions.

They are evaluated only when ``authz.enable_course_authoring`` is disabled for
the course, and must keep mirroring the behavior the dashboard had before AuthZ.
Each check has the ``(user, course_key) -> bool`` signature.
"""

from django.contrib.auth.models import AbstractUser
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.auth import has_course_author_access, has_studio_read_access
from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.courseware.access import administrative_accesses_to_course_for_user, has_access
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names, has_forum_access
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.authz import constants
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
)


def has_staff_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check whether the user has course staff access.

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True for course staff, course instructors and global staff.
    """
    return bool(has_access(user, "staff", course_key))


def has_instructor_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check whether the user has course instructor access.

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True for course instructors and global staff.
    """
    return bool(has_access(user, "instructor", course_key))


def has_course_team_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check the access to the course team.

    It follows the same rule as ``CourseTeamPermission``
    (``lms.djangoapps.instructor.permissions``).

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True with instructor access, or with staff access plus the forum
            Administrator role.
    """
    return has_instructor_access(user, course_key) or (
        has_staff_access(user, course_key) and has_forum_access(user, course_key, FORUM_ROLE_ADMINISTRATOR)
    )


def has_cohort_manage_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check the legacy access to change cohorts.

    It applies to any non-GET request of the cohorts API and uses the same rule
    as ``IsStaffOrAdmin`` (``openedx.core.djangoapps.course_groups.permissions``)
    for those requests.

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True for global staff, superusers, course staff and course
        instructors.
    """
    if GlobalStaff().has_user(user) or user.is_superuser:
        return True
    _, staff_access, instructor_access = administrative_accesses_to_course_for_user(user, course_key)
    return bool(staff_access or instructor_access)


def has_cohort_view_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check the legacy access to read cohorts (GET requests of the cohorts API).

    It uses the same rule as ``IsStaffOrAdmin``
    (``openedx.core.djangoapps.course_groups.permissions``) for GET requests.

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True for everyone who can manage cohorts, plus the forum
        Administrator, Moderator and Community TA roles.
    """
    if has_cohort_manage_access(user, course_key):
        return True
    return bool(
        get_user_role_names(user, course_key)
        & {FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA}
    )


def has_gradebook_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check the legacy access to the gradebook.

    It follows the same rule as the gradebook API (``course_author_access_required``).

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True with course author (Studio write) access.
    """
    return bool(has_course_author_access(user, course_key))


def has_grading_settings_access(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Check the legacy access to the grading settings.

    It follows the same rule as the Studio grading settings (``grading_handler``).

    Args:
        user (AbstractUser): The user to check.
        course_key (CourseKey): The course to check.

    Returns:
        bool: True with Studio read access.
    """
    return bool(has_studio_read_access(user, course_key))


def bridgekeeper_check(permission_name: str):
    """
    Build a legacy check from a ``bridgekeeper`` permission.

    The permissions are those of ``lms.djangoapps.instructor.permissions``.

    Args:
        permission_name (str): The bridgekeeper permission, e.g.
            ``instructor.view_enrollments``.

    Returns:
        Callable[[AbstractUser, CourseKey], bool]: A check that evaluates the
        permission against the course.
    """

    def _check(user: AbstractUser, course_key: CourseKey) -> bool:
        return user.has_perm(permission_name, course_key)

    return _check


# Default legacy check of every permission, as used for the tab visibility and
# ``authz_permissions``. Endpoints whose legacy rule differs (e.g. beta testers,
# which need instructor access) pass their own check.
LEGACY_PERMISSION_CHECKS = {
    constants.COURSES_VIEW_COURSE_INFO: bridgekeeper_check(permissions.VIEW_DASHBOARD),
    constants.COURSES_VIEW_ENROLLMENTS: bridgekeeper_check(permissions.VIEW_ENROLLMENTS),
    constants.COURSES_ENROLL_LEARNERS: bridgekeeper_check(permissions.CAN_ENROLL),
    constants.COURSES_UNENROLL_LEARNERS: bridgekeeper_check(permissions.CAN_ENROLL),
    constants.COURSES_VIEW_COURSE_TEAM: has_course_team_access,
    constants.COURSES_MANAGE_COURSE_TEAM: has_course_team_access,
    constants.COURSES_VIEW_GRADEBOOK: has_gradebook_access,
    constants.COURSES_VIEW_GRADING_CONFIGURATION: bridgekeeper_check(permissions.VIEW_DASHBOARD),
    constants.COURSES_VIEW_GRADING_SETTINGS: has_grading_settings_access,
    constants.COURSES_MANAGE_LEARNER_GRADING: bridgekeeper_check(permissions.OVERRIDE_GRADES),
    constants.COURSES_VIEW_LEARNER_EXTENSIONS: bridgekeeper_check(permissions.VIEW_DASHBOARD),
    constants.COURSES_MANAGE_LEARNER_EXTENSIONS: bridgekeeper_check(permissions.GIVE_STUDENT_EXTENSION),
    constants.COURSES_VIEW_COHORTS: has_cohort_view_access,
    constants.COURSES_MANAGE_COHORTS: has_cohort_manage_access,
}
