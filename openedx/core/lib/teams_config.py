"""
Safe configuration wrapper for Course Teams feature.
"""
import logging
import re
from enum import Enum

from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

from xmodule.partitions.partitions import UserPartition, UserPartitionError
log = logging.getLogger(__name__)

# "Arbitrarily large" but still limited
MANAGED_TEAM_MAX_TEAM_SIZE = 200
# Arbitrarily arbitrary
DEFAULT_COURSE_RUN_MAX_TEAM_SIZE = 50
TEAM_SCHEME = "team"
TEAMS_NAMESPACE = "teams"

# .. toggle_name: teams.content_groups_for_teams
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables content groups for teams. Content groups are virtual groupings of learners
#    who will see a particular set of course content. When this flag is enabled, course authors can create teams and
#    assign content to each of them. Then, when a learner joins a team, they will see the content that is assigned to
#    that team's content group. This flag is only relevant for courses that have teams enabled.
# .. toggle_use_cases: temporary, opt_in
# .. toggle_target_removal_date: Teak
# .. toggle_creation_date: 2024-04-01
CONTENT_GROUPS_FOR_TEAMS = CourseWaffleFlag(
    f"{TEAMS_NAMESPACE}.content_groups_for_teams", __name__
)


class TeamsConfig:
    """
    Configuration for the Course Teams feature on a course run.

    Takes in a configuration from a JSON-friendly dictionary,
    and exposes cleaned data from it.
    """
    def __init__(self, data):
        """
        Initialize a TeamsConfig object with a dictionary.
        """
        self._data = data if isinstance(data, dict) else {}

    def __str__(self):
        """
        Return user-friendly string.
        """
        return f"Teams configuration for {len(self.teamsets)} team-sets"

    def __repr__(self):
        """
        Return developer-helpful string.
        """
        return "<{} default_max_team_size={} teamsets=[{}] enabled={}>".format(
            self.__class__.__name__,
            self.default_max_team_size,
            ", ".join(repr(teamset) for teamset in self.teamsets),
            self.is_enabled,
        )

    def __eq__(self, other):
        """
        Define equality based on cleaned data.
        """
        return isinstance(other, self.__class__) and self.cleaned_data == other.cleaned_data

    def __ne__(self, other):
        """
        Overrides default inequality to be the inverse of our custom equality.
        Safe to remove once we're in Python 3 -- Py3 does this for us.
        """
        return not self.__eq__(other)

    @property
    def source_data(self):
        """
        Dictionary containing the data from which this TeamsConfig was built.
        """
        return self._data

    @cached_property
    def cleaned_data(self):
        """
        JSON-friendly dictionary containing cleaned data from this TeamsConfig.
        """
        return {
            'enabled': self.is_enabled,
            'max_team_size': self.default_max_team_size,
            'team_sets': [
                teamset.cleaned_data for teamset in self.teamsets
            ]
        }

    @property
    def is_enabled(self):
        """
        Whether the Course Teams feature is enabled for this course run.
        """
        has_teamsets = bool(self.teamsets)
        enabled = self._data.get('enabled')

        # The `enabled` field is a relatively recent addition and thus is not present in all team configurations.
        # If it is present in the data, it controls whether the feature is turned on or off.
        # If it isn't present, the feature is considered enabled if there are teamsets defined.
        if enabled is not None:
            return enabled
        else:
            return has_teamsets

    @is_enabled.setter
    def is_enabled(self, value):
        """
        Setter to set value of enabled value
        """
        self._data['enabled'] = value

    @cached_property
    def teamsets(self):
        """
        List of configurations for team-sets.

        A team-set is a logical collection of teams, generally centered around a
        discussion topic or assignment.

        A learner should be able to join one team per team-set
        (TODO MST-12... currently, a learner may join one team per course).
        """
        all_teamsets_data = self._data.get(
            'team_sets',
            # For backwards compatibility, also check "topics" key.
            self._data.get('topics', [])
        )
        if not isinstance(all_teamsets_data, list):
            return []
        all_teamsets = [
            TeamsetConfig(teamset_data)
            for teamset_data in all_teamsets_data
        ]
        good_teamsets = []
        seen_ids = set()
        for teamset in all_teamsets:
            if teamset.teamset_id and teamset.teamset_id not in seen_ids:
                good_teamsets.append(teamset)
                seen_ids.add(teamset.teamset_id)
        return good_teamsets

    @cached_property
    def teamsets_by_id(self):
        return {teamset.teamset_id: teamset for teamset in self.teamsets}

    @cached_property
    def default_max_team_size(self):
        """
        The default maximum size for teams in this course.

        Can be overriden by individual team sets; see `calc_max_team_size`.
        """
        return _clean_max_team_size(self._data.get('max_team_size')) or DEFAULT_COURSE_RUN_MAX_TEAM_SIZE

    def calc_max_team_size(self, teamset_id):
        """
        Given a team-set's ID, return the maximum allowed size of teams within it.

        For 'open' team-sets, first regards the team-set's `max_team_size`,
        then falls back to the course's `max_team_size`.
        For managed team-sets, returns `MANAGED_TEAM_MAX_TEAM_SIZE`
        (a number that is big enough to never really be a limit, but does put an upper limit for safety's sake)

        Return value of None should be regarded as "no maximum size" (TODO MST-33).
        """
        try:
            teamset = self.teamsets_by_id[teamset_id]
        except KeyError:
            raise ValueError(f"Team-set {teamset_id!r} does not exist.")  # lint-amnesty, pylint: disable=raise-missing-from
        if teamset.teamset_type != TeamsetType.open:
            return MANAGED_TEAM_MAX_TEAM_SIZE
        if teamset.max_team_size:
            return teamset.max_team_size
        return self.default_max_team_size


class TeamsetConfig:
    """
    Configuration for a team-set within a course run.

    Takes in a configuration from a JSON-friendly dictionary,
    and exposes cleaned data from it.
    """
    teamset_id_regex = re.compile(r'^[A-Za-z0-9_. -]+$')

    def __init__(self, data):
        """
        Initialize a TeamsConfig object with a dictionary.
        """
        self._data = data if isinstance(data, dict) else {}

    def __str__(self):
        """
        Return user-friendly string.
        """
        return self.name

    def __repr__(self):
        """
        Return developer-helpful string.
        """
        attrs = ['teamset_id', 'name', 'description', 'max_team_size', 'teamset_type', 'user_partition_id']
        return "<{} {}>".format(
            self.__class__.__name__,
            " ".join(
                attr + "=" + repr(getattr(self, attr))
                for attr in attrs if hasattr(self, attr)
            ),
        )

    def __eq__(self, other):
        """
        Define equality based on cleaned data.
        """
        return isinstance(other, self.__class__) and self.cleaned_data == other.cleaned_data

    def __ne__(self, other):
        """
        Overrides default inequality to be the inverse of our custom equality.
        Safe to remove once we're in Python 3 -- Py3 does this for us.
        """
        return not self.__eq__(other)

    @property
    def source_data(self):
        """
        Dictionary containing the data from which this TeamsConfig was built.
        """
        return self._data

    @cached_property
    def cleaned_data(self):
        """
        JSON-friendly dictionary containing cleaned data from this TeamsConfig.
        """
        return {
            'id': self.teamset_id,
            'name': self.name,
            'description': self.description,
            'max_team_size': self.max_team_size,
            'type': self.teamset_type.value,
            'user_partition_id': self.user_partition_id,
        }

    @cached_property
    def teamset_id(self):
        """
        An identifier for this team-set.

        Should be a URL-slug friendly string,
        but for a historical reasons may contain periods and spaces.
        """
        teamset_id = _clean_string(self._data.get('id'))
        if not self.teamset_id_regex.match(teamset_id):
            return ""
        return teamset_id

    @cached_property
    def name(self):
        """
        A human-friendly name of the team-set,
        falling back to `teamset_id`.
        """
        return _clean_string(self._data.get('name')) or self.teamset_id

    @cached_property
    def description(self):
        """
        A brief description of the team-set,
        falling back to empty string.
        """
        return _clean_string(self._data.get('description'))

    @cached_property
    def max_team_size(self):
        """
        Configured maximum team size override for this team-set,
        falling back to None.
        """
        return _clean_max_team_size(self._data.get('max_team_size'))

    @cached_property
    def teamset_type(self):
        """
        Configured TeamsetType,
        falling back to default TeamsetType.
        """
        try:
            return TeamsetType(self._data['type'])
        except (KeyError, ValueError):
            return TeamsetType.get_default()

    @cached_property
    def is_private_managed(self):
        """
        Returns true if teamsettype is private_managed
        """
        return self.teamset_type == TeamsetType.private_managed

    @cached_property
    def user_partition_id(self):
        """
        The ID of the dynamic user partition for this team-set,
        falling back to None.
        """
        return self._data.get('user_partition_id')


class TeamsetType(Enum):
    """
    Management and privacy scheme for teams within a team-set.

    "open" team-sets allow learners to freely join, leave, and create teams.

    "public_managed" team-sets forbid learners from modifying teams' membership.
    Instead, instructors manage membership (TODO MST-9).

    "private_managed" is like public_managed, except for that team names,
    team memberships, and team discussions are all private to the members
    of the teams (TODO MST-10).
    """
    open = "open"
    public_managed = "public_managed"
    private_managed = "private_managed"
    open_managed = "open_managed"

    @classmethod
    def get_default(cls):
        """
        Return default TeamsetType.
        """
        return cls.open


def _clean_string(value):
    """
    Return `str(value)` if it's a string or int, otherwise "".
    """
    if isinstance(value, (int,) + (str,)):
        return str(value)
    return ""


def _clean_max_team_size(value):
    """
    Return `value` if it's a positive int, otherwise None.
    """
    if not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def create_team_set_partitions_with_course_id(course_id, team_sets=None):
    """
    Create and return the team-set user partitions based only on course_id.
    If they cannot be created, None is returned.
    """
    if not team_sets:
        team_sets = _get_team_sets(course_id) or {}

    try:
        team_scheme = UserPartition.get_scheme(TEAM_SCHEME)
    except UserPartitionError:
        log.warning(f"No {TEAM_SCHEME} scheme registered, TeamUserPartition will not be created.")
        return None

    # Get team-sets from course and create user partitions for each team-set
    # Then get teams from each team-set and create user groups for each team
    partitions = []
    for team_set in team_sets:
        partition = team_scheme.create_user_partition(
            id=team_set.user_partition_id,
            name=_("Team Group: {team_set_name}").format(team_set_name=team_set.name),
            description=_("Partition for segmenting users by team-set"),
            parameters={
                "course_id": str(course_id),
                "team_set_id": team_set.teamset_id,
            }
        )
        if partition:
            partitions.append(partition)

    return partitions


def create_team_set_partition(course):
    """
    Get the dynamic enrollment track user partition based on the team-sets of the course.
    """
    if not course.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(course.id):
        return []
    return create_team_set_partitions_with_course_id(
        course.id,
        _get_team_sets(course.id),
    )


def _get_team_sets(course_key):
    """
    Get team-sets of the course.
    """
    # Avoid ImportError import by importing at this level:
    # TeamsConfigurationService -> is_masquerading_as_specific_student -> CourseMode -> CourseOverview
    # Raises: ImportError: cannot import name 'CourseOverview' from partially initialized module
    from xmodule.services import TeamsConfigurationService
    team_sets = TeamsConfigurationService().get_teams_configuration(course_key).teamsets
    if not team_sets:
        return None

    return team_sets
