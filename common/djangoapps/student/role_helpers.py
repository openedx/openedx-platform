"""
Helpers for student roles
"""
from __future__ import annotations

from django.contrib.auth import get_user_model

from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    Role
)
from openedx.core.lib.cache_utils import request_cached
from common.djangoapps.student.roles import (
    CourseAccessRole,
    CourseBetaTesterRole,
    CourseInstructorRole,
    CourseStaffRole,
    GlobalStaff,
    OrgInstructorRole,
    OrgStaffRole,
    RoleCache,
)


User = get_user_model()


@request_cached()
def has_staff_roles(user, course_key):
    """
    Return true if a user has any of the following roles
    Staff, Instructor, Beta Tester, Forum Community TA, Forum Group Moderator, Forum Moderator, Forum Administrator
    """
    forum_roles = [FORUM_ROLE_COMMUNITY_TA, FORUM_ROLE_GROUP_MODERATOR,
                   FORUM_ROLE_MODERATOR, FORUM_ROLE_ADMINISTRATOR]
    is_staff = CourseStaffRole(course_key).has_user(user)
    is_instructor = CourseInstructorRole(course_key).has_user(user)
    is_beta_tester = CourseBetaTesterRole(course_key).has_user(user)
    is_org_staff = OrgStaffRole(course_key.org).has_user(user)
    is_org_instructor = OrgInstructorRole(course_key.org).has_user(user)
    is_global_staff = GlobalStaff().has_user(user)
    has_forum_role = Role.user_has_role_for_course(user, course_key, forum_roles)
    if any([is_staff, is_instructor, is_beta_tester, is_org_staff,
            is_org_instructor, is_global_staff, has_forum_role]):
        return True
    return False


@request_cached()
def get_role_cache(user: User) -> RoleCache:
    """
    Returns a populated RoleCache for the given user.

    The returned RoleCache is also cached on the provided `user` to improve performance on future roles checks.

    :param user: User
    :return: All roles for all courses that this user has access to.
    """
    # pylint: disable=protected-access
    if not hasattr(user, '_roles'):
        user._roles = RoleCache(user)
    return user._roles


@request_cached()
def get_course_roles(user: User) -> list[CourseAccessRole]:
    """
    Returns a list of all course-level roles that this user has.

    :param user: User
    :return: All roles for all courses that this user has access to.
    """
    # pylint: disable=protected-access
    role_cache = get_role_cache(user)
    return list(role_cache.all_roles_set)
