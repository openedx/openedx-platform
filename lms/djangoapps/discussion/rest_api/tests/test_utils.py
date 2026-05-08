"""
Tests for Discussion REST API utils.
"""

import unittest
from datetime import datetime, timedelta

import ddt
from pytz import UTC

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from lms.djangoapps.discussion.rest_api.tests.utils import CommentsServiceMockMixin
from lms.djangoapps.discussion.rest_api.utils import (
    discussion_open_for_user,
    get_archived_topics,
    get_course_staff_users_list,
    get_course_ta_users_list,
    get_moderator_users_list,
    is_only_student,
    is_posting_allowed,
    remove_empty_sequentials,
)
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, PostingRestriction
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class DiscussionAPIUtilsTestCase(ModuleStoreTestCase):
    """
    Base test-case class for utils for Discussion REST API.
    """
    CREATE_USER = False

    def setUp(self):
        super().setUp()  # pylint: disable=super-with-arguments

        self.course = CourseFactory.create()
        self.course.discussion_blackouts = [datetime.now(UTC) - timedelta(days=3),
                                            datetime.now(UTC) + timedelta(days=3)]
        configuration = DiscussionsConfiguration.get(self.course.id)
        configuration.posting_restrictions = PostingRestriction.SCHEDULED
        configuration.save()
        self.student_role = RoleFactory(name='Student', course_id=self.course.id)
        self.moderator_role = RoleFactory(name='Moderator', course_id=self.course.id)
        self.community_ta_role = RoleFactory(name='Community TA', course_id=self.course.id)
        self.group_community_ta_role = RoleFactory(name='Group Moderator', course_id=self.course.id)

        self.student = UserFactory(username='student', email='student@edx.org')
        self.student_enrollment = CourseEnrollmentFactory(user=self.student)
        self.student_role.users.add(self.student)

        self.moderator = UserFactory(username='moderator', email='staff@edx.org', is_staff=True)
        self.moderator_enrollment = CourseEnrollmentFactory(user=self.moderator)
        self.moderator_role.users.add(self.moderator)

        self.community_ta = UserFactory(username='community_ta1', email='community_ta1@edx.org')
        self.community_ta_role.users.add(self.community_ta)

        self.group_community_ta = UserFactory(username='group_community_ta1', email='group_community_ta1@edx.org')
        self.group_community_ta_role.users.add(self.group_community_ta)

        self.course_staff_user = UserFactory(username='course_staff_user1', email='course_staff_user1@edx.org')
        self.course_instructor_user = UserFactory(username='course_instructor_user1',
                                                  email='course_instructor_user1@edx.org')
        CourseStaffRole(course_key=self.course.id).add_users(self.course_staff_user)
        CourseInstructorRole(course_key=self.course.id).add_users(self.course_instructor_user)

    def test_discussion_open_for_user(self):
        self.assertFalse(discussion_open_for_user(self.course, self.student))  # noqa: PT009
        self.assertTrue(discussion_open_for_user(self.course, self.moderator))  # noqa: PT009
        self.assertTrue(discussion_open_for_user(self.course, self.community_ta))  # noqa: PT009

    def test_course_staff_users_list(self):
        assert len(get_course_staff_users_list(self.course.id)) == 2

    def test_course_moderator_users_list(self):
        assert len(get_moderator_users_list(self.course.id)) == 1

    def test_course_ta_users_list(self):
        ta_user_list = get_course_ta_users_list(self.course.id)
        assert len(ta_user_list) == 2

    def test_get_archived_topics(self):
        # Define some example inputs
        filtered_topic_ids = ['t1', 't2', 't3', 't4']
        topics = [
            {'id': 't1', 'usage_key': 'u1', 'title': 'Topic 1'},
            {'id': 't2', 'usage_key': None, 'title': 'Topic 2'},
            {'id': 't3', 'usage_key': 'u3', 'title': 'Topic 3'},
            {'id': 't4', 'usage_key': 'u4', 'title': 'Topic 4'},
            {'id': 't5', 'usage_key': None, 'title': 'Topic 5'},
        ]
        expected_output = [
            {'id': 't1', 'usage_key': 'u1', 'title': 'Topic 1'},
            {'id': 't3', 'usage_key': 'u3', 'title': 'Topic 3'},
            {'id': 't4', 'usage_key': 'u4', 'title': 'Topic 4'},
        ]

        # Call the function with the example inputs
        output = get_archived_topics(filtered_topic_ids, topics)

        # Assert that the output matches the expected output
        assert output == expected_output

    @ddt.data(
        ('student', False, True),
        ('student', True, False),
        ('moderator', False, False),
        ('course_staff_user', False, False),
        ('course_instructor_user', False, False),
        ('community_ta', False, False),
        ('group_community_ta', False, False),
    )
    @ddt.unpack
    def test_is_only_student(self, user_attr, is_user_admin, expected_result):
        """Test is_only_student for various user role combinations."""
        user = getattr(self, user_attr)
        user.is_staff = is_user_admin
        user.save()
        result = is_only_student(self.course.id, user)
        assert result == expected_result


class TestRemoveEmptySequentials(unittest.TestCase):
    """
    Test for the remove_empty_sequentials function
    """

    def test_empty_data(self):
        # Test that the function can handle an empty list
        data = []
        result = remove_empty_sequentials(data)
        self.assertEqual(result, [])  # noqa: PT009

    def test_no_empty_sequentials(self):
        # Test that the function does not remove any sequentials if they all have children
        data = [
            {"type": "sequential", "children": [{"type": "vertical"}]},
            {"type": "chapter", "children": [
                {"type": "sequential", "children": [{"type": "vertical"}]}
            ]}
        ]
        result = remove_empty_sequentials(data)
        self.assertEqual(result, data)  # noqa: PT009

    def test_remove_empty_sequentials(self):
        # Test that the function removes empty sequentials
        data = [
            {"type": "sequential", "children": []},
            {"type": "chapter", "children": [
                {"type": "sequential", "children": [{"type": "vertical3"}]},
                {"type": "sequential", "children": []},
                {"type": "sequential", "children": []},
                {"type": "sequential", "children": [{"type": "vertical4"}]}
            ]},
            {"type": "chapter", "children": [
                {"type": "sequential", "children": [{"type": "vertical1"}]},
                {"type": "sequential", "children": []},
                {"children": [{"type": "vertical2"}]}
            ]},
            {"type": "chapter", "children": [
                {"type": "sequential", "children": []},
                {"type": "sequential", "children": []},
            ]}
        ]
        expected_output = [
            {"type": "chapter", "children": [
                {"type": "sequential", "children": [{"type": "vertical3"}]},
                {"type": "sequential", "children": [{"type": "vertical4"}]}
            ]},
            {"type": "chapter", "children": [
                {"type": "sequential", "children": [{"type": "vertical1"}]},
                {"children": [{"type": "vertical2"}]}
            ]}
        ]
        result = remove_empty_sequentials(data)
        self.assertEqual(result, expected_output)  # noqa: PT009


@ddt.ddt
class TestBlackoutDates(CommentsServiceMockMixin, ModuleStoreTestCase):
    """
    Test for the is_posting_allowed function
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def _get_date_ranges(self):
        """
        Generate date ranges for testing purposes.
        Returns:
            list: List of date range tuples.
        """
        now = datetime.now(UTC)
        date_ranges = [
            (now - timedelta(days=14), now + timedelta(days=23)),
        ]
        return date_ranges

    def _set_discussion_blackouts(self, date_ranges):
        """
        Set discussion blackouts for the given date ranges.
        Args:
            date_ranges (list): List of date range tuples.
        """
        self.course.discussion_blackouts = [
            [start_date.isoformat(), end_date.isoformat()] for start_date, end_date in date_ranges
        ]

    def _check_posting_allowed(self, posting_restriction):
        """
        Check if posting is allowed for the given posting restriction.
        Args:
            posting_restriction (str): Posting restriction type.
        Returns:
            bool: True if posting is allowed, False otherwise.
       """
        return is_posting_allowed(
            posting_restriction,
            self.course.get_discussion_blackout_datetimes()
        )

    @ddt.data(
        (PostingRestriction.DISABLED, True),
        (PostingRestriction.ENABLED, False),
        (PostingRestriction.SCHEDULED, False),
    )
    @ddt.unpack
    def test_blackout_dates(self, restriction, state):
        """
        Test is_posting_allowed function with the misc posting restriction
        """
        date_ranges = self._get_date_ranges()
        self._set_discussion_blackouts(date_ranges)

        posting_allowed = self._check_posting_allowed(restriction)
        self.assertEqual(state, posting_allowed)  # noqa: PT009

    def test_posting_scheduled_future(self):
        """
        Test posting when the posting restriction is scheduled in the future.
        Assertion:
            Posting should be allowed.
        """
        now = datetime.now(UTC)
        date_ranges = [
            (now + timedelta(days=6), now + timedelta(days=23)),
        ]
        self._set_discussion_blackouts(date_ranges)

        posting_allowed = self._check_posting_allowed(PostingRestriction.SCHEDULED)
        self.assertTrue(posting_allowed)  # noqa: PT009
