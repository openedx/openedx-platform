""" Services to expose the Teams API to XBlocks """


from django.urls import reverse


class TeamsService:
    """ Functions to provide teams functionality to XBlocks"""

    def get_team(self, user, course_id, topic_id):
        from . import api
        return api.get_team_for_user_course_topic(user, course_id, topic_id)

    def get_team_names(self, course_id, topic_id):
        """
        Given a course and topic id, return a dict mapping from team id to team name for teams in that topic
        """
        from . import api
        teams = api.get_teams_in_teamset(course_id, topic_id)
        name_mapping = {team.team_id: team.name for team in teams}
        return name_mapping

    def get_team_by_team_id(self, team_id):
        from . import api
        return api.get_team_by_team_id(team_id)

    def get_team_detail_url(self, team):
        """ Returns the url to the detail view for the given team """
        teams_dashboard_url = reverse('teams_dashboard', kwargs={'course_id': team.course_id})
        # Unfortunately required since this URL resolution is done in a Backbone view
        return "{teams_dashboard_url}#teams/{topic_id}/{team_id}".format(  # noqa: UP032
            teams_dashboard_url=teams_dashboard_url,
            topic_id=team.topic_id,
            team_id=team.team_id,
        )

    def get_anonymous_user_ids_for_team(self, user, team):
        from . import api
        return api.anonymous_user_ids_for_team(user, team)
