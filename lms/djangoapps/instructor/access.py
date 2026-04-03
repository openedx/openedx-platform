"""
Access control operations for use by instructor APIs.

Does not include any access control, be sure to check access before calling.

TO DO sync instructor and staff flags
    e.g. should these be possible?
        {instructor: true, staff: false}
        {instructor: true, staff: true}
"""


import logging

from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseCcxCoachRole,
    CourseDataResearcherRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from lms.djangoapps.instructor.enrollment import enroll_email, get_email_params
from openedx.core.djangoapps.django_comment_common.models import Role

log = logging.getLogger(__name__)

ROLES = {
    'beta': CourseBetaTesterRole,
    'instructor': CourseInstructorRole,
    'staff': CourseStaffRole,
    'limited_staff': CourseLimitedStaffRole,
    'ccx_coach': CourseCcxCoachRole,
    'data_researcher': CourseDataResearcherRole,
}


def list_with_level(course_id, level):
    """
    List users who have 'level' access.

    `level` is in ['instructor', 'staff', 'beta'] for standard courses.
    There could be other levels specific to the course.
    If there is no Group for that course-level, returns an empty list
    """
    return ROLES[level](course_id).users_with_role()


def allow_access(course, user, level, send_email=True):
    """
    Allow user access to course modification.

    `level` is one of ['instructor', 'staff', 'beta']
    """
    _change_access(course, user, level, 'allow', send_email)


def revoke_access(course, user, level, send_email=True):
    """
    Revoke access from user to course modification.

    `level` is one of ['instructor', 'staff', 'beta']
    """
    _change_access(course, user, level, 'revoke', send_email)


def _change_access(course, user, level, action, send_email=True):
    """
    Change access of user.

    `level` is one of ['instructor', 'staff', 'beta']
    action is one of ['allow', 'revoke']

    NOTE: will create a group if it does not yet exist.
    """

    try:
        role = ROLES[level](course.id)
    except KeyError:
        raise ValueError(f"unrecognized level '{level}'")  # lint-amnesty, pylint: disable=raise-missing-from

    if action == 'allow':
        if level == 'ccx_coach':
            email_params = get_email_params(course, True)
            enroll_email(
                course_id=course.id,
                student_email=user.email,
                auto_enroll=True,
                message_students=send_email,
                message_params=email_params,
            )
        role.add_users(user)
    elif action == 'revoke':
        role.remove_users(user)
    else:
        raise ValueError(f"unrecognized action '{action}'")


def update_forum_role(course_id, user, rolename, action):
    """
    Change forum access of user.

    `rolename` is one of [FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA]
    `action` is one of ['allow', 'revoke']

    if `action` is bad, raises ValueError
    if `rolename` does not exist, raises Role.DoesNotExist
    """
    role = Role.objects.get(course_id=course_id, name=rolename)

    if action == 'allow':
        role.users.add(user)
    elif action == 'revoke':
        role.users.remove(user)
    else:
        raise ValueError(f"unrecognized action '{action}'")


def is_beta_tester(user, course_id):
    """
    Returns True if the user is a beta tester in this course, and False if not
    """
    beta_testers_queryset = list_with_level(course_id, 'beta')
    return beta_testers_queryset.filter(username=user.username).exists()
