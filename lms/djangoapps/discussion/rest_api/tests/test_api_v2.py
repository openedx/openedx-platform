# pylint: disable=unused-import
"""
Tests for the internal interface of the Discussion API (rest_api/api.py).

This module directly tests the internal API functions of the Discussion API, such as create_thread,
create_comment, update_thread, update_comment, and related helpers, by invoking them with various data and
request objects.
"""

import itertools
import random
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse  # noqa: F401

import ddt
import httpretty
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey  # noqa: F401
from opaque_keys.edx.locator import CourseLocator
from pytz import UTC
from rest_framework.exceptions import PermissionDenied

from common.djangoapps.student.tests.factories import (
    AdminFactory,
    BetaTesterFactory,
    CourseEnrollmentFactory,
    StaffFactory,
    UserFactory,
)
from common.djangoapps.util.testing import UrlResetMixin
from common.test.utils import MockSignalHandlerMixin, disable_signal
from lms.djangoapps.discussion.rest_api import api
from lms.djangoapps.discussion.rest_api.api import (
    create_comment,
    create_thread,
    delete_comment,
    delete_thread,
    get_comment_list,
    get_course,
    get_course_topics,
    get_course_topics_v2,
    get_thread,
    get_thread_list,
    get_user_comments,  # noqa: F401
    update_comment,
    update_thread,
)
from lms.djangoapps.discussion.rest_api.exceptions import (
    CommentNotFoundError,
    DiscussionBlackOutException,
    DiscussionDisabledError,
    ThreadNotFoundError,
)
from lms.djangoapps.discussion.rest_api.serializers import TopicOrdering
from lms.djangoapps.discussion.rest_api.tests.utils import (
    CommentsServiceMockMixin,  # noqa: F401
    ForumMockUtilsMixin,
    make_paginated_api_response,
    parsed_body,  # noqa: F401
)
from lms.djangoapps.discussion.tests.utils import make_minimal_cs_comment, make_minimal_cs_thread
from openedx.core.djangoapps.course_groups.models import CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.discussions.models import (
    DiscussionsConfiguration,
    DiscussionTopicLink,
    PostingRestriction,
    Provider,
)
from openedx.core.djangoapps.discussions.tasks import update_discussions_settings_from_course_task
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role,
)
from openedx.core.lib.exceptions import CourseNotFoundError, PageNotFoundError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.partitions.partitions import Group, UserPartition

User = get_user_model()


def _remove_discussion_tab(course, user_id):
    """
    Remove the discussion tab for the course.

    user_id is passed to the modulestore as the editor of the xblock.
    """
    course.tabs = [tab for tab in course.tabs if not tab.type == "discussion"]
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
    Assign a discussion role to a user for a given course.

    Arguments:
            user: User to assign role to
            course_id: Course id of the course user will be assigned role in
            role: Role assigned to user for course
    """
    role = Role.objects.create(name=role, course_id=course_id)
    role.users.set([user])


def _create_course_and_cohort_with_user_role(course_is_cohorted, user, role_name):
    """
    Creates a course with the value of `course_is_cohorted`, plus `always_cohort_inline_discussions`
    set to True (which is no longer the default value). Then 1) enrolls the user in that course,
    2) creates a cohort that the user is placed in, and 3) adds the user to the given role.

    Returns: a tuple of the created course and the created cohort
    """
    cohort_course = CourseFactory.create(
        cohort_config={
            "cohorted": course_is_cohorted,
            "always_cohort_inline_discussions": True,
        }
    )
    CourseEnrollmentFactory.create(user=user, course_id=cohort_course.id)
    cohort = CohortFactory.create(course_id=cohort_course.id, users=[user])
    _assign_role_to_user(user=user, course_id=cohort_course.id, role=role_name)

    return [cohort_course, cohort]


def _set_course_discussion_blackout(course, user_id):
    """
    Set the blackout period for course discussions.

    Arguments:
            course: Course for which blackout period is set
            user_id: User id of user enrolled in the course
    """
    course.discussion_blackouts = [
        datetime.now(UTC) - timedelta(days=3),
        datetime.now(UTC) + timedelta(days=3),
    ]
    configuration = DiscussionsConfiguration.get(course.id)
    configuration.posting_restrictions = PostingRestriction.SCHEDULED
    configuration.save()
    modulestore().update_item(course, user_id)


@ddt.ddt
@disable_signal(api, "thread_created")
@disable_signal(api, "thread_voted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CreateThreadTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for create_thread"""

    LONG_TITLE = (
        "Lorem ipsum dolor sit amet, consectetuer adipiscing elit. "
        "Aenean commodo ligula eget dolor. Aenean massa. Cum sociis "
        "natoque penatibus et magnis dis parturient montes, nascetur "
        "ridiculus mus. Donec quam felis, ultricies nec, "
        "pellentesque eu, pretium quis, sem. Nulla consequat massa "
        "quis enim. Donec pede justo, fringilla vel, aliquet nec, "
        "vulputate eget, arcu. In enim justo, rhoncus ut, imperdiet "
        "a, venenatis vitae, justo. Nullam dictum felis eu pede "
        "mollis pretium. Integer tincidunt. Cras dapibus. Vivamus "
        "elementum semper nisi. Aenean vulputate eleifend tellus. "
        "Aenean leo ligula, porttitor eu, consequat vitae, eleifend "
        "ac, enim. Aliquam lorem ante, dapibus in, viverra quis, "
        "feugiat a, tellus. Phasellus viverra nulla ut metus varius "
        "laoreet. Quisque rutrum. Aenean imperdiet. Etiam ultricies "
        "nisi vel augue. Curabitur ullamcorper ultricies nisi. Nam "
        "eget dui. Etiam rhoncus. Maecenas tempus, tellus eget "
        "condimentum rhoncus, sem quam semper libero, sit amet "
        "adipiscing sem neque sed ipsum. Nam quam nunc, blandit vel, "
        "luctus pulvinar, hendrerit id, lorem. Maecenas nec odio et "
        "ante tincidunt tempus. Donec vitae sapien ut libero "
        "venenatis faucibus. Nullam quis ante. Etiam sit amet orci "
        "eget eros faucibus tincidunt. Duis leo. Sed fringilla "
        "mauris sit amet nibh. Donec sodales sagittis magna. Sed "
        "consequat, leo eget bibendum sodales, augue velit cursus "
        "nunc, quis gravida magna mi a libero. Fusce vulputate "
        "eleifend sapien. Vestibulum purus quam, scelerisque ut, "
        "mollis sed, nonummy id, metus. Nullam accumsan lorem in "
        "dui. Cras ultricies mi eu turpis hendrerit fringilla. "
        "Vestibulum ante ipsum primis in faucibus orci luctus et "
        "ultrices posuere cubilia Curae; In ac dui quis mi "
        "consectetuer lacinia. Nam pretium turpis et arcu. Duis arcu "
        "tortor, suscipit eget, imperdiet nec, imperdiet iaculis, "
        "ipsum. Sed aliquam ultrices mauris. Integer ante arcu, "
        "accumsan a, consectetuer eget, posuere ut, mauris. Praesent "
        "adipiscing. Phasellus ullamcorper ipsum rutrum nunc. Nunc "
        "nonummy metus."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.minimal_data = {
            "course_id": str(self.course.id),
            "topic_id": "test_topic",
            "type": "discussion",
            "title": "Test Title",
            "raw_body": "Test body",
        }

    @mock.patch("eventtracking.tracker.emit")
    def test_basic(self, mock_emit):
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_id",
                "username": self.user.username,
                "read": True,
            }
        )
        self.register_post_thread_response(cs_thread)
        with self.assert_signal_sent(
            api, "thread_created", sender=None, user=self.user, exclude_args=("post", "notify_all_learners")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = create_thread(self.request, self.minimal_data)
        expected = self.expected_thread_data(
            {
                "id": "test_id",
                "course_id": str(self.course.id),
                "comment_list_url": "http://testserver/api/discussion/v1/comments/?thread_id=test_id",
                "read": True,
            }
        )
        assert actual == expected
        params = {
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "thread_type": "discussion",
            "title": "Test Title",
            "body": "Test body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
        }

        self.check_mock_called_with("create_thread", -1, **params)

        event_name, event_data = mock_emit.call_args[0]
        assert event_name == "edx.forum.thread.created"
        assert event_data == {
            "commentable_id": "test_topic",
            "group_id": None,
            "thread_type": "discussion",
            "title": "Test Title",
            "title_truncated": False,
            "anonymous": False,
            "anonymous_to_peers": False,
            "options": {"followed": False, "notify_all_learners": False},
            "id": "test_id",
            "truncated": False,
            "body": "Test body",
            "url": "",
            "user_forums_roles": [FORUM_ROLE_STUDENT],
            "user_course_roles": [],
            "from_mfe_sidebar": False,
        }

    def test_basic_in_blackout_period(self):
        """
        Test case when course is in blackout period and user does not have special privileges.
        """
        _set_course_discussion_blackout(course=self.course, user_id=self.user.id)

        with self.assertRaises(DiscussionBlackOutException) as assertion:  # noqa: PT027
            create_thread(self.request, self.minimal_data)
        self.assertEqual(assertion.exception.status_code, 403)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            assertion.exception.detail, "Discussions are in blackout period."
        )

    @mock.patch("eventtracking.tracker.emit")
    def test_basic_in_blackout_period_with_user_access(self, mock_emit):
        """
        Test case when course is in blackout period and user has special privileges.
        """
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_id",
                "username": self.user.username,
                "read": True,
            }
        )
        self.register_post_thread_response(cs_thread)

        _set_course_discussion_blackout(course=self.course, user_id=self.user.id)

        _assign_role_to_user(
            user=self.user, course_id=self.course.id, role=FORUM_ROLE_MODERATOR
        )

        with self.assert_signal_sent(
            api, "thread_created", sender=None, user=self.user, exclude_args=("post", "notify_all_learners")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = create_thread(self.request, self.minimal_data)
        expected = self.expected_thread_data(
            {
                "author_label": "Moderator",
                "id": "test_id",
                "course_id": str(self.course.id),
                "comment_list_url": "http://testserver/api/discussion/v1/comments/?thread_id=test_id",
                "read": True,
                "editable_fields": [
                    "abuse_flagged",
                    "anonymous",
                    "close_reason_code",
                    "closed",
                    "copy_link",
                    "following",
                    "pinned",
                    "raw_body",
                    "read",
                    "title",
                    "topic_id",
                    "type",
                    "voted",
                ],
            }
        )
        assert actual == expected
        params = {
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "thread_type": "discussion",
            "title": "Test Title",
            "body": "Test body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
        }
        self.check_mock_called_with("create_thread", -1, **params)
        event_name, event_data = mock_emit.call_args[0]
        self.assertEqual(event_name, "edx.forum.thread.created")  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            event_data,
            {
                "commentable_id": "test_topic",
                "group_id": None,
                "thread_type": "discussion",
                "title": "Test Title",
                "title_truncated": False,
                "anonymous": False,
                "anonymous_to_peers": False,
                "options": {"followed": False, "notify_all_learners": False},
                "id": "test_id",
                "truncated": False,
                "body": "Test body",
                "url": "",
                "user_forums_roles": [FORUM_ROLE_STUDENT, FORUM_ROLE_MODERATOR],
                "user_course_roles": [],
                "from_mfe_sidebar": False,
            },
        )

    @mock.patch("eventtracking.tracker.emit")
    def test_title_truncation(self, mock_emit):
        data = self.minimal_data.copy()
        data["title"] = self.LONG_TITLE

        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_id",
                "username": self.user.username,
                "read": True,
            }
        )
        self.register_post_thread_response(cs_thread)
        with self.assert_signal_sent(
            api, "thread_created", sender=None, user=self.user, exclude_args=("post", "notify_all_learners")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                create_thread(self.request, data)
        event_name, event_data = mock_emit.call_args[0]
        assert event_name == "edx.forum.thread.created"
        assert event_data == {
            "commentable_id": "test_topic",
            "group_id": None,
            "thread_type": "discussion",
            "title": self.LONG_TITLE[:1000],
            "title_truncated": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "options": {"followed": False, "notify_all_learners": False},
            "id": "test_id",
            "truncated": False,
            "body": "Test body",
            "url": "",
            "user_forums_roles": [FORUM_ROLE_STUDENT],
            "user_course_roles": [],
            "from_mfe_sidebar": False,
        }

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            [True, False],
            ["no_group_set", "group_is_none", "group_is_set"],
        )
    )
    @ddt.unpack
    def test_group_id(
        self, role_name, course_is_cohorted, topic_is_cohorted, data_group_state
    ):
        """
        Tests whether the user has permission to create a thread with certain
        group_id values.

        If there is no group, user cannot create a thread.
        Else if group is None or set, and the course is not cohorted and/or the
        role is a student, user can create a thread.
        """

        cohort_course = CourseFactory.create(
            discussion_topics={"Test Topic": {"id": "test_topic"}},
            cohort_config={
                "cohorted": course_is_cohorted,
                "cohorted_discussions": ["test_topic"] if topic_is_cohorted else [],
            },
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=cohort_course.id)
        if course_is_cohorted:
            cohort = CohortFactory.create(course_id=cohort_course.id, users=[self.user])
        _assign_role_to_user(user=self.user, course_id=cohort_course.id, role=role_name)
        self.register_post_thread_response({"username": self.user.username})
        data = self.minimal_data.copy()
        data["course_id"] = str(cohort_course.id)
        if data_group_state == "group_is_none":
            data["group_id"] = None
        elif data_group_state == "group_is_set":
            if course_is_cohorted:
                data["group_id"] = cohort.id + 1
            else:
                data["group_id"] = 1  # Set to any value since there is no cohort
        expected_error = data_group_state in ["group_is_none", "group_is_set"] and (
            not course_is_cohorted or role_name == FORUM_ROLE_STUDENT
        )
        try:
            create_thread(self.request, data)
            assert not expected_error
            actual_post_data = self.get_mock_func_calls("create_thread")[-1][1]
            if data_group_state == "group_is_set":
                assert actual_post_data["group_id"] == data["group_id"]
            elif (
                data_group_state == "no_group_set"
                and course_is_cohorted
                and topic_is_cohorted
            ):
                assert actual_post_data["group_id"] == cohort.id
            else:
                assert "group_id" not in actual_post_data
        except ValidationError as ex:
            if not expected_error:
                self.fail(f"Unexpected validation error: {ex}")

    def test_course_id_missing(self):
        with pytest.raises(ValidationError) as assertion:
            create_thread(self.request, {})
        assert assertion.value.message_dict == {
            "course_id": ["This field is required."]
        }

    def test_course_id_invalid(self):
        with pytest.raises(ValidationError) as assertion:
            create_thread(self.request, {"course_id": "invalid!"})
        assert assertion.value.message_dict == {"course_id": ["Invalid value."]}

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            create_thread(self.request, {"course_id": "course-v1:non+existent+course"})

    def test_not_enrolled(self):
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            create_thread(self.request, self.minimal_data)

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        self.minimal_data["course_id"] = str(disabled_course.id)
        with pytest.raises(DiscussionDisabledError):
            create_thread(self.request, self.minimal_data)

    def test_invalid_field(self):
        data = self.minimal_data.copy()
        data["type"] = "invalid_type"
        with pytest.raises(ValidationError):
            create_thread(self.request, data)

    def test_abuse_flagged(self):
        self.register_post_thread_response(
            {"id": "test_id", "username": self.user.username}
        )
        self.register_thread_flag_response("test_id")
        data = self.minimal_data.copy()
        data["abuse_flagged"] = "True"
        result = create_thread(self.request, data)
        assert result["abuse_flagged"] is True

        self.check_mock_called("update_thread_flag")
        params = {
            "thread_id": "test_id",
            "action": "flag",
            "user_id": "1",
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("update_thread_flag", -1, **params)

    def test_following(self):
        self.register_post_thread_response(
            {"id": "test_id", "username": self.user.username}
        )
        self.register_subscription_response(self.user)
        data = self.minimal_data.copy()
        data["following"] = "True"
        result = create_thread(self.request, data)
        assert result["following"] is True
        self.check_mock_called("create_subscription")

        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "source_id": "test_id",
        }
        self.check_mock_called_with("create_subscription", 0, **params)


@ddt.ddt
@disable_signal(api, "comment_created")
@disable_signal(api, "comment_voted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@mock.patch(
    "lms.djangoapps.discussion.signals.handlers.send_response_notifications",
    new=mock.Mock(),
)
class CreateCommentTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for create_comment"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(self.course.id),
                    "commentable_id": "test_topic",
                }
            )
        )
        self.minimal_data = {
            "thread_id": "test_thread",
            "raw_body": "Test body",
        }

        mock_response = {
            "collection": [],
            "page": 1,
            "num_pages": 1,
            "subscriptions_count": 1,
            "corrected_text": None,
        }
        self.register_get_subscriptions("cohort_thread", mock_response)
        self.register_get_subscriptions("test_thread", mock_response)

    @ddt.data(None, "test_parent")
    @mock.patch("eventtracking.tracker.emit")
    def test_success(self, parent_id, mock_emit):
        if parent_id:
            self.register_get_comment_response(
                {"id": parent_id, "thread_id": "test_thread"}
            )
        self.register_post_comment_response(
            {
                "id": "test_comment",
                "username": self.user.username,
                "created_at": "2015-05-27T00:00:00Z",
                "updated_at": "2015-05-27T00:00:00Z",
            },
            thread_id="test_thread",
            parent_id=parent_id,
        )
        data = self.minimal_data.copy()
        if parent_id:
            data["parent_id"] = parent_id
        with self.assert_signal_sent(
            api, "comment_created", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = create_comment(self.request, data)
        expected = {
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": parent_id,
            "author": self.user.username,
            "author_label": None,
            "created_at": "2015-05-27T00:00:00Z",
            "updated_at": "2015-05-27T00:00:00Z",
            "raw_body": "Test body",
            "rendered_body": "<p>Test body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
            "child_count": 0,
            "can_delete": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "last_edit": None,
            "edit_by_label": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        assert actual == expected

        params = {
            "course_id": str(self.course.id),
            "body": "Test body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
        }
        if parent_id:
            params["parent_comment_id"] = parent_id
            self.check_mock_called_with("create_child_comment", -1, **params)
        else:
            params["thread_id"] = "test_thread"
            self.check_mock_called_with("create_parent_comment", -1, **params)

        expected_event_name = (
            "edx.forum.comment.created" if parent_id else "edx.forum.response.created"
        )
        expected_event_data = {
            "discussion": {"id": "test_thread"},
            "commentable_id": "test_topic",
            "options": {"followed": False},
            "id": "test_comment",
            "truncated": False,
            "body": "Test body",
            "url": "",
            "user_forums_roles": [FORUM_ROLE_STUDENT],
            "user_course_roles": [],
            "from_mfe_sidebar": False,
        }
        if parent_id:
            expected_event_data["response"] = {"id": parent_id}
        actual_event_name, actual_event_data = mock_emit.call_args[0]
        assert actual_event_name == expected_event_name
        assert actual_event_data == expected_event_data

    @ddt.data(None, "test_parent")
    @mock.patch("eventtracking.tracker.emit")
    def test_success_in_black_out_with_user_access(self, parent_id, mock_emit):
        """
        Test case when course is in blackout period and user has special privileges.
        """
        if parent_id:
            self.register_get_comment_response(
                {"id": parent_id, "thread_id": "test_thread"}
            )
        self.register_post_comment_response(
            {
                "id": "test_comment",
                "username": self.user.username,
                "created_at": "2015-05-27T00:00:00Z",
                "updated_at": "2015-05-27T00:00:00Z",
            },
            thread_id="test_thread",
            parent_id=parent_id,
        )
        data = self.minimal_data.copy()
        editable_fields = ["abuse_flagged", "anonymous", "raw_body", "voted"]
        if parent_id:
            data["parent_id"] = parent_id
        else:
            editable_fields.insert(2, "endorsed")

        _set_course_discussion_blackout(course=self.course, user_id=self.user.id)
        _assign_role_to_user(
            user=self.user, course_id=self.course.id, role=FORUM_ROLE_MODERATOR
        )

        with self.assert_signal_sent(
            api, "comment_created", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = create_comment(self.request, data)
        expected = {
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": parent_id,
            "author": self.user.username,
            "author_label": "Moderator",
            "created_at": "2015-05-27T00:00:00Z",
            "updated_at": "2015-05-27T00:00:00Z",
            "raw_body": "Test body",
            "rendered_body": "<p>Test body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": False,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": editable_fields,
            "child_count": 0,
            "can_delete": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "last_edit": None,
            "edit_by_label": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        assert actual == expected

        params = {
            "course_id": str(self.course.id),
            "body": "Test body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
        }
        if parent_id:
            params["parent_comment_id"] = parent_id
            self.check_mock_called_with("create_child_comment", -1, **params)
        else:
            params["thread_id"] = "test_thread"
            self.check_mock_called_with("create_parent_comment", -1, **params)

        expected_event_name = (
            "edx.forum.comment.created" if parent_id else "edx.forum.response.created"
        )
        expected_event_data = {
            "discussion": {"id": "test_thread"},
            "commentable_id": "test_topic",
            "options": {"followed": False},
            "id": "test_comment",
            "truncated": False,
            "body": "Test body",
            "url": "",
            "user_forums_roles": [FORUM_ROLE_STUDENT, FORUM_ROLE_MODERATOR],
            "user_course_roles": [],
            "from_mfe_sidebar": False,
        }
        if parent_id:
            expected_event_data["response"] = {"id": parent_id}
        actual_event_name, actual_event_data = mock_emit.call_args[0]
        self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
        self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    def test_error_in_black_out(self):
        """
        Test case when course is in blackout period and user does not have special privileges.
        """
        _set_course_discussion_blackout(course=self.course, user_id=self.user.id)

        with self.assertRaises(DiscussionBlackOutException) as assertion:  # noqa: PT027
            create_comment(self.request, self.minimal_data)
        self.assertEqual(assertion.exception.status_code, 403)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            assertion.exception.detail, "Discussions are in blackout period."
        )

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["question", "discussion"],
        )
    )
    @ddt.unpack
    def test_endorsed(self, role_name, is_thread_author, thread_type):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(self.course.id),
                    "thread_type": thread_type,
                    "user_id": (
                        str(self.user.id) if is_thread_author else str(self.user.id + 1)
                    ),
                }
            )
        )
        self.register_post_comment_response(
            {"username": self.user.username}, "test_thread"
        )
        data = self.minimal_data.copy()
        data["endorsed"] = True
        expected_error = role_name == FORUM_ROLE_STUDENT and (
            not is_thread_author or thread_type == "discussion"
        )
        try:
            create_comment(self.request, data)
            last_commemt_params = self.get_mock_func_calls("create_parent_comment")[-1][1]
            assert last_commemt_params["endorsed"]
            assert not expected_error
        except ValidationError:
            assert expected_error

    def test_abuse_flagged(self):
        self.register_post_comment_response(
            {"id": "test_comment", "username": self.user.username}, "test_thread"
        )
        self.register_comment_flag_response("test_comment")
        data = self.minimal_data.copy()
        data["abuse_flagged"] = "True"
        result = create_comment(self.request, data)
        assert result["abuse_flagged"] is True

        self.check_mock_called("update_comment_flag")
        params = {
            "comment_id": "test_comment",
            "action": "flag",
            "user_id": "1",
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("update_comment_flag", -1, **params)

    def test_thread_id_missing(self):
        with pytest.raises(ValidationError) as assertion:
            create_comment(self.request, {})
        assert assertion.value.message_dict == {
            "thread_id": ["This field is required."]
        }

    def test_thread_id_not_found(self):
        self.register_get_thread_error_response("test_thread", 404)
        with pytest.raises(ThreadNotFoundError):
            create_comment(self.request, self.minimal_data)

    def test_nonexistent_course(self):
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {"id": "test_thread", "course_id": "course-v1:non+existent+course"}
            )
        )
        with pytest.raises(CourseNotFoundError):
            create_comment(self.request, self.minimal_data)

    def test_not_enrolled(self):
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            create_comment(self.request, self.minimal_data)

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(disabled_course.id),
                    "commentable_id": "test_topic",
                }
            )
        )
        with pytest.raises(DiscussionDisabledError):
            create_comment(self.request, self.minimal_data)

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {
                    "id": "cohort_thread",
                    "course_id": str(cohort_course.id),
                    "group_id": (
                        None
                        if thread_group_state == "no_group"
                        else (
                            cohort.id
                            if thread_group_state == "match_group"
                            else cohort.id + 1
                        )
                    ),
                }
            )
        )
        self.register_post_comment_response(
            {"username": self.user.username}, thread_id="cohort_thread"
        )
        data = self.minimal_data.copy()
        data["thread_id"] = "cohort_thread"
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            create_comment(self.request, data)
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error

    def test_invalid_field(self):
        data = self.minimal_data.copy()
        del data["raw_body"]
        with pytest.raises(ValidationError):
            create_comment(self.request, data)


@ddt.ddt
@disable_signal(api, "thread_edited")
@disable_signal(api, "thread_voted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class UpdateThreadTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for update_thread"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()

        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def register_thread(self, overrides=None):
        """
        Make a thread with appropriate data overridden by the overrides
        parameter and register mock responses for both GET and PUT on its
        endpoint.
        """
        cs_data = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "original_topic",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "thread_type": "discussion",
                "title": "Original Title",
                "body": "Original body",
            }
        )
        cs_data.update(overrides or {})
        self.register_get_thread_response(cs_data)
        self.register_put_thread_response(cs_data)

    def create_user_with_request(self):
        """
        Create a user and an associated request for a specific course enrollment.
        """
        user = UserFactory.create()
        self.register_get_user_response(user)
        request = RequestFactory().get("/test_path")
        request.user = user
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id)
        return user, request

    def test_empty(self):
        """Check that an empty update does not make any modifying requests."""
        # Ensure that the default following value of False is not applied implicitly
        self.register_get_user_response(
            self.user, subscribed_thread_ids=["test_thread"]
        )
        self.register_thread()
        update_thread(self.request, "test_thread", {})
        for request in httpretty.httpretty.latest_requests:
            assert request.method == "GET"

    def test_basic(self):
        self.register_thread()
        with self.assert_signal_sent(
            api, "thread_edited", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = update_thread(
                    self.request, "test_thread", {"raw_body": "Edited body"}
                )

        assert actual == self.expected_thread_data(
            {
                "raw_body": "Edited body",
                "rendered_body": "<p>Edited body</p>",
                "preview_body": "Edited body",
                "topic_id": "original_topic",
                "read": True,
                "title": "Original Title",
            }
        )
        params = {
            "thread_id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "original_topic",
            "thread_type": "discussion",
            "title": "Original Title",
            "body": "Edited body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
            "closed": False,
            "pinned": False,
            "editing_user_id": str(self.user.id),
        }
        self.check_mock_called_with("update_thread", -1, **params)

    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_abuse_flagged(self, old_flagged, new_flagged, mock_emit):
        """
        Test attempts to edit the "abuse_flagged" field.

        old_flagged indicates whether the thread should be flagged at the start
        of the test. new_flagged indicates the value for the "abuse_flagged"
        field in the update. If old_flagged and new_flagged are the same, no
        update should be made. Otherwise, a PUT should be made to the flag or
        or unflag endpoint according to the new_flagged value.
        """
        self.register_get_user_response(self.user)
        self.register_thread_flag_response("test_thread")
        self.register_thread(
            {"abuse_flaggers": [str(self.user.id)] if old_flagged else []}
        )
        data = {"abuse_flagged": new_flagged}
        result = update_thread(self.request, "test_thread", data)
        assert result["abuse_flagged"] == new_flagged

        flag_func_calls = self.get_mock_func_calls("update_thread_flag")
        last_function_args = flag_func_calls[-1] if flag_func_calls else None

        if old_flagged == new_flagged:
            assert last_function_args is None
        else:
            assert last_function_args[1]["action"] == (
                "flag" if new_flagged else "unflag"
            )
            params = {
                "thread_id": "test_thread",
                "action": "flag" if new_flagged else "unflag",
                "user_id": "1",
                "course_id": str(self.course.id),
            }
            if not new_flagged:
                params["update_all"] = False
            self.check_mock_called_with("update_thread_flag", -1, **params)

            expected_event_name = (
                "edx.forum.thread.reported"
                if new_flagged
                else "edx.forum.thread.unreported"
            )
            expected_event_data = {
                "body": "Original body",
                "id": "test_thread",
                "content_type": "Post",
                "commentable_id": "original_topic",
                "url": "",
                "user_course_roles": [],
                "user_forums_roles": [FORUM_ROLE_STUDENT],
                "target_username": self.user.username,
                "title_truncated": False,
                "title": "Original Title",
                "thread_type": "discussion",
                "group_id": None,
                "truncated": False,
            }
            if not new_flagged:
                expected_event_data["reported_status_cleared"] = False

            actual_event_name, actual_event_data = mock_emit.call_args[0]
            self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
            self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    @ddt.data(
        (False, True),
        (True, True),
    )
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_thread_un_abuse_flag_for_moderator_role(
        self, is_author, remove_all, mock_emit
    ):
        """
        Test un-abuse flag for moderator role.

        When moderator unflags a reported thread, it should
        pass the "all" flag to the api. This will indicate
        to the api to clear all abuse_flaggers, and mark the
        thread as unreported.
        """
        _assign_role_to_user(
            user=self.user, course_id=self.course.id, role=FORUM_ROLE_ADMINISTRATOR
        )
        self.register_get_user_response(self.user)
        self.register_thread_flag_response("test_thread")
        self.register_thread(
            {
                "abuse_flaggers": ["11"],
                "user_id": str(self.user.id) if is_author else "12",
            }
        )
        data = {"abuse_flagged": False}
        update_thread(self.request, "test_thread", data)

        params = {
            "thread_id": "test_thread",
            "action": "unflag",
            "user_id": "1",
            "update_all": bool(remove_all),
            "course_id": str(self.course.id),
        }

        self.check_mock_called_with("update_thread_flag", -1, **params)

        expected_event_name = "edx.forum.thread.unreported"
        expected_event_data = {
            "body": "Original body",
            "id": "test_thread",
            "content_type": "Post",
            "commentable_id": "original_topic",
            "url": "",
            "user_course_roles": [],
            "user_forums_roles": [FORUM_ROLE_STUDENT, FORUM_ROLE_ADMINISTRATOR],
            "target_username": self.user.username,
            "title_truncated": False,
            "title": "Original Title",
            "reported_status_cleared": False,
            "thread_type": "discussion",
            "group_id": None,
            "truncated": False,
        }

        actual_event_name, actual_event_data = mock_emit.call_args[0]
        self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
        self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_voted(self, current_vote_status, new_vote_status, mock_emit):
        """
        Test attempts to edit the "voted" field.

        current_vote_status indicates whether the thread should be upvoted at
        the start of the test. new_vote_status indicates the value for the
        "voted" field in the update. If current_vote_status and new_vote_status
        are the same, no update should be made. Otherwise, a vote should be PUT
        or DELETEd according to the new_vote_status value.
        """
        # setup
        user1, request1 = self.create_user_with_request()
        if current_vote_status:
            self.register_get_user_response(user1, upvoted_ids=["test_thread"])
        self.register_thread_votes_response("test_thread")
        self.register_thread()
        data = {"voted": new_vote_status}
        result = update_thread(request1, "test_thread", data)
        assert result["voted"] == new_vote_status

        vote_update_func_calls = self.get_mock_func_calls("update_thread_votes")
        last_function_args = (
            vote_update_func_calls[-1] if vote_update_func_calls else None
        )

        if current_vote_status == new_vote_status:
            assert last_function_args is None
        else:
            if vote_update_func_calls:
                assert last_function_args[1]["value"] == (
                    "up" if new_vote_status else "down"
                )
                params = {
                    "thread_id": "test_thread",
                    "value": "up" if new_vote_status else "down",
                    "user_id": str(user1.id),
                    "course_id": str(self.course.id),
                }
                self.check_mock_called_with("update_thread_votes", -1, **params)
            else:
                params = {
                    "thread_id": "test_thread",
                    "user_id": str(user1.id),
                    "course_id": str(self.course.id),
                }
                self.check_mock_called_with("delete_thread_vote", -1, **params)
            event_name, event_data = mock_emit.call_args[0]
            assert event_name == "edx.forum.thread.voted"
            assert event_data == {
                "undo_vote": (not new_vote_status),
                "url": "",
                "target_username": self.user.username,
                "vote_value": "up",
                "user_forums_roles": [FORUM_ROLE_STUDENT],
                "user_course_roles": [],
                "commentable_id": "original_topic",
                "id": "test_thread",
            }

    @ddt.data(*itertools.product([True, False], [True, False], [True, False]))
    @ddt.unpack
    def test_vote_count(self, current_vote_status, first_vote, second_vote):
        """
        Tests vote_count increases and decreases correctly from the same user
        """
        # setup
        starting_vote_count = 0
        user, request = self.create_user_with_request()
        if current_vote_status:
            self.register_get_user_response(user, upvoted_ids=["test_thread"])
            starting_vote_count = 1
        self.register_thread_votes_response("test_thread")
        self.register_thread(overrides={"votes": {"up_count": starting_vote_count}})

        # first vote
        data = {"voted": first_vote}
        result = update_thread(request, "test_thread", data)
        self.register_thread(overrides={"voted": first_vote})
        assert result["vote_count"] == (1 if first_vote else 0)

        # second vote
        # In the previous tests, where we mocked request objects,
        # the mocked user API returned a user with upvoted_ids=[]. In our case,
        # we have used register_get_user_response again to set upvoted_ids to None.
        data = {"voted": second_vote}
        self.register_get_user_response(user)
        self.register_thread(overrides={"voted": False})
        result = update_thread(request, "test_thread", data)
        assert result["vote_count"] == (1 if second_vote else 0)

    @ddt.data(
        *itertools.product([True, False], [True, False], [True, False], [True, False])
    )
    @ddt.unpack
    def test_vote_count_two_users(
        self, current_user1_vote, current_user2_vote, user1_vote, user2_vote
    ):
        """
        Tests vote_count increases and decreases correctly from different users
        """
        # setup
        user1, request1 = self.create_user_with_request()
        user2, request2 = self.create_user_with_request()

        vote_count = 0
        if current_user1_vote:
            self.register_get_user_response(user1, upvoted_ids=["test_thread"])
            vote_count += 1
        if current_user2_vote:
            self.register_get_user_response(user2, upvoted_ids=["test_thread"])
            vote_count += 1

        for current_vote, user_vote, request in [
            (current_user1_vote, user1_vote, request1),
            (current_user2_vote, user2_vote, request2),
        ]:

            self.register_thread_votes_response("test_thread")
            self.register_thread(overrides={"votes": {"up_count": vote_count}})

            data = {"voted": user_vote}
            result = update_thread(request, "test_thread", data)
            if current_vote == user_vote:
                assert result["vote_count"] == vote_count
            elif user_vote:
                vote_count += 1
                assert result["vote_count"] == vote_count
                self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
            else:
                vote_count -= 1
                assert result["vote_count"] == vote_count
                self.register_get_user_response(self.user, upvoted_ids=[])

    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_following(self, old_following, new_following, mock_emit):
        """
        Test attempts to edit the "following" field.

        old_following indicates whether the thread should be followed at the
        start of the test. new_following indicates the value for the "following"
        field in the update. If old_following and new_following are the same, no
        update should be made. Otherwise, a subscription should be POSTed or
        DELETEd according to the new_following value.
        """
        if old_following:
            self.register_get_user_response(
                self.user, subscribed_thread_ids=["test_thread"]
            )
        self.register_subscription_response(self.user)
        self.register_thread()
        data = {"following": new_following}
        signal_name = "thread_followed" if new_following else "thread_unfollowed"
        # Patch at the api module level where the signal is imported and used
        mock_path = f"lms.djangoapps.discussion.rest_api.api.{signal_name}"
        with mock.patch(mock_path) as signal_patch:
            with self.captureOnCommitCallbacks(execute=True):
                result = update_thread(self.request, "test_thread", data)
            if old_following != new_following:
                self.assertEqual(signal_patch.send.call_count, 1)  # noqa: PT009
        assert result["following"] == new_following

        if old_following == new_following:
            assert not self.check_mock_called("create_subscription")
        else:
            params = {  # noqa: F841
                "user_id": str(self.user.id),
                "course_id": str(self.course.id),
                "source_id": "test_thread",
            }
            if new_following:
                assert self.check_mock_called("create_subscription")
            else:
                assert self.check_mock_called("delete_subscription")

            event_name, event_data = mock_emit.call_args[0]
            expected_event_action = "followed" if new_following else "unfollowed"
            assert event_name == f"edx.forum.thread.{expected_event_action}"
            assert event_data["commentable_id"] == "original_topic"
            assert event_data["id"] == "test_thread"
            assert event_data["followed"] == new_following
            assert event_data["user_forums_roles"] == ["Student"]

    def test_nonexistent_thread(self):
        self.register_get_thread_error_response("test_thread", 404)
        with pytest.raises(ThreadNotFoundError):
            update_thread(self.request, "test_thread", {})

    def test_nonexistent_course(self):
        self.register_thread({"course_id": "course-v1:non+existent+course"})
        with pytest.raises(CourseNotFoundError):
            update_thread(self.request, "test_thread", {})

    def test_not_enrolled(self):
        self.register_thread()
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            update_thread(self.request, "test_thread", {})

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        self.register_thread(overrides={"course_id": str(disabled_course.id)})
        with pytest.raises(DiscussionDisabledError):
            update_thread(self.request, "test_thread", {})

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_thread(
            {
                "course_id": str(cohort_course.id),
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            }
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            update_thread(self.request, "test_thread", {})
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    )
    def test_author_only_fields(self, role_name):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_thread({"user_id": str(self.user.id + 1)})
        data = {field: "edited" for field in ["topic_id", "title", "raw_body"]}
        data["type"] = "question"
        expected_error = role_name == FORUM_ROLE_STUDENT
        try:
            update_thread(self.request, "test_thread", data)
            assert not expected_error
        except ValidationError as err:
            assert expected_error
            assert err.message_dict == {  # noqa: PT017
                field: ["This field is not editable."] for field in data.keys()
            }

    def test_invalid_field(self):
        self.register_thread()
        with pytest.raises(ValidationError) as assertion:
            update_thread(self.request, "test_thread", {"raw_body": ""})
        assert assertion.value.message_dict == {
            "raw_body": ["This field may not be blank."]
        }

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    )
    @mock.patch(
        "lms.djangoapps.discussion.rest_api.serializers.EDIT_REASON_CODES",
        {
            "test-edit-reason": "Test Edit Reason",
        },
    )
    @mock.patch("eventtracking.tracker.emit")
    def test_update_thread_with_edit_reason_code(self, role_name, mock_emit):
        """
        Test editing comments, specifying and retrieving edit reason codes.
        """
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_thread({"user_id": str(self.user.id + 1)})
        try:
            result = update_thread(
                self.request,
                "test_thread",
                {
                    "raw_body": "Edited body",
                    "edit_reason_code": "test-edit-reason",
                },
            )
            assert role_name != FORUM_ROLE_STUDENT
            assert result["last_edit"] == {
                "original_body": "Original body",
                "reason": "Test Edit Reason",
                "reason_code": "test-edit-reason",
                "author": self.user.username,
            }
            thread_call_args = self.get_mock_func_calls("update_thread")[0][1]
            assert thread_call_args["edit_reason_code"] == "test-edit-reason"

            expected_event_name = "edx.forum.thread.edited"
            expected_event_data = {
                "id": "test_thread",
                "content_type": "Post",
                "own_content": False,
                "url": "",
                "user_course_roles": [],
                "user_forums_roles": ["Student", role_name],
                "target_username": self.user.username,
                "edit_reason": "test-edit-reason",
                "commentable_id": "original_topic",
            }

            actual_event_name, actual_event_data = mock_emit.call_args[0]
            self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
            self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

        except ValidationError as error:
            assert role_name == FORUM_ROLE_STUDENT
            assert error.message_dict == {  # noqa: PT017
                "edit_reason_code": ["This field is not editable."],
                "raw_body": ["This field is not editable."],
            }

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
        )
    )
    @ddt.unpack
    @mock.patch(
        "lms.djangoapps.discussion.rest_api.serializers.CLOSE_REASON_CODES",
        {
            "test-close-reason": "Test Close Reason",
        },
    )
    @mock.patch("eventtracking.tracker.emit")
    def test_update_thread_with_close_reason_code(self, role_name, closed, mock_emit):
        """
        Test editing comments, specifying and retrieving edit reason codes.
        """
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_thread()
        try:
            self.request.META["HTTP_REFERER"] = "https://example.com"
            result = update_thread(
                self.request,
                "test_thread",
                {
                    "closed": closed,
                    "close_reason_code": "test-close-reason",
                },
            )

            assert role_name != FORUM_ROLE_STUDENT
            assert result["closed"] == closed
            thread_call_args = self.get_mock_func_calls("update_thread")[0][1]
            assert thread_call_args["close_reason_code"] == "test-close-reason"
            assert thread_call_args["closing_user_id"] == str(self.user.id)

            expected_event_name = (
                f'edx.forum.thread.{"locked" if closed else "unlocked"}'
            )
            expected_event_data = {
                "id": "test_thread",
                "team_id": None,
                "url": self.request.META["HTTP_REFERER"],
                "user_course_roles": [],
                "user_forums_roles": ["Student", role_name],
                "target_username": self.user.username,
                "lock_reason": "test-close-reason",
                "commentable_id": "original_topic",
            }

            actual_event_name, actual_event_data = mock_emit.call_args[0]
            self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
            self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009
        except ValidationError as error:
            assert role_name == FORUM_ROLE_STUDENT
            assert error.message_dict == {  # noqa: PT017
                "closed": ["This field is not editable."],
                "close_reason_code": ["This field is not editable."],
            }


@ddt.ddt
@disable_signal(api, "comment_edited")
@disable_signal(api, "comment_voted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class UpdateCommentTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for update_comment"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()

        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def register_comment(self, overrides=None, thread_overrides=None, course=None):
        """
        Make a comment with appropriate data overridden by the overrides
        parameter and register mock responses for both GET and PUT on its
        endpoint. Also mock GET for the related thread with thread_overrides.
        """
        if course is None:
            course = self.course

        cs_thread_data = make_minimal_cs_thread(
            {"id": "test_thread", "course_id": str(course.id)}
        )
        cs_thread_data.update(thread_overrides or {})
        self.register_get_thread_response(cs_thread_data)
        cs_comment_data = make_minimal_cs_comment(
            {
                "id": "test_comment",
                "course_id": cs_thread_data["course_id"],
                "thread_id": cs_thread_data["id"],
                "username": self.user.username,
                "user_id": str(self.user.id),
                "created_at": "2015-06-03T00:00:00Z",
                "updated_at": "2015-06-03T00:00:00Z",
                "body": "Original body",
            }
        )
        cs_comment_data.update(overrides or {})
        self.register_get_comment_response(cs_comment_data)
        self.register_put_comment_response(cs_comment_data)

    def create_user_with_request(self):
        """
        Create a user and an associated request for a specific course enrollment.
        """
        user = UserFactory.create()
        self.register_get_user_response(user)
        request = RequestFactory().get("/test_path")
        request.user = user
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id)
        return user, request

    def test_empty(self):
        """Check that an empty update does not make any modifying requests."""
        self.register_comment()
        update_comment(self.request, "test_comment", {})
        for request in httpretty.httpretty.latest_requests:
            assert request.method == "GET"

    @ddt.data(None, "test_parent")
    def test_basic(self, parent_id):
        self.register_comment({"parent_id": parent_id})
        with self.assert_signal_sent(
            api, "comment_edited", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                actual = update_comment(
                    self.request, "test_comment", {"raw_body": "Edited body"}
                )
        expected = {
            "anonymous": False,
            "anonymous_to_peers": False,
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": parent_id,
            "author": self.user.username,
            "author_label": None,
            "created_at": "2015-06-03T00:00:00Z",
            "updated_at": "2015-06-03T00:00:00Z",
            "raw_body": "Edited body",
            "rendered_body": "<p>Edited body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
            "child_count": 0,
            "can_delete": True,
            "last_edit": None,
            "edit_by_label": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        assert actual == expected
        params = {
            "comment_id": "test_comment",
            "body": "Edited body",
            "course_id": str(self.course.id),
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
            "endorsed": False,
            "editing_user_id": str(self.user.id),
        }
        self.check_mock_called_with("update_comment", -1, **params)

    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_abuse_flagged(self, old_flagged, new_flagged, mock_emit):
        """
        Test attempts to edit the "abuse_flagged" field.

        old_flagged indicates whether the comment should be flagged at the start
        of the test. new_flagged indicates the value for the "abuse_flagged"
        field in the update. If old_flagged and new_flagged are the same, no
        update should be made. Otherwise, a PUT should be made to the flag or
        or unflag endpoint according to the new_flagged value.
        """
        self.register_get_user_response(self.user)
        self.register_comment_flag_response("test_comment")
        self.register_comment(
            {"abuse_flaggers": [str(self.user.id)] if old_flagged else []}
        )
        data = {"abuse_flagged": new_flagged}
        result = update_comment(self.request, "test_comment", data)
        assert result["abuse_flagged"] == new_flagged
        flag_func_calls = self.get_mock_func_calls("update_comment_flag")
        last_function_args = flag_func_calls[-1] if flag_func_calls else None

        if old_flagged == new_flagged:
            assert last_function_args is None
        else:
            assert last_function_args[1]["action"] == (
                "flag" if new_flagged else "unflag"
            )
            params = {
                "comment_id": "test_comment",
                "action": "flag" if new_flagged else "unflag",
                "user_id": "1",
                "course_id": str(self.course.id),
            }
            if not new_flagged:
                params["update_all"] = False
            self.check_mock_called_with("update_comment_flag", -1, **params)

            expected_event_name = (
                "edx.forum.response.reported"
                if new_flagged
                else "edx.forum.response.unreported"
            )
            expected_event_data = {
                "discussion": {'id': 'test_thread'},
                "body": "Original body",
                "id": "test_comment",
                "content_type": "Response",
                "commentable_id": "dummy",
                "url": "",
                "truncated": False,
                "user_course_roles": [],
                "user_forums_roles": [FORUM_ROLE_STUDENT],
                "target_username": self.user.username,
            }
            if not new_flagged:
                expected_event_data["reported_status_cleared"] = False

            actual_event_name, actual_event_data = mock_emit.call_args[0]
            self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
            self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    @ddt.data(
        (False, True),
        (True, True),
    )
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_comment_un_abuse_flag_for_moderator_role(
        self, is_author, remove_all, mock_emit
    ):
        """
        Test un-abuse flag for moderator role.

        When moderator unflags a reported comment, it should
        pass the "all" flag to the api. This will indicate
        to the api to clear all abuse_flaggers, and mark the
        comment as unreported.
        """
        _assign_role_to_user(
            user=self.user, course_id=self.course.id, role=FORUM_ROLE_ADMINISTRATOR
        )
        self.register_get_user_response(self.user)
        self.register_comment_flag_response("test_comment")
        self.register_comment(
            {
                "abuse_flaggers": ["11"],
                "user_id": str(self.user.id) if is_author else "12",
            }
        )
        data = {"abuse_flagged": False}
        update_comment(self.request, "test_comment", data)

        params = {
            "comment_id": "test_comment",
            "action": "unflag",
            "user_id": "1",
            "update_all": bool(remove_all),
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("update_comment_flag", -1, **params)

        expected_event_name = "edx.forum.response.unreported"
        expected_event_data = {
            "body": "Original body",
            "id": "test_comment",
            "content_type": "Response",
            "discussion": {'id': 'test_thread'},
            "commentable_id": "dummy",
            "truncated": False,
            "url": "",
            "user_course_roles": [],
            "user_forums_roles": [FORUM_ROLE_STUDENT, FORUM_ROLE_ADMINISTRATOR],
            "target_username": self.user.username,
            "reported_status_cleared": False,
        }

        actual_event_name, actual_event_data = mock_emit.call_args[0]
        self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
        self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    @mock.patch("eventtracking.tracker.emit")
    def test_voted(self, current_vote_status, new_vote_status, mock_emit):
        """
        Test attempts to edit the "voted" field.

        current_vote_status indicates whether the comment should be upvoted at
        the start of the test. new_vote_status indicates the value for the
        "voted" field in the update. If current_vote_status and new_vote_status
        are the same, no update should be made. Otherwise, a vote should be PUT
        or DELETEd according to the new_vote_status value.
        """
        vote_count = 0
        user1, request1 = self.create_user_with_request()
        if current_vote_status:
            self.register_get_user_response(user1, upvoted_ids=["test_comment"])
            vote_count = 1
        self.register_comment_votes_response("test_comment")
        self.register_comment(overrides={"votes": {"up_count": vote_count}})
        data = {"voted": new_vote_status}
        result = update_comment(request1, "test_comment", data)
        assert result["vote_count"] == (1 if new_vote_status else 0)
        assert result["voted"] == new_vote_status
        vote_update_func_calls = self.get_mock_func_calls("update_comment_votes")
        last_function_args = (
            vote_update_func_calls[-1] if vote_update_func_calls else None
        )
        if current_vote_status == new_vote_status:
            assert last_function_args is None
        else:

            if vote_update_func_calls:
                assert last_function_args[1]["value"] == (
                    "up" if new_vote_status else "down"
                )
                params = {
                    "comment_id": "test_comment",
                    "value": "up" if new_vote_status else "down",
                    "user_id": str(user1.id),
                    "course_id": str(self.course.id),
                }
                self.check_mock_called_with("update_comment_votes", -1, **params)
            else:
                params = {
                    "comment_id": "test_comment",
                    "user_id": str(user1.id),
                    "course_id": str(self.course.id),
                }
                self.check_mock_called_with("delete_comment_vote", -1, **params)

            event_name, event_data = mock_emit.call_args[0]
            assert event_name == "edx.forum.response.voted"

            assert event_data == {
                "undo_vote": (not new_vote_status),
                "url": "",
                "target_username": self.user.username,
                "vote_value": "up",
                "user_forums_roles": [FORUM_ROLE_STUDENT],
                "user_course_roles": [],
                "commentable_id": "dummy",
                "id": "test_comment",
            }

    @ddt.data(*itertools.product([True, False], [True, False], [True, False]))
    @ddt.unpack
    def test_vote_count(self, current_vote_status, first_vote, second_vote):
        """
        Tests vote_count increases and decreases correctly from the same user
        """
        # setup
        starting_vote_count = 0
        user1, request1 = self.create_user_with_request()
        if current_vote_status:
            self.register_get_user_response(user1, upvoted_ids=["test_comment"])
            starting_vote_count = 1
        self.register_comment_votes_response("test_comment")
        self.register_comment(overrides={"votes": {"up_count": starting_vote_count}})

        # first vote
        data = {"voted": first_vote}
        result = update_comment(request1, "test_comment", data)
        self.register_comment(overrides={"voted": first_vote})
        assert result["vote_count"] == (1 if first_vote else 0)

        # second vote
        # In the previous tests, where we mocked request objects,
        # the mocked user API returned a user with upvoted_ids=[]. In our case,
        # we have used register_get_user_response again to set upvoted_ids to None.
        data = {"voted": second_vote}
        self.register_get_user_response(user1)
        result = update_comment(request1, "test_comment", data)
        assert result["vote_count"] == (1 if second_vote else 0)

    # TODO: Refactor test logic to avoid complex conditionals and in-test logic.
    # Aim for simpler, more explicit test cases, even if it means more code,
    # to reduce the risk of introducing logic bugs within the tests themselves.
    @ddt.data(
        *itertools.product([True, False], [True, False], [True, False], [True, False])
    )
    @ddt.unpack
    def test_vote_count_two_users(
        self, current_user1_vote, current_user2_vote, user1_vote, user2_vote
    ):
        """
        Tests vote_count increases and decreases correctly from different users
        """
        user1, request1 = self.create_user_with_request()
        user2, request2 = self.create_user_with_request()

        vote_count = 0
        if current_user1_vote:
            self.register_get_user_response(user1, upvoted_ids=["test_comment"])
            vote_count += 1
        if current_user2_vote:
            self.register_get_user_response(user2, upvoted_ids=["test_comment"])
            vote_count += 1

        for current_vote, user_vote, request in [
            (current_user1_vote, user1_vote, request1),
            (current_user2_vote, user2_vote, request2),
        ]:

            self.register_comment_votes_response("test_comment")
            self.register_comment(overrides={"votes": {"up_count": vote_count}})

            data = {"voted": user_vote}
            result = update_comment(request, "test_comment", data)
            if current_vote == user_vote:
                assert result["vote_count"] == vote_count
            elif user_vote:
                vote_count += 1
                assert result["vote_count"] == vote_count
                self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
            else:
                vote_count -= 1
                assert result["vote_count"] == vote_count
                self.register_get_user_response(self.user, upvoted_ids=[])

    def test_nonexistent_comment(self):
        self.register_get_comment_error_response("test_comment", 404)
        with pytest.raises(CommentNotFoundError):
            update_comment(self.request, "test_comment", {})

    def test_nonexistent_course(self):
        self.register_comment(
            thread_overrides={"course_id": "course-v1:non+existent+course"}
        )
        with pytest.raises(CourseNotFoundError):
            update_comment(self.request, "test_comment", {})

    def test_unenrolled(self):
        self.register_comment()
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            update_comment(self.request, "test_comment", {})

    def test_discussions_disabled(self):
        self.register_comment(course=_discussion_disabled_course_for(self.user))
        with pytest.raises(DiscussionDisabledError):
            update_comment(self.request, "test_comment", {})

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_get_thread_response(make_minimal_cs_thread())
        self.register_comment(
            {"thread_id": "test_thread"},
            thread_overrides={
                "id": "test_thread",
                "course_id": str(cohort_course.id),
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            },
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            update_comment(self.request, "test_comment", {})
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            [True, False],
        )
    )
    @ddt.unpack
    def test_raw_body_access(self, role_name, is_thread_author, is_comment_author):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_comment(
            {"user_id": str(self.user.id if is_comment_author else (self.user.id + 1))},
            thread_overrides={
                "user_id": str(self.user.id if is_thread_author else (self.user.id + 1))
            },
        )
        expected_error = role_name == FORUM_ROLE_STUDENT and not is_comment_author
        try:
            update_comment(self.request, "test_comment", {"raw_body": "edited"})
            assert not expected_error
        except ValidationError as err:
            assert expected_error
            assert err.message_dict == {"raw_body": ["This field is not editable."]}  # noqa: PT017

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["question", "discussion"],
            [True, False],
        )
    )
    @ddt.unpack
    @mock.patch(
        "lms.djangoapps.discussion.rest_api.api.comment_endorsed.send"
    )
    def test_endorsed_access(
        self, role_name, is_thread_author, thread_type, is_comment_author, endorsed_mock
    ):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_comment(
            {"user_id": str(self.user.id if is_comment_author else (self.user.id + 1))},
            thread_overrides={
                "thread_type": thread_type,
                "user_id": str(
                    self.user.id if is_thread_author else (self.user.id + 1)
                ),
            },
        )
        expected_error = role_name == FORUM_ROLE_STUDENT and (
            thread_type == "discussion" or not is_thread_author
        )
        try:
            with self.captureOnCommitCallbacks(execute=True):
                update_comment(self.request, "test_comment", {"endorsed": True})
            self.assertEqual(endorsed_mock.call_count, 1)  # noqa: PT009
            assert not expected_error
        except ValidationError as err:
            assert expected_error
            assert err.message_dict == {"endorsed": ["This field is not editable."]}  # noqa: PT017

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    )
    @mock.patch(
        "lms.djangoapps.discussion.rest_api.serializers.EDIT_REASON_CODES",
        {
            "test-edit-reason": "Test Edit Reason",
        },
    )
    @mock.patch("eventtracking.tracker.emit")
    def test_update_comment_with_edit_reason_code(self, role_name, mock_emit):
        """
        Test editing comments, specifying and retrieving edit reason codes.
        """
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_comment({"user_id": str(self.user.id + 1)})
        try:
            result = update_comment(
                self.request,
                "test_comment",
                {
                    "raw_body": "Edited body",
                    "edit_reason_code": "test-edit-reason",
                },
            )
            assert role_name != FORUM_ROLE_STUDENT
            assert result["last_edit"] == {
                "original_body": "Original body",
                "reason": "Test Edit Reason",
                "reason_code": "test-edit-reason",
                "author": self.user.username,
            }
            comment_call_args = self.get_mock_func_calls("update_comment")[0][1]
            assert comment_call_args["edit_reason_code"] == "test-edit-reason"

            expected_event_name = "edx.forum.response.edited"
            expected_event_data = {
                "id": "test_comment",
                "content_type": "Response",
                "own_content": False,
                "url": "",
                "user_course_roles": [],
                "user_forums_roles": ["Student", role_name],
                "target_username": self.user.username,
                "edit_reason": "test-edit-reason",
                "commentable_id": "dummy",
            }

            actual_event_name, actual_event_data = mock_emit.call_args[0]
            self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
            self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

        except ValidationError:
            assert role_name == FORUM_ROLE_STUDENT


@ddt.ddt
@disable_signal(api, "thread_deleted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class DeleteThreadTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for delete_thread"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        self.thread_id = "test_thread"
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def register_thread(self, overrides=None):
        """
        Make a thread with appropriate data overridden by the overrides
        parameter and register mock responses for both GET and DELETE on its
        endpoint.
        """
        cs_data = make_minimal_cs_thread(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "user_id": str(self.user.id),
            }
        )
        cs_data.update(overrides or {})
        self.register_get_thread_response(cs_data)
        self.register_delete_thread_response(cs_data["id"])

    @mock.patch("eventtracking.tracker.emit")
    def test_basic(self, mock_emit):
        self.register_thread()
        with self.assert_signal_sent(
            api, "thread_deleted", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                assert delete_thread(self.request, self.thread_id) is None
        self.check_mock_called("delete_thread")
        params = {
            "thread_id": self.thread_id,
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("delete_thread", -1, **params)

        expected_event_name = "edx.forum.thread.deleted"
        expected_event_data = {
            "body": "dummy",
            "content_type": "Post",
            "own_content": True,
            "commentable_id": "dummy",
            "target_username": "dummy",
            "title_truncated": False,
            "title": "dummy",
            "id": "test_thread",
            "url": "",
            "user_forums_roles": ["Student"],
            "user_course_roles": [],
        }

        actual_event_name, actual_event_data = mock_emit.call_args[0]
        self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
        self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    def test_thread_id_not_found(self):
        self.register_get_thread_error_response("missing_thread", 404)
        with pytest.raises(ThreadNotFoundError):
            delete_thread(self.request, "missing_thread")

    def test_nonexistent_course(self):
        self.register_thread({"course_id": "course-v1:non+existent+course"})
        with pytest.raises(CourseNotFoundError):
            delete_thread(self.request, self.thread_id)

    def test_not_enrolled(self):
        self.register_thread()
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            delete_thread(self.request, self.thread_id)

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        self.register_thread(overrides={"course_id": str(disabled_course.id)})
        with pytest.raises(DiscussionDisabledError):
            delete_thread(self.request, self.thread_id)

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    )
    def test_non_author_delete_allowed(self, role_name):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_thread({"user_id": str(self.user.id + 1)})
        expected_error = role_name == FORUM_ROLE_STUDENT
        try:
            delete_thread(self.request, self.thread_id)
            assert not expected_error
        except PermissionDenied:
            assert expected_error

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        """
        Tests group access for deleting a thread

        All privileged roles are able to delete a thread. A student role can
        only delete a thread if,
        the student role is the author and the thread is not in a cohort,
        the student role is the author and the thread is in the author's cohort.
        """
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_thread(
            {
                "course_id": str(cohort_course.id),
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            }
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            delete_thread(self.request, self.thread_id)
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error


@ddt.ddt
@disable_signal(api, "comment_deleted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class DeleteCommentTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockSignalHandlerMixin,
    ForumMockUtilsMixin,
):
    """Tests for delete_comment"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        self.thread_id = "test_thread"
        self.comment_id = "test_comment"
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def register_comment_and_thread(self, overrides=None, thread_overrides=None):
        """
        Make a comment with appropriate data overridden by the override
        parameters and register mock responses for both GET and DELETE on its
        endpoint. Also mock GET for the related thread with thread_overrides.
        """
        cs_thread_data = make_minimal_cs_thread(
            {"id": self.thread_id, "course_id": str(self.course.id)}
        )
        cs_thread_data.update(thread_overrides or {})
        self.register_get_thread_response(cs_thread_data)
        cs_comment_data = make_minimal_cs_comment(
            {
                "id": self.comment_id,
                "course_id": cs_thread_data["course_id"],
                "thread_id": cs_thread_data["id"],
                "username": self.user.username,
                "user_id": str(self.user.id),
            }
        )
        cs_comment_data.update(overrides or {})
        self.register_get_comment_response(cs_comment_data)
        self.register_delete_comment_response(self.comment_id)

    @mock.patch("eventtracking.tracker.emit")
    def test_basic(self, mock_emit):
        self.register_comment_and_thread()
        with self.assert_signal_sent(
            api, "comment_deleted", sender=None, user=self.user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                assert delete_comment(self.request, self.comment_id) is None
        self.check_mock_called("delete_comment")
        params = {
            "comment_id": self.comment_id,
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("delete_comment", -1, **params)

        expected_event_name = "edx.forum.response.deleted"
        expected_event_data = {
            "body": "dummy",
            "content_type": "Response",
            "own_content": True,
            "commentable_id": "dummy",
            "target_username": self.user.username,
            "id": "test_comment",
            "url": "",
            "user_forums_roles": ["Student"],
            "user_course_roles": [],
        }

        actual_event_name, actual_event_data = mock_emit.call_args[0]
        self.assertEqual(actual_event_name, expected_event_name)  # noqa: PT009
        self.assertEqual(actual_event_data, expected_event_data)  # noqa: PT009

    def test_comment_id_not_found(self):
        self.register_get_comment_error_response("missing_comment", 404)
        with pytest.raises(CommentNotFoundError):
            delete_comment(self.request, "missing_comment")

    def test_nonexistent_course(self):
        self.register_comment_and_thread(
            thread_overrides={"course_id": "course-v1:non+existent+course"}
        )
        with pytest.raises(CourseNotFoundError):
            delete_comment(self.request, self.comment_id)

    def test_not_enrolled(self):
        self.register_comment_and_thread()
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            delete_comment(self.request, self.comment_id)

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        self.register_comment_and_thread(
            thread_overrides={"course_id": str(disabled_course.id)},
            overrides={"course_id": str(disabled_course.id)},
        )
        with pytest.raises(DiscussionDisabledError):
            delete_comment(self.request, self.comment_id)

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    )
    def test_non_author_delete_allowed(self, role_name):
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role_name)
        self.register_comment_and_thread(overrides={"user_id": str(self.user.id + 1)})
        expected_error = role_name == FORUM_ROLE_STUDENT
        try:
            delete_comment(self.request, self.comment_id)
            assert not expected_error
        except PermissionDenied:
            assert expected_error

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        """
        Tests group access for deleting a comment

        All privileged roles are able to delete a comment. A student role can
        only delete a comment if,
        the student role is the author and the comment is not in a cohort,
        the student role is the author and the comment is in the author's cohort.
        """
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_comment_and_thread(
            overrides={"thread_id": "test_thread"},
            thread_overrides={
                "course_id": str(cohort_course.id),
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            },
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            delete_comment(self.request, self.comment_id)
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class RetrieveThreadTest(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    ForumMockUtilsMixin,
):
    """Tests for get_thread"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        self.thread_id = "test_thread"
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def register_thread(self, overrides=None):
        """
        Make a thread with appropriate data overridden by the overrides
        parameter and register mock responses for GET on its
        endpoint.
        """
        cs_data = make_minimal_cs_thread(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "title": "Test Title",
                "body": "Test body",
                "resp_total": 0,
            }
        )
        cs_data.update(overrides or {})
        self.register_get_thread_response(cs_data)

    def test_basic(self):
        self.register_thread({"resp_total": 2})
        assert get_thread(self.request, self.thread_id) == self.expected_thread_data(
            {"response_count": 2, "unread_comment_count": 1}
        )
        self.check_mock_called("get_thread")

    def test_thread_id_not_found(self):
        self.register_get_thread_error_response("missing_thread", 404)
        with pytest.raises(ThreadNotFoundError):
            get_thread(self.request, "missing_thread")

    def test_nonauthor_enrolled_in_course(self):
        non_author_user = UserFactory.create()
        self.register_get_user_response(non_author_user)
        CourseEnrollmentFactory.create(user=non_author_user, course_id=self.course.id)
        self.register_thread()
        self.request.user = non_author_user
        assert get_thread(self.request, self.thread_id) == self.expected_thread_data(
            {
                "can_delete": False,
                "editable_fields": [
                    "abuse_flagged",
                    "copy_link",
                    "following",
                    "read",
                    "voted",
                ],
                "unread_comment_count": 1,
            }
        )
        self.check_mock_called("get_thread")

    def test_not_enrolled_in_course(self):
        self.register_thread()
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            get_thread(self.request, self.thread_id)

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(self, role_name, course_is_cohorted, thread_group_state):
        """
        Tests group access for retrieving a thread

        All privileged roles are able to retrieve a thread. A student role can
        only retrieve a thread if,
        the student role is the author and the thread is not in a cohort,
        the student role is the author and the thread is in the author's cohort.
        """
        cohort_course, cohort = _create_course_and_cohort_with_user_role(
            course_is_cohorted, self.user, role_name
        )
        self.register_thread(
            {
                "course_id": str(cohort_course.id),
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            }
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            get_thread(self.request, self.thread_id)
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error

    def test_course_id_mismatch(self):
        """
        Test if the api throws not found exception if course_id from params mismatches course_id in thread
        """
        self.register_thread()
        get_thread(self.request, self.thread_id, "different_course_id")
        assert ThreadNotFoundError


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetThreadListTest(
        ForumMockUtilsMixin, UrlResetMixin, SharedModuleStoreTestCase
):
    """Test for get_thread_list"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.maxDiff = None  # pylint: disable=invalid-name
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.author = UserFactory.create()
        self.course.cohort_config = {"cohorted": False}
        modulestore().update_item(self.course, ModuleStoreEnum.UserID.test)
        self.cohort = CohortFactory.create(course_id=self.course.id)

    def get_thread_list(
        self,
        threads,
        page=1,
        page_size=1,
        num_pages=1,
        course=None,
        topic_id_list=None,
    ):
        """
        Register the appropriate comments service response, then call
        get_thread_list and return the result.
        """
        course = course or self.course
        self.register_get_threads_response(threads, page, num_pages)
        ret = get_thread_list(self.request, course.id, page, page_size, topic_id_list)
        return ret

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            get_thread_list(
                self.request,
                CourseLocator.from_string("course-v1:non+existent+course"),
                1,
                1,
            )

    def test_not_enrolled(self):
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            self.get_thread_list([])

    def test_discussions_disabled(self):
        with pytest.raises(DiscussionDisabledError):
            self.get_thread_list([], course=_discussion_disabled_course_for(self.user))

    def test_empty(self):
        assert self.get_thread_list([], num_pages=0).data == {
            "pagination": {"next": None, "previous": None, "num_pages": 0, "count": 0},
            "results": [],
            "text_search_rewrite": None,
        }

    def test_get_threads_by_topic_id(self):
        self.get_thread_list([], topic_id_list=["topic_x", "topic_meow"])
        self.check_mock_called("get_user_threads")
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 1,
            "commentable_ids": ["topic_x", "topic_meow"],
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_basic_query_params(self):
        self.get_thread_list([], page=6, page_size=14)
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 6,
            "per_page": 14,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_thread_content(self):
        self.course.cohort_config = {"cohorted": True}
        modulestore().update_item(self.course, ModuleStoreEnum.UserID.test)
        source_threads = [
            make_minimal_cs_thread(
                {
                    "id": "test_thread_id_0",
                    "course_id": str(self.course.id),
                    "commentable_id": "topic_x",
                    "username": self.author.username,
                    "user_id": str(self.author.id),
                    "title": "Test Title",
                    "body": "Test body",
                    "votes": {"up_count": 4},
                    "comments_count": 5,
                    "unread_comments_count": 3,
                    "endorsed": True,
                    "read": True,
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                }
            ),
            make_minimal_cs_thread(
                {
                    "id": "test_thread_id_1",
                    "course_id": str(self.course.id),
                    "commentable_id": "topic_y",
                    "group_id": self.cohort.id,
                    "username": self.author.username,
                    "user_id": str(self.author.id),
                    "thread_type": "question",
                    "title": "Another Test Title",
                    "body": "More content",
                    "votes": {"up_count": 9},
                    "comments_count": 18,
                    "created_at": "2015-04-28T22:22:22Z",
                    "updated_at": "2015-04-28T00:33:33Z",
                }
            ),
        ]
        expected_threads = [
            self.expected_thread_data(
                {
                    "id": "test_thread_id_0",
                    "author": self.author.username,
                    "topic_id": "topic_x",
                    "vote_count": 4,
                    "comment_count": 6,
                    "unread_comment_count": 3,
                    "comment_list_url": "http://testserver/api/discussion/v1/comments/?thread_id=test_thread_id_0",
                    "editable_fields": [
                        "abuse_flagged",
                        "copy_link",
                        "following",
                        "read",
                        "voted",
                    ],
                    "has_endorsed": True,
                    "read": True,
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "abuse_flagged_count": None,
                    "can_delete": False,
                }
            ),
            self.expected_thread_data(
                {
                    "id": "test_thread_id_1",
                    "author": self.author.username,
                    "topic_id": "topic_y",
                    "group_id": self.cohort.id,
                    "group_name": self.cohort.name,
                    "type": "question",
                    "title": "Another Test Title",
                    "raw_body": "More content",
                    "preview_body": "More content",
                    "rendered_body": "<p>More content</p>",
                    "vote_count": 9,
                    "comment_count": 19,
                    "created_at": "2015-04-28T22:22:22Z",
                    "updated_at": "2015-04-28T00:33:33Z",
                    "comment_list_url": None,
                    "endorsed_comment_list_url": (
                        "http://testserver/api/discussion/v1/comments/?thread_id=test_thread_id_1&endorsed=True"
                    ),
                    "non_endorsed_comment_list_url": (
                        "http://testserver/api/discussion/v1/comments/?thread_id=test_thread_id_1&endorsed=False"
                    ),
                    "editable_fields": [
                        "abuse_flagged",
                        "copy_link",
                        "following",
                        "read",
                        "voted",
                    ],
                    "abuse_flagged_count": None,
                    "can_delete": False,
                }
            ),
        ]

        expected_result = make_paginated_api_response(
            results=expected_threads,
            count=2,
            num_pages=1,
            next_link=None,
            previous_link=None,
        )
        expected_result.update({"text_search_rewrite": None})
        assert self.get_thread_list(source_threads).data == expected_result

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
        )
    )
    @ddt.unpack
    def test_request_group(self, role_name, course_is_cohorted):
        cohort_course = CourseFactory.create(
            cohort_config={"cohorted": course_is_cohorted}
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=cohort_course.id)
        CohortFactory.create(course_id=cohort_course.id, users=[self.user])
        _assign_role_to_user(user=self.user, course_id=cohort_course.id, role=role_name)
        self.get_thread_list([], course=cohort_course)
        thread_func_params = self.get_mock_func_calls("get_user_threads")[-1][1]
        actual_has_group = "group_id" in thread_func_params
        expected_has_group = (
            course_is_cohorted and role_name in (
                FORUM_ROLE_STUDENT, FORUM_ROLE_COMMUNITY_TA, FORUM_ROLE_GROUP_MODERATOR
            )
        )
        assert actual_has_group == expected_has_group

    def test_pagination(self):
        # N.B. Empty thread list is not realistic but convenient for this test
        expected_result = make_paginated_api_response(
            results=[],
            count=0,
            num_pages=3,
            next_link="http://testserver/test_path?page=2",
            previous_link=None,
        )
        expected_result.update({"text_search_rewrite": None})
        assert self.get_thread_list([], page=1, num_pages=3).data == expected_result

        expected_result = make_paginated_api_response(
            results=[],
            count=0,
            num_pages=3,
            next_link="http://testserver/test_path?page=3",
            previous_link="http://testserver/test_path?page=1",
        )
        expected_result.update({"text_search_rewrite": None})
        assert self.get_thread_list([], page=2, num_pages=3).data == expected_result

        expected_result = make_paginated_api_response(
            results=[],
            count=0,
            num_pages=3,
            next_link=None,
            previous_link="http://testserver/test_path?page=2",
        )
        expected_result.update({"text_search_rewrite": None})
        assert self.get_thread_list([], page=3, num_pages=3).data == expected_result

        # Test page past the last one
        self.register_get_threads_response([], page=3, num_pages=3)
        with pytest.raises(PageNotFoundError):
            get_thread_list(self.request, self.course.id, page=4, page_size=10)

    @ddt.data(None, "rewritten search string")
    def test_text_search(self, text_search_rewrite):
        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": text_search_rewrite})
        self.register_get_threads_search_response([], text_search_rewrite, num_pages=0)
        assert (
            get_thread_list(
                self.request,
                self.course.id,
                page=1,
                page_size=10,
                text_search="test search string",
            ).data
            == expected_result
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            "text": "test search string",
        }
        self.check_mock_called_with(
            "search_threads",
            -1,
            **params,
        )

    def test_filter_threads_by_author(self):
        thread = make_minimal_cs_thread()
        self.register_get_threads_response([thread], page=1, num_pages=10)
        thread_results = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=10,
            author=self.user.username,
        ).data.get("results")
        assert len(thread_results) == 1

        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            "author_id": str(self.user.id),
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_filter_threads_by_missing_author(self):
        self.register_get_threads_response(
            [make_minimal_cs_thread()], page=1, num_pages=10
        )
        results = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=10,
            author="a fake and missing username",
        ).data.get("results")
        assert len(results) == 0

    @ddt.data("question", "discussion", None)
    def test_thread_type(self, thread_type):
        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})

        self.register_get_threads_response([], page=1, num_pages=0)
        assert (
            get_thread_list(
                self.request,
                self.course.id,
                page=1,
                page_size=10,
                thread_type=thread_type,
            ).data
            == expected_result
        )

        expected_last_query_params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            "thread_type": thread_type,
        }

        if thread_type is None:
            del expected_last_query_params["thread_type"]

        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **expected_last_query_params,
        )

    @ddt.data(True, False, None)
    def test_flagged(self, flagged_boolean):
        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})

        self.register_get_threads_response([], page=1, num_pages=0)
        assert (
            get_thread_list(
                self.request,
                self.course.id,
                page=1,
                page_size=10,
                flagged=flagged_boolean,
            ).data
            == expected_result
        )

        expected_last_query_params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            "flagged": flagged_boolean,
        }

        if flagged_boolean is None:
            del expected_last_query_params["flagged"]

        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **expected_last_query_params,
        )

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
    )
    def test_flagged_count(self, role):
        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})

        _assign_role_to_user(self.user, self.course.id, role=role)

        self.register_get_threads_response([], page=1, num_pages=0)
        get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=10,
            count_flagged=True,
        )

        expected_last_query_params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "count_flagged": True,
            "page": 1,
            "per_page": 10,
        }

        self.check_mock_called_with(
            "get_user_threads", -1, **expected_last_query_params
        )

    def test_flagged_count_denied(self):
        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})

        _assign_role_to_user(self.user, self.course.id, role=FORUM_ROLE_STUDENT)

        self.register_get_threads_response([], page=1, num_pages=0)

        with pytest.raises(PermissionDenied):
            get_thread_list(
                self.request,
                self.course.id,
                page=1,
                page_size=10,
                count_flagged=True,
            )

    def test_following(self):
        self.register_subscribed_threads_response(self.user, [], page=1, num_pages=1)
        result = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=11,
            following=True,
        ).data

        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=1, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})
        assert result == expected_result
        self.check_mock_called("get_user_subscriptions")

        params = {
            "course_id": str(self.course.id),
            "user_id": str(self.user.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 11,
        }
        self.check_mock_called_with("get_user_subscriptions", -1, **params)

    @ddt.data("unanswered", "unread")
    def test_view_query(self, query):
        self.register_get_threads_response([], page=1, num_pages=0)
        result = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=11,
            view=query,
        ).data

        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})
        assert result == expected_result
        self.check_mock_called("get_user_threads")
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 11,
            query: True,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    @ddt.data(
        ("last_activity_at", "activity"),
        ("comment_count", "comments"),
        ("vote_count", "votes"),
    )
    @ddt.unpack
    def test_order_by_query(self, http_query, cc_query):
        """
        Tests the order_by parameter

        Arguments:
            http_query (str): Query string sent in the http request
            cc_query (str): Query string used for the comments client service
        """
        self.register_get_threads_response([], page=1, num_pages=0)
        result = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=11,
            order_by=http_query,
        ).data

        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})
        assert result == expected_result
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": cc_query,
            "page": 1,
            "per_page": 11,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_order_direction(self):
        """
        Only "desc" is supported for order.  Also, since it is simply swallowed,
        it isn't included in the params.
        """
        self.register_get_threads_response([], page=1, num_pages=0)
        result = get_thread_list(
            self.request,
            self.course.id,
            page=1,
            page_size=11,
            order_direction="desc",
        ).data

        expected_result = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_result.update({"text_search_rewrite": None})
        assert result == expected_result
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 11,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_invalid_order_direction(self):
        """
        Test with invalid order_direction (e.g. "asc")
        """
        with pytest.raises(ValidationError) as assertion:  # noqa: PT012
            self.register_get_threads_response([], page=1, num_pages=0)
            get_thread_list(  # pylint: disable=expression-not-assigned  # noqa: B018
                self.request,
                self.course.id,
                page=1,
                page_size=11,
                order_direction="asc",
            ).data
        assert "order_direction" in assertion.value.message_dict


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetCommentListTest(
        SharedModuleStoreTestCase, ForumMockUtilsMixin
):
    """Test for get_comment_list"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.maxDiff = None  # pylint: disable=invalid-name
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/test_path")
        self.request.user = self.user
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.author = UserFactory.create()
        self.set_mock_return_value("get_course_id_by_thread", str(self.course.id))

    def make_minimal_cs_thread(self, overrides=None):
        """
        Create a thread with the given overrides, plus the course_id if not
        already in overrides.
        """
        overrides = overrides.copy() if overrides else {}
        overrides.setdefault("course_id", str(self.course.id))
        return make_minimal_cs_thread(overrides)

    def get_comment_list(
        self,
        thread,
        endorsed=None,
        page=1,
        page_size=1,
        merge_question_type_responses=False,
    ):
        """
        Register the appropriate comments service response, then call
        get_comment_list and return the result.
        """
        self.register_get_thread_response(thread)
        return get_comment_list(
            self.request,
            thread["id"],
            endorsed,
            page,
            page_size,
            merge_question_type_responses=merge_question_type_responses,
        )

    def test_nonexistent_thread(self):
        thread_id = "nonexistent_thread"
        self.register_get_thread_error_response(thread_id, 404)
        with pytest.raises(ThreadNotFoundError):
            get_comment_list(
                self.request, thread_id, endorsed=False, page=1, page_size=1
            )

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            self.get_comment_list(
                self.make_minimal_cs_thread(
                    {"course_id": "course-v1:non+existent+course"}
                )
            )

    def test_not_enrolled(self):
        self.request.user = UserFactory.create()
        with pytest.raises(CourseNotFoundError):
            self.get_comment_list(self.make_minimal_cs_thread())

    def test_discussions_disabled(self):
        disabled_course = _discussion_disabled_course_for(self.user)
        with pytest.raises(DiscussionDisabledError):
            self.get_comment_list(
                self.make_minimal_cs_thread(
                    overrides={"course_id": str(disabled_course.id)}
                )
            )

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False],
            [True, False],
            ["no_group", "match_group", "different_group"],
        )
    )
    @ddt.unpack
    def test_group_access(
        self, role_name, course_is_cohorted, topic_is_cohorted, thread_group_state
    ):
        cohort_course = CourseFactory.create(
            discussion_topics={"Test Topic": {"id": "test_topic"}},
            cohort_config={
                "cohorted": course_is_cohorted,
                "cohorted_discussions": ["test_topic"] if topic_is_cohorted else [],
            },
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=cohort_course.id)
        cohort = CohortFactory.create(course_id=cohort_course.id, users=[self.user])
        _assign_role_to_user(user=self.user, course_id=cohort_course.id, role=role_name)
        thread = self.make_minimal_cs_thread(
            {
                "course_id": str(cohort_course.id),
                "commentable_id": "test_topic",
                "group_id": (
                    None
                    if thread_group_state == "no_group"
                    else (
                        cohort.id
                        if thread_group_state == "match_group"
                        else cohort.id + 1
                    )
                ),
            }
        )
        expected_error = (
            role_name == FORUM_ROLE_STUDENT
            and course_is_cohorted
            and topic_is_cohorted
            and thread_group_state == "different_group"
        )
        try:
            self.get_comment_list(thread)
            assert not expected_error
        except ThreadNotFoundError:
            assert expected_error

    @ddt.data(True, False)
    def test_discussion_endorsed(self, endorsed_value):
        with pytest.raises(ValidationError) as assertion:
            self.get_comment_list(
                self.make_minimal_cs_thread({"thread_type": "discussion"}),
                endorsed=endorsed_value,
            )
        assert assertion.value.message_dict == {
            "endorsed": ["This field may not be specified for discussion threads."]
        }

    def test_question_without_endorsed(self):
        with pytest.raises(ValidationError) as assertion:
            self.get_comment_list(
                self.make_minimal_cs_thread({"thread_type": "question"}), endorsed=None
            )
        assert assertion.value.message_dict == {
            "endorsed": ["This field is required for question threads."]
        }

    def test_empty(self):
        discussion_thread = self.make_minimal_cs_thread(
            {"thread_type": "discussion", "children": [], "resp_total": 0}
        )
        assert self.get_comment_list(
            discussion_thread
        ).data == make_paginated_api_response(
            results=[], count=0, num_pages=1, next_link=None, previous_link=None
        )

        question_thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [],
                "non_endorsed_responses": [],
                "non_endorsed_resp_total": 0,
            }
        )
        assert self.get_comment_list(
            question_thread, endorsed=False
        ).data == make_paginated_api_response(
            results=[], count=0, num_pages=1, next_link=None, previous_link=None
        )
        assert self.get_comment_list(
            question_thread, endorsed=True
        ).data == make_paginated_api_response(
            results=[], count=0, num_pages=1, next_link=None, previous_link=None
        )

    def test_basic_query_params(self):
        self.get_comment_list(
            self.make_minimal_cs_thread(
                {
                    "children": [
                        make_minimal_cs_comment({"username": self.user.username})
                    ],
                    "resp_total": 71,
                }
            ),
            page=6,
            page_size=14,
        )
        params = {
            "thread_id": "dummy",
            "params": {
                "user_id": str(self.user.id),
                "mark_as_read": False,
                "recursive": False,
                "resp_skip": 70,
                "resp_limit": 14,
                "with_responses": True,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with("get_thread", -1, **params)

    def get_source_and_expected_comments(self):
        """
        Returns the source comments and expected comments for testing purposes.
        """
        source_comments = [
            {
                "type": "comment",
                "id": "test_comment_1",
                "thread_id": "test_thread",
                "user_id": str(self.author.id),
                "username": self.author.username,
                "anonymous": False,
                "anonymous_to_peers": False,
                "created_at": "2015-05-11T00:00:00Z",
                "updated_at": "2015-05-11T11:11:11Z",
                "body": "Test body",
                "endorsed": True,
                "abuse_flaggers": [],
                "votes": {"up_count": 4},
                "child_count": 0,
                "children": [],
            },
            {
                "type": "comment",
                "id": "test_comment_2",
                "thread_id": "test_thread",
                "user_id": str(self.author.id),
                "username": self.author.username,
                "anonymous": True,
                "anonymous_to_peers": False,
                "created_at": "2015-05-11T22:22:22Z",
                "updated_at": "2015-05-11T33:33:33Z",
                "body": "More content",
                "endorsed": False,
                "abuse_flaggers": [str(self.user.id)],
                "votes": {"up_count": 7},
                "child_count": 0,
                "children": [],
            },
        ]
        expected_comments = [
            {
                "id": "test_comment_1",
                "thread_id": "test_thread",
                "parent_id": None,
                "author": self.author.username,
                "author_label": None,
                "created_at": "2015-05-11T00:00:00Z",
                "updated_at": "2015-05-11T11:11:11Z",
                "raw_body": "Test body",
                "rendered_body": "<p>Test body</p>",
                "endorsed": True,
                "endorsed_by": None,
                "endorsed_by_label": None,
                "endorsed_at": None,
                "abuse_flagged": False,
                "abuse_flagged_any_user": None,
                "voted": False,
                "vote_count": 4,
                "editable_fields": ["abuse_flagged", "voted"],
                "child_count": 0,
                "children": [],
                "can_delete": False,
                "anonymous": False,
                "anonymous_to_peers": False,
                "last_edit": None,
                "edit_by_label": None,
                "profile_image": {
                    "has_image": False,
                    "image_url_full": "http://testserver/static/default_500.png",
                    "image_url_large": "http://testserver/static/default_120.png",
                    "image_url_medium": "http://testserver/static/default_50.png",
                    "image_url_small": "http://testserver/static/default_30.png",
                },
            },
            {
                "id": "test_comment_2",
                "thread_id": "test_thread",
                "parent_id": None,
                "author": None,
                "author_label": None,
                "created_at": "2015-05-11T22:22:22Z",
                "updated_at": "2015-05-11T33:33:33Z",
                "raw_body": "More content",
                "rendered_body": "<p>More content</p>",
                "endorsed": False,
                "endorsed_by": None,
                "endorsed_by_label": None,
                "endorsed_at": None,
                "abuse_flagged": True,
                "abuse_flagged_any_user": None,
                "voted": False,
                "vote_count": 7,
                "editable_fields": ["abuse_flagged", "voted"],
                "child_count": 0,
                "children": [],
                "can_delete": False,
                "anonymous": True,
                "anonymous_to_peers": False,
                "last_edit": None,
                "edit_by_label": None,
                "profile_image": {
                    "has_image": False,
                    "image_url_full": "http://testserver/static/default_500.png",
                    "image_url_large": "http://testserver/static/default_120.png",
                    "image_url_medium": "http://testserver/static/default_50.png",
                    "image_url_small": "http://testserver/static/default_30.png",
                },
            },
        ]
        return source_comments, expected_comments

    def test_discussion_content(self):
        source_comments, expected_comments = self.get_source_and_expected_comments()
        actual_comments = self.get_comment_list(
            self.make_minimal_cs_thread({"children": source_comments})
        ).data["results"]
        assert actual_comments == expected_comments

    def test_question_content_with_merge_question_type_responses(self):
        source_comments, expected_comments = self.get_source_and_expected_comments()
        actual_comments = self.get_comment_list(
            self.make_minimal_cs_thread(
                {
                    "thread_type": "question",
                    "children": source_comments,
                    "resp_total": len(source_comments),
                }
            ),
            merge_question_type_responses=True,
        ).data["results"]
        assert actual_comments == expected_comments

    def test_question_content_(self):
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment(
                        {"id": "endorsed_comment", "username": self.user.username}
                    )
                ],
                "non_endorsed_responses": [
                    make_minimal_cs_comment(
                        {"id": "non_endorsed_comment", "username": self.user.username}
                    )
                ],
                "non_endorsed_resp_total": 1,
            }
        )

        endorsed_actual = self.get_comment_list(thread, endorsed=True).data
        assert endorsed_actual["results"][0]["id"] == "endorsed_comment"

        non_endorsed_actual = self.get_comment_list(thread, endorsed=False).data
        assert non_endorsed_actual["results"][0]["id"] == "non_endorsed_comment"

    def test_endorsed_by_anonymity(self):
        """
        Ensure thread anonymity is properly considered in serializing
        endorsed_by.
        """
        thread = self.make_minimal_cs_thread(
            {
                "anonymous": True,
                "children": [
                    make_minimal_cs_comment(
                        {
                            "username": self.user.username,
                            "endorsement": {
                                "user_id": str(self.author.id),
                                "time": "2015-05-18T12:34:56Z",
                            },
                        }
                    )
                ],
            }
        )
        actual_comments = self.get_comment_list(thread).data["results"]
        assert actual_comments[0]["endorsed_by"] is None

    @ddt.data(
        ("discussion", None, "children", "resp_total", False),
        ("question", False, "non_endorsed_responses", "non_endorsed_resp_total", False),
        ("question", None, "children", "resp_total", True),
    )
    @ddt.unpack
    def test_cs_pagination(
        self,
        thread_type,
        endorsed_arg,
        response_field,
        response_total_field,
        merge_question_type_responses,
    ):
        """
        Test cases in which pagination is done by the comments service.

        thread_type is the type of thread (question or discussion).
        endorsed_arg is the value of the endorsed argument.
        repsonse_field is the field in which responses are returned for the
          given thread type.
        response_total_field is the field in which the total number of responses
          is returned for the given thread type.
        """
        # N.B. The mismatch between the number of children and the listed total
        # number of responses is unrealistic but convenient for this test
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": thread_type,
                response_field: [
                    make_minimal_cs_comment({"username": self.user.username})
                ],
                response_total_field: 5,
            }
        )

        # Only page
        actual = self.get_comment_list(
            thread,
            endorsed=endorsed_arg,
            page=1,
            page_size=5,
            merge_question_type_responses=merge_question_type_responses,
        ).data
        assert actual["pagination"]["next"] is None
        assert actual["pagination"]["previous"] is None

        # First page of many
        actual = self.get_comment_list(
            thread,
            endorsed=endorsed_arg,
            page=1,
            page_size=2,
            merge_question_type_responses=merge_question_type_responses,
        ).data
        assert actual["pagination"]["next"] == "http://testserver/test_path?page=2"
        assert actual["pagination"]["previous"] is None

        # Middle page of many
        actual = self.get_comment_list(
            thread,
            endorsed=endorsed_arg,
            page=2,
            page_size=2,
            merge_question_type_responses=merge_question_type_responses,
        ).data
        assert actual["pagination"]["next"] == "http://testserver/test_path?page=3"
        assert actual["pagination"]["previous"] == "http://testserver/test_path?page=1"

        # Last page of many
        actual = self.get_comment_list(
            thread,
            endorsed=endorsed_arg,
            page=3,
            page_size=2,
            merge_question_type_responses=merge_question_type_responses,
        ).data
        assert actual["pagination"]["next"] is None
        assert actual["pagination"]["previous"] == "http://testserver/test_path?page=2"

        # Page past the end
        thread = self.make_minimal_cs_thread(
            {"thread_type": thread_type, response_field: [], response_total_field: 5}
        )
        with pytest.raises(PageNotFoundError):
            self.get_comment_list(
                thread,
                endorsed=endorsed_arg,
                page=2,
                page_size=5,
                merge_question_type_responses=merge_question_type_responses,
            )

    def test_question_endorsed_pagination(self):
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment(
                        {"id": f"comment_{i}", "username": self.user.username}
                    )
                    for i in range(10)
                ],
            }
        )

        def assert_page_correct(
            page, page_size, expected_start, expected_stop, expected_next, expected_prev
        ):
            """
            Check that requesting the given page/page_size returns the expected
            output
            """
            actual = self.get_comment_list(
                thread, endorsed=True, page=page, page_size=page_size
            ).data
            result_ids = [result["id"] for result in actual["results"]]
            assert result_ids == [
                f"comment_{i}" for i in range(expected_start, expected_stop)
            ]
            assert actual["pagination"]["next"] == (
                f"http://testserver/test_path?page={expected_next}"
                if expected_next
                else None
            )
            assert actual["pagination"]["previous"] == (
                f"http://testserver/test_path?page={expected_prev}"
                if expected_prev
                else None
            )

        # Only page
        assert_page_correct(
            page=1,
            page_size=10,
            expected_start=0,
            expected_stop=10,
            expected_next=None,
            expected_prev=None,
        )

        # First page of many
        assert_page_correct(
            page=1,
            page_size=4,
            expected_start=0,
            expected_stop=4,
            expected_next=2,
            expected_prev=None,
        )

        # Middle page of many
        assert_page_correct(
            page=2,
            page_size=4,
            expected_start=4,
            expected_stop=8,
            expected_next=3,
            expected_prev=1,
        )

        # Last page of many
        assert_page_correct(
            page=3,
            page_size=4,
            expected_start=8,
            expected_stop=10,
            expected_next=None,
            expected_prev=2,
        )

        # Page past the end
        with pytest.raises(PageNotFoundError):
            self.get_comment_list(thread, endorsed=True, page=2, page_size=10)


@mock.patch("lms.djangoapps.discussion.rest_api.api._get_course", mock.Mock())
class CourseTopicsV2Test(ModuleStoreTestCase):
    """
    Tests for discussions topic API v2 code.
    """

    def setUp(self) -> None:
        super().setUp()
        self.course = CourseFactory.create(
            discussion_topics={
                f"Course Wide Topic {idx}": {"id": f"course-wide-topic-{idx}"}
                for idx in range(10)
            }
        )
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Week 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Lesson 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.verticals = [
            BlockFactory.create(
                parent_location=self.sequential.location,
                category="vertical",
                display_name=f"vertical-{idx}",
                start=datetime(2015, 4, 1, tzinfo=UTC),
            )
            for idx in range(10)
        ]
        staff_only_unit = BlockFactory.create(
            parent_location=self.sequential.location,
            category="vertical",
            display_name="staff-vertical-1",
            metadata=dict(visible_to_staff_only=True),
        )
        self.course_key = course_key = self.course.id
        self.config = DiscussionsConfiguration.objects.create(
            context_key=course_key, provider_type=Provider.OPEN_EDX
        )
        topic_links = []
        update_discussions_settings_from_course_task(str(self.course_key))
        self.staff_only_id = (
            DiscussionTopicLink.objects.filter(usage_key__in=[staff_only_unit.location])
            .values_list(
                "external_id",
                flat=True,
            )
            .get()
        )
        topic_id_query = DiscussionTopicLink.objects.filter(
            context_key=course_key
        ).values_list(
            "external_id",
            flat=True,
        )
        topic_ids = list(topic_id_query.order_by("ordering"))
        topic_ids.remove(self.staff_only_id)
        topic_ids_by_name = list(topic_id_query.order_by("title"))
        topic_ids_by_name.remove(self.staff_only_id)
        self.deleted_topic_ids = deleted_topic_ids = [
            f"disabled-topic-{idx}" for idx in range(10)
        ]
        for idx, topic_id in enumerate(deleted_topic_ids):
            usage_key = course_key.make_usage_key("vertical", topic_id)
            topic_links.append(
                DiscussionTopicLink(
                    context_key=course_key,
                    usage_key=usage_key,
                    title=f"Discussion on {topic_id}",
                    external_id=topic_id,
                    provider_id=Provider.OPEN_EDX,
                    ordering=idx,
                    enabled_in_context=False,
                )
            )
        DiscussionTopicLink.objects.bulk_create(topic_links)
        self.topic_ids = topic_ids
        self.topic_ids_by_name = topic_ids_by_name
        self.user = UserFactory.create()
        self.staff = AdminFactory.create()
        self.all_topic_ids = (
            set(topic_ids) | set(deleted_topic_ids) | {self.staff_only_id}
        )
        # Set up topic stats for all topics, but have one deleted topic
        # and one active topic return zero stats for testing.
        self.topic_stats = {
            **{
                topic_id: dict(
                    discussion=random.randint(0, 10), question=random.randint(0, 10)
                )
                for topic_id in self.all_topic_ids
            },
            deleted_topic_ids[0]: dict(discussion=0, question=0),
            self.topic_ids[0]: dict(discussion=0, question=0),
        }
        patcher = mock.patch(
            "lms.djangoapps.discussion.rest_api.api.get_course_commentable_counts",
            mock.Mock(return_value=self.topic_stats),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_response(self):
        """
        Test that the standard response contains the correct number of items
        """
        topics_list = get_course_topics_v2(course_key=self.course_key, user=self.user)
        assert {t["id"] for t in topics_list} == set(self.topic_ids)

    def test_filtering(self):
        """
        Tests that filtering by topic id works
        """
        filter_ids = set(random.sample(self.topic_ids, 4))
        topics_list = get_course_topics_v2(
            course_key=self.course_key, user=self.user, topic_ids=filter_ids
        )
        assert len(topics_list) == 4
        # All the filtered ids should be returned
        assert filter_ids == set(topic_data.get("id") for topic_data in topics_list)

    def test_sort_by_name(self):
        """
        Test sorting by name
        """
        topics_list = get_course_topics_v2(
            course_key=self.course_key,
            user=self.user,
            order_by=TopicOrdering.NAME,
        )
        returned_topic_ids = [topic_data.get("id") for topic_data in topics_list]
        assert returned_topic_ids == self.topic_ids_by_name

    def test_sort_by_structure(self):
        """
        Test sorting by course structure
        """
        topics_list = get_course_topics_v2(
            course_key=self.course_key,
            user=self.user,
            order_by=TopicOrdering.COURSE_STRUCTURE,
        )
        returned_topic_ids = [topic_data.get("id") for topic_data in topics_list]
        # The topics are already sorted in their simulated course order
        sorted_topic_ids = self.topic_ids
        assert returned_topic_ids == sorted_topic_ids

    def test_sort_by_activity(self):
        """
        Test sorting by activity
        """
        topics_list = get_course_topics_v2(
            course_key=self.course_key,
            user=self.user,
            order_by=TopicOrdering.ACTIVITY,
        )
        returned_topic_ids = [topic_data.get("id") for topic_data in topics_list]
        # The topics are already sorted in their simulated course order
        sorted_topic_ids = sorted(
            self.topic_ids,
            key=lambda tid: sum(self.topic_stats.get(tid, {}).values()),
            reverse=True,
        )
        assert returned_topic_ids == sorted_topic_ids

    def test_other_providers_ordering_error(self):
        """
        Test that activity sorting raises an error for other providers
        """
        self.config.provider_type = "other"
        self.config.save()
        with pytest.raises(ValidationError):
            get_course_topics_v2(
                course_key=self.course_key,
                user=self.user,
                order_by=TopicOrdering.ACTIVITY,
            )


@mock.patch.dict("django.conf.settings.FEATURES", {"DISABLE_START_DATES": False})
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetCourseTopicsTest(ForumMockUtilsMixin, UrlResetMixin, ModuleStoreTestCase):
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

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

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


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_settings(DISCUSSION_MODERATION_EDIT_REASON_CODES={"test-edit-reason": "Test Edit Reason"})
@override_settings(DISCUSSION_MODERATION_CLOSE_REASON_CODES={"test-close-reason": "Test Close Reason"})
@ddt.ddt
class GetCourseTest(UrlResetMixin, SharedModuleStoreTestCase):
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
            'has_moderation_privileges': False,
            "is_course_staff": False,
            "is_course_admin": False,
            'is_group_ta': False,
            'is_user_admin': False,
            'user_roles': {'Student'},
            'edit_reasons': [{'code': 'test-edit-reason', 'label': 'Test Edit Reason'}],
            'post_close_reasons': [{'code': 'test-close-reason', 'label': 'Test Close Reason'}],
            'show_discussions': True,
            'has_bulk_delete_privileges': False,
            'is_notify_all_learners_enabled': False,
            'captcha_settings': {'enabled': False, 'site_key': None},
            'is_email_verified': True,
            'only_verified_users_can_post': False,
            'content_creation_rate_limited': False,
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
class GetCourseTestBlackouts(UrlResetMixin, ModuleStoreTestCase):
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
