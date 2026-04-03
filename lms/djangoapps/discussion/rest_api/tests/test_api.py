"""
Tests for Discussion API internal interface
"""

from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import urlencode, urlunparse

import ddt
import httpretty
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from pytz import UTC

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.partitions.partitions import Group, UserPartition

from common.djangoapps.student.tests.factories import (
    BetaTesterFactory,
    CourseEnrollmentFactory,
    StaffFactory,
    UserFactory
)
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.discussion.django_comment_client.tests.utils import ForumsEnableMixin
from lms.djangoapps.discussion.rest_api.api import (
    create_comment,
    create_thread,
    get_course,
    get_course_topics,
    get_user_comments,
)
from lms.djangoapps.discussion.rest_api.exceptions import (
    DiscussionDisabledError,
)
from rest_framework.exceptions import PermissionDenied
from lms.djangoapps.discussion.rest_api.tests.utils import (
    CommentsServiceMockMixin,
    make_minimal_cs_comment,
)
from openedx.core.djangoapps.course_groups.models import CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role
)
from openedx.core.djangoapps.django_comment_common.comment_client.utils import (
    CommentClient500Error,
    CommentClientRequestError,
)
from openedx.core.lib.exceptions import CourseNotFoundError, PageNotFoundError

User = get_user_model()


def _remove_discussion_tab(course, user_id):
    """
    Remove the discussion tab for the course.

    user_id is passed to the modulestore as the editor of the xblock.
    """
    course.tabs = [tab for tab in course.tabs if not tab.type == 'discussion']
    modulestore().update_item(course, user_id)


def _discussion_disabled_course_for(user):
    """
    Create and return a course with discussions disabled.

    The user passed in will be enrolled in the course.
    """
    course_with_disabled_forums = CourseFactory.create()
    CourseEnrollmentFactory.create(user=user, course_id=course_with_disabled_forums.id)
    _remove_discussion_tab(course_with_disabled_forums, user.id)

    return course_with_disabled_forums


def _assign_role_to_user(user, course_id, role):
    """
    Unset the blackout period for course discussions.

    Arguments:
            user: User to assign role to
            course_id: Course id of the course user will be assigned role in
            role: Role assigned to user for course
    """
    role = Role.objects.create(name=role, course_id=course_id)
    role.users.set([user])


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_settings(DISCUSSION_MODERATION_EDIT_REASON_CODES={"test-edit-reason": "Test Edit Reason"})
@override_settings(DISCUSSION_MODERATION_CLOSE_REASON_CODES={"test-close-reason": "Test Close Reason"})
@ddt.ddt
class GetCourseTest(ForumsEnableMixin, UrlResetMixin, SharedModuleStoreTestCase):
    """Test for get_course"""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org="x", course="y", run="z")

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            get_course(self.request, CourseLocator.from_string("course-v1:non+existent+course"))

    def test_not_enrolled(self):
        unenrolled_user = UserFactory.create()
        self.request.user = unenrolled_user
        with pytest.raises(CourseNotFoundError):
            get_course(self.request, self.course.id)

    def test_discussions_disabled(self):
        with pytest.raises(DiscussionDisabledError):
            get_course(self.request, _discussion_disabled_course_for(self.user).id)

    def test_discussions_disabled_v2(self):
        data = get_course(self.request, _discussion_disabled_course_for(self.user).id, False)
        assert data['show_discussions'] is False

    def test_basic(self):
        assert get_course(self.request, self.course.id) == {
            'id': str(self.course.id),
            'is_posting_enabled': True,
            'is_user_banned': False,
            'blackouts': [],
            'thread_list_url': 'http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz',
            'following_thread_list_url':
                'http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=True',
            'topics_url': 'http://testserver/api/discussion/v1/course_topics/course-v1:x+y+z',
            'allow_anonymous': True,
            'allow_anonymous_to_peers': False,
            'enable_in_context': True,
            'group_at_subsection': False,
            'provider': 'legacy',
            "has_bulk_delete_privileges": False,
            'has_moderation_privileges': False,
            "is_course_staff": False,
            "is_course_admin": False,
            'is_group_ta': False,
            'is_user_admin': False,
            'user_roles': {'Student'},
            'edit_reasons': [{'code': 'test-edit-reason', 'label': 'Test Edit Reason'}],
            'post_close_reasons': [{'code': 'test-close-reason', 'label': 'Test Close Reason'}],
            'show_discussions': True,
            'is_notify_all_learners_enabled': False,
            'captcha_settings': {
                'enabled': False,
                'site_key': None,
            },
            "is_email_verified": True,
            "only_verified_users_can_post": False,
            "content_creation_rate_limited": False,
            "enable_discussion_ban": False,
        }

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
    )
    def test_privileged_roles(self, role):
        """
        Test that the api returns the correct roles and privileges.
        """
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role)
        course_meta = get_course(self.request, self.course.id)
        assert course_meta["has_moderation_privileges"]
        assert course_meta["user_roles"] == {FORUM_ROLE_STUDENT} | {role}


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetCourseTestBlackouts(ForumsEnableMixin, UrlResetMixin, ModuleStoreTestCase):
    """
    Tests of get_course for courses that have blackout dates.
    """
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org="x", course="y", run="z")
        self.user = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    def test_blackout(self):
        # A variety of formats is accepted
        self.course.discussion_blackouts = [
            ["2015-06-09T00:00:00Z", "6-10-15"],
            [1433980800000, datetime(2015, 6, 12, tzinfo=UTC)],
        ]
        self.update_course(self.course, self.user.id)
        result = get_course(self.request, self.course.id)
        assert result['blackouts'] == [
            {'start': '2015-06-09T00:00:00Z', 'end': '2015-06-10T00:00:00Z'},
            {'start': '2015-06-11T00:00:00Z', 'end': '2015-06-12T00:00:00Z'}
        ]

    @ddt.data(None, "not a datetime", "2015", [])
    def test_blackout_errors(self, bad_value):
        self.course.discussion_blackouts = [
            [bad_value, "2015-06-09T00:00:00Z"],
            ["2015-06-10T00:00:00Z", "2015-06-11T00:00:00Z"],
        ]
        modulestore().update_item(self.course, self.user.id)
        result = get_course(self.request, self.course.id)
        assert result['blackouts'] == []


@mock.patch.dict("django.conf.settings.FEATURES", {"DISABLE_START_DATES": False})
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetCourseTopicsTest(CommentsServiceMockMixin, ForumsEnableMixin, UrlResetMixin, ModuleStoreTestCase):
    """Test for get_course_topics"""
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        super().setUp()
        self.maxDiff = None  # pylint: disable=invalid-name
        self.partition = UserPartition(
            0,
            "partition",
            "Test Partition",
            [Group(0, "Cohort A"), Group(1, "Cohort B")],
            scheme_id="cohort"
        )
        self.course = CourseFactory.create(
            org="x",
            course="y",
            run="z",
            start=datetime.now(UTC),
            discussion_topics={"Test Topic": {"id": "non-courseware-topic-id"}},
            user_partitions=[self.partition],
            cohort_config={"cohorted": True},
            days_early_for_beta=3
        )
        self.user = UserFactory.create()
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.thread_counts_map = {
            "courseware-1": {"discussion": 2, "question": 3},
            "courseware-2": {"discussion": 4, "question": 5},
            "courseware-3": {"discussion": 7, "question": 2},
        }
        self.register_get_course_commentable_counts_response(self.course.id, self.thread_counts_map)

    def make_discussion_xblock(self, topic_id, category, subcategory, **kwargs):
        """
        Build a discussion xblock in self.course.
        """
        BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id=topic_id,
            discussion_category=category,
            discussion_target=subcategory,
            **kwargs
        )

    def get_thread_list_url(self, topic_id_list):
        """
        Returns the URL for the thread_list_url field, given a list of topic_ids
        """
        path = "http://testserver/api/discussion/v1/threads/"
        topic_ids_to_query = [("topic_id", topic_id) for topic_id in topic_id_list]
        query_list = [("course_id", str(self.course.id))] + topic_ids_to_query
        return urlunparse(("", "", path, "", urlencode(query_list), ""))

    def get_course_topics(self):
        """
        Get course topics for self.course, using the given user or self.user if
        not provided, and generating absolute URIs with a test scheme/host.
        """
        return get_course_topics(self.request, self.course.id)

    def make_expected_tree(self, topic_id, name, children=None):
        """
        Build an expected result tree given a topic id, display name, and
        children
        """
        topic_id_list = [topic_id] if topic_id else [child["id"] for child in children]
        children = children or []
        thread_counts = self.thread_counts_map.get(topic_id, {"discussion": 0, "question": 0})
        node = {
            "id": topic_id,
            "name": name,
            "children": children,
            "thread_list_url": self.get_thread_list_url(topic_id_list),
            "thread_counts": thread_counts if not children else None
        }

        return node

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            get_course_topics(self.request, CourseLocator.from_string("course-v1:non+existent+course"))

    def test_not_enrolled(self):
        unenrolled_user = UserFactory.create()
        self.request.user = unenrolled_user
        with pytest.raises(CourseNotFoundError):
            self.get_course_topics()

    def test_discussions_disabled(self):
        _remove_discussion_tab(self.course, self.user.id)
        with pytest.raises(DiscussionDisabledError):
            self.get_course_topics()

    def test_without_courseware(self):
        actual = self.get_course_topics()
        expected = {
            "courseware_topics": [],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic")
            ],
        }
        assert actual == expected

    def test_with_courseware(self):
        self.make_discussion_xblock("courseware-topic-id", "Foo", "Bar")
        actual = self.get_course_topics()
        expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "Foo",
                    [self.make_expected_tree("courseware-topic-id", "Bar")]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic")
            ],
        }
        assert actual == expected

    def test_many(self):
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.course.discussion_topics = {
                "A": {"id": "non-courseware-1"},
                "B": {"id": "non-courseware-2"},
            }
            self.store.update_item(self.course, self.user.id)
            self.make_discussion_xblock("courseware-1", "Week 1", "1")
            self.make_discussion_xblock("courseware-2", "Week 1", "2")
            self.make_discussion_xblock("courseware-3", "Week 10", "1")
            self.make_discussion_xblock("courseware-4", "Week 10", "2")
            self.make_discussion_xblock("courseware-5", "Week 9", "1")
        actual = self.get_course_topics()
        expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "Week 1",
                    [
                        self.make_expected_tree("courseware-1", "1"),
                        self.make_expected_tree("courseware-2", "2"),
                    ]
                ),
                self.make_expected_tree(
                    None,
                    "Week 9",
                    [self.make_expected_tree("courseware-5", "1")]
                ),
                self.make_expected_tree(
                    None,
                    "Week 10",
                    [
                        self.make_expected_tree("courseware-3", "1"),
                        self.make_expected_tree("courseware-4", "2"),
                    ]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-1", "A"),
                self.make_expected_tree("non-courseware-2", "B"),
            ],
        }
        assert actual == expected

    def test_sort_key_doesnot_work(self):
        """
        Test to check that providing sort_key doesn't change the sort order
        """
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.course.discussion_topics = {
                "W": {"id": "non-courseware-1", "sort_key": "Z"},
                "X": {"id": "non-courseware-2"},
                "Y": {"id": "non-courseware-3", "sort_key": "Y"},
                "Z": {"id": "non-courseware-4", "sort_key": "W"},
            }
            self.store.update_item(self.course, self.user.id)
            self.make_discussion_xblock("courseware-1", "First", "A", sort_key="B")
            self.make_discussion_xblock("courseware-2", "First", "B", sort_key="D")
            self.make_discussion_xblock("courseware-3", "First", "C", sort_key="E")
            self.make_discussion_xblock("courseware-4", "Second", "A", sort_key="A")
            self.make_discussion_xblock("courseware-5", "Second", "B", sort_key="B")
            self.make_discussion_xblock("courseware-6", "Second", "C")
            self.make_discussion_xblock("courseware-7", "Second", "D", sort_key="D")

        actual = self.get_course_topics()
        expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "First",
                    [
                        self.make_expected_tree("courseware-1", "A"),
                        self.make_expected_tree("courseware-2", "B"),
                        self.make_expected_tree("courseware-3", "C"),
                    ]
                ),
                self.make_expected_tree(
                    None,
                    "Second",
                    [
                        self.make_expected_tree("courseware-4", "A"),
                        self.make_expected_tree("courseware-5", "B"),
                        self.make_expected_tree("courseware-6", "C"),
                        self.make_expected_tree("courseware-7", "D"),
                    ]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-1", "W"),
                self.make_expected_tree("non-courseware-2", "X"),
                self.make_expected_tree("non-courseware-3", "Y"),
                self.make_expected_tree("non-courseware-4", "Z"),
            ],
        }
        assert actual == expected

    def test_access_control(self):
        """
        Test that only topics that a user has access to are returned. The
        ways in which a user may not have access are:

        * Block is visible to staff only
        * Block is accessible only to a group the user is not in

        Also, there is a case that ensures that a category with no accessible
        subcategories does not appear in the result.
        """
        beta_tester = BetaTesterFactory.create(course_key=self.course.id)
        CourseEnrollmentFactory.create(user=beta_tester, course_id=self.course.id)
        staff = StaffFactory.create(course_key=self.course.id)
        for user, group_idx in [(self.user, 0), (beta_tester, 1)]:
            cohort = CohortFactory.create(
                course_id=self.course.id,
                name=self.partition.groups[group_idx].name,
                users=[user]
            )
            CourseUserGroupPartitionGroup.objects.create(
                course_user_group=cohort,
                partition_id=self.partition.id,
                group_id=self.partition.groups[group_idx].id
            )

        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.store.update_item(self.course, self.user.id)
            self.make_discussion_xblock(
                "courseware-2",
                "First",
                "Cohort A",
                group_access={self.partition.id: [self.partition.groups[0].id]}
            )
            self.make_discussion_xblock(
                "courseware-3",
                "First",
                "Cohort B",
                group_access={self.partition.id: [self.partition.groups[1].id]}
            )
            self.make_discussion_xblock("courseware-1", "First", "Everybody")
            self.make_discussion_xblock(
                "courseware-5",
                "Second",
                "Future Start Date",
                start=datetime.now(UTC) + timedelta(days=1)
            )
            self.make_discussion_xblock("courseware-4", "Second", "Staff Only", visible_to_staff_only=True)

        student_actual = self.get_course_topics()
        student_expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "First",
                    [
                        self.make_expected_tree("courseware-2", "Cohort A"),
                        self.make_expected_tree("courseware-1", "Everybody"),
                    ]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic"),
            ],
        }
        assert student_actual == student_expected
        self.request.user = beta_tester
        beta_actual = self.get_course_topics()
        beta_expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "First",
                    [
                        self.make_expected_tree("courseware-3", "Cohort B"),
                        self.make_expected_tree("courseware-1", "Everybody"),
                    ]
                )
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic"),
            ],
        }
        assert beta_actual == beta_expected

        self.request.user = staff
        staff_actual = self.get_course_topics()
        staff_expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "First",
                    [
                        self.make_expected_tree("courseware-2", "Cohort A"),
                        self.make_expected_tree("courseware-3", "Cohort B"),
                        self.make_expected_tree("courseware-1", "Everybody"),
                    ]
                ),
                self.make_expected_tree(
                    None,
                    "Second",
                    [
                        self.make_expected_tree("courseware-4", "Staff Only"),
                    ]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic"),
            ],
        }
        assert staff_actual == staff_expected

    def test_un_released_discussion_topic(self):
        """
        Test discussion topics that have not yet started
        """
        staff = StaffFactory.create(course_key=self.course.id)
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.store.update_item(self.course, self.user.id)
            self.make_discussion_xblock(
                "courseware-2",
                "First",
                "Released",
                start=datetime.now(UTC) - timedelta(days=1)
            )
            self.make_discussion_xblock(
                "courseware-3",
                "First",
                "Future release",
                start=datetime.now(UTC) + timedelta(days=1)
            )

        self.request.user = staff
        staff_actual = self.get_course_topics()
        staff_expected = {
            "courseware_topics": [
                self.make_expected_tree(
                    None,
                    "First",
                    [
                        self.make_expected_tree("courseware-2", "Released"),
                    ]
                ),
            ],
            "non_courseware_topics": [
                self.make_expected_tree("non-courseware-topic-id", "Test Topic"),
            ],
        }
        assert staff_actual == staff_expected

    def test_discussion_topic(self):
        """
        Tests discussion topic details against a requested topic id
        """
        topic_id_1 = "topic_id_1"
        topic_id_2 = "topic_id_2"
        self.make_discussion_xblock(topic_id_1, "test_category_1", "test_target_1")
        self.make_discussion_xblock(topic_id_2, "test_category_2", "test_target_2")
        actual = get_course_topics(self.request, self.course.id, {"topic_id_1", "topic_id_2"})
        assert actual == {
            'non_courseware_topics': [],
            'courseware_topics': [
                {
                    'children': [
                        {
                            'children': [],
                            'id': 'topic_id_1',
                            'thread_list_url': 'http://testserver/api/discussion/v1/threads/'
                                               '?course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_1',
                            'name': 'test_target_1',
                            'thread_counts': {'discussion': 0, 'question': 0},
                        },
                    ],
                    'id': None,
                    'thread_list_url': 'http://testserver/api/discussion/v1/threads/'
                                       '?course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_1',
                    'name': 'test_category_1',
                    'thread_counts': None,
                },
                {
                    'children': [
                        {
                            'children': [],
                            'id': 'topic_id_2',
                            'thread_list_url': 'http://testserver/api/discussion/v1/threads/'
                                               '?course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_2',
                            'name': 'test_target_2',
                            'thread_counts': {'discussion': 0, 'question': 0},
                        }
                    ],
                    'id': None,
                    'thread_list_url': 'http://testserver/api/discussion/v1/threads/'
                                       '?course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_2',
                    'name': 'test_category_2',
                    'thread_counts': None,
                }
            ]
        }


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetUserCommentsTest(ForumsEnableMixin, CommentsServiceMockMixin, SharedModuleStoreTestCase):
    """
    Tests for get_user_comments.
    """

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()

        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)

        self.course = CourseFactory.create()

        # create staff user so that we don't need to worry about
        # permissions here
        self.user = UserFactory.create(is_staff=True)
        self.register_get_user_response(self.user)

        self.request = RequestFactory().get(f'/api/discussion/v1/users/{self.user.username}/{self.course.id}')
        self.request.user = self.user

    def test_call_with_single_results_page(self):
        """
        Assert that a minimal call with valid inputs, and single result,
        returns the expected response structure.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
        )
        assert "results" in response.data
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 1
        assert response.data["pagination"]["num_pages"] == 1
        assert response.data["pagination"]["next"] is None
        assert response.data["pagination"]["previous"] is None

    @ddt.data(1, 2, 3)
    def test_call_with_paginated_results(self, page):
        """
        Assert that paginated results return the correct pagination
        information at the pagination boundaries.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment() for _ in range(30)],
            page=page,
            num_pages=3,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
            page=page,
        )
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 30
        assert response.data["pagination"]["num_pages"] == 3

        if page in (1, 2):
            assert response.data["pagination"]["next"] is not None
            assert f"page={page + 1}" in response.data["pagination"]["next"]
        if page in (2, 3):
            assert response.data["pagination"]["previous"] is not None
            assert f"page={page - 1}" in response.data["pagination"]["previous"]
        if page == 1:
            assert response.data["pagination"]["previous"] is None
        if page == 3:
            assert response.data["pagination"]["next"] is None

    def test_call_with_invalid_page(self):
        """
        Assert that calls for pages that exceed the existing number of
        results pages raise PageNotFoundError.
        """
        self.register_get_comments_response([], page=2, num_pages=1)
        with pytest.raises(PageNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=self.course.id,
                page=2,
            )

    def test_call_with_non_existent_course(self):
        """
        Assert that calls for comments in a course that doesn't exist
        result in a CourseNotFoundError error.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        with pytest.raises(CourseNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=CourseKey.from_string("course-v1:x+y+z"),
                page=2,
            )


def test_create_thread_denies_banned_user():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock()

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_course",
        return_value=mock.Mock(),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.get_context",
        return_value={},
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_thread_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        return_value=True,
        create=True,
    ):
        with pytest.raises(PermissionDenied, match="You are banned from posting"):
            create_thread(request, {"course_id": "course-v1:x+y+z"})


def test_create_comment_denies_banned_user():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock()
    course = mock.Mock()
    course.id = CourseKey.from_string("course-v1:x+y+z")

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_thread_and_context",
        return_value=({"closed": False}, {"course": course}),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_comment_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        return_value=True,
        create=True,
    ):
        with pytest.raises(PermissionDenied, match="You are banned from posting"):
            create_comment(request, {"thread_id": "test_thread"})


def test_create_thread_ban_check_backend_error_fails_open():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock(id=123)

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_course",
        return_value=mock.Mock(),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.get_context",
        return_value={},
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_thread_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        side_effect=CommentClientRequestError("temporary backend failure"),
        create=True,
    ), mock.patch("lms.djangoapps.discussion.rest_api.api.log.warning") as warning_log:
        with pytest.raises(ValidationError):
            create_thread(request, {"course_id": "course-v1:x+y+z"})

    warning_log.assert_called_once()


def test_create_comment_ban_check_backend_error_fails_open():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock(id=123)
    course = mock.Mock()
    course.id = CourseKey.from_string("course-v1:x+y+z")

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_thread_and_context",
        return_value=({"closed": False}, {"course": course}),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_comment_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        side_effect=CommentClient500Error("temporary backend failure"),
        create=True,
    ), mock.patch("lms.djangoapps.discussion.rest_api.api.log.warning") as warning_log:
        with pytest.raises(ValidationError):
            create_comment(request, {"thread_id": "test_thread"})

    warning_log.assert_called_once()
