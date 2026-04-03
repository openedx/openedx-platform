# pylint: skip-file
"""Tests for django comment client views."""

import json
import logging
from contextlib import contextmanager
from unittest import mock
from unittest.mock import ANY, Mock, patch

import ddt
import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test.client import RequestFactory
from django.urls import reverse
from eventtracking.processors.exceptions import EventEmissionExit
from opaque_keys.edx.keys import CourseKey
from openedx_events.learning.signals import (
    FORUM_RESPONSE_COMMENT_CREATED,
    FORUM_THREAD_CREATED,
    FORUM_THREAD_RESPONSE_CREATED
)

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.roles import CourseStaffRole, UserBasedRole
from common.djangoapps.student.tests.factories import CourseAccessRoleFactory, CourseEnrollmentFactory, UserFactory
from common.djangoapps.track.middleware import TrackMiddleware
from common.djangoapps.track.views import segmentio
from common.djangoapps.track.views.tests.base import SEGMENTIO_TEST_USER_ID, SegmentIOTrackingTestCaseBase
from common.djangoapps.util.testing import UrlResetMixin
from common.test.utils import MockSignalHandlerMixin, assert_dict_contains_subset, disable_signal
from lms.djangoapps.discussion.django_comment_client.base import views
from lms.djangoapps.discussion.django_comment_client.tests.group_id import (
    CohortedTopicGroupIdTestMixinV2,
    GroupIdAssertionMixinV2,
    NonCohortedTopicGroupIdTestMixinV2
)
from lms.djangoapps.discussion.django_comment_client.tests.mixins import MockForumApiMixin
from lms.djangoapps.discussion.django_comment_client.tests.unicode import UnicodeTestMixin
from lms.djangoapps.discussion.django_comment_client.tests.utils import CohortedTestCase
from lms.djangoapps.discussion.tests.utils import make_minimal_cs_comment, make_minimal_cs_thread
from lms.djangoapps.teams.tests.factories import CourseTeamFactory, CourseTeamMembershipFactory
from openedx.core.djangoapps.course_groups.cohorts import set_course_cohorted
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.comment_client import Thread
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_STUDENT,
    CourseDiscussionSettings,
    Role,
    assign_role
)
from openedx.core.djangoapps.django_comment_common.utils import ThreadContext, seed_permissions_roles
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase
)
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, check_mongo_calls

from .event_transformers import ForumThreadViewedEventTransformer

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES


class CreateThreadGroupIdTestCase(
    MockForumApiMixin,
    CohortedTestCase,
    CohortedTopicGroupIdTestMixinV2,
    NonCohortedTopicGroupIdTestMixinV2,
):
    function_name = "create_thread"

    @classmethod
    def setUpClass(cls):
        """Set up class and forum mock."""
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    def call_view(self, commentable_id, user, group_id, pass_group_id=True):
        self.set_mock_return_value("get_thread", {})
        self.set_mock_return_value("create_thread", {})
        request_data = {"body": "body", "title": "title", "thread_type": "discussion"}
        if pass_group_id:
            request_data["group_id"] = group_id
        request = RequestFactory().post("dummy_url", request_data)
        request.user = user
        request.view_name = "create_thread"

        return views.create_thread(
            request, course_id=str(self.course.id), commentable_id=commentable_id
        )

    def test_group_info_in_response(self):
        response = self.call_view("cohorted_topic", self.student, "")
        self._assert_json_response_contains_group_info(response)


@disable_signal(views, "thread_edited")
@disable_signal(views, "thread_voted")
@disable_signal(views, "thread_deleted")
class ThreadActionGroupIdTestCase(
    CohortedTestCase, GroupIdAssertionMixinV2, MockForumApiMixin
):
    """Test case for thread actions with group ID assertions."""

    @classmethod
    def setUpClass(cls):
        """Set up class and forum mock."""
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    def call_view(
        self, view_name, mock_function, user=None, post_params=None, view_args=None
    ):
        """Call a view with the given parameters."""
        thread_response = make_minimal_cs_thread(
            {"user_id": str(self.student.id), "group_id": self.student_cohort.id}
        )

        self.set_mock_return_value("get_course_id_by_thread", str(self.course.id))
        self.set_mock_return_value("get_thread", thread_response)
        self.set_mock_return_value(mock_function, thread_response)

        request = RequestFactory().post("dummy_url", post_params or {})
        request.user = user or self.student
        request.view_name = view_name

        return getattr(views, view_name)(
            request,
            course_id=str(self.course.id),
            thread_id="dummy",
            **(view_args or {}),
        )

    def test_flag(self):
        with mock.patch(
            "openedx.core.djangoapps.django_comment_common.signals.thread_flagged.send"
        ) as signal_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call_view("flag_abuse_for_thread", "update_thread_flag")
            self._assert_json_response_contains_group_info(response)
            self.assertEqual(signal_mock.call_count, 1)
        response = self.call_view("un_flag_abuse_for_thread", "update_thread_flag")
        self._assert_json_response_contains_group_info(response)

    def test_pin_thread(self):
        """Test pinning a thread."""
        response = self.call_view("pin_thread", "pin_thread", user=self.moderator)
        assert response.status_code == 200
        self._assert_json_response_contains_group_info(response)

        response = self.call_view("un_pin_thread", "unpin_thread", user=self.moderator)
        assert response.status_code == 200
        self._assert_json_response_contains_group_info(response)

    def test_vote(self):
        response = self.call_view(
            "vote_for_thread", "update_thread_votes", view_args={"value": "up"}
        )
        self._assert_json_response_contains_group_info(response)
        response = self.call_view("undo_vote_for_thread", "delete_thread_vote")
        self._assert_json_response_contains_group_info(response)

    def test_update(self):
        response = self.call_view(
            "update_thread",
            "update_thread",
            post_params={"body": "body", "title": "title"},
        )
        self._assert_json_response_contains_group_info(response)

    def test_delete(self):
        response = self.call_view("delete_thread", "delete_thread")
        self._assert_json_response_contains_group_info(response)

    def test_openclose(self):
        response = self.call_view(
            "openclose_thread", "update_thread", user=self.moderator
        )
        self._assert_json_response_contains_group_info(response, lambda d: d["content"])


class ViewsTestCaseMixin:

    def set_up_course(self, block_count=0):
        """
        Creates a course, optionally with block_count discussion blocks, and
        a user with appropriate permissions.
        """

        # create a course
        self.course = CourseFactory.create(
            org="MITx",
            course="999",
            discussion_topics={"Some Topic": {"id": "some_topic"}},
            display_name="Robot Super Course",
        )
        self.course_id = self.course.id

        # add some discussion blocks
        for i in range(block_count):
            BlockFactory.create(
                parent_location=self.course.location,
                category="discussion",
                discussion_id=f"id_module_{i}",
                discussion_category=f"Category {i}",
                discussion_target=f"Discussion {i}",
            )

        # seed the forums permissions and roles
        call_command("seed_permissions_roles", str(self.course_id))

        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch("common.djangoapps.student.models.user.cc.User.save"):
            uname = "student"
            email = "student@edx.org"
            self.password = "Password1234"

            # Create the user and make them active so we can log them in.
            self.student = UserFactory.create(
                username=uname, email=email, password=self.password
            )
            self.student.is_active = True
            self.student.save()

            # Add a discussion moderator
            self.moderator = UserFactory.create(password=self.password)

            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student, course_id=self.course_id)

            # Enroll the moderator and give them the appropriate roles
            CourseEnrollmentFactory(user=self.moderator, course_id=self.course.id)
            self.moderator.roles.add(
                Role.objects.get(name="Moderator", course_id=self.course.id)
            )

            assert self.client.login(username="student", password=self.password)

    def _setup_mock_request(self, mock_function, include_depth=False):
        """
        Ensure that mock_request returns the data necessary to make views
        function correctly
        """
        data = {
            "user_id": str(self.student.id),
            "closed": False,
            "commentable_id": "non_team_dummy_id",
            "thread_id": "dummy",
            "thread_type": "discussion",
        }
        if include_depth:
            data["depth"] = 0
        self.set_mock_return_value(mock_function, data)

    def create_thread_helper(self, extra_request_data=None, extra_response_data=None):
        """
        Issues a request to create a thread and verifies the result.
        """
        thread_data = {
            "thread_type": "discussion",
            "title": "Hello",
            "body": "this is a post",
            "course_id": "MITx/999/Robot_Super_Course",
            "anonymous": False,
            "anonymous_to_peers": False,
            "commentable_id": "i4x-MITx-999-course-Robot_Super_Course",
            "created_at": "2013-05-10T18:53:43Z",
            "updated_at": "2013-05-10T18:53:43Z",
            "at_position_list": [],
            "closed": False,
            "id": "518d4237b023791dca00000d",
            "user_id": "1",
            "username": "robot",
            "votes": {"count": 0, "up_count": 0, "down_count": 0, "point": 0},
            "abuse_flaggers": [],
            "type": "thread",
            "group_id": None,
            "pinned": False,
            "endorsed": False,
            "unread_comments_count": 0,
            "read": False,
            "comments_count": 0,
        }
        self.set_mock_return_value("create_thread", thread_data)
        self.set_mock_return_value("get_thread", thread_data)

        url = reverse(
            "create_thread",
            kwargs={
                "commentable_id": "i4x-MITx-999-course-Robot_Super_Course",
                "course_id": str(self.course_id),
            },
        )

        thread = {
            "thread_type": "discussion",
            "body": "this is a post",
            "anonymous_to_peers": False,
            "auto_subscribe": False,
            "anonymous": False,
            "title": "Hello",
        }
        if extra_request_data:
            thread.update(extra_request_data)

        response = self.client.post(url, data=thread)
        self.check_mock_called("create_thread")
        expected_data = {
            "thread_type": "discussion",
            "body": "this is a post",
            "context": ThreadContext.COURSE,
            "anonymous_to_peers": False,
            "user_id": "1",
            "title": "Hello",
            "commentable_id": "i4x-MITx-999-course-Robot_Super_Course",
            "anonymous": False,
            "course_id": str(self.course_id),
        }
        if extra_response_data:
            expected_data.update(extra_response_data)
        self.check_mock_called_with("create_thread", -1, **expected_data)
        assert response.status_code == 200

    def update_thread_helper(self):
        """
        Issues a request to update a thread and verifies the result.
        """
        self._setup_mock_request("get_thread")
        self._setup_mock_request("update_thread")

        # Mock out saving in order to test that content is correctly
        # updated. Otherwise, the call to thread.save() receives the
        # same mocked request data that the original call to retrieve
        # the thread did, overwriting any changes.
        with patch.object(Thread, "save"):
            response = self.client.post(
                reverse(
                    "update_thread",
                    kwargs={"thread_id": "dummy", "course_id": str(self.course_id)},
                ),
                data={"body": "foo", "title": "foo", "commentable_id": "some_topic"},
            )
        assert response.status_code == 200
        data = json.loads(response.content.decode("utf-8"))
        assert data["body"] == "foo"
        assert data["title"] == "foo"
        assert data["commentable_id"] == "some_topic"


@ddt.ddt
@disable_signal(views, "comment_flagged")
@disable_signal(views, "thread_flagged")
class ViewsTestCase(
    MockForumApiMixin,
    UrlResetMixin,
    SharedModuleStoreTestCase,
    ViewsTestCaseMixin,
    MockSignalHandlerMixin,
):

    @classmethod
    def setUpClass(cls):
        # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()
        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create(
                org="MITx",
                course="999",
                discussion_topics={"Some Topic": {"id": "some_topic"}},
                display_name="Robot Super Course",
            )

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.course_id = cls.course.id

        # seed the forums permissions and roles
        call_command("seed_permissions_roles", str(cls.course_id))

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        # Patching the ENABLE_DISCUSSION_SERVICE value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)
        super().setUp()
        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch("common.djangoapps.student.models.user.cc.User.save"):
            uname = "student"
            email = "student@edx.org"
            self.password = "Password1234"

            # Create the user and make them active so we can log them in.
            self.student = UserFactory.create(
                username=uname, email=email, password=self.password
            )
            self.student.is_active = True
            self.student.save()

            # Add a discussion moderator
            self.moderator = UserFactory.create(password=self.password)

            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student, course_id=self.course_id)

            # Enroll the moderator and give them the appropriate roles
            CourseEnrollmentFactory(user=self.moderator, course_id=self.course.id)
            self.moderator.roles.add(
                Role.objects.get(name="Moderator", course_id=self.course.id)
            )

            assert self.client.login(username="student", password=self.password)

        self.set_mock_return_value("get_course_id_by_thread", str(self.course.id))
        self.set_mock_return_value("get_course_id_by_comment", str(self.course.id))

    @contextmanager
    def assert_discussion_signals(self, signal, user=None):
        if user is None:
            user = self.student
        # Use captureOnCommitCallbacks to execute on_commit callbacks during tests,
        # since signals are now deferred until after transaction commit.
        # Order matters: assert_signal_sent must be outer context so the signal
        # fires (via captureOnCommitCallbacks) before the assertion check.
        with self.assert_signal_sent(
            views, signal, sender=None, user=user, exclude_args=("post",)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                yield

    def test_create_thread(self):
        with self.assert_discussion_signals("thread_created"):
            self.create_thread_helper()

    def test_create_thread_standalone(self):
        team = CourseTeamFactory.create(
            name="A Team",
            course_id=self.course_id,
            topic_id="topic_id",
            discussion_topic_id="i4x-MITx-999-course-Robot_Super_Course",
        )

        # Add the student to the team so they can post to the commentable.
        team.add_user(self.student)

        # create_thread_helper verifies that extra data are passed through to the comments service
        self.create_thread_helper(
            extra_response_data={"context": ThreadContext.STANDALONE}
        )

    def test_delete_thread(self):
        mocked_data = {
            "user_id": str(self.student.id),
            "closed": False,
            "body": "test body",
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("delete_thread", mocked_data)
        test_thread_id = "test_thread_id"
        request = RequestFactory().post("dummy_url", {"id": test_thread_id})
        request.user = self.student
        request.view_name = "delete_thread"
        with self.assert_discussion_signals("thread_deleted"):
            response = views.delete_thread(
                request, course_id=str(self.course.id), thread_id=test_thread_id
            )
        assert response.status_code == 200
        self.check_mock_called("delete_thread")

    def test_delete_comment(self):
        mocked_data = {
            "user_id": str(self.student.id),
            "closed": False,
            "body": "test body",
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("get_parent_comment", mocked_data)
        self.set_mock_return_value("delete_comment", mocked_data)
        test_comment_id = "test_comment_id"
        request = RequestFactory().post("dummy_url", {"id": test_comment_id})
        request.user = self.student
        request.view_name = "delete_comment"
        with self.assert_discussion_signals("comment_deleted"):
            response = views.delete_comment(
                request, course_id=str(self.course.id), comment_id=test_comment_id
            )
        assert response.status_code == 200
        self.check_mock_called("delete_comment")

    def _test_request_error(self, view_name, view_kwargs, data, mock_functions):
        """
        Submit a request against the given view with the given data and ensure
        that the result is a 400 error and that no data was posted using
        mock_request
        """
        mock_functions = mock_functions or []
        for mock_func in mock_functions:
            self._setup_mock_request(
                mock_func, include_depth=(view_name == "create_sub_comment")
            )

        response = self.client.post(reverse(view_name, kwargs=view_kwargs), data=data)
        assert response.status_code == 400

    def test_create_thread_no_title(self):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo"},
            ["create_thread"],
        )

    def test_create_thread_empty_title(self):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": " "},
            ["create_thread"],
        )

    def test_create_thread_no_body(self):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"title": "foo"},
            ["create_thread"],
        )

    def test_create_thread_empty_body(self):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": " ", "title": "foo"},
            ["create_thread"],
        )

    def test_update_thread_no_title(self):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo"},
            ["get_thread", "update_thread"],
        )

    def test_update_thread_empty_title(self):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": " "},
            ["get_thread", "update_thread"],
        )

    def test_update_thread_no_body(self):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"title": "foo"},
            ["get_thread", "update_thread"],
        )

    def test_update_thread_empty_body(self):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": " ", "title": "foo"},
            ["get_thread", "update_thread"],
        )

    def test_update_thread_course_topic(self):
        with self.assert_discussion_signals("thread_edited"):
            self.update_thread_helper()

    @patch(
        "lms.djangoapps.discussion.django_comment_client.utils.get_discussion_categories_ids",
        return_value=["test_commentable"],
    )
    def test_update_thread_wrong_commentable_id(self, mock_get_discussion_id_map):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": "foo", "commentable_id": "wrong_commentable"},
            ["get_thread", "update_thread"],
        )

    def test_create_comment(self):
        self._setup_mock_request("get_thread")
        self._setup_mock_request("create_parent_comment")
        with self.assert_discussion_signals("comment_created"):
            response = self.client.post(
                reverse(
                    "create_comment",
                    kwargs={"course_id": str(self.course_id), "thread_id": "dummy"},
                ),
                data={"body": "body"},
            )
        assert response.status_code == 200

    def test_create_comment_no_body(self):
        self._test_request_error(
            "create_comment",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {},
            ["get_thread", "create_parent_comment"],
        )

    def test_create_comment_empty_body(self):
        self._test_request_error(
            "create_comment",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "},
            ["get_thread", "create_parent_comment"],
        )

    def test_create_sub_comment_no_body(self):
        self._test_request_error(
            "create_sub_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {},
            ["get_thread", "get_parent_comment"],
        )

    def test_create_sub_comment_empty_body(self):
        self._test_request_error(
            "create_sub_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "},
            ["get_thread", "get_parent_comment", "create_child_comment"],
        )

    def test_update_comment_no_body(self):
        self._test_request_error(
            "update_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {},
            ["get_thread", "get_parent_comment", "update_comment"],
        )

    def test_update_comment_empty_body(self):
        self._test_request_error(
            "update_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "},
            ["get_thread", "get_parent_comment", "update_comment"],
        )

    def test_update_comment_basic(self):
        self._setup_mock_request("get_parent_comment")
        self._setup_mock_request("update_comment")
        comment_id = "test_comment_id"
        updated_body = "updated body"
        with self.assert_discussion_signals("comment_edited"):
            response = self.client.post(
                reverse(
                    "update_comment",
                    kwargs={"course_id": str(self.course_id), "comment_id": comment_id},
                ),
                data={"body": updated_body},
            )
        assert response.status_code == 200
        params = {
            "comment_id": comment_id,
            "body": updated_body,
            "course_id": str(self.course_id),
        }
        self.check_mock_called_with("update_comment", -1, **params)

    def test_endorse_comment(self):
        self._setup_mock_request("get_thread")
        self._setup_mock_request("get_parent_comment")
        self._setup_mock_request("update_comment")
        self.client.login(username=self.moderator.username, password=self.password)
        with self.assert_discussion_signals("comment_endorsed", user=self.moderator):
            response = self.client.post(
                reverse(
                    "endorse_comment",
                    kwargs={"comment_id": "dummy", "course_id": str(self.course_id)},
                )
            )
        assert response.status_code == 200

    def test_flag_thread_open(self):
        self.flag_thread(False)

    def test_flag_thread_close(self):
        self.flag_thread(True)

    def flag_thread(self, is_closed):
        thread_data = make_minimal_cs_thread(
            {
                "id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
                "closed": is_closed,
                "user_id": "1",
                "username": "robot",
                "abuse_flaggers": [1],
            }
        )
        self.set_mock_return_value("get_thread", thread_data)
        self.set_mock_return_value("update_thread_flag", thread_data)
        url = reverse(
            "flag_abuse_for_thread",
            kwargs={
                "thread_id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
            },
        )
        response = self.client.post(url)
        self.check_mock_called("update_thread_flag")

        self.check_mock_called_with(
            "get_thread",
            0,
            thread_id="518d4237b023791dca00000d",
            params={
                "mark_as_read": True,
                "with_responses": False,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "update_thread_flag",
            0,
            thread_id="518d4237b023791dca00000d",
            action="flag",
            user_id=ANY,
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "get_thread",
            1,
            thread_id="518d4237b023791dca00000d",
            params={
                "mark_as_read": True,
                "with_responses": False,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            course_id=str(self.course_id),
        )

        assert response.status_code == 200

    def test_un_flag_thread_open(self):
        self.un_flag_thread(False)

    def test_un_flag_thread_close(self):
        self.un_flag_thread(True)

    def un_flag_thread(self, is_closed):
        thread_data = make_minimal_cs_thread(
            {
                "id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
                "closed": is_closed,
                "user_id": "1",
                "username": "robot",
                "abuse_flaggers": [1],
            }
        )

        self.set_mock_return_value("get_thread", thread_data)
        self.set_mock_return_value("update_thread_flag", thread_data)
        url = reverse(
            "un_flag_abuse_for_thread",
            kwargs={
                "thread_id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
            },
        )
        response = self.client.post(url)
        self.check_mock_called("update_thread_flag")

        self.check_mock_called_with(
            "get_thread",
            0,
            thread_id="518d4237b023791dca00000d",
            params={
                "mark_as_read": True,
                "with_responses": False,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "update_thread_flag",
            0,
            thread_id="518d4237b023791dca00000d",
            action="unflag",
            user_id=ANY,
            update_all=False,
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "get_thread",
            1,
            thread_id="518d4237b023791dca00000d",
            params={
                "mark_as_read": True,
                "with_responses": False,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            course_id=str(self.course_id),
        )

        assert response.status_code == 200

    def test_flag_comment_open(self):
        self.flag_comment(False)

    def test_flag_comment_close(self):
        self.flag_comment(True)

    def flag_comment(self, is_closed):
        comment_data = make_minimal_cs_comment(
            {
                "id": "518d4237b023791dca00000d",
                "body": "this is a comment",
                "course_id": str(self.course_id),
                "closed": is_closed,
                "user_id": "1",
                "username": "robot",
                "abuse_flaggers": [1],
            }
        )

        self.set_mock_return_value("get_parent_comment", comment_data)
        self.set_mock_return_value("update_comment_flag", comment_data)
        url = reverse(
            "flag_abuse_for_comment",
            kwargs={
                "comment_id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
            },
        )

        response = self.client.post(url)
        self.check_mock_called("update_thread_flag")

        self.check_mock_called_with(
            "get_parent_comment",
            0,
            comment_id="518d4237b023791dca00000d",
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "update_comment_flag",
            0,
            comment_id="518d4237b023791dca00000d",
            action="flag",
            user_id=ANY,
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "get_parent_comment",
            1,
            comment_id="518d4237b023791dca00000d",
            course_id=str(self.course_id),
        )

        assert response.status_code == 200

    def test_un_flag_comment_open(self):
        self.un_flag_comment(False)

    def test_un_flag_comment_close(self):
        self.un_flag_comment(True)

    def un_flag_comment(self, is_closed):
        comment_data = make_minimal_cs_comment(
            {
                "id": "518d4237b023791dca00000d",
                "body": "this is a comment",
                "course_id": str(self.course_id),
                "closed": is_closed,
                "user_id": "1",
                "username": "robot",
                "abuse_flaggers": [],
            }
        )

        self.set_mock_return_value("get_parent_comment", comment_data)
        self.set_mock_return_value("update_comment_flag", comment_data)
        url = reverse(
            "un_flag_abuse_for_comment",
            kwargs={
                "comment_id": "518d4237b023791dca00000d",
                "course_id": str(self.course_id),
            },
        )

        response = self.client.post(url)
        self.check_mock_called("update_thread_flag")

        self.check_mock_called_with(
            "get_parent_comment",
            0,
            comment_id="518d4237b023791dca00000d",
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "update_comment_flag",
            0,
            comment_id="518d4237b023791dca00000d",
            action="unflag",
            update_all=False,
            user_id=ANY,
            course_id=str(self.course_id),
        )

        self.check_mock_called_with(
            "get_parent_comment",
            1,
            comment_id="518d4237b023791dca00000d",
            course_id=str(self.course_id),
        )

        assert response.status_code == 200

    @ddt.data(
        ("upvote_thread", "update_thread_votes", "thread_id", "thread_voted"),
        ("upvote_comment", "update_comment_votes", "comment_id", "comment_voted"),
        ("downvote_thread", "update_thread_votes", "thread_id", "thread_voted"),
        ("downvote_comment", "update_comment_votes", "comment_id", "comment_voted"),
    )
    @ddt.unpack
    def test_voting(self, view_name, function_name, item_id, signal):
        self._setup_mock_request("get_thread")
        self._setup_mock_request("get_parent_comment")
        self._setup_mock_request(function_name)
        with self.assert_discussion_signals(signal):
            response = self.client.post(
                reverse(
                    view_name,
                    kwargs={item_id: "dummy", "course_id": str(self.course_id)},
                )
            )
        assert response.status_code == 200

    @ddt.data(
        ("follow_thread", "thread_followed"),
        ("unfollow_thread", "thread_unfollowed"),
    )
    @ddt.unpack
    def test_follow_unfollow_thread_signals(self, view_name, signal):
        self._setup_mock_request("get_thread")
        with self.assert_discussion_signals(signal):
            response = self.client.post(
                reverse(
                    view_name,
                    kwargs={
                        "course_id": str(self.course_id),
                        "thread_id": "i4x-MITx-999-course-Robot_Super_Course",
                    },
                )
            )
        assert response.status_code == 200


@disable_signal(views, "comment_endorsed")
class ViewPermissionsTestCase(
    UrlResetMixin,
    SharedModuleStoreTestCase,
    MockForumApiMixin,
):
    """Test case for view permissions."""

    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        """Set up class and forum mock."""
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)

        cls.password = "test password"
        cls.student = UserFactory.create(password=cls.password)
        cls.moderator = UserFactory.create(password=cls.password)

        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)
        CourseEnrollmentFactory(user=cls.moderator, course_id=cls.course.id)

        cls.moderator.roles.add(
            Role.objects.get(name="Moderator", course_id=cls.course.id)
        )

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        """Set up the test case."""
        super().setUp()

        # Set return values dynamically using the mixin method
        self.set_mock_return_value("get_course_id_by_comment", self.course.id)
        self.set_mock_return_value("get_course_id_by_thread", self.course.id)
        self.set_mock_return_value("get_thread", {})
        self.set_mock_return_value("pin_thread", {})
        self.set_mock_return_value("unpin_thread", {})

    def _set_mock_data_thread_and_comment(self, thread_data, comment_data):
        """Set up mocked data for threads and comments"""
        self.set_mock_return_value("get_thread", thread_data)
        self.set_mock_return_value("get_parent_comment", comment_data)
        self.set_mock_return_value("update_thread", thread_data)
        self.set_mock_return_value("update_comment", comment_data)

    def test_pin_thread_as_student(self):
        """Test pinning a thread as a student."""
        self.client.login(username=self.student.username, password=self.password)
        response = self.client.post(
            reverse(
                "pin_thread",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            )
        )
        assert response.status_code == 401

    def test_pin_thread_as_moderator(self):
        """Test pinning a thread as a moderator."""
        self.client.login(username=self.moderator.username, password=self.password)
        response = self.client.post(
            reverse(
                "pin_thread",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            )
        )
        assert response.status_code == 200

    def test_un_pin_thread_as_student(self):
        """Test unpinning a thread as a student."""
        self.client.login(username=self.student.username, password=self.password)
        response = self.client.post(
            reverse(
                "un_pin_thread",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            )
        )
        assert response.status_code == 401

    def test_un_pin_thread_as_moderator(self):
        """Test unpinning a thread as a moderator."""
        self.client.login(username=self.moderator.username, password=self.password)
        response = self.client.post(
            reverse(
                "un_pin_thread",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            )
        )
        assert response.status_code == 200

    def test_endorse_response_as_staff(self):
        thread_data = {
            "type": "thread",
            "thread_type": "question",
            "user_id": str(self.student.id),
            "commentable_id": "course",
        }
        comment_data = {"type": "comment", "thread_id": "dummy"}
        self._set_mock_data_thread_and_comment(thread_data, comment_data)

        self.client.login(username=self.moderator.username, password=self.password)
        response = self.client.post(
            reverse(
                "endorse_comment",
                kwargs={"course_id": str(self.course.id), "comment_id": "dummy"},
            )
        )
        assert response.status_code == 200

    def test_endorse_response_as_student(self):
        thread_data = {
            "type": "thread",
            "thread_type": "question",
            "user_id": str(self.moderator.id),
            "commentable_id": "course",
        }
        comment_data = {"type": "comment", "thread_id": "dummy"}
        self._set_mock_data_thread_and_comment(thread_data, comment_data)

        self.client.login(username=self.student.username, password=self.password)
        response = self.client.post(
            reverse(
                "endorse_comment",
                kwargs={"course_id": str(self.course.id), "comment_id": "dummy"},
            )
        )
        assert response.status_code == 401

    def test_endorse_response_as_student_question_author(self):
        thread_data = {
            "type": "thread",
            "thread_type": "question",
            "user_id": str(self.student.id),
            "commentable_id": "course",
        }
        comment_data = {"type": "comment", "thread_id": "dummy"}
        self._set_mock_data_thread_and_comment(thread_data, comment_data)

        self.client.login(username=self.student.username, password=self.password)
        response = self.client.post(
            reverse(
                "endorse_comment",
                kwargs={"course_id": str(self.course.id), "comment_id": "dummy"},
            )
        )
        assert response.status_code == 200


class CommentActionTestCase(CohortedTestCase, MockForumApiMixin):
    """Test case for thread actions with group ID assertions."""

    @classmethod
    def setUpClass(cls):
        """Set up class and forum mock."""
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    def call_view(
        self, view_name, mock_function, user=None, post_params=None, view_args=None
    ):
        """Call a view with the given parameters."""
        comment_response = make_minimal_cs_comment(
            {"user_id": str(self.student.id), "group_id": self.student_cohort.id}
        )

        self.set_mock_return_value("get_course_id_by_comment", str(self.course.id))
        self.set_mock_return_value("get_parent_comment", comment_response)
        self.set_mock_return_value(mock_function, comment_response)

        request = RequestFactory().post("dummy_url", post_params or {})
        request.user = user or self.student
        request.view_name = view_name

        return getattr(views, view_name)(
            request,
            course_id=str(self.course.id),
            comment_id="dummy",
            **(view_args or {}),
        )

    def test_flag(self):
        with mock.patch(
            "openedx.core.djangoapps.django_comment_common.signals.comment_flagged.send"
        ) as signal_mock:
            with self.captureOnCommitCallbacks(execute=True):
                self.call_view("flag_abuse_for_comment", "update_comment_flag")
            self.assertEqual(signal_mock.call_count, 1)


@ddt.ddt
@disable_signal(views, "thread_voted")
@disable_signal(views, "thread_edited")
@disable_signal(views, "comment_created")
@disable_signal(views, "comment_voted")
@disable_signal(views, "comment_deleted")
@disable_signal(views, "comment_flagged")
@disable_signal(views, "thread_flagged")
class TeamsPermissionsTestCase(
    UrlResetMixin, SharedModuleStoreTestCase, MockForumApiMixin
):
    # Most of the test points use the same ddt data.
    # args: user, commentable_id, status_code
    ddt_permissions_args = [
        # Student in team can do operations on threads/comments within the team commentable.
        ("student_in_team", "team_commentable_id", 200),
        # Non-team commentables can be edited by any student.
        ("student_in_team", "course_commentable_id", 200),
        # Student not in team cannot do operations within the team commentable.
        ("student_not_in_team", "team_commentable_id", 401),
        # Non-team commentables can be edited by any student.
        ("student_not_in_team", "course_commentable_id", 200),
        # Moderators can always operator on threads within a team, regardless of team membership.
        ("moderator", "team_commentable_id", 200),
        # Group moderators have regular student privileges for creating a thread and commenting
        ("group_moderator", "course_commentable_id", 200),
    ]

    def change_divided_discussion_settings(self, scheme):
        """
        Change divided discussion settings for the current course.
        If dividing by cohorts, create and assign users to a cohort.
        """
        enable_cohorts = True if scheme is CourseDiscussionSettings.COHORT else False
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        discussion_settings.update(
            {
                "enable_cohorts": enable_cohorts,
                "divided_discussions": [],
                "always_divide_inline_discussions": True,
                "division_scheme": scheme,
            }
        )
        set_course_cohorted(self.course.id, enable_cohorts)

    @classmethod
    def setUpClass(cls):
        super().setUpClassAndForumMock()
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            teams_config_data = {
                "topics": [
                    {
                        "id": "topic_id",
                        "name": "Solar Power",
                        "description": "Solar power is hot",
                    }
                ]
            }
            cls.course = CourseFactory.create(
                teams_configuration=TeamsConfig(teams_config_data)
            )

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.password = "test password"
        seed_permissions_roles(cls.course.id)

        # Create enrollment tracks
        CourseModeFactory.create(course_id=cls.course.id, mode_slug=CourseMode.VERIFIED)
        CourseModeFactory.create(course_id=cls.course.id, mode_slug=CourseMode.AUDIT)

        # Create 6 users--
        # student in team (in the team, audit)
        # student not in team (not in the team, audit)
        # cohorted (in the cohort, audit)
        # verified (not in the cohort, verified)
        # moderator (in the cohort, audit, moderator permissions)
        # group moderator (in the cohort, verified, group moderator permissions)
        def create_users_and_enroll(coursemode):
            student = UserFactory.create(password=cls.password)
            CourseEnrollmentFactory(
                course_id=cls.course.id, user=student, mode=coursemode
            )
            return student

        cls.student_in_team, cls.student_not_in_team, cls.moderator, cls.cohorted = [
            create_users_and_enroll(CourseMode.AUDIT) for _ in range(4)
        ]
        cls.verified, cls.group_moderator = [
            create_users_and_enroll(CourseMode.VERIFIED) for _ in range(2)
        ]

        # Give moderator and group moderator permissions
        cls.moderator.roles.add(
            Role.objects.get(name="Moderator", course_id=cls.course.id)
        )
        assign_role(cls.course.id, cls.group_moderator, "Group Moderator")

        # Create a team
        cls.team_commentable_id = "team_discussion_id"
        cls.team = CourseTeamFactory.create(
            name="The Only Team",
            course_id=cls.course.id,
            topic_id="topic_id",
            discussion_topic_id=cls.team_commentable_id,
        )
        CourseTeamMembershipFactory.create(team=cls.team, user=cls.student_in_team)

        # Dummy commentable ID not linked to a team
        cls.course_commentable_id = "course_level_commentable"

        # Create cohort and add students to it
        CohortFactory(
            course_id=cls.course.id,
            name="Test Cohort",
            users=[cls.group_moderator, cls.cohorted],
        )

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()

    def _setup_mock(self, user, mock_functions=[], data=None):
        user = getattr(self, user)
        mock_functions = mock_functions or []
        for mock_func in mock_functions:
            self.set_mock_return_value(mock_func, data or {})
        self.client.login(username=user.username, password=self.password)

    @ddt.data(*ddt_permissions_args)
    @ddt.unpack
    def test_comment_actions(self, user, commentable_id, status_code):
        """
        Verify that voting and flagging of comments is limited to members of the team or users with
        'edit_content' permission.
        """
        commentable_id = getattr(self, commentable_id)
        self._setup_mock(
            user,
            [
                "get_parent_comment",
                "update_comment_flag",
                "update_comment_votes",
                "delete_comment_vote",
            ],
            make_minimal_cs_comment(
                {
                    "closed": False,
                    "commentable_id": commentable_id,
                    "course_id": str(self.course.id),
                }
            ),
        )
        # "un_flag_abuse_for_comment", "flag_abuse_for_comment",
        for action in ["upvote_comment", "downvote_comment"]:
            response = self.client.post(
                reverse(
                    action,
                    kwargs={
                        "course_id": str(self.course.id),
                        "comment_id": "dummy",
                    },
                )
            )
            assert response.status_code == status_code

    @ddt.data(*ddt_permissions_args)
    @ddt.unpack
    def test_threads_actions(self, user, commentable_id, status_code):
        """
        Verify that voting, flagging, and following of threads is limited to members of the team or users with
        'edit_content' permission.
        """
        commentable_id = getattr(self, commentable_id)
        self._setup_mock(
            user,
            [
                "get_thread",
                "update_thread_flag",
                "update_thread_votes",
                "delete_thread_vote",
            ],
            make_minimal_cs_thread(
                {
                    "commentable_id": commentable_id,
                    "course_id": str(self.course.id),
                }
            ),
        )

        for action in [
            "un_flag_abuse_for_thread",
            "flag_abuse_for_thread",
            "upvote_thread",
            "downvote_thread",
        ]:
            response = self.client.post(
                reverse(
                    action,
                    kwargs={
                        "course_id": str(self.course.id),
                        "thread_id": "dummy",
                    },
                )
            )
            assert response.status_code == status_code

    @ddt.data(
        # student_in_team will be able to update their own post, regardless of team membership
        (
            "student_in_team",
            "student_in_team",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        (
            "student_in_team",
            "student_in_team",
            "course_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        # students can only update their own posts
        (
            "student_in_team",
            "moderator",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
        # Even though student_not_in_team is not in the team, he can still modify posts he created while in the team.
        (
            "student_not_in_team",
            "student_not_in_team",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        # Moderators can change their own posts and other people's posts.
        (
            "moderator",
            "moderator",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        (
            "moderator",
            "student_in_team",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        # Group moderator can do operations on commentables within their group if the course is divided
        (
            "group_moderator",
            "verified",
            "course_commentable_id",
            200,
            CourseDiscussionSettings.ENROLLMENT_TRACK,
        ),
        (
            "group_moderator",
            "cohorted",
            "course_commentable_id",
            200,
            CourseDiscussionSettings.COHORT,
        ),
        # Group moderators cannot do operations on commentables outside of their group
        (
            "group_moderator",
            "verified",
            "course_commentable_id",
            401,
            CourseDiscussionSettings.COHORT,
        ),
        (
            "group_moderator",
            "cohorted",
            "course_commentable_id",
            401,
            CourseDiscussionSettings.ENROLLMENT_TRACK,
        ),
        # Group moderators cannot do operations when the course is not divided
        (
            "group_moderator",
            "verified",
            "course_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
        (
            "group_moderator",
            "cohorted",
            "course_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
    )
    @ddt.unpack
    def test_update_thread(
        self, user, thread_author, commentable_id, status_code, division_scheme
    ):
        """
        Verify that update_thread is limited to thread authors and privileged users (team membership does not matter).
        """
        self.change_divided_discussion_settings(division_scheme)
        commentable_id = getattr(self, commentable_id)
        # thread_author is who is marked as the author of the thread being updated.
        thread_author = getattr(self, thread_author)

        self._setup_mock(
            user,
            ["get_thread", "update_thread"],  # user is the person making the request.
            {
                "user_id": str(thread_author.id),
                "closed": False,
                "commentable_id": commentable_id,
                "context": "standalone",
                "username": thread_author.username,
                "course_id": str(self.course.id),
            },
        )
        response = self.client.post(
            reverse(
                "update_thread",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            ),
            data={"body": "foo", "title": "foo", "commentable_id": commentable_id},
        )
        assert response.status_code == status_code

    @ddt.data(
        # Students can delete their own posts
        (
            "student_in_team",
            "student_in_team",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        # Moderators can delete any post
        (
            "moderator",
            "student_in_team",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.NONE,
        ),
        # Others cannot delete posts
        (
            "student_in_team",
            "moderator",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
        (
            "student_not_in_team",
            "student_in_team",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
        # Group moderator can do operations on commentables within their group if the course is divided
        (
            "group_moderator",
            "verified",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.ENROLLMENT_TRACK,
        ),
        (
            "group_moderator",
            "cohorted",
            "team_commentable_id",
            200,
            CourseDiscussionSettings.COHORT,
        ),
        # Group moderators cannot do operations on commentables outside of their group
        (
            "group_moderator",
            "verified",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.COHORT,
        ),
        (
            "group_moderator",
            "cohorted",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.ENROLLMENT_TRACK,
        ),
        # Group moderators cannot do operations when the course is not divided
        (
            "group_moderator",
            "verified",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
        (
            "group_moderator",
            "cohorted",
            "team_commentable_id",
            401,
            CourseDiscussionSettings.NONE,
        ),
    )
    @ddt.unpack
    def test_delete_comment(
        self, user, comment_author, commentable_id, status_code, division_scheme
    ):
        commentable_id = getattr(self, commentable_id)
        comment_author = getattr(self, comment_author)
        self.change_divided_discussion_settings(division_scheme)

        self._setup_mock(
            user,
            ["get_thread", "get_parent_comment", "delete_comment"],
            {
                "closed": False,
                "commentable_id": commentable_id,
                "user_id": str(comment_author.id),
                "username": comment_author.username,
                "course_id": str(self.course.id),
                "body": "test body",
            },
        )

        response = self.client.post(
            reverse(
                "delete_comment",
                kwargs={"course_id": str(self.course.id), "comment_id": "dummy"},
            ),
            data={"body": "foo", "title": "foo"},
        )
        assert response.status_code == status_code

    @ddt.data(*ddt_permissions_args)
    @ddt.unpack
    def test_create_comment(self, user, commentable_id, status_code):
        """
        Verify that create_comment is limited to members of the team or users with 'edit_content' permission.
        """
        commentable_id = getattr(self, commentable_id)
        self._setup_mock(
            user,
            ["get_thread", "create_parent_comment"],
            {"closed": False, "commentable_id": commentable_id},
        )

        response = self.client.post(
            reverse(
                "create_comment",
                kwargs={"course_id": str(self.course.id), "thread_id": "dummy"},
            ),
            data={"body": "foo", "title": "foo"},
        )
        assert response.status_code == status_code

    @ddt.data(*ddt_permissions_args)
    @ddt.unpack
    def test_create_sub_comment(self, user, commentable_id, status_code):
        """
        Verify that create_subcomment is limited to members of the team or users with 'edit_content' permission.
        """
        commentable_id = getattr(self, commentable_id)
        self._setup_mock(
            user,
            ["get_thread", "get_parent_comment", "create_child_comment"],
            {
                "closed": False,
                "commentable_id": commentable_id,
                "thread_id": "dummy_thread",
            },
        )
        response = self.client.post(
            reverse(
                "create_sub_comment",
                kwargs={
                    "course_id": str(self.course.id),
                    "comment_id": "dummy_comment",
                },
            ),
            data={"body": "foo", "title": "foo"},
        )
        assert response.status_code == status_code


TEAM_COMMENTABLE_ID = "test-team-discussion"


@disable_signal(views, "comment_created")
@ddt.ddt
class ForumEventTestCase(
        SharedModuleStoreTestCase, MockForumApiMixin
):
    """
    Forum actions are expected to launch analytics events. Test these here.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClassAndForumMock()
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)

        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)
        cls.student.roles.add(Role.objects.get(name="Student", course_id=cls.course.id))
        CourseAccessRoleFactory(
            course_id=cls.course.id, user=cls.student, role="Wizard"
        )

    @ddt.data(
        ("vote_for_thread", "update_thread_votes", "thread_id", "thread"),
        ("undo_vote_for_thread", "delete_thread_vote", "thread_id", "thread"),
        ("vote_for_comment", "update_comment_votes", "comment_id", "response"),
        ("undo_vote_for_comment", "delete_comment_vote", "comment_id", "response"),
    )
    @ddt.unpack
    @patch("eventtracking.tracker.emit")
    def test_thread_voted_event(
        self, view_name, function_name, obj_id_name, obj_type, mock_emit
    ):
        undo = view_name.startswith("undo")
        cs_thread = make_minimal_cs_thread(
            {
                "commentable_id": "test_commentable_id",
                "username": "gumprecht",
            }
        )
        cs_comment = make_minimal_cs_comment(
            {
                "closed": False,
                "commentable_id": "test_commentable_id",
                "username": "gumprecht",
            }
        )
        self.set_mock_return_value("get_thread", cs_thread)
        self.set_mock_return_value("get_parent_comment", cs_comment)
        self.set_mock_return_value(
            function_name, cs_thread if "thread" in view_name else cs_comment
        )

        request = RequestFactory().post("dummy_url", {})
        request.user = self.student
        request.view_name = view_name
        view_function = getattr(views, view_name)
        kwargs = dict(course_id=str(self.course.id))
        kwargs[obj_id_name] = obj_id_name
        if not undo:
            kwargs.update(value="up")
        view_function(request, **kwargs)

        assert mock_emit.called
        event_name, event = mock_emit.call_args[0]
        assert event_name == f"edx.forum.{obj_type}.voted"
        assert event["target_username"] == "gumprecht"
        assert event["undo_vote"] == undo
        assert event["vote_value"] == "up"

    @patch("eventtracking.tracker.emit")
    @ddt.data(
        (
            "create_thread",
            "edx.forum.thread.created",
            {
                "thread_type": "discussion",
                "body": "Test text",
                "title": "Test",
                "auto_subscribe": True,
            },
            {"commentable_id": TEAM_COMMENTABLE_ID},
        ),
        (
            "create_comment",
            "edx.forum.response.created",
            {"body": "Test comment", "auto_subscribe": True},
            {"thread_id": "test_thread_id"},
        ),
        (
            "create_sub_comment",
            "edx.forum.comment.created",
            {"body": "Another comment"},
            {"comment_id": "dummy_comment_id"},
        ),
    )
    @ddt.unpack
    def test_team_events(
        self, view_name, event_name, view_data, view_kwargs, mock_emit
    ):
        user = self.student
        team = CourseTeamFactory.create(discussion_topic_id=TEAM_COMMENTABLE_ID)
        CourseTeamMembershipFactory.create(team=team, user=user)
        mock_request_data = {
            "closed": False,
            "commentable_id": TEAM_COMMENTABLE_ID,
            "thread_id": "test_thread_id",
        }
        self.set_mock_return_value("create_thread", mock_request_data)
        self.set_mock_return_value("get_thread", mock_request_data)
        self.set_mock_return_value("create_comment", mock_request_data)
        self.set_mock_return_value("create_parent_comment", mock_request_data)
        self.set_mock_return_value("get_parent_comment", mock_request_data)
        self.set_mock_return_value("create_child_comment", mock_request_data)
        self.set_mock_return_value("create_sub_comment", mock_request_data)

        event_receiver = Mock()
        forum_event = views.TRACKING_LOG_TO_EVENT_MAPS.get(event_name)
        forum_event.connect(event_receiver)

        request = RequestFactory().post("dummy_url", view_data)
        request.user = user
        request.view_name = view_name

        getattr(views, view_name)(request, course_id=str(self.course.id), **view_kwargs)

        name, event = mock_emit.call_args[0]
        assert name == event_name
        assert event["team_id"] == team.team_id

        assert_dict_contains_subset(
            self,
            {
                "signal": forum_event,
                "sender": None,
            },
            event_receiver.call_args.kwargs,
        )

        self.assertIn("thread", event_receiver.call_args.kwargs)

    @ddt.data(
        "follow_thread",
        "unfollow_thread",
    )
    @patch("eventtracking.tracker.emit")
    def test_thread_followed_event(self, view_name, mock_emit):
        event_receiver = Mock()
        for signal in views.TRACKING_LOG_TO_EVENT_MAPS.values():
            signal.connect(event_receiver)

        mock_request_data = {
            "closed": False,
            "commentable_id": "test_commentable_id",
            "username": "test_user",
        }
        self.set_mock_return_value("get_thread", mock_request_data)
        self.set_mock_return_value("follow_thread", mock_request_data)
        self.set_mock_return_value("unfollow_thread", mock_request_data)
        request = RequestFactory().post("dummy_url", {})
        request.user = self.student
        request.view_name = view_name
        view_function = getattr(views, view_name)
        kwargs = dict(course_id=str(self.course.id))
        kwargs["thread_id"] = "thread_id"
        view_function(request, **kwargs)

        assert mock_emit.called
        event_name, event_data = mock_emit.call_args[0]
        action_name = "followed" if view_name == "follow_thread" else "unfollowed"
        expected_action_value = True if view_name == "follow_thread" else False
        assert event_name == f"edx.forum.thread.{action_name}"
        assert event_data["commentable_id"] == "test_commentable_id"
        assert event_data["id"] == "thread_id"
        assert event_data["followed"] == expected_action_value
        assert event_data["user_forums_roles"] == ["Student"]
        assert event_data["user_course_roles"] == ["Wizard"]

        # In case of events that doesn't have a correspondig Open edX events signal
        # we need to check that none of the openedx signals is called.
        # This is tested for all the events that are not tested above.
        event_receiver.assert_not_called()

    @patch("eventtracking.tracker.emit")
    def test_response_event(self, mock_emit):
        """
        Check to make sure an event is fired when a user responds to a thread.
        """
        event_receiver = Mock()
        FORUM_THREAD_RESPONSE_CREATED.connect(event_receiver)
        mocked_data = {
            "closed": False,
            "commentable_id": "test_commentable_id",
            "thread_id": "test_thread_id",
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("create_parent_comment", mocked_data)
        request = RequestFactory().post(
            "dummy_url", {"body": "Test comment", "auto_subscribe": True}
        )
        request.user = self.student
        request.view_name = "create_comment"
        views.create_comment(
            request, course_id=str(self.course.id), thread_id="test_thread_id"
        )

        event_name, event = mock_emit.call_args[0]
        assert event_name == "edx.forum.response.created"
        assert event["body"] == "Test comment"
        assert event["commentable_id"] == "test_commentable_id"
        assert event["user_forums_roles"] == ["Student"]
        assert event["user_course_roles"] == ["Wizard"]
        assert event["discussion"]["id"] == "test_thread_id"
        assert event["options"]["followed"] is True

        event_receiver.assert_called_once()

        assert_dict_contains_subset(
            self,
            {
                "signal": FORUM_THREAD_RESPONSE_CREATED,
                "sender": None,
            },
            event_receiver.call_args.kwargs,
        )

        self.assertIn("thread", event_receiver.call_args.kwargs)

    @patch("eventtracking.tracker.emit")
    def test_comment_event(self, mock_emit):
        """
        Ensure an event is fired when someone comments on a response.
        """
        event_receiver = Mock()
        FORUM_RESPONSE_COMMENT_CREATED.connect(event_receiver)
        mocked_data = {
            "closed": False,
            "depth": 1,
            "thread_id": "test_thread_id",
            "commentable_id": "test_commentable_id",
            "parent_id": "test_response_id",
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("get_parent_comment", mocked_data)
        self.set_mock_return_value("create_child_comment", mocked_data)
        request = RequestFactory().post("dummy_url", {"body": "Another comment"})
        request.user = self.student
        request.view_name = "create_sub_comment"
        views.create_sub_comment(
            request, course_id=str(self.course.id), comment_id="dummy_comment_id"
        )

        event_name, event = mock_emit.call_args[0]
        assert event_name == "edx.forum.comment.created"
        assert event["body"] == "Another comment"
        assert event["discussion"]["id"] == "test_thread_id"
        assert event["response"]["id"] == "test_response_id"
        assert event["user_forums_roles"] == ["Student"]
        assert event["user_course_roles"] == ["Wizard"]
        assert event["options"]["followed"] is False

        assert_dict_contains_subset(
            self,
            {
                "signal": FORUM_RESPONSE_COMMENT_CREATED,
                "sender": None,
            },
            event_receiver.call_args.kwargs,
        )

        self.assertIn("thread", event_receiver.call_args.kwargs)


@disable_signal(views, "thread_edited")
class UpdateThreadUnicodeTestCase(
    SharedModuleStoreTestCase,
    UnicodeTestMixin,
    MockForumApiMixin,
):
    def setUp(self):
        super().setUp()

    @classmethod
    def setUpClass(cls):
        super().setUpClassAndForumMock()
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)
        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)

    @patch(
        "lms.djangoapps.discussion.django_comment_client.utils.get_discussion_categories_ids",
        return_value=["test_commentable"],
    )
    def _test_unicode_data(self, text, mock_get_discussion_id_map):
        mocked_data = {
            "user_id": str(self.student.id),
            "closed": False,
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("update_thread", mocked_data)
        request = RequestFactory().post(
            "dummy_url",
            {
                "body": text,
                "title": text,
                "thread_type": "question",
                "commentable_id": "test_commentable",
            },
        )
        request.user = self.student
        request.view_name = "update_thread"
        response = views.update_thread(
            request, course_id=str(self.course.id), thread_id="dummy_thread_id"
        )

        assert response.status_code == 200

        self.check_mock_called("update_thread")
        mock_params = self.get_mock_func_calls("update_thread")[-1][1]
        assert mock_params["body"] == text
        assert mock_params["title"] == text
        assert mock_params["thread_type"] == "question"
        assert mock_params["commentable_id"] == "test_commentable"


class CreateThreadUnicodeTestCase(
        SharedModuleStoreTestCase, UnicodeTestMixin, MockForumApiMixin
):

    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)
        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)

    def _test_unicode_data(self, text):
        """
        Test to make sure unicode data in a thread doesn't break it.
        """
        # self.set_mock_return_value('get_thread', {})
        self.set_mock_return_value("create_thread", {})

        request = RequestFactory().post(
            "dummy_url", {"thread_type": "discussion", "body": text, "title": text}
        )
        request.user = self.student
        request.view_name = "create_thread"
        response = views.create_thread(
            # The commentable ID contains a username, the Unicode char below ensures it works fine
            request,
            course_id=str(self.course.id),
            commentable_id="non_tåem_dummy_id",
        )

        assert response.status_code == 200
        self.check_mock_called("create_thread")
        create_call_params = self.get_mock_func_calls("create_thread")[-1][1]
        assert create_call_params["body"] == text
        assert create_call_params["title"] == text


@disable_signal(views, "comment_created")
class CreateCommentUnicodeTestCase(
        SharedModuleStoreTestCase, UnicodeTestMixin, MockForumApiMixin
):

    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)
        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)

    def _test_unicode_data(self, text):
        commentable_id = "non_team_dummy_id"
        mocked_data = {"closed": False, "commentable_id": commentable_id}
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("create_parent_comment", mocked_data)

        # We have to get clever here due to Thread's setters and getters.
        # Patch won't work with it.
        try:
            Thread.commentable_id = commentable_id
            request = RequestFactory().post("dummy_url", {"body": text})
            request.user = self.student
            request.view_name = "create_comment"
            response = views.create_comment(
                request, course_id=str(self.course.id), thread_id="dummy_thread_id"
            )

            assert response.status_code == 200
            self.check_mock_called("create_parent_comment")
            create_call_params = self.get_mock_func_calls("create_parent_comment")[-1][
                1
            ]
            assert create_call_params["body"] == text
        finally:
            del Thread.commentable_id


@disable_signal(views, "comment_edited")
class UpdateCommentUnicodeTestCase(
        SharedModuleStoreTestCase, UnicodeTestMixin, MockForumApiMixin
):
    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)
        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)

    def _test_unicode_data(self, text):
        mocked_data = {
            "user_id": str(self.student.id),
            "closed": False,
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("get_parent_comment", mocked_data)
        self.set_mock_return_value("update_comment", mocked_data)

        request = RequestFactory().post("dummy_url", {"body": text})
        request.user = self.student
        request.view_name = "update_comment"
        response = views.update_comment(
            request, course_id=str(self.course.id), comment_id="dummy_comment_id"
        )

        assert response.status_code == 200
        self.check_mock_called("update_comment")
        update_call_params = self.get_mock_func_calls("update_comment")[-1][1]
        assert update_call_params["body"] == text


@disable_signal(views, "comment_created")
class CreateSubCommentUnicodeTestCase(
        SharedModuleStoreTestCase, UnicodeTestMixin, MockForumApiMixin
):
    """
    Make sure comments under a response can handle unicode.
    """

    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)
        cls.student = UserFactory.create()
        CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)

    def _test_unicode_data(self, text):
        """
        Create a comment with unicode in it.
        """
        mocked_data = {
            "closed": False,
            "depth": 1,
            "thread_id": "test_thread",
            "commentable_id": "non_team_dummy_id",
        }
        self.set_mock_return_value("get_thread", mocked_data)
        self.set_mock_return_value("get_parent_comment", mocked_data)
        self.set_mock_return_value("create_child_comment", mocked_data)

        request = RequestFactory().post("dummy_url", {"body": text})
        request.user = self.student
        request.view_name = "create_sub_comment"
        Thread.commentable_id = "test_commentable"
        try:
            response = views.create_sub_comment(
                request, course_id=str(self.course.id), comment_id="dummy_comment_id"
            )

            assert response.status_code == 200
            self.check_mock_called("create_child_comment")
            create_call_params = self.get_mock_func_calls("create_child_comment")[-1][1]
            assert create_call_params["body"] == text
        finally:
            del Thread.commentable_id


class UsersEndpointTestCase(SharedModuleStoreTestCase, MockForumApiMixin):

    @classmethod
    def setUpClass(cls):  # pylint: disable=super-method-not-called
        super().setUpClassAndForumMock()

        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        seed_permissions_roles(cls.course.id)

        cls.student = UserFactory.create()
        cls.enrollment = CourseEnrollmentFactory(user=cls.student, course_id=cls.course.id)
        cls.other_user = UserFactory.create(username="other")
        CourseEnrollmentFactory(user=cls.other_user, course_id=cls.course.id)

    def set_post_counts(self, threads_count=1, comments_count=1):
        """
        sets up a mock response from the comments service for getting post counts for our other_user
        """
        self.set_mock_return_value("get_user", {
            "threads_count": threads_count,
            "comments_count": comments_count,
        })

    def make_request(self, method='get', course_id=None, **kwargs):
        course_id = course_id or self.course.id
        request = getattr(RequestFactory(), method)("dummy_url", kwargs)
        request.user = self.student
        request.view_name = "users"
        return views.users(request, course_id=str(course_id))

    def test_finds_exact_match(self):
        self.set_post_counts()
        response = self.make_request(username="other")
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8'))['users'] == [
            {'id': self.other_user.id, 'username': self.other_user.username}
        ]

    def test_finds_no_match(self):
        self.set_post_counts()
        response = self.make_request(username="othor")
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8'))['users'] == []

    def test_requires_GET(self):
        response = self.make_request(method='post', username="other")
        assert response.status_code == 405

    def test_requires_username_param(self):
        response = self.make_request()
        assert response.status_code == 400
        content = json.loads(response.content.decode('utf-8'))
        assert 'errors' in content
        assert 'users' not in content

    def test_course_does_not_exist(self):
        course_id = CourseKey.from_string("does/not/exist")
        response = self.make_request(course_id=course_id, username="other")

        assert response.status_code == 404
        content = json.loads(response.content.decode('utf-8'))
        assert 'errors' in content
        assert 'users' not in content

    def test_requires_requestor_enrolled_in_course(self):
        # unenroll self.student from the course.
        self.enrollment.delete()

        response = self.make_request(username="other")
        assert response.status_code == 404
        content = json.loads(response.content.decode('utf-8'))
        assert 'errors' in content
        assert 'users' not in content

    def test_requires_matched_user_has_forum_content(self):
        self.set_post_counts(0, 0)
        response = self.make_request(username="other")
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8'))['users'] == []


@ddt.ddt
class SegmentIOForumThreadViewedEventTestCase(SegmentIOTrackingTestCaseBase):

    def _raise_navigation_event(self, label, include_name):
        middleware = TrackMiddleware(get_response=lambda request: None)
        kwargs = {'label': label}
        if include_name:
            kwargs['name'] = 'edx.bi.app.navigation.screen'
        else:
            kwargs['exclude_name'] = True
        request = self.create_request(
            data=self.create_segmentio_event_json(**kwargs),
            content_type='application/json',
        )
        User.objects.create(pk=SEGMENTIO_TEST_USER_ID, username=str(mock.sentinel.username))
        middleware.process_request(request)
        try:
            response = segmentio.segmentio_event(request)
            assert response.status_code == 200
        finally:
            middleware.process_response(request, None)

    @ddt.data(True, False)
    def test_thread_viewed(self, include_name):
        """
        Tests that a SegmentIO thread viewed event is accepted and transformed.

        Only tests that the transformation happens at all; does not
        comprehensively test that it happens correctly.
        ForumThreadViewedEventTransformerTestCase tests for correctness.
        """
        self._raise_navigation_event('Forum: View Thread', include_name)
        event = self.get_event()
        assert event['name'] == 'edx.forum.thread.viewed'
        assert event['event_type'] == event['name']

    @ddt.data(True, False)
    def test_non_thread_viewed(self, include_name):
        """
        Tests that other BI events are thrown out.
        """
        self._raise_navigation_event('Forum: Create Thread', include_name)
        self.assert_no_events_emitted()


def _get_transformed_event(input_event):
    transformer = ForumThreadViewedEventTransformer(**input_event)
    transformer.transform()
    return transformer


def _create_event(
    label='Forum: View Thread',
    include_context=True,
    inner_context=None,
    username=None,
    course_id=None,
    **event_data
):
    result = {'name': 'edx.bi.app.navigation.screen'}
    if include_context:
        result['context'] = {'label': label}
        if course_id:
            result['context']['course_id'] = str(course_id)
    if username:
        result['username'] = username
    if event_data:
        result['event'] = event_data
    if inner_context:
        if not event_data:
            result['event'] = {}
        result['event']['context'] = inner_context
    return result


def _create_and_transform_event(**kwargs):
    event = _create_event(**kwargs)
    return event, _get_transformed_event(event)


@ddt.ddt
class ForumThreadViewedEventTransformerTestCase(UrlResetMixin, ModuleStoreTestCase):
    """
    Test that the ForumThreadViewedEventTransformer transforms events correctly
    and without raising exceptions.

    Because the events passed through the transformer can come from external
    sources (e.g., a mobile app), we carefully test a myriad of cases, including
    those with incomplete and malformed events.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    CATEGORY_ID = 'i4x-edx-discussion-id'
    CATEGORY_NAME = 'Discussion 1'
    PARENT_CATEGORY_NAME = 'Chapter 1'

    TEAM_CATEGORY_ID = 'i4x-edx-team-discussion-id'
    TEAM_CATEGORY_NAME = 'Team Chat'
    TEAM_PARENT_CATEGORY_NAME = PARENT_CATEGORY_NAME

    DUMMY_CATEGORY_ID = 'i4x-edx-dummy-commentable-id'
    DUMMY_THREAD_ID = 'dummy_thread_id'

    @mock.patch.dict("common.djangoapps.student.models.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='TestX',
            course='TR-101S',
            run='Event_Transform_Test_Split',
            default_store=ModuleStoreEnum.Type.split,
        )
        self.student = UserFactory.create()
        self.staff = UserFactory.create(is_staff=True)
        UserBasedRole(user=self.staff, role=CourseStaffRole.ROLE).add_course(self.course.id)
        CourseEnrollmentFactory.create(user=self.student, course_id=self.course.id)
        self.category = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id=self.CATEGORY_ID,
            discussion_category=self.PARENT_CATEGORY_NAME,
            discussion_target=self.CATEGORY_NAME,
        )
        self.team_category = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id=self.TEAM_CATEGORY_ID,
            discussion_category=self.TEAM_PARENT_CATEGORY_NAME,
            discussion_target=self.TEAM_CATEGORY_NAME,
        )
        self.team = CourseTeamFactory.create(
            name='Team 1',
            course_id=self.course.id,
            topic_id='arbitrary-topic-id',
            discussion_topic_id=self.team_category.discussion_id,
        )

    def test_missing_context(self):
        event = _create_event(include_context=False)
        with pytest.raises(EventEmissionExit):
            _get_transformed_event(event)

    def test_no_data(self):
        event, event_trans = _create_and_transform_event()
        event['name'] = 'edx.forum.thread.viewed'
        event['event_type'] = event['name']
        event['event'] = {}
        self.assertDictEqual(event_trans, event)

    def test_inner_context(self):
        _, event_trans = _create_and_transform_event(inner_context={})
        assert 'context' not in event_trans['event']

    def test_non_thread_view(self):
        event = _create_event(
            label='Forum: Create Thread',
            course_id=self.course.id,
            topic_id=self.DUMMY_CATEGORY_ID,
            thread_id=self.DUMMY_THREAD_ID,
        )
        with pytest.raises(EventEmissionExit):
            _get_transformed_event(event)

    def test_bad_field_types(self):
        event, event_trans = _create_and_transform_event(
            course_id={},
            topic_id=3,
            thread_id=object(),
            action=3.14,
        )
        event['name'] = 'edx.forum.thread.viewed'
        event['event_type'] = event['name']
        self.assertDictEqual(event_trans, event)

    def test_bad_course_id(self):
        event, event_trans = _create_and_transform_event(course_id='non-existent-course-id')
        event_data = event_trans['event']
        assert 'category_id' not in event_data
        assert 'category_name' not in event_data
        assert 'url' not in event_data
        assert 'user_forums_roles' not in event_data
        assert 'user_course_roles' not in event_data

    def test_bad_username(self):
        event, event_trans = _create_and_transform_event(username='non-existent-username')
        event_data = event_trans['event']
        assert 'category_id' not in event_data
        assert 'category_name' not in event_data
        assert 'user_forums_roles' not in event_data
        assert 'user_course_roles' not in event_data

    def test_bad_url(self):
        event, event_trans = _create_and_transform_event(
            course_id=self.course.id,
            topic_id='malformed/commentable/id',
            thread_id='malformed/thread/id',
        )
        assert 'url' not in event_trans['event']

    def test_renamed_fields(self):
        AUTHOR = 'joe-the-plumber'
        event, event_trans = _create_and_transform_event(
            course_id=self.course.id,
            topic_id=self.DUMMY_CATEGORY_ID,
            thread_id=self.DUMMY_THREAD_ID,
            author=AUTHOR,
        )
        assert event_trans['event']['commentable_id'] == self.DUMMY_CATEGORY_ID
        assert event_trans['event']['id'] == self.DUMMY_THREAD_ID
        assert event_trans['event']['target_username'] == AUTHOR

    def test_titles(self):

        # No title
        _, event_1_trans = _create_and_transform_event()
        assert 'title' not in event_1_trans['event']
        assert 'title_truncated' not in event_1_trans['event']

        # Short title
        _, event_2_trans = _create_and_transform_event(
            action='!',
        )
        assert 'title' in event_2_trans['event']
        assert 'title_truncated' in event_2_trans['event']
        assert not event_2_trans['event']['title_truncated']

        # Long title
        _, event_3_trans = _create_and_transform_event(
            action=('covfefe' * 200),
        )
        assert 'title' in event_3_trans['event']
        assert 'title_truncated' in event_3_trans['event']
        assert event_3_trans['event']['title_truncated']

    def test_urls(self):
        commentable_id = self.DUMMY_CATEGORY_ID
        thread_id = self.DUMMY_THREAD_ID
        _, event_trans = _create_and_transform_event(
            course_id=self.course.id,
            topic_id=commentable_id,
            thread_id=thread_id,
        )
        expected_path = '/courses/{}/discussion/forum/{}/threads/{}'.format(
            self.course.id, commentable_id, thread_id
        )
        assert event_trans['event'].get('url').endswith(expected_path)

    def test_categories(self):

        # Bad category
        _, event_trans_1 = _create_and_transform_event(
            username=self.student.username,
            course_id=self.course.id,
            topic_id='non-existent-category-id',
        )
        assert 'category_id' not in event_trans_1['event']
        assert 'category_name' not in event_trans_1['event']

        # Good category
        _, event_trans_2 = _create_and_transform_event(
            username=self.student.username,
            course_id=self.course.id,
            topic_id=self.category.discussion_id,
        )
        assert event_trans_2['event'].get('category_id') == self.category.discussion_id
        full_category_name = f'{self.category.discussion_category} / {self.category.discussion_target}'
        assert event_trans_2['event'].get('category_name') == full_category_name

    def test_roles(self):

        # No user
        _, event_trans_1 = _create_and_transform_event(
            course_id=self.course.id,
        )
        assert 'user_forums_roles' not in event_trans_1['event']
        assert 'user_course_roles' not in event_trans_1['event']

        # Student user
        _, event_trans_2 = _create_and_transform_event(
            course_id=self.course.id,
            username=self.student.username,
        )
        assert event_trans_2['event'].get('user_forums_roles') == [FORUM_ROLE_STUDENT]
        assert event_trans_2['event'].get('user_course_roles') == []

        # Course staff user
        _, event_trans_3 = _create_and_transform_event(
            course_id=self.course.id,
            username=self.staff.username,
        )
        assert event_trans_3['event'].get('user_forums_roles') == []
        assert event_trans_3['event'].get('user_course_roles') == [CourseStaffRole.ROLE]

    def test_teams(self):

        # No category
        _, event_trans_1 = _create_and_transform_event(
            course_id=self.course.id,
        )
        assert 'team_id' not in event_trans_1

        # Non-team category
        _, event_trans_2 = _create_and_transform_event(
            course_id=self.course.id,
            topic_id=self.CATEGORY_ID,
        )
        assert 'team_id' not in event_trans_2

        # Team category
        _, event_trans_3 = _create_and_transform_event(
            course_id=self.course.id,
            topic_id=self.TEAM_CATEGORY_ID,
        )
        assert event_trans_3['event'].get('team_id') == self.team.team_id
