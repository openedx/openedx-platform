"""  # pylint: disable=cyclic-import
Tests for course_team reindex command.
"""


from unittest.mock import patch

import ddt
from django.core.management import CommandError, call_command
from opaque_keys.edx.keys import CourseKey
from search.search_engine_base import SearchEngine

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase

from ....search_indexes import CourseTeamIndexer
from ....tests.factories import CourseTeamFactory

COURSE_KEY1 = CourseKey.from_string('edx/history/1')


@ddt.ddt
class ReindexCourseTeamTest(SharedModuleStoreTestCase):
    """
    Tests for the ReindexCourseTeam command
    """

    def setUp(self):
        """
        Set up tests.
        """
        super().setUp()

        self.team1 = CourseTeamFactory(course_id=COURSE_KEY1, team_id='team1')
        self.team2 = CourseTeamFactory(course_id=COURSE_KEY1, team_id='team2')
        self.team3 = CourseTeamFactory(course_id=COURSE_KEY1, team_id='team3')

        self.search_engine = SearchEngine.get_search_engine(index='index_course_team')

    def test_given_no_arguments_raises_command_error(self):
        """
        Test that raises CommandError for incorrect arguments.
        """
        with self.assertRaisesRegex(CommandError, '.*At least one course_team_id or --all needs to be specified.*'):  # noqa: PT027  # pylint: disable=line-too-long
            call_command('reindex_course_team')

    def test_given_conflicting_arguments_raises_command_error(self):
        """
        Test that raises CommandError for incorrect arguments.
        """
        with self.assertRaisesRegex(CommandError, '.*Course teams cannot be specified when --all is also specified.*'):  # noqa: PT027  # pylint: disable=line-too-long
            call_command('reindex_course_team', self.team1.team_id, all=True)

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_TEAMS': False})
    def test_teams_search_flag_disabled_raises_command_error(self):
        """
        Test that raises CommandError for disabled feature flag.
        """
        with self.assertRaisesRegex(CommandError, '.*ENABLE_TEAMS must be enabled.*'):  # noqa: PT027
            call_command('reindex_course_team', self.team1.team_id)

    def test_given_invalid_team_id_raises_command_error(self):
        """
        Test that raises CommandError for invalid team id.
        """
        team_id = 'team4'
        error_str = f'Argument {team_id} is not a course_team team_id'
        with self.assertRaisesRegex(CommandError, error_str):  # noqa: PT027
            call_command('reindex_course_team', team_id)

    @patch.object(CourseTeamIndexer, 'index')
    def test_single_team_id(self, mock_index):
        """
        Test that command indexes a single passed team.
        """
        call_command('reindex_course_team', self.team1.team_id)
        mock_index.assert_called_once_with(self.team1)
        mock_index.reset_mock()

    @patch.object(CourseTeamIndexer, 'index')
    def test_multiple_team_id(self, mock_index):
        """
        Test that command indexes multiple passed teams.
        """
        call_command('reindex_course_team', self.team1.team_id, self.team2.team_id)
        mock_index.assert_any_call(self.team1)
        mock_index.assert_any_call(self.team2)
        mock_index.reset_mock()

    @patch.object(CourseTeamIndexer, 'index')
    def test_all_teams(self, mock_index):
        """
        Test that command indexes all teams.
        """
        call_command('reindex_course_team', all=True)
        mock_index.assert_any_call(self.team1)
        mock_index.assert_any_call(self.team2)
        mock_index.assert_any_call(self.team3)
        mock_index.reset_mock()
