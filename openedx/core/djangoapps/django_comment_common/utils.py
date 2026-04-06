# pylint: disable=missing-docstring
"""
Common comment client utility functions.
"""
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role,
)


class ThreadContext:
    """ An enumeration that represents the context of a thread. Used primarily by the comments service. """
    STANDALONE = 'standalone'
    COURSE = 'course'


STUDENT_ROLE_PERMISSIONS = ["vote", "update_thread", "follow_thread", "unfollow_thread",
                            "update_comment", "create_sub_comment", "unvote", "create_thread",
                            "follow_commentable", "unfollow_commentable", "create_comment", ]

MODERATOR_ROLE_PERMISSIONS = ["edit_content", "delete_thread", "openclose_thread",
                              "endorse_comment", "delete_comment", "see_all_cohorts"]

GROUP_MODERATOR_ROLE_PERMISSIONS = ["group_edit_content", "group_delete_thread", "group_openclose_thread",
                                    "group_endorse_comment", "group_delete_comment"]

ADMINISTRATOR_ROLE_PERMISSIONS = ["manage_moderator"]

GLOBAL_STAFF_ROLE_PERMISSIONS = ["see_all_cohorts"]


def _save_forum_role(course_key, name):
    """
    Save and Update 'course_key' for all roles which are already created to keep course_id same
    as actual passed course key
    """
    role, created = Role.objects.get_or_create(name=name, course_id=course_key)
    if created is False:
        role.course_id = course_key
        role.save()

    return role


def seed_permissions_roles(course_key):
    """
    Create and assign permissions for forum roles
    """
    administrator_role = _save_forum_role(course_key, FORUM_ROLE_ADMINISTRATOR)
    moderator_role = _save_forum_role(course_key, FORUM_ROLE_MODERATOR)
    group_moderator_role = _save_forum_role(course_key, FORUM_ROLE_GROUP_MODERATOR)
    community_ta_role = _save_forum_role(course_key, FORUM_ROLE_COMMUNITY_TA)
    student_role = _save_forum_role(course_key, FORUM_ROLE_STUDENT)

    for per in STUDENT_ROLE_PERMISSIONS:
        student_role.add_permission(per)

    for per in MODERATOR_ROLE_PERMISSIONS:
        moderator_role.add_permission(per)

    for per in GROUP_MODERATOR_ROLE_PERMISSIONS:
        group_moderator_role.add_permission(per)

    for per in ADMINISTRATOR_ROLE_PERMISSIONS:
        administrator_role.add_permission(per)

    moderator_role.inherit_permissions(student_role)
    group_moderator_role.inherit_permissions(student_role)
    # For now, Community TA == Moderator, except for the styling.
    community_ta_role.inherit_permissions(moderator_role)

    administrator_role.inherit_permissions(moderator_role)


def are_permissions_roles_seeded(course_id):
    """
    Returns whether the forums permissions for a course have been provisioned in
    the database
    """
    try:
        administrator_role = Role.objects.get(name=FORUM_ROLE_ADMINISTRATOR, course_id=course_id)
        moderator_role = Role.objects.get(name=FORUM_ROLE_MODERATOR, course_id=course_id)
        group_moderator_role = Role.objects.get(name=FORUM_ROLE_GROUP_MODERATOR, course_id=course_id)
        student_role = Role.objects.get(name=FORUM_ROLE_STUDENT, course_id=course_id)
    except:  # pylint: disable=bare-except
        return False

    for per in STUDENT_ROLE_PERMISSIONS:
        if not student_role.has_permission(per):
            return False

    for per in MODERATOR_ROLE_PERMISSIONS + STUDENT_ROLE_PERMISSIONS:
        if not moderator_role.has_permission(per):
            return False

    for per in GROUP_MODERATOR_ROLE_PERMISSIONS + STUDENT_ROLE_PERMISSIONS:
        if not group_moderator_role.has_permission(per):
            return False

    for per in ADMINISTRATOR_ROLE_PERMISSIONS + MODERATOR_ROLE_PERMISSIONS + STUDENT_ROLE_PERMISSIONS:
        if not administrator_role.has_permission(per):
            return False

    return True
