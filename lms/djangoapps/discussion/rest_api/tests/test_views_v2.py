"""
Tests for the external REST API endpoints of the Discussion API (views_v2.py).

This module focuses on integration tests for the Django REST Framework views that expose the Discussion API.
It verifies the correct behavior of the API endpoints, including authentication, permissions, request/response formats,
and integration with the underlying discussion service. These tests ensure that the endpoints correctly handle
various user roles, input data, and edge cases, and that they return appropriate HTTP status codes and response bodies.
"""

import json
import random
from datetime import datetime
from unittest import mock

import ddt
import httpretty
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from pytz import UTC
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment, get_retired_username_by_username
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    SuperuserFactory,
    UserFactory,
)
from common.djangoapps.util.testing import PatchMediaTypeMixin, UrlResetMixin
from common.test.utils import disable_signal
from lms.djangoapps.discussion.django_comment_client.tests.utils import config_course_discussions, topic_name_to_id
from lms.djangoapps.discussion.rest_api import api
from lms.djangoapps.discussion.rest_api.tests.utils import (
    CommentsServiceMockMixin,
    ForumMockUtilsMixin,
    ProfileImageTestMixin,
    make_minimal_cs_comment,
    make_minimal_cs_thread,
    make_paginated_api_response,
)
from lms.djangoapps.discussion.rest_api.utils import get_usernames_from_search_string
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from openedx.core.djangoapps.course_groups.tests.helpers import config_course_cohorts
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, DiscussionTopicLink, Provider
from openedx.core.djangoapps.discussions.tasks import update_discussions_settings_from_course_task
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings, Role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_storage
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, check_mongo_calls


class DiscussionAPIViewTestMixin(ForumMockUtilsMixin, UrlResetMixin):
    """
    Mixin for common code in tests of Discussion API views. This includes
    creation of common structures (e.g. a course, user, and enrollment), logging
    in the test client, utility functions, and a test case for unauthenticated
    requests. Subclasses must set self.url in their setUp methods.
    """

    client_class = APIClient

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        self.maxDiff = None  # pylint: disable=invalid-name
        self.course = CourseFactory.create(
            org="x",
            course="y",
            run="z",
            start=datetime.now(UTC),
            discussion_topics={"Test Topic": {"id": "test_topic"}},
        )
        self.password = "Password1234"
        self.user = UserFactory.create(password=self.password)
        # Ensure that parental controls don't apply to this user
        self.user.profile.year_of_birth = 1970
        self.user.profile.save()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password=self.password)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and parsed content
        """
        assert response.status_code == expected_status
        parsed_content = json.loads(response.content.decode("utf-8"))
        assert parsed_content == expected_content

    def register_thread(self, overrides=None):
        """
        Create cs_thread with minimal fields and register response
        """
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "thread_type": "discussion",
                "title": "Test Title",
                "body": "Test body",
            }
        )
        cs_thread.update(overrides or {})
        self.register_get_thread_response(cs_thread)
        self.register_put_thread_response(cs_thread)

    def register_comment(self, overrides=None):
        """
        Create cs_comment with minimal fields and register response
        """
        cs_comment = make_minimal_cs_comment(
            {
                "id": "test_comment",
                "course_id": str(self.course.id),
                "thread_id": "test_thread",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "body": "Original body",
            }
        )
        cs_comment.update(overrides or {})
        self.register_get_comment_response(cs_comment)
        self.register_put_comment_response(cs_comment)
        self.register_post_comment_response(cs_comment, thread_id="test_thread")

    def test_not_authenticated(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            401,
            {"developer_message": "Authentication credentials were not provided."},
        )

    def test_inactive(self):
        self.user.is_active = False
        self.test_basic()


@ddt.ddt
@httpretty.activate
@disable_signal(api, "thread_edited")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetPartialUpdateTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, PatchMediaTypeMixin
):
    """Tests for ThreadViewSet partial_update"""

    def setUp(self):
        self.unsupported_media_type = JSONParser.media_type
        super().setUp()
        self.url = reverse("thread-detail", kwargs={"thread_id": "test_thread"})

    def test_basic(self):
        self.register_get_user_response(self.user)
        self.register_thread(
            {
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
                "read": True,
                "resp_total": 2,
            }
        )
        request_data = {"raw_body": "Edited body"}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "raw_body": "Edited body",
                "rendered_body": "<p>Edited body</p>",
                "preview_body": "Edited body",
                "editable_fields": [
                    "abuse_flagged",
                    "anonymous",
                    "copy_link",
                    "following",
                    "raw_body",
                    "read",
                    "title",
                    "topic_id",
                    "type",
                ],
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
                "comment_count": 1,
                "read": True,
                "response_count": 2,
            }
        )

        params = {
            "thread_id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "thread_type": "discussion",
            "title": "Test Title",
            "body": "Edited body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
            "closed": False,
            "pinned": False,
            "editing_user_id": str(self.user.id),
        }
        self.check_mock_called_with("update_thread", -1, **params)

    def test_error(self):
        self.register_get_user_response(self.user)
        self.register_thread()
        request_data = {"title": ""}
        response = self.request_patch(request_data)
        expected_response_data = {
            "field_errors": {
                "title": {"developer_message": "This field may not be blank."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data

    @ddt.data(
        ("abuse_flagged", True),
        ("abuse_flagged", False),
    )
    @ddt.unpack
    def test_closed_thread(self, field, value):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True, "read": True})
        self.register_flag_response("thread", "test_thread")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "read": True,
                "closed": True,
                "abuse_flagged": value,
                "editable_fields": ["abuse_flagged", "copy_link", "read"],
                "comment_count": 1,
                "unread_comment_count": 0,
            }
        )

    @ddt.data(
        ("raw_body", "Edited body"),
        ("voted", True),
        ("following", True),
    )
    @ddt.unpack
    def test_closed_thread_error(self, field, value):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True})
        self.register_flag_response("thread", "test_thread")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 400

    def test_patch_read_owner_user(self):
        self.register_get_user_response(self.user)
        self.register_thread({"resp_total": 2})
        self.register_read_response(self.user, "thread", "test_thread")
        request_data = {"read": True}

        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "comment_count": 1,
                "read": True,
                "editable_fields": [
                    "abuse_flagged",
                    "anonymous",
                    "copy_link",
                    "following",
                    "raw_body",
                    "read",
                    "title",
                    "topic_id",
                    "type",
                ],
                "response_count": 2,
            }
        )

    def test_patch_read_non_owner_user(self):
        self.register_get_user_response(self.user)
        thread_owner_user = UserFactory.create(password=self.password)
        CourseEnrollmentFactory.create(user=thread_owner_user, course_id=self.course.id)
        self.register_thread(
            {
                "username": thread_owner_user.username,
                "user_id": str(thread_owner_user.id),
                "resp_total": 2,
            }
        )
        self.register_read_response(self.user, "thread", "test_thread")

        request_data = {"read": True}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        expected_data = self.expected_thread_data(
            {
                "author": str(thread_owner_user.username),
                "comment_count": 1,
                "can_delete": False,
                "read": True,
                "editable_fields": [
                    "abuse_flagged",
                    "copy_link",
                    "following",
                    "read",
                    "voted",
                ],
                "response_count": 2,
            }
        )
        assert response_data == expected_data


@ddt.ddt
@disable_signal(api, "comment_edited")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetPartialUpdateTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, PatchMediaTypeMixin
):
    """Tests for CommentViewSet partial_update"""

    def setUp(self):
        self.unsupported_media_type = JSONParser.media_type
        super().setUp()
        self.register_get_user_response(self.user)
        self.url = reverse("comment-detail", kwargs={"comment_id": "test_comment"})

    def expected_response_data(self, overrides=None):
        """
        create expected response data from comment update endpoint
        """
        response_data = {
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": None,
            "author": self.user.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "Original body",
            "rendered_body": "<p>Original body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": [],
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
        response_data.update(overrides or {})
        return response_data

    def test_basic(self):
        self.register_thread()
        self.register_comment(
            {"created_at": "Test Created Date", "updated_at": "Test Updated Date"}
        )
        request_data = {"raw_body": "Edited body"}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_response_data(
            {
                "raw_body": "Edited body",
                "rendered_body": "<p>Edited body</p>",
                "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
            }
        )
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

    def test_error(self):
        self.register_thread()
        self.register_comment()
        request_data = {"raw_body": ""}
        response = self.request_patch(request_data)
        expected_response_data = {
            "field_errors": {
                "raw_body": {"developer_message": "This field may not be blank."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data

    @ddt.data(
        ("abuse_flagged", True),
        ("abuse_flagged", False),
    )
    @ddt.unpack
    def test_closed_thread(self, field, value):
        self.register_thread({"closed": True})
        self.register_comment()
        self.register_flag_response("comment", "test_comment")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_response_data(
            {
                "abuse_flagged": value,
                "abuse_flagged_any_user": None,
                "editable_fields": ["abuse_flagged"],
            }
        )

    @ddt.data(
        ("raw_body", "Edited body"),
        ("voted", True),
        ("following", True),
    )
    @ddt.unpack
    def test_closed_thread_error(self, field, value):
        self.register_thread({"closed": True})
        self.register_comment()
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 400


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetListTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin
):
    """Tests for ThreadViewSet list"""

    def setUp(self):
        super().setUp()
        self.author = UserFactory.create()
        self.url = reverse("thread-list")

    def create_source_thread(self, overrides=None):
        """
        Create a sample source cs_thread
        """
        thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "user_id": str(self.user.id),
                "username": self.user.username,
                "created_at": "2015-04-28T00:00:00Z",
                "updated_at": "2015-04-28T11:11:11Z",
                "title": "Test Title",
                "body": "Test body",
                "votes": {"up_count": 4},
                "comments_count": 5,
                "unread_comments_count": 3,
            }
        )

        thread.update(overrides or {})
        return thread

    def test_course_id_missing(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            400,
            {"field_errors": {"course_id": {"developer_message": "This field is required."}}}
        )

    def test_404(self):
        response = self.client.get(self.url, {"course_id": "non/existent/course"})
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Course not found."}
        )

    def test_basic(self):
        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        source_threads = [
            self.create_source_thread(
                {"user_id": str(self.author.id), "username": self.author.username}
            )
        ]
        expected_threads = [
            self.expected_thread_data(
                {
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "vote_count": 4,
                    "comment_count": 6,
                    "can_delete": False,
                    "unread_comment_count": 3,
                    "voted": True,
                    "author": self.author.username,
                    "editable_fields": [
                        "abuse_flagged",
                        "copy_link",
                        "following",
                        "read",
                        "voted",
                    ],
                    "abuse_flagged_count": None,
                }
            )
        ]
        self.register_get_threads_response(source_threads, page=1, num_pages=2)
        response = self.client.get(
            self.url, {"course_id": str(self.course.id), "following": ""}
        )
        expected_response = make_paginated_api_response(
            results=expected_threads,
            count=1,
            num_pages=2,
            next_link="http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=&page=2",
            previous_link=None,
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    @ddt.data("unread", "unanswered", "unresponded")
    def test_view_query(self, query):
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "view": query,
            },
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            query: True,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_pagination(self):
        self.register_get_user_response(self.user)
        self.register_get_threads_response([], page=1, num_pages=1)
        response = self.client.get(
            self.url, {"course_id": str(self.course.id), "page": "18", "page_size": "4"}
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."},
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 18,
            "per_page": 4,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_text_search(self):
        self.register_get_user_response(self.user)
        self.register_get_threads_search_response([], None, num_pages=0)
        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "text_search": "test search string"},
        )

        expected_response = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)
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

    @ddt.data(True, "true", "1")
    def test_following_true(self, following):
        self.register_get_user_response(self.user)
        self.register_subscribed_threads_response(self.user, [], page=1, num_pages=1)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": following,
            },
        )

        expected_response = make_paginated_api_response(
            results=[], count=0, num_pages=1, next_link=None, previous_link=None
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)
        self.check_mock_called("get_user_subscriptions")

    @ddt.data(False, "false", "0")
    def test_following_false(self, following):
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": following,
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "following": {
                        "developer_message": "The value of the 'following' parameter must be true."
                    }
                }
            },
        )

    def test_following_error(self):
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": "invalid-boolean",
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "following": {"developer_message": "Invalid Boolean Value."}
                }
            },
        )

    @ddt.data(
        ("last_activity_at", "activity"),
        ("comment_count", "comments"),
        ("vote_count", "votes"),
    )
    @ddt.unpack
    def test_order_by(self, http_query, cc_query):
        """
        Tests the order_by parameter

        Arguments:
            http_query (str): Query string sent in the http request
            cc_query (str): Query string used for the comments client service
        """
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "order_by": http_query,
            },
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "page": 1,
            "per_page": 10,
            "sort_key": cc_query,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_order_direction(self):
        """
        Test order direction, of which "desc" is the only valid option.  The
        option actually just gets swallowed, so it doesn't affect the params.
        """
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "order_direction": "desc",
            },
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_mutually_exclusive(self):
        """
        Tests GET thread_list api does not allow filtering on mutually exclusive parameters
        """
        self.register_get_user_response(self.user)
        self.register_get_threads_search_response([], None, num_pages=0)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "text_search": "test search string",
                "topic_id": "topic1, topic2",
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "developer_message": "The following query parameters are mutually exclusive: topic_id, "
                "text_search, following"
            },
        )

    def test_profile_image_requested_field(self):
        """
        Tests thread has user profile image details if called in requested_fields
        """
        user_2 = UserFactory.create(password=self.password)
        # Ensure that parental controls don't apply to this user
        user_2.profile.year_of_birth = 1970
        user_2.profile.save()
        source_threads = [
            self.create_source_thread(),
            self.create_source_thread(
                {"user_id": str(user_2.id), "username": user_2.username}
            ),
        ]

        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        self.register_get_threads_response(source_threads, page=1, num_pages=1)
        self.create_profile_image(self.user, get_profile_image_storage())
        self.create_profile_image(user_2, get_profile_image_storage())

        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "requested_fields": "profile_image"},
        )
        assert response.status_code == 200
        response_threads = json.loads(response.content.decode("utf-8"))["results"]

        for response_thread in response_threads:
            expected_profile_data = self.get_expected_user_profile(
                response_thread["author"]
            )
            response_users = response_thread["users"]
            assert expected_profile_data == response_users[response_thread["author"]]

    def test_profile_image_requested_field_anonymous_user(self):
        """
        Tests profile_image in requested_fields for thread created with anonymous user
        """
        source_threads = [
            self.create_source_thread(
                {
                    "user_id": None,
                    "username": None,
                    "anonymous": True,
                    "anonymous_to_peers": True,
                }
            ),
        ]

        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        self.register_get_threads_response(source_threads, page=1, num_pages=1)

        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "requested_fields": "profile_image"},
        )
        assert response.status_code == 200
        response_thread = json.loads(response.content.decode("utf-8"))["results"][0]
        assert response_thread["author"] is None
        assert {} == response_thread["users"]


@ddt.ddt
class BulkDeleteUserPostsTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Tests for the BulkDeleteUserPostsViewSet
    """

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self) -> None:
        super().setUp()
        self.course = CourseFactory.create()
        self.course_key = str(self.course.id)
        seed_permissions_roles(self.course.id)
        self.user = UserFactory(username='user')
        self.moderator = UserFactory(username='moderator')
        moderator_role = Role.objects.get(name="Moderator", course_id=self.course.id)
        moderator_role.users.add(self.moderator)
        self.stats = [
            {
                "active_flags": random.randint(0, 3),
                "inactive_flags": random.randint(0, 2),
                "replies": random.randint(0, 30),
                "responses": random.randint(0, 100),
                "threads": random.randint(0, 10),
                "username": f"user-{idx}"
            }
            for idx in range(10)
        ]

        for stat in self.stats:
            user = UserFactory.create(
                username=stat['username'],
                email=f"{stat['username']}@example.com",
                password=self.TEST_PASSWORD
            )
            CourseEnrollment.enroll(user, self.course.id, mode='audit')

        CourseEnrollment.enroll(self.moderator, self.course.id, mode='audit')
        self.stats_without_flags = [{**stat, "active_flags": None, "inactive_flags": None} for stat in self.stats]
        self.register_course_stats_response(self.course_key, self.stats, 1, 3)
        self.url = reverse("discussion_course_activity_stats", kwargs={"course_key_string": self.course_key})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_regular_user(self):
        """
        Tests that for a regular user stats are returned without flag counts
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        data = response.json()
        assert data["results"] == self.stats_without_flags

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_moderator_user(self):
        """
        Tests that for a moderator user stats are returned with flag counts
        """
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        data = response.json()
        assert data["results"] == self.stats

    @ddt.data(
        ("moderator", "flagged", "flagged"),
        ("moderator", "activity", "activity"),
        ("moderator", "recency", "recency"),
        ("moderator", None, "flagged"),
        ("user", None, "activity"),
        ("user", "activity", "activity"),
        ("user", "recency", "recency"),
    )
    @ddt.unpack
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_sorting(self, username, ordering_requested, ordering_performed):
        """
        Test valid sorting options and defaults
        """
        self.client.login(username=username, password=self.TEST_PASSWORD)
        params = {}
        if ordering_requested:
            params = {"order_by": ordering_requested}
        self.client.get(self.url, params)
        self.check_mock_called("get_user_course_stats")
        params = self.get_mock_func_calls("get_user_course_stats")[-1][1]
        assert params["sort_key"] == ordering_performed

    @ddt.data("flagged", "xyz")
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_sorting_error_regular_user(self, order_by):
        """
        Test for invalid sorting options for regular users.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url, {"order_by": order_by})
        assert "order_by" in response.json()["field_errors"]

    @ddt.data(
        ('user', 'user-0,user-1,user-2,user-3,user-4,user-5,user-6,user-7,user-8,user-9'),
        ('moderator', 'moderator'),
    )
    @ddt.unpack
    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param(self, username_search_string, comma_separated_usernames):
        """
        Test for endpoint with username param.
        """
        params = {'username': username_search_string}
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        self.client.get(self.url, params)
        self.check_mock_called("get_user_course_stats")
        params = self.get_mock_func_calls("get_user_course_stats")[-1][1]
        assert params["usernames"] == comma_separated_usernames

    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param_with_no_matches(self):
        """
        Test for endpoint with username param with no matches.
        """
        params = {'username': 'unknown'}
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url, params)
        data = response.json()
        self.assertFalse(data['results'])  # noqa: PT009
        assert data['pagination']['count'] == 0

    @ddt.data(
        'user-0',
        'USER-1',
        'User-2',
        'UsEr-3'
    )
    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param_case(self, username_search_string):
        """
        Test user search function is case-insensitive.
        """
        response = get_usernames_from_search_string(self.course_key, username_search_string, 1, 1)
        assert response == (username_search_string.lower(), 1, 1)

    def test_basic(self):
        """
        Basic test method required by DiscussionAPIViewTestMixin
        """

    def user_login(self):
        """
        Authenticates the test client with the example user.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_settings(DISCUSSION_MODERATION_EDIT_REASON_CODES={"test-edit-reason": "Test Edit Reason"})
@override_settings(DISCUSSION_MODERATION_CLOSE_REASON_CODES={"test-close-reason": "Test Close Reason"})
class CourseViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CourseView"""

    def setUp(self):
        super().setUp()
        self.url = reverse("discussion_course", kwargs={"course_id": str(self.course.id)})

    def test_404(self):
        response = self.client.get(
            reverse("course_topics", kwargs={"course_id": "non/existent/course"})
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Course not found."}
        )

    def test_basic(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            200,
            {
                "id": str(self.course.id),
                "is_posting_enabled": True,
                "blackouts": [],
                "thread_list_url": "http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz",
                "following_thread_list_url": (
                    "http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=True"
                ),
                "topics_url": "http://testserver/api/discussion/v1/course_topics/course-v1:x+y+z",
                "enable_in_context": True,
                "group_at_subsection": False,
                "provider": "legacy",
                "allow_anonymous": True,
                "allow_anonymous_to_peers": False,
                "has_moderation_privileges": False,
                'is_course_admin': False,
                'is_course_staff': False,
                "is_group_ta": False,
                'is_user_admin': False,
                "user_roles": ["Student"],
                "edit_reasons": [{"code": "test-edit-reason", "label": "Test Edit Reason"}],
                "post_close_reasons": [{"code": "test-close-reason", "label": "Test Close Reason"}],
                'show_discussions': True,
                'has_bulk_delete_privileges': False,
                'is_notify_all_learners_enabled': False,
                'captcha_settings': {'enabled': False, 'site_key': None},
                'is_email_verified': True,
                'only_verified_users_can_post': False,
                'content_creation_rate_limited': False,
            }
        )


@ddt.ddt
@httpretty.activate
@mock.patch('django.conf.settings.USERNAME_REPLACEMENT_WORKER', 'test_replace_username_service_worker')
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ReplaceUsernamesViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ReplaceUsernamesView"""

    def setUp(self):
        super().setUp()
        self.worker = UserFactory()
        self.worker.username = "test_replace_username_service_worker"
        self.worker_client = APIClient()
        self.new_username = "test_username_replacement"
        self.url = reverse("replace_discussion_username")

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and content
        """
        assert response.status_code == expected_status

        if expected_content:
            assert str(response.content) == expected_content

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}
        return headers

    def call_api(self, user, client, data):
        """ Helper function to call API with data """
        data = json.dumps(data)
        headers = self.build_jwt_headers(user)
        return client.post(self.url, data, content_type='application/json', **headers)

    @ddt.data(
        [{}, {}],
        {},
        [{"test_key": "test_value", "test_key_2": "test_value_2"}]
    )
    def test_bad_schema(self, mapping_data):
        """ Verify the endpoint rejects bad data schema """
        data = {
            "username_mappings": mapping_data
        }
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 400

    def test_auth(self):
        """ Verify the endpoint only works with the service worker """
        data = {
            "username_mappings": [
                {"test_username_1": "test_new_username_1"},
                {"test_username_2": "test_new_username_2"}
            ]
        }

        # Test unauthenticated
        response = self.client.post(self.url, data)
        assert response.status_code == 403

        # Test non-service worker
        random_user = UserFactory()
        response = self.call_api(random_user, APIClient(), data)
        assert response.status_code == 403

        # Test service worker
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 200

    def test_basic(self):
        """ Check successful replacement """
        data = {
            "username_mappings": [
                {self.user.username: self.new_username},
            ]
        }
        expected_response = {
            'failed_replacements': [],
            'successful_replacements': data["username_mappings"]
        }
        self.register_get_username_replacement_response(self.user)
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 200
        assert response.data == expected_response

    def test_not_authenticated(self):
        """
        Override the parent implementation of this, we JWT auth for this API
        """
        pass  # pylint: disable=unnecessary-pass


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CourseTopicsViewTest(DiscussionAPIViewTestMixin, CommentsServiceMockMixin, ModuleStoreTestCase):
    """
    Tests for CourseTopicsView
    """

    def setUp(self):
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        super().setUp()
        self.url = reverse("course_topics", kwargs={"course_id": str(self.course.id)})
        self.thread_counts_map = {
            "courseware-1": {"discussion": 2, "question": 3},
            "courseware-2": {"discussion": 4, "question": 5},
            "courseware-3": {"discussion": 7, "question": 2},
        }
        self.register_get_course_commentable_counts_response(self.course.id, self.thread_counts_map)

    def create_course(self, blocks_count, module_store, topics):
        """
        Create a course in a specified module store with discussion xblocks and topics
        """
        course = CourseFactory.create(
            org="a",
            course="b",
            run="c",
            start=datetime.now(UTC),
            default_store=module_store,
            discussion_topics=topics
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
        course_url = reverse("course_topics", kwargs={"course_id": str(course.id)})
        # add some discussion xblocks
        for i in range(blocks_count):
            BlockFactory.create(
                parent_location=course.location,
                category='discussion',
                discussion_id=f'id_module_{i}',
                discussion_category=f'Category {i}',
                discussion_target=f'Discussion {i}',
                publish_item=False,
            )
        return course_url, course.id

    def make_discussion_xblock(self, topic_id, category, subcategory, **kwargs):
        """
        Build a discussion xblock in self.course
        """
        BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id=topic_id,
            discussion_category=category,
            discussion_target=subcategory,
            **kwargs
        )

    def test_404(self):
        response = self.client.get(
            reverse("course_topics", kwargs={"course_id": "non/existent/course"})
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Course not found."}
        )

    def test_basic(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            200,
            {
                "courseware_topics": [],
                "non_courseware_topics": [{
                    "id": "test_topic",
                    "name": "Test Topic",
                    "children": [],
                    "thread_list_url": 'http://testserver/api/discussion/v1/threads/'
                                       '?course_id=course-v1%3Ax%2By%2Bz&topic_id=test_topic',
                    "thread_counts": {"discussion": 0, "question": 0},
                }],
            }
        )

    @ddt.data(
        (2, ModuleStoreEnum.Type.split, 2, {"Test Topic 1": {"id": "test_topic_1"}}),
        (2, ModuleStoreEnum.Type.split, 2,
         {"Test Topic 1": {"id": "test_topic_1"}, "Test Topic 2": {"id": "test_topic_2"}}),
        (10, ModuleStoreEnum.Type.split, 2, {"Test Topic 1": {"id": "test_topic_1"}}),
    )
    @ddt.unpack
    def test_bulk_response(self, blocks_count, module_store, mongo_calls, topics):
        course_url, course_id = self.create_course(blocks_count, module_store, topics)
        self.register_get_course_commentable_counts_response(course_id, {})
        with check_mongo_calls(mongo_calls):
            with modulestore().default_store(module_store):
                self.client.get(course_url)

    def test_discussion_topic_404(self):
        """
        Tests discussion topic does not exist for the given topic id.
        """
        topic_id = "courseware-topic-id"
        self.make_discussion_xblock(topic_id, "test_category", "test_target")
        url = f"{self.url}?topic_id=invalid_topic_id"
        response = self.client.get(url)
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Discussion not found for 'invalid_topic_id'."}
        )

    def test_topic_id(self):
        """
        Tests discussion topic details against a requested topic id
        """
        topic_id_1 = "topic_id_1"
        topic_id_2 = "topic_id_2"
        self.make_discussion_xblock(topic_id_1, "test_category_1", "test_target_1")
        self.make_discussion_xblock(topic_id_2, "test_category_2", "test_target_2")
        url = f"{self.url}?topic_id=topic_id_1,topic_id_2"
        response = self.client.get(url)
        self.assert_response_correct(
            response,
            200,
            {
                "non_courseware_topics": [],
                "courseware_topics": [
                    {
                        "children": [{
                            "children": [],
                            "id": "topic_id_1",
                            "thread_list_url": "http://testserver/api/discussion/v1/threads/?"
                                               "course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_1",
                            "name": "test_target_1",
                            "thread_counts": {"discussion": 0, "question": 0},
                        }],
                        "id": None,
                        "thread_list_url": "http://testserver/api/discussion/v1/threads/?"
                                           "course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_1",
                        "name": "test_category_1",
                        "thread_counts": None,
                    },
                    {
                        "children":
                            [{
                                "children": [],
                                "id": "topic_id_2",
                                "thread_list_url": "http://testserver/api/discussion/v1/threads/?"
                                                   "course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_2",
                                "name": "test_target_2",
                                "thread_counts": {"discussion": 0, "question": 0},
                            }],
                        "id": None,
                        "thread_list_url": "http://testserver/api/discussion/v1/threads/?"
                                           "course_id=course-v1%3Ax%2By%2Bz&topic_id=topic_id_2",
                        "name": "test_category_2",
                        "thread_counts": None,
                    }
                ]
            }
        )

    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, True)
    def test_new_course_structure_response(self):
        """
        Tests whether the new structure is available on old topics API
        (For mobile compatibility)
        """
        chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name="Week 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        sequential = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            display_name="Lesson 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        BlockFactory.create(
            parent_location=sequential.location,
            category='vertical',
            display_name='vertical',
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        DiscussionsConfiguration.objects.create(
            context_key=self.course.id,
            provider_type=Provider.OPEN_EDX
        )
        update_discussions_settings_from_course_task(str(self.course.id))
        response = json.loads(self.client.get(self.url).content.decode())
        keys = ['children', 'id', 'name', 'thread_counts', 'thread_list_url']
        assert list(response.keys()) == ['courseware_topics', 'non_courseware_topics']
        assert len(response['courseware_topics']) == 1
        courseware_keys = list(response['courseware_topics'][0].keys())
        courseware_keys.sort()
        assert courseware_keys == keys
        assert len(response['non_courseware_topics']) == 1
        non_courseware_keys = list(response['non_courseware_topics'][0].keys())
        non_courseware_keys.sort()
        assert non_courseware_keys == keys


@ddt.ddt
@mock.patch('lms.djangoapps.discussion.rest_api.api._get_course', mock.Mock())
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, True)
class CourseTopicsViewV3Test(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Tests for CourseTopicsViewV3
    """

    def setUp(self) -> None:
        super().setUp()
        self.password = self.TEST_PASSWORD
        self.user = UserFactory.create(password=self.password)
        self.client.login(username=self.user.username, password=self.password)
        self.staff = AdminFactory.create()
        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            discussion_topics={"Course Wide Topic": {
                "id": 'course-wide-topic',
                "usage_key": None,
            }}
        )
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name="Week 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name="Lesson 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.verticals = [
            BlockFactory.create(
                parent_location=self.sequential.location,
                category='vertical',
                display_name='vertical',
                start=datetime(2015, 4, 1, tzinfo=UTC),
            )
        ]
        course_key = self.course.id
        self.config = DiscussionsConfiguration.objects.create(context_key=course_key, provider_type=Provider.OPEN_EDX)
        topic_links = []
        update_discussions_settings_from_course_task(str(course_key))
        topic_id_query = DiscussionTopicLink.objects.filter(context_key=course_key).values_list(
            'external_id', flat=True,
        )
        topic_ids = list(topic_id_query.order_by('ordering'))
        DiscussionTopicLink.objects.bulk_create(topic_links)
        self.topic_stats = {
            **{topic_id: dict(discussion=random.randint(0, 10), question=random.randint(0, 10))
               for topic_id in set(topic_ids)},
            topic_ids[0]: dict(discussion=0, question=0),
        }
        patcher = mock.patch(
            'lms.djangoapps.discussion.rest_api.api.get_course_commentable_counts',
            mock.Mock(return_value=self.topic_stats),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("course_topics_v3", kwargs={"course_id": str(self.course.id)})

    def test_basic(self):
        response = self.client.get(self.url)
        data = json.loads(response.content.decode())
        expected_non_courseware_keys = [
            'id', 'usage_key', 'name', 'thread_counts', 'enabled_in_context',
            'courseware'
        ]
        expected_courseware_keys = [
            'id', 'block_id', 'lms_web_url', 'legacy_web_url', 'student_view_url',
            'type', 'display_name', 'children', 'courseware'
        ]
        assert response.status_code == 200
        assert len(data) == 2
        non_courseware_topic_keys = list(data[0].keys())
        assert non_courseware_topic_keys == expected_non_courseware_keys
        courseware_topic_keys = list(data[1].keys())
        assert courseware_topic_keys == expected_courseware_keys
        expected_courseware_keys.remove('courseware')
        sequential_keys = list(data[1]['children'][0].keys())
        assert sequential_keys == (expected_courseware_keys + ['thread_counts'])
        expected_non_courseware_keys.remove('courseware')
        vertical_keys = list(data[1]['children'][0]['children'][0].keys())
        assert vertical_keys == expected_non_courseware_keys


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class LearnerThreadViewAPITest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for LearnerThreadView list"""

    def setUp(self):
        """
        Sets up the test case
        """
        super().setUp()
        self.author = self.user
        self.remove_keys = [
            "abuse_flaggers",
            "body",
            "children",
            "commentable_id",
            "endorsed",
            "last_activity_at",
            "resp_total",
            "thread_type",
            "user_id",
            "username",
            "votes",
        ]
        self.replace_keys = [
            {"from": "unread_comments_count", "to": "unread_comment_count"},
            {"from": "comments_count", "to": "comment_count"},
        ]
        self.add_keys = [
            {"key": "author", "value": self.author.username},
            {"key": "abuse_flagged", "value": False},
            {"key": "author_label", "value": None},
            {"key": "can_delete", "value": True},
            {"key": "close_reason", "value": None},
            {
                "key": "comment_list_url",
                "value": "http://testserver/api/discussion/v1/comments/?thread_id=test_thread"
            },
            {
                "key": "editable_fields",
                "value": [
                    'abuse_flagged', 'anonymous', 'copy_link', 'following', 'raw_body',
                    'read', 'title', 'topic_id', 'type'
                ]
            },
            {"key": "endorsed_comment_list_url", "value": None},
            {"key": "following", "value": False},
            {"key": "group_name", "value": None},
            {"key": "has_endorsed", "value": False},
            {"key": "last_edit", "value": None},
            {"key": "non_endorsed_comment_list_url", "value": None},
            {"key": "preview_body", "value": "Test body"},
            {"key": "raw_body", "value": "Test body"},

            {"key": "rendered_body", "value": "<p>Test body</p>"},
            {"key": "response_count", "value": 0},
            {"key": "topic_id", "value": "test_topic"},
            {"key": "type", "value": "discussion"},
            {"key": "users", "value": {
                self.user.username: {
                    "profile": {
                        "image": {
                            "has_image": False,
                            "image_url_full": "http://testserver/static/default_500.png",
                            "image_url_large": "http://testserver/static/default_120.png",
                            "image_url_medium": "http://testserver/static/default_50.png",
                            "image_url_small": "http://testserver/static/default_30.png",
                        }
                    }
                }
            }},
            {"key": "vote_count", "value": 4},
            {"key": "voted", "value": False},

        ]
        self.url = reverse("discussion_learner_threads", kwargs={'course_id': str(self.course.id)})

    def update_thread(self, thread):
        """
        This function updates the thread by adding and remove some keys.
        Value of these keys has been defined in setUp function
        """
        for element in self.add_keys:
            thread[element['key']] = element['value']
        for pair in self.replace_keys:
            thread[pair['to']] = thread.pop(pair['from'])
        for key in self.remove_keys:
            thread.pop(key)
        thread['comment_count'] += 1
        return thread

    def test_basic(self):
        """
        Tests the data is fetched correctly

        Note: test_basic is required as the name because DiscussionAPIViewTestMixin
              calls this test case automatically
        """
        self.register_get_user_response(self.user)
        expected_cs_comments_response = {
            "collection": [make_minimal_cs_thread({
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "user_id": str(self.user.id),
                "username": self.user.username,
                "created_at": "2015-04-28T00:00:00Z",
                "updated_at": "2015-04-28T11:11:11Z",
                "title": "Test Title",
                "body": "Test body",
                "votes": {"up_count": 4},
                "comments_count": 5,
                "unread_comments_count": 3,
                "closed_by_label": None,
                "edit_by_label": None,
            })],
            "page": 1,
            "num_pages": 1,
        }
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        self.url += f"?username={self.user.username}"
        response = self.client.get(self.url)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode('utf-8'))
        expected_api_response = expected_cs_comments_response['collection']

        for thread in expected_api_response:
            self.update_thread(thread)

        assert response_data['results'] == expected_api_response
        assert response_data['pagination'] == {
            "next": None,
            "previous": None,
            "count": 1,
            "num_pages": 1,
        }

    def test_no_username_given(self):
        """
        Tests that 404 response is returned when no username is passed
        """
        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_not_authenticated(self):
        """
        This test is called by DiscussionAPIViewTestMixin and is not required in
        our case
        """
        assert True

    @ddt.data("None", "discussion", "question")
    def test_thread_type_by(self, thread_type):
        """
        Tests the thread_type parameter

        Arguments:
            thread_type (str): Value of thread_type can be 'None',
                          'discussion' and 'question'
        """
        threads = [make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(self.user.id),
            "username": self.user.username,
            "created_at": "2015-04-28T00:00:00Z",
            "updated_at": "2015-04-28T11:11:11Z",
            "title": "Test Title",
            "body": "Test body",
            "votes": {"up_count": 4},
            "comments_count": 5,
            "unread_comments_count": 3,
        })]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "thread_type": thread_type,
            }
        )
        assert response.status_code == 200
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "page": 1,
            "per_page": 10,
            "thread_type": thread_type,
            "sort_key": 'activity',
            "count_flagged": False
        }

        self.check_mock_called_with("get_user_active_threads", -1, **params)

    @ddt.data(
        ("last_activity_at", "activity"),
        ("comment_count", "comments"),
        ("vote_count", "votes")
    )
    @ddt.unpack
    def test_order_by(self, http_query, cc_query):
        """
        Tests the order_by parameter for active threads

        Arguments:
            http_query (str): Query string sent in the http request
            cc_query (str): Query string used for the comments client service
        """
        threads = [make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(self.user.id),
            "username": self.user.username,
            "created_at": "2015-04-28T00:00:00Z",
            "updated_at": "2015-04-28T11:11:11Z",
            "title": "Test Title",
            "body": "Test body",
            "votes": {"up_count": 4},
            "comments_count": 5,
            "unread_comments_count": 3,
        })]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "order_by": http_query,
            }
        )
        assert response.status_code == 200
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "page": 1,
            "per_page": 10,
            "sort_key": cc_query,
            "count_flagged": False
        }
        self.check_mock_called_with("get_user_active_threads", -1, **params)

    @ddt.data("flagged", "unanswered", "unread", "unresponded")
    def test_status_by(self, post_status):
        """
        Tests the post_status parameter

        Arguments:
            post_status (str): Value of post_status can be 'flagged',
                          'unanswered' and 'unread'
        """
        threads = [make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(self.user.id),
            "username": self.user.username,
            "created_at": "2015-04-28T00:00:00Z",
            "updated_at": "2015-04-28T11:11:11Z",
            "title": "Test Title",
            "body": "Test body",
            "votes": {"up_count": 4},
            "comments_count": 5,
            "unread_comments_count": 3,
        })]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "status": post_status,
            }
        )
        if post_status == "flagged":
            assert response.status_code == 403
        else:
            assert response.status_code == 200
            params = {
                "user_id": str(self.user.id),
                "course_id": str(self.course.id),
                "page": 1,
                "per_page": 10,
                post_status: True,
                "sort_key": 'activity',
                "count_flagged": False
            }
            self.check_mock_called_with("get_user_active_threads", -1, **params)


@ddt.ddt
@httpretty.activate
@override_waffle_flag(ENABLE_DISCUSSIONS_MFE, True)
class CourseActivityStatsTest(UrlResetMixin, ForumMockUtilsMixin, APITestCase,
                              SharedModuleStoreTestCase):
    """
    Tests for the course stats endpoint
    """

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self) -> None:
        super().setUp()
        self.course = CourseFactory.create()
        self.course_key = str(self.course.id)
        seed_permissions_roles(self.course.id)
        self.user = UserFactory(username='user')
        self.moderator = UserFactory(username='moderator')
        moderator_role = Role.objects.get(name="Moderator", course_id=self.course.id)
        moderator_role.users.add(self.moderator)
        self.stats = [
            {
                "active_flags": random.randint(0, 3),
                "inactive_flags": random.randint(0, 2),
                "replies": random.randint(0, 30),
                "responses": random.randint(0, 100),
                "threads": random.randint(0, 10),
                "username": f"user-{idx}"
            }
            for idx in range(10)
        ]

        for stat in self.stats:
            user = UserFactory.create(
                username=stat['username'],
                email=f"{stat['username']}@example.com",
                password=self.TEST_PASSWORD
            )
            CourseEnrollment.enroll(user, self.course.id, mode='audit')

        CourseEnrollment.enroll(self.moderator, self.course.id, mode='audit')
        self.stats_without_flags = [{**stat, "active_flags": None, "inactive_flags": None} for stat in self.stats]
        self.register_course_stats_response(self.course_key, self.stats, 1, 3)
        self.url = reverse("discussion_course_activity_stats", kwargs={"course_key_string": self.course_key})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_regular_user(self):
        """
        Tests that for a regular user stats are returned without flag counts
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        data = response.json()
        assert data["results"] == self.stats_without_flags

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_moderator_user(self):
        """
        Tests that for a moderator user stats are returned with flag counts
        """
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        data = response.json()
        assert data["results"] == self.stats

    @ddt.data(
        ("moderator", "flagged", "flagged"),
        ("moderator", "activity", "activity"),
        ("moderator", "recency", "recency"),
        ("moderator", None, "flagged"),
        ("user", None, "activity"),
        ("user", "activity", "activity"),
        ("user", "recency", "recency"),
    )
    @ddt.unpack
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_sorting(self, username, ordering_requested, ordering_performed):
        """
        Test valid sorting options and defaults
        """
        self.client.login(username=username, password=self.TEST_PASSWORD)
        params = {}
        if ordering_requested:
            params = {"order_by": ordering_requested}
        self.client.get(self.url, params)
        self.check_mock_called("get_user_course_stats")
        params = self.get_mock_func_calls("get_user_course_stats")[-1][1]
        assert params["sort_key"] == ordering_performed

    @ddt.data("flagged", "xyz")
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def test_sorting_error_regular_user(self, order_by):
        """
        Test for invalid sorting options for regular users.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url, {"order_by": order_by})
        assert "order_by" in response.json()["field_errors"]

    @ddt.data(
        ('user', 'user-0,user-1,user-2,user-3,user-4,user-5,user-6,user-7,user-8,user-9'),
        ('moderator', 'moderator'),
    )
    @ddt.unpack
    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param(self, username_search_string, comma_separated_usernames):
        """
        Test for endpoint with username param.
        """
        params = {'username': username_search_string}
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        self.client.get(self.url, params)
        self.check_mock_called("get_user_course_stats")
        params = self.get_mock_func_calls("get_user_course_stats")[-1][1]
        assert params["usernames"] == comma_separated_usernames

    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param_with_no_matches(self):
        """
        Test for endpoint with username param with no matches.
        """
        params = {'username': 'unknown'}
        self.client.login(username=self.moderator.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url, params)
        data = response.json()
        self.assertFalse(data['results'])  # noqa: PT009
        assert data['pagination']['count'] == 0

    @ddt.data(
        'user-0',
        'USER-1',
        'User-2',
        'UsEr-3'
    )
    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_DISCUSSION_SERVICE': True})
    def test_with_username_param_case(self, username_search_string):
        """
        Test user search function is case-insensitive.
        """
        response = get_usernames_from_search_string(self.course_key, username_search_string, 1, 1)
        assert response == (username_search_string.lower(), 1, 1)


@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class RetireViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CourseView"""

    def setUp(self):
        super().setUp()
        RetirementState.objects.create(state_name='PENDING', state_execution_order=1)
        self.retire_forums_state = RetirementState.objects.create(state_name='RETIRE_FORUMS', state_execution_order=11)

        self.retirement = UserRetirementStatus.create_retirement(self.user)
        self.retirement.current_state = self.retire_forums_state
        self.retirement.save()

        self.superuser = SuperuserFactory()
        self.superuser_client = APIClient()
        self.retired_username = get_retired_username_by_username(self.user.username)
        self.url = reverse("retire_discussion_user")

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and content
        """
        assert response.status_code == expected_status

        if expected_content:
            assert response.content.decode('utf-8') == expected_content

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}
        return headers

    def test_basic(self):
        """
        Check successful retirement case
        """
        self.register_get_user_retire_response(self.user)
        headers = self.build_jwt_headers(self.superuser)
        data = {'username': self.user.username}
        response = self.superuser_client.post(self.url, data, **headers)
        self.assert_response_correct(response, 204, b"")

    def test_nonexistent_user(self):
        """
        Check that we handle unknown users appropriately
        """
        nonexistent_username = "nonexistent user"
        self.retired_username = get_retired_username_by_username(nonexistent_username)
        data = {'username': nonexistent_username}
        headers = self.build_jwt_headers(self.superuser)
        response = self.superuser_client.post(self.url, data, **headers)
        self.assert_response_correct(response, 404, None)

    def test_not_authenticated(self):
        """
        Override the parent implementation of this, we JWT auth for this API
        """
        pass  # pylint: disable=unnecessary-pass


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class UploadFileViewTest(ForumMockUtilsMixin, UrlResetMixin, ModuleStoreTestCase):
    """
    Tests for UploadFileView.
    """

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.valid_file = {
            "uploaded_file": SimpleUploadedFile(
                "test.jpg",
                b"test content",
                content_type="image/jpeg",
            ),
        }
        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.course = CourseFactory.create(org='a', course='b', run='c', start=datetime.now(UTC))
        self.url = reverse("upload_file", kwargs={"course_id": str(self.course.id)})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    def user_login(self):
        """
        Authenticates the test client with the example user.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def enroll_user_in_course(self):
        """
        Makes the example user enrolled to the course.
        """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def assert_upload_success(self, response):
        """
        Asserts that the upload response was successful and returned the
        expected contents.
        """
        assert response.status_code == status.HTTP_200_OK
        assert response.content_type == "application/json"
        response_data = json.loads(response.content)
        assert "location" in response_data

    def test_file_upload_by_unauthenticated_user(self):
        """
        Should fail if an unauthenticated user tries to upload a file.
        """
        response = self.client.post(self.url, self.valid_file)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_file_upload_by_unauthorized_user(self):
        """
        Should fail if the user is not either staff or a student
        enrolled in the course.
        """
        self.user_login()
        response = self.client.post(self.url, self.valid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_by_enrolled_user(self):
        """
        Should succeed when a valid file is uploaded by an authenticated
        user who's enrolled in the course.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_global_staff(self):
        """
        Should succeed when a valid file is uploaded by a global staff
        member.
        """
        self.user_login()
        GlobalStaff().add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_instructor(self):
        """
        Should succeed when a valid file is uploaded by a course instructor.
        """
        self.user_login()
        CourseInstructorRole(course_key=self.course.id).add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_course_staff(self):
        """
        Should succeed when a valid file is uploaded by a course staff
        member.
        """
        self.user_login()
        CourseStaffRole(course_key=self.course.id).add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_with_thread_key(self):
        """
        Should contain the given thread_key in the uploaded file name.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(self.url, {
            **self.valid_file,
            "thread_key": "somethread",
        })
        response_data = json.loads(response.content)
        assert "/somethread/" in response_data["location"]

    def test_file_upload_with_invalid_file(self):
        """
        Should fail if the uploaded file format is not allowed.
        """
        self.user_login()
        self.enroll_user_in_course()
        invalid_file = {
            "uploaded_file": SimpleUploadedFile(
                "test.txt",
                b"test content",
                content_type="text/plain",
            ),
        }
        response = self.client.post(self.url, invalid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_with_invalid_course_id(self):
        """
        Should fail if the course does not exist.
        """
        self.user_login()
        self.enroll_user_in_course()
        url = reverse("upload_file", kwargs={"course_id": "d/e/f"})
        response = self.client.post(url, self.valid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_with_no_data(self):
        """
        Should fail when the user sends a request missing an
        `uploaded_file` field.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(self.url, data={})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@ddt.ddt
class CourseDiscussionSettingsAPIViewTest(APITestCase, UrlResetMixin, ModuleStoreTestCase):
    """
    Test the course discussion settings handler API endpoint.
    """
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org="x",
            course="y",
            run="z",
            start=datetime.now(UTC),
            discussion_topics={"Test Topic": {"id": "test_topic"}}
        )
        self.path = reverse('discussion_course_settings', kwargs={'course_id': str(self.course.id)})
        self.password = self.TEST_PASSWORD
        self.user = UserFactory(username='staff', password=self.password, is_staff=True)

    def _get_oauth_headers(self, user):
        """Return the OAuth headers for testing OAuth authentication"""
        access_token = AccessTokenFactory.create(user=user, application=ApplicationFactory()).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }
        return headers

    def _login_as_staff(self):
        """Log the client in as the staff."""
        self.client.login(username=self.user.username, password=self.password)

    def _login_as_discussion_staff(self):
        user = UserFactory(username='abc', password='abc')
        role = Role.objects.create(name='Administrator', course_id=self.course.id)
        role.users.set([user])
        self.client.login(username=user.username, password='abc')

    def _create_divided_discussions(self):
        """Create some divided discussions for testing."""
        divided_inline_discussions = ['Topic A', ]
        divided_course_wide_discussions = ['Topic B', ]
        divided_discussions = divided_inline_discussions + divided_course_wide_discussions

        BlockFactory.create(
            parent=self.course,
            category='discussion',
            discussion_id=topic_name_to_id(self.course, 'Topic A'),
            discussion_category='Chapter',
            discussion_target='Discussion',
            start=datetime.now()
        )
        discussion_topics = {
            "Topic B": {"id": "Topic B"},
        }
        config_course_cohorts(self.course, is_cohorted=True)
        config_course_discussions(
            self.course,
            discussion_topics=discussion_topics,
            divided_discussions=divided_discussions
        )
        return divided_inline_discussions, divided_course_wide_discussions

    def _get_expected_response(self):
        """Return the default expected response before any changes to the discussion settings."""
        return {
            'always_divide_inline_discussions': False,
            'divided_inline_discussions': [],
            'divided_course_wide_discussions': [],
            'id': 1,
            'division_scheme': 'cohort',
            'available_division_schemes': ['cohort'],
            'reported_content_email_notifications': False,
        }

    def patch_request(self, data, headers=None):
        headers = headers if headers else {}
        return self.client.patch(self.path, json.dumps(data), content_type='application/merge-patch+json', **headers)

    def _assert_current_settings(self, expected_response):
        """Validate the current discussion settings against the expected response."""
        response = self.client.get(self.path)
        assert response.status_code == 200
        content = json.loads(response.content.decode('utf-8'))
        assert content == expected_response

    def _assert_patched_settings(self, data, expected_response):
        """Validate the patched settings against the expected response."""
        response = self.patch_request(data)
        assert response.status_code == 204
        self._assert_current_settings(expected_response)

    @ddt.data('get', 'patch')
    def test_authentication_required(self, method):
        """Test and verify that authentication is required for this endpoint."""
        self.client.logout()
        response = getattr(self.client, method)(self.path)
        assert response.status_code == 401

    @ddt.data(
        {'is_staff': False, 'get_status': 403, 'put_status': 403},
        {'is_staff': True, 'get_status': 200, 'put_status': 204},
    )
    @ddt.unpack
    def test_oauth(self, is_staff, get_status, put_status):
        """Test that OAuth authentication works for this endpoint."""
        user = UserFactory(is_staff=is_staff)
        headers = self._get_oauth_headers(user)
        self.client.logout()

        response = self.client.get(self.path, **headers)
        assert response.status_code == get_status

        response = self.patch_request(
            {'always_divide_inline_discussions': True}, headers
        )
        assert response.status_code == put_status

    def test_non_existent_course_id(self):
        """Test the response when this endpoint is passed a non-existent course id."""
        self._login_as_staff()
        response = self.client.get(
            reverse('discussion_course_settings', kwargs={
                'course_id': 'course-v1:a+b+c'
            })
        )
        assert response.status_code == 404

    def test_patch_request_by_discussion_staff(self):
        """Test the response when patch request is sent by a user with discussions staff role."""
        self._login_as_discussion_staff()
        response = self.patch_request(
            {'always_divide_inline_discussions': True}
        )
        assert response.status_code == 403

    def test_get_request_by_discussion_staff(self):
        """Test the response when get request is sent by a user with discussions staff role."""
        self._login_as_discussion_staff()
        divided_inline_discussions, divided_course_wide_discussions = self._create_divided_discussions()
        response = self.client.get(self.path)
        assert response.status_code == 200
        expected_response = self._get_expected_response()
        expected_response['divided_course_wide_discussions'] = [
            topic_name_to_id(self.course, name) for name in divided_course_wide_discussions
        ]
        expected_response['divided_inline_discussions'] = [
            topic_name_to_id(self.course, name) for name in divided_inline_discussions
        ]
        content = json.loads(response.content.decode('utf-8'))
        assert content == expected_response

    def test_get_request_by_non_staff_user(self):
        """Test the response when get request is sent by a regular user with no staff role."""
        user = UserFactory(username='abc', password='abc')
        self.client.login(username=user.username, password='abc')
        response = self.client.get(self.path)
        assert response.status_code == 403

    def test_patch_request_by_non_staff_user(self):
        """Test the response when patch request is sent by a regular user with no staff role."""
        user = UserFactory(username='abc', password='abc')
        self.client.login(username=user.username, password='abc')
        response = self.patch_request(
            {'always_divide_inline_discussions': True}
        )
        assert response.status_code == 403

    def test_get_settings(self):
        """Test the current discussion settings against the expected response."""
        divided_inline_discussions, divided_course_wide_discussions = self._create_divided_discussions()
        self._login_as_staff()
        response = self.client.get(self.path)
        assert response.status_code == 200
        expected_response = self._get_expected_response()
        expected_response['divided_course_wide_discussions'] = [
            topic_name_to_id(self.course, name) for name in divided_course_wide_discussions
        ]
        expected_response['divided_inline_discussions'] = [
            topic_name_to_id(self.course, name) for name in divided_inline_discussions
        ]
        content = json.loads(response.content.decode('utf-8'))
        assert content == expected_response

    def test_available_schemes(self):
        """Test the available division schemes against the expected response."""
        config_course_cohorts(self.course, is_cohorted=False)
        self._login_as_staff()
        expected_response = self._get_expected_response()
        expected_response['available_division_schemes'] = []
        self._assert_current_settings(expected_response)

        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)

        expected_response['available_division_schemes'] = [CourseDiscussionSettings.ENROLLMENT_TRACK]
        self._assert_current_settings(expected_response)

        config_course_cohorts(self.course, is_cohorted=True)
        expected_response['available_division_schemes'] = [
            CourseDiscussionSettings.COHORT, CourseDiscussionSettings.ENROLLMENT_TRACK
        ]
        self._assert_current_settings(expected_response)

    def test_empty_body_patch_request(self):
        """Test the response status code on sending a PATCH request with an empty body or missing fields."""
        self._login_as_staff()
        response = self.patch_request("")
        assert response.status_code == 400

        response = self.patch_request({})
        assert response.status_code == 400

    @ddt.data(
        {'abc': 123},
        {'divided_course_wide_discussions': 3},
        {'divided_inline_discussions': 'a'},
        {'always_divide_inline_discussions': ['a']},
        {'division_scheme': True}
    )
    def test_invalid_body_parameters(self, body):
        """Test the response status code on sending a PATCH request with parameters having incorrect types."""
        self._login_as_staff()
        response = self.patch_request(body)
        assert response.status_code == 400

    def test_update_always_divide_inline_discussion_settings(self):
        """Test whether the 'always_divide_inline_discussions' setting is updated."""
        config_course_cohorts(self.course, is_cohorted=True)
        self._login_as_staff()
        expected_response = self._get_expected_response()
        self._assert_current_settings(expected_response)
        expected_response['always_divide_inline_discussions'] = True

        self._assert_patched_settings({'always_divide_inline_discussions': True}, expected_response)

    def test_update_course_wide_discussion_settings(self):
        """Test whether the 'divided_course_wide_discussions' setting is updated."""
        discussion_topics = {
            'Topic B': {'id': 'Topic B'}
        }
        config_course_cohorts(self.course, is_cohorted=True)
        config_course_discussions(self.course, discussion_topics=discussion_topics)
        expected_response = self._get_expected_response()
        self._login_as_staff()
        self._assert_current_settings(expected_response)
        expected_response['divided_course_wide_discussions'] = [
            topic_name_to_id(self.course, "Topic B")
        ]
        self._assert_patched_settings(
            {'divided_course_wide_discussions': [topic_name_to_id(self.course, "Topic B")]},
            expected_response
        )
        expected_response['divided_course_wide_discussions'] = []
        self._assert_patched_settings(
            {'divided_course_wide_discussions': []},
            expected_response
        )

    def test_update_inline_discussion_settings(self):
        """Test whether the 'divided_inline_discussions' setting is updated."""
        config_course_cohorts(self.course, is_cohorted=True)
        self._login_as_staff()
        expected_response = self._get_expected_response()
        self._assert_current_settings(expected_response)

        now = datetime.now()
        BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='Topic_A',
            discussion_category='Chapter',
            discussion_target='Discussion',
            start=now
        )
        expected_response['divided_inline_discussions'] = ['Topic_A', ]
        self._assert_patched_settings({'divided_inline_discussions': ['Topic_A']}, expected_response)

        expected_response['divided_inline_discussions'] = []
        self._assert_patched_settings({'divided_inline_discussions': []}, expected_response)

    def test_update_division_scheme(self):
        """Test whether the 'division_scheme' setting is updated."""
        config_course_cohorts(self.course, is_cohorted=True)
        self._login_as_staff()
        expected_response = self._get_expected_response()
        self._assert_current_settings(expected_response)
        expected_response['division_scheme'] = 'none'
        self._assert_patched_settings({'division_scheme': 'none'}, expected_response)

    def test_update_reported_content_email_notifications(self):
        """Test whether the 'reported_content_email_notifications' setting is updated."""
        config_course_cohorts(self.course, is_cohorted=True)
        config_course_discussions(self.course, reported_content_email_notifications=True)
        expected_response = self._get_expected_response()
        expected_response['reported_content_email_notifications'] = True
        self._login_as_staff()
        self._assert_current_settings(expected_response)


@ddt.ddt
class CourseDiscussionRolesAPIViewTest(APITestCase, UrlResetMixin, ModuleStoreTestCase):
    """
    Test the course discussion roles management endpoint.
    """
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org="x",
            course="y",
            run="z",
            start=datetime.now(UTC),
        )
        self.password = self.TEST_PASSWORD
        self.user = UserFactory(username='staff', password=self.password, is_staff=True)
        course_key = CourseKey.from_string('course-v1:x+y+z')
        seed_permissions_roles(course_key)

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def path(self, course_id=None, role=None):
        """Return the URL path to the endpoint based on the provided arguments."""
        course_id = str(self.course.id) if course_id is None else course_id
        role = 'Moderator' if role is None else role
        return reverse(
            'discussion_course_roles',
            kwargs={'course_id': course_id, 'rolename': role}
        )

    def _get_oauth_headers(self, user):
        """Return the OAuth headers for testing OAuth authentication."""
        access_token = AccessTokenFactory.create(user=user, application=ApplicationFactory()).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }
        return headers

    def _login_as_staff(self):
        """Log the client is as the staff user."""
        self.client.login(username=self.user.username, password=self.password)

    def _create_and_enroll_users(self, count):
        """Create 'count' number of users and enroll them in self.course."""
        users = []
        for _ in range(count):
            user = UserFactory()
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)
            users.append(user)
        return users

    def _add_users_to_role(self, users, rolename):
        """Add the given users to the given role."""
        role = Role.objects.get(name=rolename, course_id=self.course.id)
        for user in users:
            role.users.add(user)

    def post(self, role, user_id, action):
        """Make a POST request to the endpoint using the provided parameters."""
        self._login_as_staff()
        return self.client.post(self.path(role=role), {'user_id': user_id, 'action': action})

    @ddt.data('get', 'post')
    def test_authentication_required(self, method):
        """Test and verify that authentication is required for this endpoint."""
        self.client.logout()
        response = getattr(self.client, method)(self.path())
        assert response.status_code == 401

    def test_oauth(self):
        """Test that OAuth authentication works for this endpoint."""
        oauth_headers = self._get_oauth_headers(self.user)
        self.client.logout()
        response = self.client.get(self.path(), **oauth_headers)
        assert response.status_code == 200
        body = {'user_id': 'staff', 'action': 'allow'}
        response = self.client.post(self.path(), body, format='json', **oauth_headers)
        assert response.status_code == 200

    @ddt.data(
        {'username': 'u1', 'is_staff': False, 'expected_status': 403},
        {'username': 'u2', 'is_staff': True, 'expected_status': 200},
    )
    @ddt.unpack
    def test_staff_permission_required(self, username, is_staff, expected_status):
        """Test and verify that only users with staff permission can access this endpoint."""
        UserFactory(username=username, password='edx', is_staff=is_staff)
        self.client.login(username=username, password='edx')
        response = self.client.get(self.path())
        assert response.status_code == expected_status

        response = self.client.post(self.path(), {'user_id': username, 'action': 'allow'}, format='json')
        assert response.status_code == expected_status

    def test_non_existent_course_id(self):
        """Test the response when the endpoint URL contains a non-existent course id."""
        self._login_as_staff()
        path = self.path(course_id='course-v1:a+b+c')
        response = self.client.get(path)

        assert response.status_code == 404

        response = self.client.post(path)
        assert response.status_code == 404

    def test_non_existent_course_role(self):
        """Test the response when the endpoint URL contains a non-existent role."""
        self._login_as_staff()
        path = self.path(role='A')
        response = self.client.get(path)

        assert response.status_code == 400

        response = self.client.post(path)
        assert response.status_code == 400

    @ddt.data(
        {'role': 'Moderator', 'count': 0},
        {'role': 'Moderator', 'count': 1},
        {'role': 'Group Moderator', 'count': 2},
        {'role': 'Community TA', 'count': 3},
    )
    @ddt.unpack
    def test_get_role_members(self, role, count):
        """Test the get role members endpoint response."""
        config_course_cohorts(self.course, is_cohorted=True)
        users = self._create_and_enroll_users(count=count)

        self._add_users_to_role(users, role)
        self._login_as_staff()
        response = self.client.get(self.path(role=role))

        assert response.status_code == 200

        content = json.loads(response.content.decode('utf-8'))
        assert content['course_id'] == 'course-v1:x+y+z'
        assert len(content['results']) == count
        expected_fields = ('username', 'email', 'first_name', 'last_name', 'group_name')
        for item in content['results']:
            for expected_field in expected_fields:
                assert expected_field in item
        assert content['division_scheme'] == 'cohort'

    def test_post_missing_body(self):
        """Test the response with a POST request without a body."""
        self._login_as_staff()
        response = self.client.post(self.path())
        assert response.status_code == 400

    @ddt.data(
        {'a': 1},
        {'user_id': 'xyz', 'action': 'allow'},
        {'user_id': 'staff', 'action': 123},
    )
    def test_missing_or_invalid_parameters(self, body):
        """
        Test the response when the POST request has missing required parameters or
        invalid values for the required parameters.
        """
        self._login_as_staff()
        response = self.client.post(self.path(), body)
        assert response.status_code == 400

        response = self.client.post(self.path(), body, format='json')
        assert response.status_code == 400

    @ddt.data(
        {'action': 'allow', 'user_in_role': False},
        {'action': 'allow', 'user_in_role': True},
        {'action': 'revoke', 'user_in_role': False},
        {'action': 'revoke', 'user_in_role': True}
    )
    @ddt.unpack
    def test_post_update_user_role(self, action, user_in_role):
        """Test the response when updating the user's role"""
        users = self._create_and_enroll_users(count=1)
        user = users[0]
        role = 'Moderator'
        if user_in_role:
            self._add_users_to_role(users, role)

        response = self.post(role, user.username, action)
        assert response.status_code == 200
        content = json.loads(response.content.decode('utf-8'))
        assertion = self.assertTrue if action == 'allow' else self.assertFalse
        assertion(any(user.username in x['username'] for x in content['results']))
