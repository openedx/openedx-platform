"""
The Python API other app should use to work with Teams feature
"""


import logging
from enum import Enum

from django.db.models import Count, Q
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, anonymous_id_for_user
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from lms.djangoapps.courseware.courses import has_access
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership
from openedx.core.lib.teams_config import TeamsetType
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

logger = logging.getLogger(__name__)


class OrganizationProtectionStatus(Enum):
    """
    Enum for the different protection status a user can be in related to their
    course enrollment mode.

    "protection_exempt" means the user is a course or org staff that do not need to be protected.

    "protected" means the learner is part of an organization and should be in an exclusive team

    "unprotected" means the learner is part of the general edX learner in audit or verified tracks
    """
    protected = 'org_protected'
    protection_exempt = 'org_protection_exempt'
    unprotected = 'org_unprotected'

    @property
    def is_protected(self):
        return self == self.protected

    @property
    def is_exempt(self):
        return self == self.protection_exempt


ORGANIZATION_PROTECTED_MODES = (
    CourseMode.MASTERS,
)


def get_team_by_team_id(team_id):
    """
    API Function to lookup team object by team_id, which is globally unique.
    If there is no such team, return None.
    """
    try:
        return CourseTeam.objects.get(team_id=team_id)
    except CourseTeam.DoesNotExist:
        return None


def get_team_by_discussion(discussion_id):
    """
    This is a function to get team object by the discussion_id passed in.
    If the discussion_id is not associated with any team, we return None
    """
    try:
        return CourseTeam.objects.get(discussion_topic_id=discussion_id)
    except CourseTeam.DoesNotExist:
        # When the discussion does not belong to a team. It's visible in
        # any team context
        return None


def _get_teamset_type(course_id, teamset_id):
    """
    Helper to get teamset type from a course_id and teamset_id.
    Assumes course_id exists and teamset_id is defined
    """
    course = modulestore().get_course(course_id)
    return course.teams_configuration.teamsets_by_id[teamset_id].teamset_type


def is_team_discussion_private(team):
    """
    Checks to see if the team is configured to have its discussion to be private
    """
    if not team:
        return False
    return _get_teamset_type(team.course_id, team.topic_id) == TeamsetType.private_managed


def is_instructor_managed_team(team):
    """
    Return true if the team is managed by instructors.
    """
    if not team:
        return False
    return is_instructor_managed_topic(team.course_id, team.topic_id)


def is_instructor_managed_topic(course_id, topic):
    """
    Return true if the topic is managed by instructors.
    """
    if not course_id or not topic:
        return False
    managed_types = (TeamsetType.private_managed, TeamsetType.public_managed)
    return _get_teamset_type(course_id, topic) in managed_types


def user_is_a_team_member(user, team):
    """
    Return if the user is a member of the team
    If the team is not defined, return False
    """
    if team:
        return team.users.filter(id=user.id).exists()
    return False


def discussion_visible_by_user(discussion_id, user):
    """
    This function checks whether the discussion should be visible to the user.
    The discussion should not be visible to the user if
    * The discussion is part of the Team AND
    * The team is configured to hide the discussions from non-teammembers AND
    * The user is not part of the team
    """
    team = get_team_by_discussion(discussion_id)
    return not is_team_discussion_private(team) or user_is_a_team_member(user, team)


def has_course_staff_privileges(user, course_key):
    """
    Returns True if the user is an admin for the course, else returns False
    """
    if user.is_staff:
        return True
    if CourseStaffRole(course_key).has_user(user):
        return True
    if CourseInstructorRole(course_key).has_user(user):
        return True
    return False


def has_team_api_access(user, course_key, access_username=None):
    """Returns True if the user has access to the Team API for the course
    given by `course_key`. The user must either be enrolled in the course,
    be course staff, be global staff, or have discussion privileges.

    Args:
      user (User): The user to check access for.
      course_key (CourseKey): The key to the course which we are checking access to.
      access_username (string): If provided, access_username must match user.username for non staff access.

    Returns:
      bool: True if the user has access, False otherwise.
    """
    if has_course_staff_privileges(user, course_key):
        return True
    if has_discussion_privileges(user, course_key):
        return True
    if not access_username or access_username == user.username:
        return CourseEnrollment.is_enrolled(user, course_key)
    return False


def user_organization_protection_status(user, course_key):
    """
    Returns the organization protection status of the user related to this course
    If the user is in the Masters track of the course, we return the protected status.
    If the user is a staff of the course, we return the protection_exempt status
    else, we return the unprotected status
    """
    if has_course_staff_privileges(user, course_key):
        return OrganizationProtectionStatus.protection_exempt
    enrollment = CourseEnrollment.get_enrollment(user, course_key)
    if enrollment and enrollment.is_active:
        if enrollment.mode in ORGANIZATION_PROTECTED_MODES:
            return OrganizationProtectionStatus.protected
        else:
            return OrganizationProtectionStatus.unprotected
    else:
        raise ValueError(  # pylint: disable=raising-format-tuple
            'Cannot check the org_protection status on a student [%s] not enrolled in course [%s]',
            user.id,
            course_key
        )


def has_specific_team_access(user, team):
    """
    To have access to a team a user must:
        - Be course staff
        OR
        - be in the correct bubble
        - be in the team if it is private
    """
    return has_course_staff_privileges(user, team.course_id) or (
        user_protection_status_matches_team(user, team) and user_on_team_or_team_is_public(user, team)
    )


def has_specific_teamset_access(user, course_block, teamset_id):
    """
    Staff have access to all teamsets.
    All non-staff users have access to open and public_managed teamsets.
    Non-staff users only have access to a private_managed teamset if they are in a team in that teamset
    """
    return has_course_staff_privileges(user, course_block.id) or \
        teamset_is_public_or_user_is_on_team_in_teamset(user, course_block, teamset_id)


def teamset_is_public_or_user_is_on_team_in_teamset(user, course_block, teamset_id):
    """
    The only users who should be able to see private_managed teamsets
    or recieve any information about them at all from the API are:
    - Course staff
    - Users who are enrolled in a team in a private_managed teamset

    course_block is passed in because almost universally where we'll be calling this, we will already
    need to have looked up the course from modulestore to make sure that the topic we're interested in
    exists in the course.
    """
    teamset = course_block.teams_configuration.teamsets_by_id[teamset_id]
    if teamset.teamset_type != TeamsetType.private_managed:
        return True
    return CourseTeamMembership.user_in_team_for_teamset(user, course_block.id, topic_id=teamset_id)


def user_on_team_or_team_is_public(user, team):
    """
    The only users who should be able to see private_managed teams
    or recieve any information about them at all from the API are:
    - Course staff
    - Users who are enrolled in a team in a private_managed teamset
    * They should only be able to see their own team, no other teams.
    """
    if CourseTeamMembership.is_user_on_team(user, team):
        return True
    course_block = modulestore().get_course(team.course_id)
    teamset = course_block.teams_configuration.teamsets_by_id[team.topic_id]
    return teamset.teamset_type != TeamsetType.private_managed


def user_protection_status_matches_team(user, team):
    """
    Check whether the user have access to the specific team.
    The user can be of a different organization protection bubble with the team in question.
    If user is not in the same organization protection bubble with the team, return False.
    Else, return True. If the user is a course admin, also return true
    """
    protection_status = user_organization_protection_status(user, team.course_id)
    if protection_status == OrganizationProtectionStatus.protection_exempt:
        return True
    if team.organization_protected:
        return OrganizationProtectionStatus.protected == protection_status
    else:
        return OrganizationProtectionStatus.unprotected == protection_status


def _get_team_filter_query(topic_id_set, course_id, organization_protection_status):
    """ Helper function to get the team count query set based on the filters provided """

    filter_query = {'course_id': course_id}
    if len(topic_id_set) == 1:
        filter_query.update({'topic_id': topic_id_set[0]})
    else:
        filter_query.update({'topic_id__in': topic_id_set})

    if organization_protection_status != OrganizationProtectionStatus.protection_exempt:
        filter_query.update(
            {'organization_protected': organization_protection_status == OrganizationProtectionStatus.protected}
        )
    return filter_query


def get_teams_accessible_by_user(user, topic_id_set, course_id, organization_protection_status):
    """ Get teams taking for a user, taking into account user visibility privileges """
    # Filter by topics, course, and protection status
    filter_query = _get_team_filter_query(topic_id_set, course_id, organization_protection_status)

    # Staff gets unfiltered list of teams
    if has_access(user, 'staff', course_id):
        return CourseTeam.objects.filter(**filter_query)

    # Private teams should be hidden unless the student is a member
    course_block = modulestore().get_course(course_id)
    private_teamset_ids = [ts.teamset_id for ts in course_block.teamsets if ts.is_private_managed]
    return CourseTeam.objects.filter(**filter_query).exclude(
        Q(topic_id__in=private_teamset_ids), ~Q(membership__user=user)
    )


def add_team_count(user, topics, course_id, organization_protection_status):
    """
    Helper method to add team_count for a list of topics.
    This allows for a more efficient single query.
    """
    topic_ids = [topic['id'] for topic in topics]
    teams_query_set = get_teams_accessible_by_user(
        user,
        topic_ids,
        course_id,
        organization_protection_status
    )

    teams_per_topic = teams_query_set.values('topic_id').annotate(team_count=Count('topic_id'))

    topics_to_team_count = {d['topic_id']: d['team_count'] for d in teams_per_topic}
    for topic in topics:
        topic['team_count'] = topics_to_team_count.get(topic['id'], 0)


def can_user_modify_team(user, team):
    """
    Returns whether a User has permission to modify the membership of a CourseTeam.

    Assumes that user is enrolled in course run.
    """
    return (
        (not is_instructor_managed_team(team)) or
        has_course_staff_privileges(user, team.course_id)
    )


def can_user_create_team_in_topic(user, course_id, topic_id):
    """
    Returns whether a User has permission to create a team in the given topic.

    Assumes that user is enrolled in course run.
    """
    return (
        (not is_instructor_managed_topic(course_id, topic_id)) or
        has_course_staff_privileges(user, course_id)
    )


def get_team_for_user_course_topic(user, course_id, topic_id):
    """
    Returns the matching CourseTeam for the given user, course, and topic

    If course_id is invalid, a ValueError is raised
    """
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        raise ValueError("The supplied course id {course_id} is not valid.".format(  # pylint: disable=raise-missing-from  # noqa: B904, UP032
            course_id=course_id
        ))
    try:
        return CourseTeam.objects.get(
            course_id=course_key,
            membership__user__username=user.username,
            topic_id=topic_id,
        )
    except CourseTeam.DoesNotExist:
        return None
    except CourseTeam.MultipleObjectsReturned:
        # This shouldn't ever happen but it's here for safety's sake
        msg = "user {username} is on multiple teams within course {course} topic {topic}"
        logger.error(msg.format(
            username=user.username,
            course=course_id,
            topic=topic_id,
        ))
        return CourseTeam.objects.filter(
            course_id=course_key,
            membership__user__username=user.username,
            topic_id=topic_id,
        ).first()


def get_teams_in_teamset(course_id, topic_id):
    """
    Given a course_id and topic_id, return all CourseTeams in the course and topic
    """
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError as exc:
        raise ValueError("The supplied course id {course_id} is not valid.".format(  # noqa: UP032
            course_id=course_id
        )) from exc
    return CourseTeam.objects.filter(
        course_id=course_key,
        topic_id=topic_id,
    ).all()


def anonymous_user_ids_for_team(user, team):
    """ Get the anonymous user IDs for members of a team, used in team submissions
        Requesting user must be a member of the team or course staff

        Returns:
            (Array) User IDs, sorted to remove any correlation to usernames
    """
    if not user or not team:
        raise Exception("User and team must be provided for ID lookup")

    if not has_course_staff_privileges(user, team.course_id) and not user_is_a_team_member(user, team):
        raise Exception("User {user} is not permitted to access team info for {team}".format(  # noqa: UP032
            user=user.username,
            team=team.team_id
        ))

    return sorted([
        anonymous_id_for_user(user=team_member, course_id=team.course_id)
        for team_member in team.users.all()
    ])


def get_assignments_for_team(user, team):
    """ Get openassessment XBlocks configured for the current teamset """
    # Confirm access
    if not has_specific_team_access(user, team):
        raise Exception("User {user} is not permitted to access team info for {team}".format(  # noqa: UP032
            user=user.username,
            team=team.team_id
        ))

    # Limit to team-enabled ORAs for the matching teamset in the course
    return modulestore().get_items(
        team.course_id,
        qualifiers={'category': 'openassessment'},
        settings={'teams_enabled': True, 'selected_teamset_id': team.topic_id}
    )
