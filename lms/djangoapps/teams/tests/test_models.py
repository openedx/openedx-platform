"""
Tests for the teams API at the HTTP request level.
"""


import itertools
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import ddt
import pytest
import pytz
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import EventTestMixin
from lms.djangoapps.teams import TEAM_DISCUSSION_CONTEXT
from lms.djangoapps.teams.errors import AddToIncompatibleTeamError
from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership
from lms.djangoapps.teams.tests.factories import CourseTeamFactory, CourseTeamMembershipFactory
from openedx.core.djangoapps.django_comment_common.signals import (
    comment_created,
    comment_deleted,
    comment_edited,
    comment_endorsed,
    comment_voted,
    thread_created,
    thread_deleted,
    thread_edited,
    thread_voted,
)
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

COURSE_KEY1 = CourseKey.from_string('edx/history/1')
COURSE_KEY2 = CourseKey.from_string('edx/math/1')
TEAMSET_1_ID = "the-teamset"
TEAMSET_2_ID = "the-teamset-2"
TEAMS_CONFIG_1 = TeamsConfig({
    'team_sets': [{'id': TEAMSET_1_ID, 'name': 'Teamset1Name', 'description': 'Teamset1Desc'}]
})
TEAMS_CONFIG_2 = TeamsConfig({
    'team_sets': [{'id': TEAMSET_2_ID, 'name': 'Teamset2Name', 'description': 'Teamset2Desc'}]
})


def create_course(course_key, teams_config):
    return CourseFactory.create(
        teams_configuration=teams_config,
        org=course_key.org,
        course=course_key.course,
        run=course_key.run
    )


class TestModelStrings(SharedModuleStoreTestCase):
    """
    Test `__repr__` and `__str__` methods of this app's models.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_id = "edx/the-course/1"
        cls.course1 = create_course(CourseKey.from_string(cls.course_id), TEAMS_CONFIG_1)
        cls.user = UserFactory.create(username="the-user")
        CourseEnrollmentFactory.create(user=cls.user, course_id=cls.course_id)
        cls.team = CourseTeamFactory(
            course_id=cls.course_id,
            team_id="the-team",
            topic_id=TEAMSET_1_ID,
            name="The Team"
        )
        cls.team_membership = cls.team.add_user(cls.user)

    def test_team_repr(self):
        assert repr(self.team) == (
            "<CourseTeam"
            " id=1"
            " team_id=the-team"
            " team_size=1"
            " topic_id=the-teamset"
            " course_id=edx/the-course/1"
            ">"
        )

    def test_team_text(self):
        assert str(self.team) == (
            "The Team in edx/the-course/1"
        )

    def test_team_membership_repr(self):
        assert repr(self.team_membership) == (
            "<CourseTeamMembership id=1 user_id=1 team_id=1>"
        )

    def test_team_membership_str(self):
        assert str(self.team_membership) == (
            "the-user is member of The Team in edx/the-course/1"
        )


class CourseTeamTest(SharedModuleStoreTestCase):
    """Tests for the CourseTeam model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_id = "edx/the-course/1"
        cls.course1 = create_course(CourseKey.from_string(cls.course_id), TEAMS_CONFIG_1)

        cls.audit_learner = UserFactory.create(username="audit")
        CourseEnrollmentFactory.create(user=cls.audit_learner, course_id="edx/the-course/1", mode=CourseMode.AUDIT)
        cls.audit_team = CourseTeamFactory(
            course_id="edx/the-course/1",
            team_id="audit-team",
            topic_id=TEAMSET_1_ID,
            name="The Team"
        )

        cls.masters_learner = UserFactory.create(username="masters")
        CourseEnrollmentFactory.create(user=cls.masters_learner, course_id="edx/the-course/1", mode=CourseMode.MASTERS)
        cls.masters_team = CourseTeamFactory(
            course_id="edx/the-course/1",
            team_id="masters-team",
            topic_id=TEAMSET_1_ID,
            name="The Team",
            organization_protected=True
        )

    def test_add_user(self):
        """Test that we can add users with correct protection status to a team"""
        assert self.masters_team.add_user(self.masters_learner) is not None
        assert self.audit_team.add_user(self.audit_learner) is not None

    def test_add_user_bad_team_access(self):
        """Test that we are blocked from adding a user to a team of mixed enrollment types"""

        with pytest.raises(AddToIncompatibleTeamError):
            self.audit_team.add_user(self.masters_learner)

        with pytest.raises(AddToIncompatibleTeamError):
            self.masters_team.add_user(self.audit_learner)


@ddt.ddt
class TeamMembershipTest(SharedModuleStoreTestCase):
    """Tests for the TeamMembership model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_course(COURSE_KEY1, TEAMS_CONFIG_1)
        create_course(COURSE_KEY2, TEAMS_CONFIG_2)

    def setUp(self):
        """
        Set up tests.
        """
        super().setUp()

        self.user1 = UserFactory.create(username='user1')
        self.user2 = UserFactory.create(username='user2')
        self.user3 = UserFactory.create(username='user3')

        for user in (self.user1, self.user2, self.user3):
            CourseEnrollmentFactory.create(user=user, course_id=COURSE_KEY1)
        CourseEnrollmentFactory.create(user=self.user1, course_id=COURSE_KEY2)

        self.team1 = CourseTeamFactory(
            course_id=COURSE_KEY1,
            team_id='team1',
            topic_id=TEAMSET_1_ID,
        )
        self.team2 = CourseTeamFactory(
            course_id=COURSE_KEY2,
            team_id='team2',
            topic_id=TEAMSET_2_ID,
        )

        self.team_membership11 = self.team1.add_user(self.user1)
        self.team_membership12 = self.team1.add_user(self.user2)
        self.team_membership21 = self.team2.add_user(self.user1)

    def test_membership_last_activity_set(self):
        current_last_activity = self.team_membership11.last_activity_at
        # Assert that the first save in the setUp sets a value.
        assert current_last_activity is not None

        self.team_membership11.save()

        # Verify that we only change the last activity_at when it doesn't
        # already exist.
        assert self.team_membership11.last_activity_at == current_last_activity

    def test_team_size_delete_membership(self):
        """Test that the team size field is correctly updated when deleting a
        team membership.
        """
        assert self.team1.team_size == 2
        self.team_membership11.delete()
        team = CourseTeam.objects.get(id=self.team1.id)
        assert team.team_size == 1

    def test_team_size_create_membership(self):
        """Test that the team size field is correctly updated when creating a
        team membership.
        """
        assert self.team1.team_size == 2
        self.team1.add_user(self.user3)
        team = CourseTeam.objects.get(id=self.team1.id)
        assert team.team_size == 3

    @ddt.data(
        (None, None, None, 3),
        ('user1', None, None, 2),
        ('user1', [COURSE_KEY1], None, 1),
        ('user1', None, ['team1'], 1),
        ('user2', None, None, 1),
    )
    @ddt.unpack
    def test_get_memberships(self, username, course_ids, team_ids, expected_count):
        assert CourseTeamMembership.get_memberships(username=username,
                                                    course_ids=course_ids,
                                                    team_ids=team_ids).count() == expected_count

    @ddt.data(
        ('user1', COURSE_KEY1, TEAMSET_1_ID, True),
        ('user1', COURSE_KEY1, TEAMSET_2_ID, False),
        ('user2', COURSE_KEY1, TEAMSET_1_ID, True),
        ('user2', COURSE_KEY1, TEAMSET_2_ID, False),
        ('user1', COURSE_KEY2, TEAMSET_1_ID, False),
        ('user2', COURSE_KEY2, TEAMSET_1_ID, False),
    )
    @ddt.unpack
    def test_user_in_team_for_course_teamset(self, username, course_id, teamset_id, expected_value):
        user = getattr(self, username)
        assert CourseTeamMembership.user_in_team_for_teamset(user, course_id, teamset_id) == expected_value


@ddt.ddt
class TeamSignalsTest(EventTestMixin, SharedModuleStoreTestCase):
    """Tests for handling of team-related signals."""

    SIGNALS = {
        'thread_created': thread_created,
        'thread_edited': thread_edited,
        'thread_deleted': thread_deleted,
        'thread_voted': thread_voted,
        'comment_created': comment_created,
        'comment_edited': comment_edited,
        'comment_deleted': comment_deleted,
        'comment_voted': comment_voted,
        'comment_endorsed': comment_endorsed,
    }

    DISCUSSION_TOPIC_ID = 'test_topic'

    def setUp(self):  # pylint: disable=arguments-differ
        """Create a user with a team to test signals."""
        super().setUp('lms.djangoapps.teams.utils.tracker')
        self.user = UserFactory.create(username="user")
        self.moderator = UserFactory.create(username="moderator")
        self.team = CourseTeamFactory(discussion_topic_id=self.DISCUSSION_TOPIC_ID)
        self.team_membership = CourseTeamMembershipFactory(user=self.user, team=self.team)

    def mock_comment(self, context=TEAM_DISCUSSION_CONTEXT, user=None):
        """Create a mock comment service object with the given context."""
        if user is None:
            user = self.user
        return MagicMock(
            user_id=user.id,
            commentable_id=self.DISCUSSION_TOPIC_ID,
            context=context,
            **{'thread.user_id': self.user.id}
        )

    @contextmanager
    def assert_last_activity_updated(self, should_update):
        """If `should_update` is True, assert that the team and team
        membership have had their `last_activity_at` updated. Otherwise,
        assert that it was not updated.
        """
        team_last_activity = self.team.last_activity_at
        team_membership_last_activity = self.team_membership.last_activity_at
        yield
        # Reload team and team membership from the database in order to pick up changes
        team = CourseTeam.objects.get(id=self.team.id)
        team_membership = CourseTeamMembership.objects.get(id=self.team_membership.id)
        if should_update:
            assert team.last_activity_at > team_last_activity
            assert team_membership.last_activity_at > team_membership_last_activity
            now = datetime.utcnow().replace(tzinfo=pytz.utc)
            assert now > team.last_activity_at
            assert now > team_membership.last_activity_at
            self.assert_event_emitted(
                'edx.team.activity_updated',
                team_id=team.team_id,
            )
        else:
            assert team.last_activity_at == team_last_activity
            assert team_membership.last_activity_at == team_membership_last_activity
            self.assert_no_events_were_emitted()

    @ddt.data(
        *itertools.product(
            list(SIGNALS.keys()),
            (('user', True), ('moderator', False))
        )
    )
    @ddt.unpack
    def test_signals(self, signal_name, user_should_update):
        """Test that `last_activity_at` is correctly updated when team-related
        signals are sent.
        """
        (user, should_update) = user_should_update
        with self.assert_last_activity_updated(should_update):
            user = getattr(self, user)
            with patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
                with patch('lms.djangoapps.discussion.rest_api.tasks.send_thread_created_notification.apply_async'):
                    with patch(
                        'lms.djangoapps.discussion.rest_api.tasks.send_response_endorsed_notifications.apply_async'
                    ):
                        signal = self.SIGNALS[signal_name]
                        signal.send(sender=None, user=user, post=self.mock_comment())

    @ddt.data('thread_voted', 'comment_voted')
    def test_vote_others_post(self, signal_name):
        """Test that voting on another user's post correctly fires a
        signal."""
        with self.assert_last_activity_updated(True):
            signal = self.SIGNALS[signal_name]
            signal.send(sender=None, user=self.user, post=self.mock_comment(user=self.moderator))

    @ddt.data(*list(SIGNALS.keys()))
    def test_signals_course_context(self, signal_name):
        """Test that `last_activity_at` is not updated when activity takes
        place in discussions outside of a team.
        """
        with self.assert_last_activity_updated(False):
            with patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
                with patch('lms.djangoapps.discussion.rest_api.tasks.send_thread_created_notification.apply_async'):
                    with patch(
                        'lms.djangoapps.discussion.rest_api.tasks.send_response_endorsed_notifications.apply_async'
                    ):
                        signal = self.SIGNALS[signal_name]
                        signal.send(sender=None, user=self.user, post=self.mock_comment(context='course'))
