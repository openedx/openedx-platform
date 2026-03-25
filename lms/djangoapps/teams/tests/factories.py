"""
Factories for testing the Teams API.
"""


from datetime import datetime  # noqa: I001
from uuid import uuid4

import factory
import pytz
from factory.django import DjangoModelFactory

from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership

LAST_ACTIVITY_AT = datetime(2015, 8, 15, 0, 0, 0, tzinfo=pytz.utc)


class CourseTeamFactory(DjangoModelFactory):
    """Factory for CourseTeams.

    Note that team_id is not auto-generated from name when using the factory.
    """
    class Meta:
        model = CourseTeam
        django_get_or_create = ('team_id',)

    team_id = factory.Sequence('team-{}'.format)
    topic_id = factory.Sequence('topic-{}'.format)
    discussion_topic_id = factory.LazyAttribute(lambda a: uuid4().hex)
    name = factory.Sequence("Awesome Team {}".format)
    description = "A simple description"
    last_activity_at = LAST_ACTIVITY_AT


class CourseTeamMembershipFactory(DjangoModelFactory):
    """Factory for CourseTeamMemberships."""
    class Meta:
        model = CourseTeamMembership

    last_activity_at = LAST_ACTIVITY_AT

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Create the team membership. """
        obj = model_class(*args, **kwargs)
        obj.save()
        return obj
