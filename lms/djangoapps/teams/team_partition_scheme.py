"""
Provides a UserPartition driver for teams.
"""
import logging

from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.masquerade import (
    get_course_masquerade,
    get_masquerading_user_group,
    is_masquerading_as_specific_student
)
from lms.djangoapps.teams.api import get_teams_in_teamset
from lms.djangoapps.teams.models import CourseTeamMembership
from openedx.core.lib.teams_config import CONTENT_GROUPS_FOR_TEAMS

from xmodule.modulestore.django import modulestore
from xmodule.partitions.partitions import (  # lint-amnesty, pylint: disable=wrong-import-order
    Group,
    UserPartition
)
from xmodule.services import TeamsConfigurationService


log = logging.getLogger(__name__)


class TeamUserPartition(UserPartition):
    """Extends UserPartition to support dynamic groups pulled from the current
    course teams.
    """

    @property
    def groups(self):
        """Dynamically generate groups (based on teams) for this partition.

        Returns:
            list of Group: The groups in this partition.
        """
        course_key = CourseKey.from_string(self.parameters["course_id"])
        course = modulestore().get_course(course_key)
        if not course.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(course_key):
            return []

        # Get the team-set for this partition via the partition parameters and then get the teams in that team-set
        # to create the groups for this partition.
        team_sets = TeamsConfigurationService().get_teams_configuration(course_key).teamsets
        team_set_id = self.parameters["team_set_id"]
        team_set = next((team_set for team_set in team_sets if team_set.teamset_id == team_set_id), None)
        teams = get_teams_in_teamset(str(course_key), team_set.teamset_id)
        return [
            Group(team.id, str(team.name)) for team in teams
        ]


class TeamPartitionScheme:
    """Uses course team memberships to map learners into partition groups.

    The scheme is only available if the CONTENT_GROUPS_FOR_TEAMS feature flag is enabled.

    This is how it works:
    - A user partition is created for each team-set in the course with a unused partition ID generated in runtime
    by using generate_int_id() with min=MINIMUM_UNUSED_PARTITION_ID and max=MYSQL_MAX_INT.
    - A (Content) group is created for each team in the team-set with the database team ID as the group ID,
    and the team name as the group name.
    - A user is assigned to a group if they are a member of the team.
    """

    read_only = True

    @classmethod
    def get_group_for_user(cls, course_key, user, user_partition):
        """Get the (Content) Group from the specified user partition for the user.

        A user is assigned to the group via their team membership and any mappings from teams to
        partitions / groups that might exist.

        Args:
            course_key (CourseKey): The course key.
            user (User): The user.
            user_partition (UserPartition): The user partition.

        Returns:
            Group: The group in the specified user partition
        """
        course = modulestore().get_course(course_key)
        if not course.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(course_key):
            return None

        # First, check if we have to deal with masquerading.
        # If the current user is masquerading as a specific student, use the
        # same logic as normal to return that student's group. If the current
        # user is masquerading as a generic student in a specific group, then
        # return that group.
        if get_course_masquerade(user, course_key) and not is_masquerading_as_specific_student(user, course_key):
            return get_masquerading_user_group(course_key, user, user_partition)

        # A user cannot belong to more than one team in a team-set by definition, so we can just get the first team.
        teams = get_teams_in_teamset(str(course_key), user_partition.parameters["team_set_id"])
        team_ids = [team.team_id for team in teams]
        user_team = CourseTeamMembership.get_memberships(user.username, [str(course_key)], team_ids).first()
        if not user_team:
            return None

        return Group(user_team.team.id, str(user_team.team.name))

    @classmethod
    def create_user_partition(cls, id, name, description, groups=None, parameters=None, active=True):    # pylint: disable=redefined-builtin, invalid-name, unused-argument
        """Create a custom UserPartition to support dynamic groups based on teams.

        A Partition has an id, name, scheme, description, parameters, and a list
        of groups. The id is intended to be unique within the context where these
        are used. (e.g., for partitions of users within a course, the ids should
        be unique per-course). The scheme is used to assign users into groups.
        The parameters field is used to save extra parameters e.g., location of
        the course ID for this partition scheme.

        Partitions can be marked as inactive by setting the "active" flag to False.
        Any group access rule referencing inactive partitions will be ignored
        when performing access checks.

        Args:
            id (int): The id of the partition.
            name (str): The name of the partition.
            description (str): The description of the partition.
            groups (list of Group): The groups in the partition.
            parameters (dict): The parameters for the partition.
            active (bool): Whether the partition is active.

        Returns:
            TeamUserPartition: The user partition.
        """
        course_key = CourseKey.from_string(parameters["course_id"])
        course = modulestore().get_course(course_key)
        if not course.teams_enabled and not CONTENT_GROUPS_FOR_TEAMS.is_enabled(course_key):
            return None

        # Team-set used to create partition was created before this feature was
        # introduced.  In that case, we need to create a new partition with a
        # new team-set id.
        if not id:
            return None

        team_set_partition = TeamUserPartition(
            id,
            str(name),
            str(description),
            groups,
            cls,
            parameters,
            active=True,
        )
        return team_set_partition
