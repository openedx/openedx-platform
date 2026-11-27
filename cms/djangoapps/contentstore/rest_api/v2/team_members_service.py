"""Service layer for the v2 course team members endpoints."""

from cms.djangoapps.contentstore.rest_api.v2.serializers.team_members import TEAM_ROLES
from cms.djangoapps.contentstore.utils import get_course_team
from common.djangoapps.student.auth import get_user_permissions


def _course_team(course_key, user):
    """Return Studio's course team context for ``user`` on the course ``course_key``."""
    return get_course_team(user, course_key, get_user_permissions(user, course_key))


def list_team_members(course_key, user):
    """
    Return the members of the team of the course ``course_key``, as seen by ``user``.

    Instructors come first, then the staff who are not instructors, each in
    order of username. A user holding both roles is listed once, as an
    instructor.
    """
    members = _course_team(course_key, user)["users"]
    return sorted(members, key=lambda member: (TEAM_ROLES.index(member["role"]), member["username"]))


def describe_caller(course_key, user):
    """
    Return ``user`` as the team of the course ``course_key`` sees them.

    ``role`` is the role under which the user is listed among the members, or
    None when they are not listed; ``can_manage_team`` and
    ``is_only_instructor`` are Studio's own answers for the course.
    """
    team = _course_team(course_key, user)
    role = next((member["role"] for member in team["users"] if member["id"] == user.id), None)
    return {
        "username": user.username,
        "email": user.email,
        "role": role,
        "can_manage_team": team["allow_actions"],
        "is_only_instructor": team["show_transfer_ownership_hint"],
    }
