"""Serializers for the members of a course's team."""

from rest_framework import serializers

INSTRUCTOR_ROLE = "instructor"
STAFF_ROLE = "staff"
# The roles a member may hold, in the order members are listed.
TEAM_ROLES = (INSTRUCTOR_ROLE, STAFF_ROLE)


class CourseTeamMemberSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One member of a course's team and the role they hold on it."""

    id = serializers.IntegerField(help_text="Numeric identifier of the member's user account.")
    username = serializers.CharField(help_text="Username of the member.")
    email = serializers.CharField(help_text="Email address of the member.")
    role = serializers.ChoiceField(
        choices=TEAM_ROLES,
        help_text="The member's role: instructor, or staff for members who are not instructors.",
    )

    class Meta:
        ref_name = "CourseTeamMember.v2"


class CourseTeamCallerSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The caller as the course's team sees them, and whether they may change the team."""

    username = serializers.CharField(help_text="Username of the caller.")
    email = serializers.CharField(help_text="Email address of the caller.")
    role = serializers.ChoiceField(
        choices=TEAM_ROLES,
        allow_null=True,
        help_text=(
            "The caller's role on the team, or null when the caller is not a member, as for global staff "
            "who hold no role on the course."
        ),
    )
    can_manage_team = serializers.BooleanField(
        help_text=(
            "Whether the caller may add members to the team, remove members, and change their roles. "
            "Always true for an instructor. A staff member without it may still remove themself from the team."
        ),
    )
    is_only_instructor = serializers.BooleanField(
        help_text=(
            "Whether the caller is the course's only instructor. The only instructor cannot be removed "
            "or made staff until another instructor is added."
        ),
    )

    class Meta:
        ref_name = "CourseTeamCaller.v2"
