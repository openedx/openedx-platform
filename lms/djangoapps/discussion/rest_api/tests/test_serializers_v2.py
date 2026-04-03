# pylint: disable=unused-import
"""
Tests for Discussion API serializers
"""

import itertools
from unittest import mock

import ddt
import httpretty
from django.test.client import RequestFactory

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.discussion.rest_api.serializers import CommentSerializer, ThreadSerializer, get_context
from lms.djangoapps.discussion.rest_api.tests.utils import (
    ForumMockUtilsMixin,
    make_minimal_cs_comment,
    make_minimal_cs_thread
)
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role
)
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class CommentSerializerDeserializationTest(ForumMockUtilsMixin, SharedModuleStoreTestCase):
    """Tests for ThreadSerializer deserialization."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment"
        )
        self.mock_get_course_id_by_comment = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        )
        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user
        self.minimal_data = {
            "thread_id": "test_thread",
            "raw_body": "Test body",
        }
        self.existing_comment = Comment(**make_minimal_cs_comment({
            "id": "existing_comment",
            "thread_id": "dummy",
            "body": "Original body",
            "user_id": str(self.user.id),
            "username": self.user.username,
            "course_id": str(self.course.id),
        }))

    def save_and_reserialize(self, data, instance=None):
        """
        Create a serializer with the given data, ensure that it is valid, save
        the result, and return the full comment data from the serializer.
        """
        context = get_context(
            self.course,
            self.request,
            make_minimal_cs_thread({"course_id": str(self.course.id)})
        )
        serializer = CommentSerializer(
            instance,
            data=data,
            partial=(instance is not None),
            context=context
        )
        assert serializer.is_valid()
        serializer.save()
        return serializer.data

    @ddt.data(None, "test_parent")
    def test_create_success(self, parent_id):
        data = self.minimal_data.copy()
        if parent_id:
            data["parent_id"] = parent_id
            self.register_get_comment_response({"thread_id": "test_thread", "id": parent_id})
        self.register_post_comment_response(
            {"id": "test_comment", "username": self.user.username},
            thread_id="test_thread",
            parent_id=parent_id
        )
        saved = self.save_and_reserialize(data)
        expected_url = (
            f"/api/v1/comments/{parent_id}" if parent_id else
            "/api/v1/threads/test_thread/comments"
        )
        self.check_mock_called("create_parent_comment")
        params = {
            'course_id': str(self.course.id),
            'body': 'Test body',
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': False,
            'thread_id': 'test_thread',
        }
        if not data:
            self.check_mock_called_with(
                "create_parent_comment",
                0,
                **params,
            )
        assert saved['id'] == 'test_comment'
        assert saved['parent_id'] == parent_id

    def test_create_all_fields(self):
        data = self.minimal_data.copy()
        data["parent_id"] = "test_parent"
        data["endorsed"] = True
        self.register_get_comment_response({"thread_id": "test_thread", "id": "test_parent"})
        self.register_post_comment_response(
            {"id": "test_comment", "username": self.user.username},
            thread_id="test_thread",
            parent_id="test_parent"
        )
        self.save_and_reserialize(data)
        params = {
            'course_id': str(self.course.id),
            'body': 'Test body',
            'user_id': str(self.user.id),
            'endorsed': True,
            'anonymous': False,
            'anonymous_to_peers': False,
            'parent_comment_id': 'test_parent',
        }
        self.check_mock_called("create_parent_comment")
        self.check_mock_called_with(
            "create_child_comment",
            0,
            **params
        )

    def test_update_all(self):
        cs_response_data = self.existing_comment.attributes.copy()
        cs_response_data["endorsement"] = {
            "user_id": str(self.user.id),
            "time": "2015-06-05T00:00:00Z",
        }
        self.register_put_comment_response(cs_response_data)
        data = {"raw_body": "Edited body", "endorsed": True}
        self.register_get_thread_response(
            make_minimal_cs_thread({
                "id": "dummy",
                "course_id": str(self.course.id),
            })
        )
        saved = self.save_and_reserialize(data, instance=self.existing_comment)

        params = {
            'body': 'Edited body',
            'course_id': str(self.course.id),
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': 'False',
            'endorsed': 'True',
            'endorsement_user_id': str(self.user.id),
            'editing_user_id': str(self.user.id),
        }
        self.check_mock_called("update_comment")
        for key in data:
            assert saved[key] == data[key]
        assert saved['endorsed_by'] == self.user.username
        assert saved['endorsed_at'] == '2015-06-05T00:00:00Z'

    @ddt.data("", " ")
    def test_update_empty_raw_body(self, value):
        serializer = CommentSerializer(
            self.existing_comment,
            data={"raw_body": value},
            partial=True,
            context=get_context(self.course, self.request)
        )
        assert not serializer.is_valid()
        assert serializer.errors == {'raw_body': ['This field may not be blank.']}

    def test_create_parent_id_nonexistent(self):
        self.register_get_comment_error_response("bad_parent", 404)
        data = self.minimal_data.copy()
        data["parent_id"] = "bad_parent"
        context = get_context(self.course, self.request, make_minimal_cs_thread())
        serializer = CommentSerializer(data=data, context=context)
        assert not serializer.is_valid()
        assert serializer.errors == {
            'non_field_errors': ['parent_id does not identify a comment in the thread identified by thread_id.']
        }

    def test_create_parent_id_wrong_thread(self):
        self.register_get_comment_response({"thread_id": "different_thread", "id": "test_parent"})
        data = self.minimal_data.copy()
        data["parent_id"] = "test_parent"
        context = get_context(self.course, self.request, make_minimal_cs_thread())
        serializer = CommentSerializer(data=data, context=context)
        assert not serializer.is_valid()
        assert serializer.errors == {
            'non_field_errors': ['parent_id does not identify a comment in the thread identified by thread_id.']
        }

    def test_create_anonymous(self):
        """
        Test that serializer correctly deserializes the anonymous field when
        creating a new comment.
        """
        self.register_post_comment_response({"username": self.user.username}, thread_id="test_thread")
        data = self.minimal_data.copy()
        data["anonymous"] = True
        self.save_and_reserialize(data)
        call_args = self.get_mock_func_calls("create_parent_comment")[0]
        args, kwargs = call_args
        assert kwargs['anonymous']

    def test_create_anonymous_to_peers(self):
        """
        Test that serializer correctly deserializes the anonymous_to_peers
        field when creating a new comment.
        """
        self.register_post_comment_response({"username": self.user.username}, thread_id="test_thread")
        data = self.minimal_data.copy()
        data["anonymous_to_peers"] = True
        self.save_and_reserialize(data)
        call_args = self.get_mock_func_calls("create_parent_comment")[-1]
        args, kwargs = call_args
        assert kwargs['anonymous_to_peers']

    @ddt.data(None, -1, 0, 2, 5)
    def test_create_parent_id_too_deep(self, max_depth):
        with mock.patch("lms.djangoapps.discussion.django_comment_client.utils.MAX_COMMENT_DEPTH", max_depth):
            data = self.minimal_data.copy()
            context = get_context(self.course, self.request, make_minimal_cs_thread())
            if max_depth is None or max_depth >= 0:
                if max_depth != 0:
                    self.register_get_comment_response({
                        "id": "not_too_deep",
                        "thread_id": "test_thread",
                        "depth": max_depth - 1 if max_depth else 100
                    })
                    data["parent_id"] = "not_too_deep"
                else:
                    data["parent_id"] = None
                serializer = CommentSerializer(data=data, context=context)
                assert serializer.is_valid(), serializer.errors
            if max_depth is not None:
                if max_depth >= 0:
                    self.register_get_comment_response({
                        "id": "too_deep",
                        "thread_id": "test_thread",
                        "depth": max_depth
                    })
                    data["parent_id"] = "too_deep"
                else:
                    data["parent_id"] = None
                serializer = CommentSerializer(data=data, context=context)
                assert not serializer.is_valid()
                assert serializer.errors == {'non_field_errors': ['Comment level is too deep.']}

    def test_create_endorsed(self):
        # TODO: The comments service doesn't populate the endorsement field on
        # comment creation, so this is sadly realistic
        self.register_post_comment_response({"username": self.user.username}, thread_id="test_thread")
        data = self.minimal_data.copy()
        data["endorsed"] = True
        saved = self.save_and_reserialize(data)

        params = {
            'course_id': str(self.course.id),
            'body': 'Test body',
            'user_id': str(self.user.id),
            'endorsed': True,
            'anonymous': False,
            'anonymous_to_peers': False,
            'thread_id': 'test_thread',
        }
        self.check_mock_called("create_parent_comment")
        self.check_mock_called_with(
            "create_parent_comment",
            -1,
            **params
        )
        assert saved['endorsed']
        assert saved['endorsed_by'] is None
        assert saved['endorsed_by_label'] is None
        assert saved['endorsed_at'] is None

    def test_create_missing_field(self):
        for field in self.minimal_data:
            data = self.minimal_data.copy()
            data.pop(field)
            serializer = CommentSerializer(
                data=data,
                context=get_context(self.course, self.request, make_minimal_cs_thread())
            )
            assert not serializer.is_valid()
            assert serializer.errors == {field: ['This field is required.']}

    def test_update_empty(self):
        self.register_put_comment_response(self.existing_comment.attributes)
        self.save_and_reserialize({}, instance=self.existing_comment)
        parsed_body = {
            'body': 'Original body',
            'course_id': str(self.course.id),
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': False,
            'endorsed': False,
            'comment_id': 'existing_comment',
        }
        self.check_mock_called("update_comment")
        self.check_mock_called_with(
            "update_comment",
            -1,
            **parsed_body
        )

    def test_update_anonymous(self):
        """
        Test that serializer correctly deserializes the anonymous field when
        updating an existing comment.
        """
        self.register_put_comment_response(self.existing_comment.attributes)
        data = {
            "anonymous": True,
        }
        self.save_and_reserialize(data, self.existing_comment)
        call_args = self.get_mock_func_calls("update_comment")[0]
        args, kwargs = call_args
        assert kwargs['anonymous']

    def test_update_anonymous_to_peers(self):
        """
        Test that serializer correctly deserializes the anonymous_to_peers
        field when updating an existing comment.
        """
        self.register_put_comment_response(self.existing_comment.attributes)
        data = {
            "anonymous_to_peers": True,
        }
        self.save_and_reserialize(data, self.existing_comment)

        call_args = self.get_mock_func_calls("update_comment")[0]
        args, kwargs = call_args
        assert kwargs['anonymous_to_peers']

    @ddt.data("thread_id", "parent_id")
    def test_update_non_updatable(self, field):
        serializer = CommentSerializer(
            self.existing_comment,
            data={field: "different_value"},
            partial=True,
            context=get_context(self.course, self.request)
        )
        assert not serializer.is_valid()
        assert serializer.errors == {field: ['This field is not allowed in an update.']}


@ddt.ddt
class ThreadSerializerDeserializationTest(
        ForumMockUtilsMixin,
        UrlResetMixin,
        SharedModuleStoreTestCase
):
    """Tests for ThreadSerializer deserialization."""
    @classmethod
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user
        self.minimal_data = {
            "course_id": str(self.course.id),
            "topic_id": "test_topic",
            "type": "discussion",
            "title": "Test Title",
            "raw_body": "Test body",
        }
        self.existing_thread = Thread(**make_minimal_cs_thread({
            "id": "existing_thread",
            "course_id": str(self.course.id),
            "commentable_id": "original_topic",
            "thread_type": "discussion",
            "title": "Original Title",
            "body": "Original body",
            "user_id": str(self.user.id),
            "username": self.user.username,
            "read": "False",
            "endorsed": "False"
        }))

    def save_and_reserialize(self, data, instance=None):
        """
        Create a serializer with the given data and (if updating) instance,
        ensure that it is valid, save the result, and return the full thread
        data from the serializer.
        """
        serializer = ThreadSerializer(
            instance,
            data=data,
            partial=(instance is not None),
            context=get_context(self.course, self.request)
        )
        assert serializer.is_valid()
        serializer.save()
        return serializer.data

    def test_create_minimal(self):
        self.register_post_thread_response({"id": "test_id", "username": self.user.username})
        saved = self.save_and_reserialize(self.minimal_data)
        params = {
            'course_id': str(self.course.id),
            'commentable_id': 'test_topic',
            'thread_type': 'discussion',
            'title': 'Test Title',
            'body': 'Test body',
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': False,
        }
        self.check_mock_called("create_thread")
        self.check_mock_called_with(
            "create_thread",
            -1,
            **params
        )
        assert saved['id'] == 'test_id'

    def test_create_type(self):
        self.register_post_thread_response({"id": "test_id", "username": self.user.username})
        data = self.minimal_data.copy()
        data["type"] = "question"
        self.save_and_reserialize(data)

        data["type"] = "invalid_type"
        serializer = ThreadSerializer(data=data)
        assert not serializer.is_valid()

    def test_create_all_fields(self):
        self.register_post_thread_response({"id": "test_id", "username": self.user.username})
        data = self.minimal_data.copy()
        data["group_id"] = 42
        self.save_and_reserialize(data)
        params = {
            'course_id': str(self.course.id),
            'commentable_id': 'test_topic',
            'thread_type': 'discussion',
            'title': 'Test Title',
            'body': 'Test body',
            'user_id': str(self.user.id),
            'group_id': 42,
            'anonymous': False,
            'anonymous_to_peers': False,
        }
        self.check_mock_called("create_thread")
        self.check_mock_called_with(
            "create_thread",
            -1,
            **params
        )

    def test_create_anonymous(self):
        """
        Test that serializer correctly deserializes the anonymous field when
        creating a new thread.
        """
        self.register_post_thread_response({"id": "test_id", "username": self.user.username})
        data = self.minimal_data.copy()
        data["anonymous"] = True
        self.save_and_reserialize(data)
        call_args = self.get_mock_func_calls("create_thread")[0]
        args, kwargs = call_args
        assert kwargs['anonymous']

    def test_create_anonymous_to_peers(self):
        """
        Test that serializer correctly deserializes the anonymous_to_peers field
        when creating a new thread.
        """
        self.register_post_thread_response({"id": "test_id", "username": self.user.username})
        data = self.minimal_data.copy()
        data["anonymous_to_peers"] = True
        self.save_and_reserialize(data)
        call_args = self.get_mock_func_calls("create_thread")[0]
        args, kwargs = call_args
        assert kwargs['anonymous_to_peers']


@ddt.ddt
class SerializerTestMixin(UrlResetMixin, ForumMockUtilsMixin):
    """
    Test Mixin for Serializer tests
    """
    @classmethod
    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment"
        )
        self.mock_get_course_id_by_comment = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        )

        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)
        self.maxDiff = None  # pylint: disable=invalid-name
        self.user = UserFactory.create()
        self.register_get_user_response(self.user)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user
        self.author = UserFactory.create()

    def create_role(self, role_name, users, course=None):
        """Create a Role in self.course with the given name and users"""
        course = course or self.course
        role = Role.objects.create(name=role_name, course_id=course.id)
        role.users.set(users)

    @ddt.data(
        (FORUM_ROLE_ADMINISTRATOR, True, False, True),
        (FORUM_ROLE_ADMINISTRATOR, False, True, False),
        (FORUM_ROLE_MODERATOR, True, False, True),
        (FORUM_ROLE_MODERATOR, False, True, False),
        (FORUM_ROLE_COMMUNITY_TA, True, False, True),
        (FORUM_ROLE_COMMUNITY_TA, False, True, False),
        (FORUM_ROLE_STUDENT, True, False, True),
        (FORUM_ROLE_STUDENT, False, True, True),
    )
    @ddt.unpack
    def test_anonymity(self, role_name, anonymous, anonymous_to_peers, expected_serialized_anonymous):
        """
        Test that content is properly made anonymous.

        Content should be anonymous if the anonymous field is true or the
        anonymous_to_peers field is true and the requester does not have a
        privileged role.

        role_name is the name of the requester's role.
        anonymous is the value of the anonymous field in the content.
        anonymous_to_peers is the value of the anonymous_to_peers field in the
          content.
        expected_serialized_anonymous is whether the content should actually be
          anonymous in the API output when requested by a user with the given
          role.
        """
        self.register_get_user_response(self.user)
        self.create_role(role_name, [self.user])
        serialized = self.serialize(
            self.make_cs_content({"anonymous": anonymous, "anonymous_to_peers": anonymous_to_peers})
        )
        actual_serialized_anonymous = serialized["author"] is None
        assert actual_serialized_anonymous == expected_serialized_anonymous

    @ddt.data(
        (FORUM_ROLE_ADMINISTRATOR, False, "Moderator"),
        (FORUM_ROLE_ADMINISTRATOR, True, None),
        (FORUM_ROLE_MODERATOR, False, "Moderator"),
        (FORUM_ROLE_MODERATOR, True, None),
        (FORUM_ROLE_COMMUNITY_TA, False, "Community TA"),
        (FORUM_ROLE_COMMUNITY_TA, True, None),
        (FORUM_ROLE_STUDENT, False, None),
        (FORUM_ROLE_STUDENT, True, None),
    )
    @ddt.unpack
    def test_author_labels(self, role_name, anonymous, expected_label):
        """
        Test correctness of the author_label field.

        The label should be "Staff", "Moderator", or "Community TA" for the
        Administrator, Moderator, and Community TA roles, respectively, but
        the label should not be present if the content is anonymous.

        role_name is the name of the author's role.
        anonymous is the value of the anonymous field in the content.
        expected_label is the expected value of the author_label field in the
          API output.
        """
        self.register_get_user_response(self.user)
        self.create_role(role_name, [self.author])
        serialized = self.serialize(self.make_cs_content({"anonymous": anonymous}))
        assert serialized['author_label'] == expected_label

    def test_abuse_flagged(self):
        self.register_get_user_response(self.user)
        serialized = self.serialize(self.make_cs_content({"abuse_flaggers": [str(self.user.id)]}))
        assert serialized['abuse_flagged'] is True

    def test_voted(self):
        thread_id = "test_thread"
        self.register_get_user_response(self.user, upvoted_ids=[thread_id])
        serialized = self.serialize(self.make_cs_content({"id": thread_id}))
        assert serialized['voted'] is True


@ddt.ddt
class CommentSerializerTest(SerializerTestMixin, SharedModuleStoreTestCase):
    """Tests for CommentSerializer."""

    def setUp(self):
        super().setUp()
        self.endorser = UserFactory.create()
        self.endorsed_at = "2015-05-18T12:34:56Z"
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        """Stop patches after tests complete."""
        super().tearDownClass()
        super().disposeForumMocks()

    def make_cs_content(self, overrides=None, with_endorsement=False):
        """
        Create a comment with the given overrides, plus some useful test data.
        """
        merged_overrides = {
            "user_id": str(self.author.id),
            "username": self.author.username
        }
        if with_endorsement:
            merged_overrides["endorsement"] = {
                "user_id": str(self.endorser.id),
                "time": self.endorsed_at
            }
        merged_overrides.update(overrides or {})
        return make_minimal_cs_comment(merged_overrides)

    def serialize(self, comment, thread_data=None):
        """
        Create a serializer with an appropriate context and use it to serialize
        the given comment, returning the result.
        """
        context = get_context(self.course, self.request, make_minimal_cs_thread(thread_data))
        return CommentSerializer(comment, context=context).data

    def test_basic(self):
        self.register_get_user_response(self.user)
        comment = {
            "type": "comment",
            "id": "test_comment",
            "thread_id": "test_thread",
            "user_id": str(self.author.id),
            "username": self.author.username,
            "anonymous": False,
            "anonymous_to_peers": False,
            "created_at": "2015-04-28T00:00:00Z",
            "updated_at": "2015-04-28T11:11:11Z",
            "body": "Test body",
            "endorsed": False,
            "abuse_flaggers": [],
            "votes": {"up_count": 4},
            "children": [],
            "child_count": 0,
        }
        expected = {
            "anonymous": False,
            "anonymous_to_peers": False,
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": None,
            "author": self.author.username,
            "author_label": None,
            "created_at": "2015-04-28T00:00:00Z",
            "updated_at": "2015-04-28T11:11:11Z",
            "raw_body": "Test body",
            "rendered_body": "<p>Test body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 4,
            "children": [],
            "editable_fields": ["abuse_flagged", "voted"],
            "child_count": 0,
            "can_delete": False,
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

        assert self.serialize(comment) == expected

    @ddt.data(
        *itertools.product(
            [
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_STUDENT,
            ],
            [True, False]
        )
    )
    @ddt.unpack
    def test_endorsed_by(self, endorser_role_name, thread_anonymous):
        """
        Test correctness of the endorsed_by field.

        The endorser should be anonymous iff the thread is anonymous to the
        requester, and the endorser is not a privileged user.

        endorser_role_name is the name of the endorser's role.
        thread_anonymous is the value of the anonymous field in the thread.
        """
        self.register_get_user_response(self.user)
        self.create_role(endorser_role_name, [self.endorser])
        serialized = self.serialize(
            self.make_cs_content(with_endorsement=True),
            thread_data={"anonymous": thread_anonymous}
        )
        actual_endorser_anonymous = serialized["endorsed_by"] is None
        expected_endorser_anonymous = endorser_role_name == FORUM_ROLE_STUDENT and thread_anonymous
        assert actual_endorser_anonymous == expected_endorser_anonymous

    @ddt.data(
        (FORUM_ROLE_ADMINISTRATOR, "Moderator"),
        (FORUM_ROLE_MODERATOR, "Moderator"),
        (FORUM_ROLE_COMMUNITY_TA, "Community TA"),
        (FORUM_ROLE_STUDENT, None),
    )
    @ddt.unpack
    def test_endorsed_by_labels(self, role_name, expected_label):
        """
        Test correctness of the endorsed_by_label field.

        The label should be "Staff", "Moderator", or "Community TA" for the
        Administrator, Moderator, and Community TA roles, respectively.

        role_name is the name of the author's role.
        expected_label is the expected value of the author_label field in the
          API output.
        """
        self.register_get_user_response(self.user)
        self.create_role(role_name, [self.endorser])
        serialized = self.serialize(self.make_cs_content(with_endorsement=True))
        assert serialized['endorsed_by_label'] == expected_label

    def test_endorsed_at(self):
        self.register_get_user_response(self.user)
        serialized = self.serialize(self.make_cs_content(with_endorsement=True))
        assert serialized['endorsed_at'] == self.endorsed_at

    def test_children(self):
        self.register_get_user_response(self.user)
        comment = self.make_cs_content({
            "id": "test_root",
            "children": [
                self.make_cs_content({
                    "id": "test_child_1",
                    "parent_id": "test_root",
                }),
                self.make_cs_content({
                    "id": "test_child_2",
                    "parent_id": "test_root",
                    "children": [
                        self.make_cs_content({
                            "id": "test_grandchild",
                            "parent_id": "test_child_2"
                        })
                    ],
                }),
            ],
        })
        serialized = self.serialize(comment)
        assert serialized['children'][0]['id'] == 'test_child_1'
        assert serialized['children'][0]['parent_id'] == 'test_root'
        assert serialized['children'][1]['id'] == 'test_child_2'
        assert serialized['children'][1]['parent_id'] == 'test_root'
        assert serialized['children'][1]['children'][0]['id'] == 'test_grandchild'
        assert serialized['children'][1]['children'][0]['parent_id'] == 'test_child_2'


@ddt.ddt
class ThreadSerializerSerializationTest(SerializerTestMixin, SharedModuleStoreTestCase, ForumMockUtilsMixin):
    """Tests for ThreadSerializer serialization."""

    def make_cs_content(self, overrides):
        """
        Create a thread with the given overrides, plus some useful test data.
        """
        merged_overrides = {
            "course_id": str(self.course.id),
            "user_id": str(self.author.id),
            "username": self.author.username,
            "read": True,
            "endorsed": True,
            "resp_total": 0,
        }
        merged_overrides.update(overrides)
        return make_minimal_cs_thread(merged_overrides)

    def serialize(self, thread):
        """
        Create a serializer with an appropriate context and use it to serialize
        the given thread, returning the result.
        """
        return ThreadSerializer(thread, context=get_context(self.course, self.request)).data

    def test_basic(self):
        thread = make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(self.author.id),
            "username": self.author.username,
            "title": "Test Title",
            "body": "Test body",
            "pinned": True,
            "votes": {"up_count": 4},
            "comments_count": 5,
            "unread_comments_count": 3,
        })
        expected = self.expected_thread_data({
            "author": self.author.username,
            "can_delete": False,
            "vote_count": 4,
            "comment_count": 6,
            "unread_comment_count": 3,
            "pinned": True,
            "editable_fields": ["abuse_flagged", "copy_link", "following", "read", "voted"],
            "abuse_flagged_count": None,
            "edit_by_label": None,
            "closed_by_label": None,
        })
        assert self.serialize(thread) == expected

        thread["thread_type"] = "question"
        expected.update({
            "type": "question",
            "comment_list_url": None,
            "endorsed_comment_list_url": (
                "http://testserver/api/discussion/v1/comments/?thread_id=test_thread&endorsed=True"
            ),
            "non_endorsed_comment_list_url": (
                "http://testserver/api/discussion/v1/comments/?thread_id=test_thread&endorsed=False"
            ),
        })
        assert self.serialize(thread) == expected

    def test_pinned_missing(self):
        """
        Make sure that older threads in the comments service without the pinned
        field do not break serialization
        """
        thread_data = self.make_cs_content({})
        del thread_data["pinned"]
        self.register_get_thread_response(thread_data)
        serialized = self.serialize(thread_data)
        assert serialized['pinned'] is False

    def test_group(self):
        self.course.cohort_config = {"cohorted": True}
        modulestore().update_item(self.course, ModuleStoreEnum.UserID.test)
        cohort = CohortFactory.create(course_id=self.course.id)
        serialized = self.serialize(self.make_cs_content({"group_id": cohort.id}))
        assert serialized['group_id'] == cohort.id
        assert serialized['group_name'] == cohort.name

    def test_following(self):
        thread_id = "test_thread"
        self.register_get_user_response(self.user, subscribed_thread_ids=[thread_id])
        serialized = self.serialize(self.make_cs_content({"id": thread_id}))
        assert serialized['following'] is True

    def test_response_count(self):
        thread_data = self.make_cs_content({"resp_total": 2})
        self.register_get_thread_response(thread_data)
        serialized = self.serialize(thread_data)
        assert serialized['response_count'] == 2

    def test_response_count_missing(self):
        thread_data = self.make_cs_content({})
        del thread_data["resp_total"]
        self.register_get_thread_response(thread_data)
        serialized = self.serialize(thread_data)
        assert 'response_count' not in serialized

    @ddt.data(
        (FORUM_ROLE_MODERATOR, True),
        (FORUM_ROLE_STUDENT, False),
        ("author", True),
    )
    @ddt.unpack
    def test_closed_by_label_field(self, role, visible):
        """
        Tests if closed by field is visible to author and priviledged users
        """
        moderator = UserFactory()
        request_role = FORUM_ROLE_STUDENT if role == "author" else role
        author = self.user if role == "author" else self.author
        self.create_role(FORUM_ROLE_MODERATOR, [moderator])
        self.create_role(request_role, [self.user])

        thread = make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(author.id),
            "username": author.username,
            "title": "Test Title",
            "body": "Test body",
            "pinned": True,
            "votes": {"up_count": 4},
            "comments_count": 5,
            "unread_comments_count": 3,
            "closed_by": moderator
        })
        closed_by_label = "Moderator" if visible else None
        closed_by = moderator if visible else None
        can_delete = role != FORUM_ROLE_STUDENT
        editable_fields = ["abuse_flagged", "copy_link", "following", "read", "voted"]
        if role == "author":
            editable_fields.remove("voted")
            editable_fields.extend(['anonymous', 'raw_body', 'title', 'topic_id', 'type'])
        elif role == FORUM_ROLE_MODERATOR:
            editable_fields.extend(['close_reason_code', 'closed', 'edit_reason_code', 'pinned',
                                    'raw_body', 'title', 'topic_id', 'type'])
        expected = self.expected_thread_data({
            "author": author.username,
            "can_delete": can_delete,
            "vote_count": 4,
            "comment_count": 6,
            "unread_comment_count": 3,
            "pinned": True,
            "editable_fields": sorted(editable_fields),
            "abuse_flagged_count": None,
            "edit_by_label": None,
            "closed_by_label": closed_by_label,
            "closed_by": closed_by,
        })
        assert self.serialize(thread) == expected

    @ddt.data(
        (FORUM_ROLE_MODERATOR, True),
        (FORUM_ROLE_STUDENT, False),
        ("author", True),
    )
    @ddt.unpack
    def test_edit_by_label_field(self, role, visible):
        """
        Tests if closed by field is visible to author and priviledged users
        """
        moderator = UserFactory()
        request_role = FORUM_ROLE_STUDENT if role == "author" else role
        author = self.user if role == "author" else self.author
        self.create_role(FORUM_ROLE_MODERATOR, [moderator])
        self.create_role(request_role, [self.user])

        thread = make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "user_id": str(author.id),
            "username": author.username,
            "title": "Test Title",
            "body": "Test body",
            "pinned": True,
            "votes": {"up_count": 4},
            "edit_history": [{"editor_username": moderator}],
            "comments_count": 5,
            "unread_comments_count": 3,
            "closed_by": None
        })
        edit_by_label = "Moderator" if visible else None
        can_delete = role != FORUM_ROLE_STUDENT
        last_edit = None if role == FORUM_ROLE_STUDENT else {"editor_username": moderator}
        editable_fields = ["abuse_flagged", "copy_link", "following", "read", "voted"]

        if role == "author":
            editable_fields.remove("voted")
            editable_fields.extend(['anonymous', 'raw_body', 'title', 'topic_id', 'type'])

        elif role == FORUM_ROLE_MODERATOR:
            editable_fields.extend(['close_reason_code', 'closed', 'edit_reason_code', 'pinned',
                                    'raw_body', 'title', 'topic_id', 'type'])

        expected = self.expected_thread_data({
            "author": author.username,
            "can_delete": can_delete,
            "vote_count": 4,
            "comment_count": 6,
            "unread_comment_count": 3,
            "pinned": True,
            "editable_fields": sorted(editable_fields),
            "abuse_flagged_count": None,
            "last_edit": last_edit,
            "edit_by_label": edit_by_label,
            "closed_by_label": None,
            "closed_by": None,
        })
        assert self.serialize(thread) == expected

    def test_get_preview_body(self):
        """
        Test for the 'get_preview_body' method.

        This test verifies that the 'get_preview_body' method returns a cleaned
        version of the thread's body that is suitable for display as a preview.
        The test specifically focuses on handling the presence of multiple
        spaces within the body.
        """
        thread_data = self.make_cs_content(
            {"body": "<p>This  is  a test thread body  with some text.</p>"}
        )
        serialized = self.serialize(thread_data)
        assert serialized['preview_body'] == "This  is  a test thread body  with some text."
