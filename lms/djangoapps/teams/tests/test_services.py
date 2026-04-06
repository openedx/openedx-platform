"""
Tests for any Teams app services
"""


from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.teams.services import TeamsService
from lms.djangoapps.teams.tests.factories import CourseTeamFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)


class TeamsServiceTests(ModuleStoreTestCase):
    """ Tests for the TeamsService """

    def setUp(self):
        super().setUp()
        self.course_run = CourseRunFactory.create()
        self.course_key = self.course_run['key']
        self.team = CourseTeamFactory.create(course_id=self.course_key)
        self.service = TeamsService()
        self.user = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_key)
        self.team.add_user(self.user)

    def test_get_team_by_team_id(self):
        team = self.service.get_team_by_team_id('NONEXISTANCE')
        assert team is None

        team = self.service.get_team_by_team_id(self.team.team_id)
        assert team == self.team

    def test_get_team(self):
        user_team = self.service.get_team(self.user, self.course_key, self.team.topic_id)
        assert user_team == self.team

        user2 = UserFactory.create()
        user2_team = self.service.get_team(user2, self.course_key, self.team.topic_id)
        assert user2_team is None

    def test_get_team_detail_url(self):
        # edx.org/courses/blah/teams/#teams/topic_id/team_id
        team_detail_url = self.service.get_team_detail_url(self.team)
        split_url = team_detail_url.split('/')
        assert split_url[1:] ==\
               ['courses', str(self.course_run['key']), 'teams', '#teams', self.team.topic_id, self.team.team_id]

    def test_get_team_names(self):
        """
        get_team_names will return a dict mapping the team id to the team name for all teams in the given teamset
        """
        additional_teams = [
            CourseTeamFactory.create(course_id=self.course_key, topic_id=self.team.topic_id)
            for _ in range(3)
        ]

        result = self.service.get_team_names(self.course_key, self.team.topic_id)

        assert result == {
            self.team.team_id: self.team.name,
            additional_teams[0].team_id: additional_teams[0].name,
            additional_teams[1].team_id: additional_teams[1].name,
            additional_teams[2].team_id: additional_teams[2].name,
        }

    def test_get_team_names__none(self):
        """ If there are no teams in the teamset, the function will return an empty list"""
        course_run = CourseRunFactory.create()
        course_key = course_run['key']
        result = self.service.get_team_names(course_key, "some-topic-id")
        assert result == {}
