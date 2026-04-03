"""
Discussion API views
"""
import logging
import uuid

import edx_api_doc_tools as apidocs
from opaque_keys import InvalidKeyError
from django.contrib.auth import get_user_model
from django.core.exceptions import BadRequest, ValidationError
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from xmodule.modulestore.django import modulestore

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.file import store_uploaded_file
from lms.djangoapps.course_api.blocks.api import get_blocks
from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.discussion.rate_limit import is_content_creation_rate_limited
from lms.djangoapps.discussion.rest_api.permissions import IsAllowedToBulkDelete
from lms.djangoapps.discussion.rest_api.tasks import delete_course_post_for_user
from lms.djangoapps.discussion.toggles import ONLY_VERIFIED_USERS_CAN_POST
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSION_BAN
from lms.djangoapps.discussion.django_comment_client import settings as cc_settings
from lms.djangoapps.discussion.django_comment_client.utils import get_group_id_for_comments_service
from lms.djangoapps.instructor.access import update_forum_role
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.discussions.serializers import DiscussionSettingsSerializer
from openedx.core.djangoapps.django_comment_common import comment_client
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings, Role
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.user_api.accounts.permissions import CanReplaceUsername, CanRetireUser
from openedx.core.djangoapps.user_api.models import UserRetirementStatus
from openedx.core.lib.api.authentication import BearerAuthentication, BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from ..rest_api.api import (
    create_comment,
    create_thread,
    delete_comment,
    delete_thread,
    get_comment_list,
    get_course,
    get_course_discussion_user_stats,
    get_course_topics,
    get_course_topics_v2,
    get_response_comments,
    get_thread,
    get_thread_list,
    get_learner_active_thread_list,
    get_user_comments,
    get_v2_course_topics_as_v1,
    update_comment,
    update_thread,
)
from ..rest_api.forms import (
    CommentGetForm,
    CommentListGetForm,
    CourseActivityStatsForm,
    CourseDiscussionRolesForm,
    CourseDiscussionSettingsForm,
    ThreadListGetForm,
    TopicListGetForm,
    UserCommentListGetForm,
    UserOrdering,
)
from ..rest_api.permissions import IsStaffOrAdmin, IsStaffOrCourseTeamOrEnrolled
from ..rest_api.serializers import (
    CourseMetadataSerailizer,
    DiscussionRolesListSerializer,
    DiscussionRolesSerializer,
    DiscussionTopicSerializerV2,
    TopicOrdering,
)
from .utils import (
    create_blocks_params,
    create_topics_v3_structure,
    is_captcha_enabled,
    verify_recaptcha_token,
    get_course_id_from_thread_id,
    is_only_student,
)

log = logging.getLogger(__name__)

User = get_user_model()


@view_auth_classes()
class CourseView(DeveloperErrorViewMixin, APIView):
    """
    General discussion metadata API.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID")
        ],
        responses={
            200: CourseMetadataSerailizer(read_only=True, required=False),
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        }
    )
    def get(self, request, course_id):
        """
        Retrieve general discussion metadata for a course.

        **Example Requests**:

            GET /api/discussion/v1/courses/course-v1:ExampleX+Subject101+2015
        """
        course_key = CourseKey.from_string(course_id)  # TODO: which class is right?
        # Record user activity for tracking progress towards a user's course goals (for mobile app)
        UserActivity.record_user_activity(request.user, course_key, request=request, only_if_mobile_app=True)
        return Response(get_course(request, course_key))


@view_auth_classes()
class CourseViewV2(DeveloperErrorViewMixin, APIView):
    """
    General discussion metadata API v2.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID")
        ],
        responses={
            200: CourseMetadataSerailizer(read_only=True, required=False),
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        }
    )
    def get(self, request, course_id):
        """
        Retrieve general discussion metadata for a course.

        **Example Requests**:
            GET /api/discussion/v2/courses/course-v1:ExampleX+Subject101+2015
        """
        course_key = CourseKey.from_string(course_id)
        # Record user activity for tracking progress towards a user's course goals (for mobile app)
        UserActivity.record_user_activity(request.user, course_key, request=request, only_if_mobile_app=True)
        return Response(get_course(request, course_key, False))


@view_auth_classes()
class CourseActivityStatsView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Fetch statistics about a user's activity in a course.

    **Example Requests**:

        GET /api/discussion/v1/courses/course-v1:ExampleX+Subject101+2015/activity_stats?order_by=activity

    **Response Values**:


    **Example Response**
    ```json
    {
        "pagination": {
            "count": 3,
            "next": null,
            "num_pages": 1,
            "previous": null
        },
        "results": [
            {
                "active_flags": 3,
                "inactive_flags": 0,
                "replies": 13,
                "responses": 21,
                "threads": 32,
                "username": "edx"
            },
            {
                "active_flags": 1,
                "inactive_flags": 0,
                "replies": 6,
                "responses": 8,
                "threads": 13,
                "username": "honor"
            },
            ...
        ]
    }
    ```
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (
        permissions.IsAuthenticated,
        IsStaffOrCourseTeamOrEnrolled,
    )

    def get(self, request, course_key_string):
        """Implements the GET method as described in the class docstring."""
        form_query_string = CourseActivityStatsForm(request.query_params)
        if not form_query_string.is_valid():
            raise ValidationError(form_query_string.errors)
        order_by = form_query_string.cleaned_data.get('order_by', None)
        order_by = UserOrdering(order_by) if order_by else None
        username_search_string = form_query_string.cleaned_data.get('username', None)
        data = get_course_discussion_user_stats(
            request,
            course_key_string,
            form_query_string.cleaned_data['page'],
            form_query_string.cleaned_data['page_size'],
            order_by,
            username_search_string,
        )
        return data


@view_auth_classes()
class CourseTopicsView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Retrieve the topic listing for a course. Only topics accessible to the
        authenticated user are included.

    **Example Requests**:

        GET /api/discussion/v1/course_topics/course-v1:ExampleX+Subject101+2015
            ?topic_id={topic_id_1, topid_id_2}

    **Response Values**:
        * courseware_topics: The list of topic trees for courseware-linked
            topics. Each item in the list includes:

            * id: The id of the discussion topic (null for a topic that only
              has children but cannot contain threads itself).

            * name: The display name of the topic.

            * children: A list of child subtrees of the same format.

        * non_courseware_topics: The list of topic trees that are not linked to
              courseware. Items are of the same format as in courseware_topics.
    """

    def get(self, request, course_id):
        """
        Implements the GET method as described in the class docstring.
        """
        course_key = CourseKey.from_string(course_id)
        topic_ids = self.request.GET.get('topic_id')
        topic_ids = set(topic_ids.strip(',').split(',')) if topic_ids else None
        with modulestore().bulk_operations(course_key):
            configuration = DiscussionsConfiguration.get(context_key=course_key)
            provider = configuration.provider_type
            # This will be removed when mobile app will support new topic structure
            new_structure_enabled = ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled(course_key)
            if provider == Provider.OPEN_EDX and new_structure_enabled:
                response = get_v2_course_topics_as_v1(
                    request,
                    course_key,
                    topic_ids
                )
            else:
                response = get_course_topics(
                    request,
                    course_key,
                    topic_ids,
                )
            # Record user activity for tracking progress towards a user's course goals (for mobile app)
            UserActivity.record_user_activity(request.user, course_key, request=request, only_if_mobile_app=True)
        return Response(response)


@view_auth_classes()
class CourseTopicsViewV2(DeveloperErrorViewMixin, APIView):
    """
    View for listing course topics.

    For more information visit the
    [API Documentation](/api-docs/?filter=discussion#/discussion/discussion_v2_course_topics_read)
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course ID",
            ),
            apidocs.string_parameter(
                'topic_id',
                apidocs.ParameterLocation.QUERY,
                description="Comma-separated list of topic ids to filter",
            ),
            openapi.Parameter(
                'order_by',
                apidocs.ParameterLocation.QUERY,
                required=False,
                type=openapi.TYPE_STRING,
                enum=list(TopicOrdering),
                description="Sort ordering for topics",
            ),
        ],
        responses={
            200: DiscussionTopicSerializerV2(read_only=True, required=False),
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        }
    )
    def get(self, request, course_id):
        """
        **Use Cases**

            Retrieve the topic listing for a course.

        **Example Requests**:

            GET /api/discussion/v2/course_topics/course-v1:ExampleX+Subject101+2015
                ?topic_id={topic_id_1, topid_id_2}&order_by=course_structure
        """
        course_key = CourseKey.from_string(course_id)
        form_query_params = TopicListGetForm(self.request.query_params)
        if not form_query_params.is_valid():
            raise ValidationError(form_query_params.errors)
        response = get_course_topics_v2(
            course_key,
            request.user,
            form_query_params.cleaned_data["topic_id"],
            form_query_params.cleaned_data["order_by"]
        )
        return Response(response)


@view_auth_classes()
class CourseTopicsViewV3(DeveloperErrorViewMixin, APIView):
    """
    View for listing course topics v3.

    ** Response Example **:
    [
        {
            "id": "non-courseware-discussion-id",
            "usage_key": None,
            "name": "Non Courseware Topic",
            "thread_counts": {"discussion": 0, "question": 0},
            "enabled_in_context": true,
            "courseware": false
        },
        {
            "id": "id",
            "block_id": "block_id",
            "lms_web_url": "",
            "legacy_web_url": "",
            "student_view_url": "",
            "type": "chapter",
            "display_name": "First section",
            "children": [
                "id": "id",
                "block_id": "block_id",
                "lms_web_url": "",
                "legacy_web_url": "",
                "student_view_url": "",
                "type": "sequential",
                "display_name": "First Sub-Section",
                "children": [
                    "id": "id",
                    "usage_key": "",
                    "name": "First Unit?",
                    "thread_counts": { "discussion": 0, "question": 0 },
                    "enabled_in_context": true
                ]
            ],
            "courseware": true,
        }
    ]
    """

    def get(self, request, course_id):
        """
        **Use Cases**

            Retrieve the topic listing for a course.

        **Example Requests**:

            GET /api/discussion/v3/course_topics/course-v1:ExampleX+Subject101+2015
        """
        course_key = CourseKey.from_string(course_id)
        topics = get_course_topics_v2(
            course_key,
            request.user,
        )
        course_usage_key = modulestore().make_course_usage_key(course_key)
        blocks_params = create_blocks_params(course_usage_key, request.user)
        blocks = get_blocks(
            request,
            blocks_params['usage_key'],
            blocks_params['user'],
            blocks_params['depth'],
            blocks_params['nav_depth'],
            blocks_params['requested_fields'],
            blocks_params['block_counts'],
            blocks_params['student_view_data'],
            blocks_params['return_type'],
            blocks_params['block_types_filter'],
            hide_access_denials=False,
        )['blocks']

        topics = create_topics_v3_structure(blocks, topics)
        return Response(topics)


@view_auth_classes()
class ThreadViewSet(DeveloperErrorViewMixin, ViewSet):
    """
    **Use Cases**

        Retrieve the list of threads for a course, retrieve thread details,
        post a new thread, or modify or delete an existing thread.

    **Example Requests**:

        GET /api/discussion/v1/threads/?course_id=ExampleX/Demo/2015

        GET /api/discussion/v1/threads/{thread_id}

        POST /api/discussion/v1/threads
        {
          "course_id": "foo/bar/baz",
          "topic_id": "quux",
          "type": "discussion",
          "title": "Title text",
          "raw_body": "Body text"
        }

        PATCH /api/discussion/v1/threads/thread_id
        {"raw_body": "Edited text"}
        Content Type: "application/merge-patch+json"

        DELETE /api/discussion/v1/threads/thread_id

    **GET Thread List Parameters**:

        * course_id (required): The course to retrieve threads for

        * page: The (1-indexed) page to retrieve (default is 1)

        * page_size: The number of items per page (default is 10, max is 100)

        * topic_id: The id of the topic to retrieve the threads. There can be
            multiple topic_id queries to retrieve threads from multiple topics
            at once.

        * author: The username of an author. If provided, only threads by this
            author will be returned.

        * thread_type: Can be 'discussion' or 'question', only return threads of
            the selected thread type.

        * flagged: If True, only return threads that have been flagged (reported)

        * count_flagged: If True, return the count of flagged comments for each thread.
          (can only be used by moderators or above)

        * text_search: A search string to match. Any thread whose content
            (including the bodies of comments in the thread) matches the search
            string will be returned.

        * order_by: Must be "last_activity_at", "comment_count", or
            "vote_count". The key to sort the threads by. The default is
            "last_activity_at".

        * order_direction: Must be "desc". The direction in which to sort the
            threads by. The default and only value is "desc". This will be
            removed in a future major version.

        * following: If true, retrieve only threads the requesting user is
            following

        * view: "unread" for threads the requesting user has not read, or
            "unanswered" for question threads with no marked answer. Only one
            can be selected, or unresponded for discussion type posts with no response

        * requested_fields: (list) Indicates which additional fields to return
          for each thread. (supports 'profile_image')

        The topic_id, text_search, and following parameters are mutually
        exclusive (i.e. only one may be specified in a request)

    **GET Thread Parameters**:

        * thread_id (required): The id of the thread

        * requested_fields (optional parameter): (list) Indicates which additional
         fields to return for each thread. (supports 'profile_image')

    **POST Parameters**:

        * course_id (required): The course to create the thread in

        * topic_id (required): The topic to create the thread in

        * type (required): The thread's type (either "question" or "discussion")

        * title (required): The thread's title

        * raw_body (required): The thread's raw body text

        * following (optional): A boolean indicating whether the user should
            follow the thread upon its creation; defaults to false

        * anonymous (optional): A boolean indicating whether the post is
        anonymous; defaults to false

        * anonymous_to_peers (optional): A boolean indicating whether the post
        is anonymous to peers; defaults to false

    **PATCH Parameters**:

        * abuse_flagged (optional): A boolean to mark thread as abusive

        * voted (optional): A boolean to vote for thread

        * read (optional): A boolean to mark thread as read

        * closed (optional, privileged): A boolean to mark thread as closed.

        * edit_reason_code (optional, privileged): A string containing a reason
        code for editing the thread's body.

        * close_reason_code (optional, privileged): A string containing a reason
        code for closing the thread.

        * topic_id, type, title, raw_body, anonymous, and anonymous_to_peers
        are accepted with the same meaning as in a POST request

        If "application/merge-patch+json" is not the specified content type,
        a 415 error is returned.

    **GET Thread List Response Values**:

        * results: The list of threads; each item in the list has the same
            fields as the POST/PATCH response below

        * next: The URL of the next page (or null if first page)

        * previous: The URL of the previous page (or null if last page)

        * text_search_rewrite: The search string to which the text_search
            parameter was rewritten in order to match threads (e.g. for spelling
            correction)

    **GET Thread Details Response Values**:

        Same response fields as the POST/PATCH response below

    **POST/PATCH response values**:

        * id: The id of the thread

        * course_id: The id of the thread's course

        * topic_id: The id of the thread's topic

        * created_at: The ISO 8601 timestamp for the creation of the thread

        * updated_at: The ISO 8601 timestamp for the last modification of
            the thread, which may not have been an update of the title/body

        * type: The thread's type (either "question" or "discussion")

        * title: The thread's title

        * raw_body: The thread's raw body text without any rendering applied

        * pinned: Boolean indicating whether the thread has been pinned

        * closed: Boolean indicating whether the thread has been closed

        * comment_count: The number of comments within the thread

        * unread_comment_count: The number of comments within the thread
            that were created or updated since the last time the user read
            the thread

        * editable_fields: The fields that the requesting user is allowed to
            modify with a PATCH request

        * read: Boolean indicating whether the user has read this thread

        * has_endorsed: Boolean indicating whether this thread has been answered

        * response_count: The number of direct responses for a thread

        * abuse_flagged_count: The number of flags(reports) on and within the
            thread. Returns null if requesting user is not a moderator

        * anonymous: A boolean indicating whether the post is anonymous

        * anonymous_to_peers: A boolean indicating whether the post is
        anonymous to peers

    **DELETE response values:

        No content is returned for a DELETE request

    """
    lookup_field = "thread_id"
    parser_classes = (JSONParser, MergePatchParser,)

    def list(self, request):
        """
        Implements the GET method for the list endpoint as described in the
        class docstring.
        """
        form = ThreadListGetForm(request.GET)
        if not form.is_valid():
            raise ValidationError(form.errors)

        # Record user activity for tracking progress towards a user's course goals (for mobile app)
        UserActivity.record_user_activity(
            request.user, form.cleaned_data["course_id"], request=request, only_if_mobile_app=True
        )

        return get_thread_list(
            request,
            form.cleaned_data["course_id"],
            form.cleaned_data["page"],
            form.cleaned_data["page_size"],
            form.cleaned_data["topic_id"],
            form.cleaned_data["text_search"],
            form.cleaned_data["following"],
            form.cleaned_data["author"],
            form.cleaned_data["thread_type"],
            form.cleaned_data["flagged"],
            form.cleaned_data["view"],
            form.cleaned_data["order_by"],
            form.cleaned_data["order_direction"],
            form.cleaned_data["requested_fields"],
            form.cleaned_data["count_flagged"],
        )

    def retrieve(self, request, thread_id=None):
        """
        Implements the GET method for thread ID
        """
        requested_fields = request.GET.get('requested_fields')
        course_id = request.GET.get('course_id')
        return Response(get_thread(request, thread_id, requested_fields, course_id))

    def create(self, request):
        """
        Implements the POST method for the list endpoint as described in the
        class docstring.
        """
        if not request.data.get("course_id"):
            raise ValidationError({"course_id": ["This field is required."]})
        course_key_str = request.data.get("course_id")
        course_key = CourseKey.from_string(course_key_str)

        if is_content_creation_rate_limited(request, course_key=course_key):
            return Response("Too many requests", status=status.HTTP_429_TOO_MANY_REQUESTS)

        if is_captcha_enabled(course_key) and is_only_student(course_key, request.user):
            captcha_token = request.data.get('captcha_token')
            if not captcha_token:
                raise ValidationError({'captcha_token': 'This field is required.'})

            if not verify_recaptcha_token(captcha_token):
                return Response({'error': 'CAPTCHA verification failed.'}, status=400)

        if ONLY_VERIFIED_USERS_CAN_POST.is_enabled(course_key) and not request.user.is_active:
            raise ValidationError({"detail": "Only verified users can post in discussions."})

        data = request.data.copy()
        data.pop('captcha_token', None)
        return Response(create_thread(request, data))

    def partial_update(self, request, thread_id):
        """
        Implements the PATCH method for the instance endpoint as described in
        the class docstring.
        """
        if request.content_type != MergePatchParser.media_type:
            raise UnsupportedMediaType(request.content_type)
        return Response(update_thread(request, thread_id, request.data))

    def destroy(self, request, thread_id):
        """
        Implements the DELETE method for the instance endpoint as described in
        the class docstring
        """
        delete_thread(request, thread_id)
        return Response(status=204)


class LearnerThreadView(APIView):
    """
    **Use Cases**

        Fetch user's active threads

    **Example Requests**:

        GET /api/discussion/v1/courses/course-v1:ExampleX+Subject101+2015/learner/?username=edx&page=1&page_size=10

    **GET Thread List Parameters**:

        * username: (Required) Username of the user whose active threads are required

        * page: The (1-indexed) page to retrieve (default is 1)

        * page_size: The number of items per page (default is 10)

        * count_flagged: If True, return the count of flagged comments for each thread.
        (can only be used by moderators or above)

        * thread_type: The type of thread to filter None, "discussion" or "question".

        * order_by: Sort order for threads "last_activity_at", "comment_count" or
        "vote_count".

        * status: Filter for threads "flagged", "unanswered", "unread".

        * group_id: Filter threads w.r.t cohorts (Cohort ID).
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthentication,
        SessionAuthentication,
    )
    permission_classes = (
        permissions.IsAuthenticated,
        IsStaffOrCourseTeamOrEnrolled,
    )

    def get(self, request, course_id=None):
        """
        Implements the GET method as described in the class docstring.
        """
        course_key = CourseKey.from_string(course_id)
        page_num = request.GET.get('page', 1)
        threads_per_page = request.GET.get('page_size', 10)
        count_flagged = request.GET.get('count_flagged', False)
        thread_type = request.GET.get('thread_type')
        order_by = request.GET.get('order_by')
        order_by_mapping = {
            "last_activity_at": "activity",
            "comment_count": "comments",
            "vote_count": "votes"
        }
        order_by = order_by_mapping.get(order_by, 'activity')
        post_status = request.GET.get('status', None)
        discussion_id = None
        username = request.GET.get('username', None)
        user = get_object_or_404(User, username=username)
        group_id = None
        try:
            group_id = get_group_id_for_comments_service(request, course_key, discussion_id)
        except ValueError:
            pass

        query_params = {
            "page": page_num,
            "per_page": threads_per_page,
            "course_id": str(course_key),
            "user_id": user.id,
            "group_id": group_id,
            "count_flagged": count_flagged,
            "thread_type": thread_type,
            "sort_key": order_by,
        }
        if post_status:
            if post_status not in ['flagged', 'unanswered', 'unread', 'unresponded']:
                raise ValidationError({
                    "status": [
                        f"Invalid value. '{post_status}' must be 'flagged', 'unanswered', 'unread' or 'unresponded"
                    ]
                })
            query_params[post_status] = True
        return get_learner_active_thread_list(request, course_key, query_params)


@view_auth_classes()
class CommentViewSet(DeveloperErrorViewMixin, ViewSet):
    """
    **Use Cases**

        Retrieve the list of comments in a thread, retrieve the list of
        comments from an user in a course, retrieve the list of child
        comments for a response comment, create a comment, or modify or
        delete an existing comment.

    **Example Requests**:

        GET /api/discussion/v1/comments/?username=edx&course_id=course-v1:edX+DemoX+Demo_Course

        GET /api/discussion/v1/comments/?username=edx&course_id=course-v1:edX+DemoX+Demo_Course&flagged=true

        GET /api/discussion/v1/comments/?thread_id=0123456789abcdef01234567

        GET /api/discussion/v1/comments/2123456789abcdef01234555

        POST /api/discussion/v1/comments/
        {
            "thread_id": "0123456789abcdef01234567",
            "raw_body": "Body text"
        }

        PATCH /api/discussion/v1/comments/comment_id
        {"raw_body": "Edited text"}
        Content Type: "application/merge-patch+json"

        DELETE /api/discussion/v1/comments/comment_id

    **GET Comment List Parameters**:

        * thread_id (required when username is not provided): The thread to retrieve comments for

        * username (required when thread_id is not provided): The user from whom to retrieve comments

        * course_id (required when username is provided): The course from which to retrive the user's comments

        * endorsed: If specified, only retrieve the endorsed or non-endorsed
          comments accordingly. Required for a question thread, must be absent
          for a discussion thread.
          This parameter has no effect when fetching comments by `username`.

        * flagged: Only retrieve comments that were flagged for abuse.
          This requires the requester to have elevated privileges, and
          has no effect otherwise.

        * page: The (1-indexed) page to retrieve (default is 1)

        * page_size: The number of items per page (default is 10, max is 100)

        * requested_fields: (list) Indicates which additional fields to return
          for each thread. (supports 'profile_image')

    **GET Child Comment List Parameters**:

        * comment_id (required): The comment to retrieve child comments for

        * page: The (1-indexed) page to retrieve (default is 1)

        * page_size: The number of items per page (default is 10, max is 100)

        * requested_fields: (list) Indicates which additional fields to return
          for each thread. (supports 'profile_image')


    **POST Parameters**:

        * thread_id (required): The thread to post the comment in

        * parent_id: The parent comment of the new comment. Can be null or
          omitted for a comment that should be directly under the thread

        * raw_body: The comment's raw body text

        * anonymous (optional): A boolean indicating whether the comment is
        anonymous; defaults to false

        * anonymous_to_peers (optional): A boolean indicating whether the
        comment is anonymous to peers; defaults to false

    **PATCH Parameters**:

        * raw_body, anonymous and anonymous_to_peers are accepted with the same
        meaning as in a POST request

        * edit_reason_code (optional, privileged): A string containing a reason
        code for a moderator to edit the comment.

        If "application/merge-patch+json" is not the specified content type,
        a 415 error is returned.

    **GET Response Values**:

        * results: The list of comments; each item in the list has the same
            fields as the POST response below

        * next: The URL of the next page (or null if first page)

        * previous: The URL of the previous page (or null if last page)

    **POST/PATCH Response Values**:

        * id: The id of the comment

        * thread_id: The id of the comment's thread

        * parent_id: The id of the comment's parent

        * author: The username of the comment's author, or None if the
          comment is anonymous

        * author_label: A label indicating whether the author has a special
          role in the course, either "Staff" for moderators and
          administrators or "Community TA" for community TAs

        * created_at: The ISO 8601 timestamp for the creation of the comment

        * updated_at: The ISO 8601 timestamp for the last modification of
            the comment, which may not have been an update of the body

        * raw_body: The comment's raw body text without any rendering applied

        * endorsed: Boolean indicating whether the comment has been endorsed
            (by a privileged user or, for a question thread, the thread
            author)

        * endorsed_by: The username of the endorsing user, if available

        * endorsed_by_label: A label indicating whether the endorsing user
            has a special role in the course (see author_label)

        * endorsed_at: The ISO 8601 timestamp for the endorsement, if
            available

        * abuse_flagged: Boolean indicating whether the requesting user has
          flagged the comment for abuse

        * abuse_flagged_any_user: Boolean indicating whether any user has
            flagged the comment for abuse. Returns null if requesting user
            is not a moderator.

        * voted: Boolean indicating whether the requesting user has voted
          for the comment

        * vote_count: The number of votes for the comment

        * children: The list of child comments (with the same format)

        * editable_fields: The fields that the requesting user is allowed to
            modify with a PATCH request

        * anonymous: A boolean indicating whether the comment is anonymous

        * anonymous_to_peers: A boolean indicating whether the comment is
        anonymous to peers

    **DELETE Response Value**

        No content is returned for a DELETE request

    """
    lookup_field = "comment_id"
    parser_classes = (JSONParser, MergePatchParser,)

    def list(self, request):
        """
        Implements the GET method for the list endpoint as described in
        the class docstring.

        This endpoint implements two distinct usage contexts.

        When `username` is provided, the `course_id` parameter is
        required, and `thread_id` is ignored.
        The behavior is to retrieve all of the user's non-anonymous
        comments from the specified course, outside of the context of a
        forum thread. In this context, endorsement information is
        unavailable.

        When `username` is not provided, `thread_id` is required, and
        `course_id` is ignored, since the thread already belongs to a course.
        In this context, all information relevant to usage in the
        discussions forum is available.
        """
        if "username" in request.GET:
            return self.list_by_user(request)
        else:
            return self.list_by_thread(request)

    def list_by_thread(self, request):
        """
        Handles the case of fetching a thread's comments.
        """
        form = CommentListGetForm(request.GET)
        if not form.is_valid():
            raise ValidationError(form.errors)
        return get_comment_list(
            request,
            form.cleaned_data["thread_id"],
            form.cleaned_data["endorsed"],
            form.cleaned_data["page"],
            form.cleaned_data["page_size"],
            form.cleaned_data["flagged"],
            form.cleaned_data["requested_fields"],
            form.cleaned_data["merge_question_type_responses"]
        )

    def list_by_user(self, request):
        """
        Handles the case of fetching an user's comments.
        """
        form = UserCommentListGetForm(request.GET)
        if not form.is_valid():
            raise ValidationError(form.errors)
        author = get_object_or_404(User, username=request.GET["username"])
        return get_user_comments(
            request,
            author,
            form.cleaned_data["course_id"],
            form.cleaned_data["flagged"],
            form.cleaned_data["page"],
            form.cleaned_data["page_size"],
            form.cleaned_data["requested_fields"],
        )

    def retrieve(self, request, comment_id=None):
        """
        Implements the GET method for comments against response ID
        """
        form = CommentGetForm(request.GET)
        if not form.is_valid():
            raise ValidationError(form.errors)
        return get_response_comments(
            request,
            comment_id,
            form.cleaned_data["page"],
            form.cleaned_data["page_size"],
            form.cleaned_data["requested_fields"],
        )

    def create(self, request):
        """
        Implements the POST method for the list endpoint as described in the
        class docstring.
        """
        if not request.data.get("thread_id"):
            raise ValidationError({"thread_id": ["This field is required."]})
        course_key_str = get_course_id_from_thread_id(request.data["thread_id"])
        course_key = CourseKey.from_string(course_key_str)

        if is_content_creation_rate_limited(request, course_key=course_key):
            return Response("Too many requests", status=status.HTTP_429_TOO_MANY_REQUESTS)

        if is_captcha_enabled(course_key) and is_only_student(course_key, request.user):
            captcha_token = request.data.get('captcha_token')
            if not captcha_token:
                raise ValidationError({'captcha_token': 'This field is required.'})

            if not verify_recaptcha_token(captcha_token):
                return Response({'error': 'CAPTCHA verification failed.'}, status=400)

        if ONLY_VERIFIED_USERS_CAN_POST.is_enabled(course_key) and not request.user.is_active:
            raise ValidationError({"detail": "Only verified users can post in discussions."})

        data = request.data.copy()
        data.pop('captcha_token', None)
        return Response(create_comment(request, data))

    def destroy(self, request, comment_id):
        """
        Implements the DELETE method for the instance endpoint as described in
        the class docstring
        """
        delete_comment(request, comment_id)
        return Response(status=204)

    def partial_update(self, request, comment_id):
        """
        Implements the PATCH method for the instance endpoint as described in
        the class docstring.
        """
        if request.content_type != MergePatchParser.media_type:
            raise UnsupportedMediaType(request.content_type)
        return Response(update_comment(request, comment_id, request.data))


class UploadFileView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Upload a file to be attached to a thread or comment.

    **URL Parameters**

        * course_id:
            The ID of the course where this thread or comment belongs.

    **POST Upload File Parameters**

        * thread_key:
            If the upload belongs to a comment, refer to the parent
            `thread_id`, otherwise it should be `"root"`.

    **Example Requests**:
        POST /api/discussion/v1/courses/{course_id}/upload/
        Content-Type: multipart/form-data; boundary=--Boundary

        ----Boundary
        Content-Disposition: form-data; name="thread_key"

        <thread_key>
        ----Boundary
        Content-Disposition: form-data; name="uploaded_file"; filename="<filename>.<ext>"
        Content-Type: <mimetype>

        <file_content>
        ----Boundary--

    **Response Values**

        * location: The URL to access the uploaded file.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthentication,
        SessionAuthentication,
    )
    permission_classes = (
        permissions.IsAuthenticated,
        IsStaffOrCourseTeamOrEnrolled,
    )

    def post(self, request, course_id):
        """
        Handles a file upload.
        """
        thread_key = request.POST.get("thread_key", "root")
        unique_file_name = f"{course_id}/{thread_key}/{uuid.uuid4()}"
        try:
            file_storage, stored_file_name = store_uploaded_file(
                request, "uploaded_file", cc_settings.ALLOWED_UPLOAD_FILE_TYPES,
                unique_file_name, max_file_size=cc_settings.MAX_UPLOAD_FILE_SIZE,
            )
        except ValueError as err:
            raise BadRequest("no `uploaded_file` was provided") from err

        file_absolute_url = file_storage.url(stored_file_name)

        # this is a no-op in production, but is required in development,
        # since the filesystem storage returns the path without a base_url
        file_absolute_url = request.build_absolute_uri(file_absolute_url)

        return Response(
            {"location": file_absolute_url},
            content_type="application/json",
        )


class RetireUserView(APIView):
    """
    **Use Cases**

        A superuser or the user with the settings.RETIREMENT_SERVICE_WORKER_USERNAME
        can "retire" the user's data from the comments service, which will remove
        personal information and blank all posts / comments the user has made.

    **Example Requests**:
        POST /api/discussion/v1/retire_user/
        {
            "username": "an_original_user_name"
        }

    **Example Response**:
        Empty string
    """

    permission_classes = (permissions.IsAuthenticated, CanRetireUser)

    def post(self, request):
        """
        Implements the retirement endpoint.
        """
        username = request.data['username']

        try:
            retirement = UserRetirementStatus.get_retirement_for_retirement_action(username)
            cc_user = comment_client.User.from_django_user(retirement.user)

            # Send the retired username to the forums service, as the service cannot generate
            # the retired username itself. Forums users are referenced by Django auth_user id.
            cc_user.retire(retirement.retired_username)
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except comment_client.CommentClientRequestError as exc:
            # 404s from client service for users that don't exist there are expected
            # we can just pass those up.
            if exc.status_code == 404:
                return Response(status=status.HTTP_404_NOT_FOUND)
            raise
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ReplaceUsernamesView(APIView):
    """
    WARNING: This API is only meant to be used as part of a larger job that
    updates usernames across all services. DO NOT run this alone or users will
    not match across the system and things will be broken.

    API will recieve a list of current usernames and their new username.

    POST /api/discussion/v1/accounts/replace_usernames/
        {
            "username_mappings": [
                {"current_username_1": "desired_username_1"},
                {"current_username_2": "desired_username_2"}
            ]
        }

    """

    permission_classes = (permissions.IsAuthenticated, CanReplaceUsername)

    def post(self, request):
        """
        Implements the username replacement endpoint
        """

        username_mappings = request.data.get("username_mappings")

        if not self._has_valid_schema(username_mappings):
            raise ParseError("Request data does not match schema")

        successful_replacements, failed_replacements = [], []

        for username_pair in username_mappings:
            current_username = list(username_pair.keys())[0]
            new_username = list(username_pair.values())[0]
            successfully_replaced = self._replace_username(current_username, new_username)
            if successfully_replaced:
                successful_replacements.append({current_username: new_username})
            else:
                failed_replacements.append({current_username: new_username})

        return Response(
            status=status.HTTP_200_OK,
            data={
                "successful_replacements": successful_replacements,
                "failed_replacements": failed_replacements
            }
        )

    def _replace_username(self, current_username, new_username):
        """
        Replaces the current username with the new username in the forums service
        """
        try:
            # This API will be called after the regular LMS API, so the username in
            # the DB will have already been updated to new_username
            current_user = User.objects.get(username=new_username)
            cc_user = comment_client.User.from_django_user(current_user)
            cc_user.replace_username(new_username)
        except User.DoesNotExist:
            log.warning(
                "Unable to change username from %s to %s in forums because %s doesn't exist in LMS DB.",
                current_username,
                new_username,
                new_username,
            )
            return True
        except comment_client.CommentClientRequestError as exc:
            if exc.status_code == 404:
                log.info(
                    "Unable to change username from %s to %s in forums because user doesn't exist in forums",
                    current_username,
                    new_username,
                )
                return True
            else:
                log.exception(
                    "Unable to change username from %s to %s in forums because forums API call failed with: %s.",
                    current_username,
                    new_username,
                    exc,
                )
            return False

        log.info(
            "Successfully changed username from %s to %s in forums.",
            current_username,
            new_username,
        )
        return True

    def _has_valid_schema(self, post_data):
        """ Verifies the data is a list of objects with a single key:value pair """
        if not isinstance(post_data, list):
            return False
        for obj in post_data:
            if not (isinstance(obj, dict) and len(obj) == 1):
                return False
        return True


class CourseDiscussionSettingsAPIView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**
    Retrieve all the discussion settings for a course or update one or more of them.

    **Example Requests**

        GET /api/discussion/v1/courses/{course_id}/settings

        PATCH /api/discussion/v1/courses/{course_id}/settings
        {"always_divide_inline_discussions": true}

    **GET Discussion Settings Parameters**:

        * course_id (required): The course to retrieve the discussion settings for.

    **PATCH Discussion Settings Parameters**:

        * course_id (required): The course to retrieve the discussion settings for.

        The body should have the 'application/merge-patch+json' content type.

        * divided_inline_discussions: A list of IDs of the topics to be marked as divided inline discussions.

        * divided_course_wide_discussions: A list of IDs of the topics to be marked as divided course-wide discussions.

        * always_divide_inline_discussions: A boolean indicating whether inline discussions should always be
          divided or not.

        * division_scheme: A string corresponding to the division scheme to be used from the list of
          available division schemes.

    **GET and PATCH Discussion Settings Parameters Response Values**:

        A HTTP 404 Not Found response status code is returned when the requested course is invalid.

        A HTTP 400 Bad Request response status code is returned when the request is invalid.

        A HTTP 200 OK response status denote is returned to denote success.

        * id: The discussion settings id.

        * divided_inline_discussions: A list of divided inline discussions.

        * divided_course_wide_discussions: A list of divided course-wide discussions.

        * division_scheme: The division scheme used for the course discussions.

        * available_division_schemes: A list of available division schemes for the course.

    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    parser_classes = (JSONParser, MergePatchParser,)
    permission_classes = (permissions.IsAuthenticated, IsStaffOrAdmin)

    def _get_request_kwargs(self, course_id):
        return dict(course_id=course_id)

    def get(self, request, course_id):
        """
        Implement a handler for the GET method.
        """
        kwargs = self._get_request_kwargs(course_id)
        form = CourseDiscussionSettingsForm(kwargs, request_user=request.user)

        if not form.is_valid():
            raise ValidationError(form.errors)

        course_key = form.cleaned_data['course_key']
        course = form.cleaned_data['course']
        discussion_settings = CourseDiscussionSettings.get(course_key)
        serializer = DiscussionSettingsSerializer(
            discussion_settings,
            context={
                'course': course,
                'settings': discussion_settings,
            },
            partial=True,
        )
        response = Response(serializer.data)
        return response

    def patch(self, request, course_id):
        """
        Implement a handler for the PATCH method.
        """
        if request.content_type != MergePatchParser.media_type:
            raise UnsupportedMediaType(request.content_type)

        kwargs = self._get_request_kwargs(course_id)
        form = CourseDiscussionSettingsForm(kwargs, request_user=request.user)
        if not form.is_valid():
            raise ValidationError(form.errors)

        course = form.cleaned_data['course']
        course_key = form.cleaned_data['course_key']
        discussion_settings = CourseDiscussionSettings.get(course_key)

        serializer = DiscussionSettingsSerializer(
            discussion_settings,
            context={
                'course': course,
                'settings': discussion_settings,
            },
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CourseDiscussionRolesAPIView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**
    Retrieve all the members of a given forum discussion role or update the membership of a role.

    **Example Requests**

        GET /api/discussion/v1/courses/{course_id}/roles/{rolename}

        POST /api/discussion/v1/courses/{course_id}/roles/{rolename}
        {"user_id": "<username or email>", "action": "<allow or revoke>"}

    **GET List Members of a Role Parameters**:

        * course_id (required): The course to which the role belongs to.

        * rolename (required): The name of the forum discussion role, the members of which have to be listed.
          Currently supported values are 'Moderator', 'Group Moderator', 'Community TA'. If the value has a space
          it has to be URL encoded.

    **POST Update the membership of a Role Parameters**:

        * course_id (required): The course to which the role belongs to.

        * rolename (required): The name of the forum discussion role, the members of which have to be listed.
          Currently supported values are 'Moderator', 'Group Moderator', 'Community TA'. If the value has a space
          it has to be URL encoded.

        The body can use either 'application/x-www-form-urlencoded' or 'application/json' content type.

        * user_id (required): The username or email address of the user whose membership has to be updated.

        * action (required): Either 'allow' or 'revoke', depending on the action to be performed on the membership.

    **GET and POST Response Values**:

        A HTTP 404 Not Found response status code is returned when the requested course is invalid.

        A HTTP 400 Bad Request response status code is returned when the request is invalid.

        A HTTP 200 OK response status denote is returned to denote success.

        * course_id: The course to which the role belongs to.

        * results: A list of the members belonging to the specified role.

            * username: Username of the user.

            * email: Email address of the user.

            * first_name: First name of the user.

            * last_name: Last name of the user.

            * group_name: Name of the group the user belongs to.

        * division_scheme: The division scheme used by the course.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, permissions.IsAdminUser)

    def _get_request_kwargs(self, course_id, rolename):
        return dict(course_id=course_id, rolename=rolename)

    def get(self, request, course_id, rolename):
        """
        Implement a handler for the GET method.
        """
        kwargs = self._get_request_kwargs(course_id, rolename)
        form = CourseDiscussionRolesForm(kwargs, request_user=request.user)

        if not form.is_valid():
            raise ValidationError(form.errors)

        course_id = form.cleaned_data['course_key']
        role = form.cleaned_data['role']

        data = {'course_id': course_id, 'users': role.users.all()}
        context = {'course_discussion_settings': CourseDiscussionSettings.get(course_id)}

        serializer = DiscussionRolesListSerializer(data, context=context)
        return Response(serializer.data)

    def post(self, request, course_id, rolename):
        """
        Implement a handler for the POST method.
        """
        kwargs = self._get_request_kwargs(course_id, rolename)
        form = CourseDiscussionRolesForm(kwargs, request_user=request.user)
        if not form.is_valid():
            raise ValidationError(form.errors)

        course_id = form.cleaned_data['course_key']
        rolename = form.cleaned_data['rolename']

        serializer = DiscussionRolesSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        action = serializer.validated_data['action']
        user = serializer.validated_data['user']
        try:
            update_forum_role(course_id, user, rolename, action)
        except Role.DoesNotExist as err:
            raise ValidationError(f"Role '{rolename}' does not exist") from err

        role = form.cleaned_data['role']
        data = {'course_id': course_id, 'users': role.users.all()}
        context = {'course_discussion_settings': CourseDiscussionSettings.get(course_id)}
        serializer = DiscussionRolesListSerializer(data, context=context)
        return Response(serializer.data)


class BulkDeleteUserPosts(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**
        A privileged user that can delete all posts and comments made by a user.
        It returns expected number of comments and threads that will be deleted

    **Example Requests**:
        POST /api/discussion/v1/bulk_delete_user_posts/{course_id}
        Query Parameters:
            username: The username of the user whose posts are to be deleted
            course_id: Course id for which posts are to be removed
            execute: If True, runs deletion task
            course_or_org: If 'course', deletes posts in the course, if 'org', deletes posts in all courses of the org

    **Example Response**:
        Empty string
    """

    authentication_classes = (
        JwtAuthentication, BearerAuthentication, SessionAuthentication,
    )
    permission_classes = (permissions.IsAuthenticated, IsAllowedToBulkDelete)

    def post(self, request, course_id):
        """
        Implements the delete user posts endpoint.
        TODO: Add support for MySQLBackend as well
        """
        username = request.GET.get("username", None)
        execute_task = request.GET.get("execute", "false").lower() == "true"
        if (not username) or (not course_id):
            raise BadRequest("username and course_id are required.")
        course_or_org = request.GET.get("course_or_org", "course")
        if course_or_org not in ["course", "org"]:
            raise BadRequest("course_or_org must be either 'course' or 'org'.")

        user = get_object_or_404(User, username=username)
        course_ids = [course_id]
        if course_or_org == "org":
            org_id = CourseKey.from_string(course_id).org
            enrollments = CourseEnrollment.objects.filter(user=request.user).values_list('course_id', flat=True)
            course_ids.extend([
                str(c_id)
                for c_id in enrollments
                if c_id.org == org_id
            ])
            course_ids = list(set(course_ids))
            log.info(f"<<Bulk Delete>> {username} enrolled in {enrollments}")
        log.info(f"<<Bulk Delete>> Posts for {username} in {course_ids} - for {course_or_org} {course_id}")

        comment_count = Comment.get_user_comment_count(user.id, course_ids)
        thread_count = Thread.get_user_threads_count(user.id, course_ids)
        log.info(f"<<Bulk Delete>> {username} in {course_ids} - Count thread {thread_count}, comment {comment_count}")

        if execute_task:
            event_data = {
                "triggered_by": request.user.username,
                "username": username,
                "course_or_org": course_or_org,
                "course_key": course_id,
            }
            delete_course_post_for_user.apply_async(
                args=(user.id, username, course_ids, event_data),
            )
        return Response(
            {"comment_count": comment_count, "thread_count": thread_count},
            status=status.HTTP_202_ACCEPTED
        )


class DiscussionModerationViewSet(DeveloperErrorViewMixin, ViewSet):
    """
    **Use Cases**

        Perform bulk moderation actions on discussion posts and manage user bans.

    **Example Requests**

        POST /api/discussion/v1/moderation/bulk-delete-ban/
        GET /api/discussion/v1/moderation/banned-users/course-v1:edX+DemoX+Demo
        POST /api/discussion/v1/moderation/123/unban/
    """

    authentication_classes = (
        JwtAuthentication, BearerAuthentication, SessionAuthentication,
    )
    permission_classes = (permissions.IsAuthenticated, IsAllowedToBulkDelete)

    def get_permissions(self):
        """
        Return permission instances for the view.

        For unban_user, unban_user_by_id, and banned_users actions, we only need IsAuthenticated
        because we check course-specific permissions inside the action method after retrieving the ban.
        For ban_user, we check permissions inside the action based on scope.
        """
        if self.action in ['unban_user', 'unban_user_by_id', 'banned_users', 'ban_user']:
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    @apidocs.schema(
        body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['course_id'],
            properties={
                'user_id': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='ID of the user to ban (required if username is not provided)'
                ),
                'username': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Username of the user to ban (required if user_id is not provided)'
                ),
                'course_id': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Course ID (e.g., course-v1:edX+DemoX+Demo_Course)'
                ),
                'scope': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Scope of ban: "course" or "organization"',
                    enum=['course', 'organization'],
                    default='course'
                ),
                'reason': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Reason for the ban (optional)',
                    max_length=1000
                ),
            },
        ),
        responses={
            201: openapi.Response(
                description='User banned successfully',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'status': openapi.Schema(type=openapi.TYPE_STRING, example='success'),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'ban_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'user_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'username': openapi.Schema(type=openapi.TYPE_STRING),
                        'scope': openapi.Schema(type=openapi.TYPE_STRING),
                        'course_id': openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            400: 'Invalid request data or user already banned.',
            401: 'The requester is not authenticated.',
            403: 'The requester does not have permission to ban users.',
            404: 'The specified user does not exist.',
        },
    )
    def _validate_ban_request_and_get_user(self, request, serializer_data):
        """
        Validate ban request and retrieve target user.

        Returns tuple of (user, course_key, ban_scope, reason) or Response object on error.
        """
        from lms.djangoapps.discussion.rest_api.utils import (
            _is_privileged_user,
        )

        user_id = serializer_data.get('user_id')
        lookup_username = serializer_data.get('lookup_username')
        course_id_str = serializer_data['course_id']
        ban_scope = serializer_data.get('scope', 'course')
        reason = serializer_data.get('reason', '').strip()

        try:
            course_key = CourseKey.from_string(course_id_str)
        except InvalidKeyError:
            return Response(
                {'error': f'Invalid course_id: {course_id_str}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if user_id:
                user = User.objects.get(id=user_id)
            elif lookup_username:
                user = User.objects.get(username=lookup_username)
            else:
                return Response(
                    {'error': 'Either user_id or username must be provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except User.DoesNotExist:
            identifier = user_id if user_id else lookup_username
            return Response(
                {'error': f'User {identifier} does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if user is staff/privileged - they shouldn't be banned
        if _is_privileged_user(user, course_key):
            return Response(
                {
                    'error': (
                        f'Cannot ban staff or privileged users. User {user.username} '
                        f'has elevated permissions in this course.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        return user, course_key, ban_scope, reason

    def _check_ban_permissions(self, request, ban_scope, course_key):
        """
        Check if user has permission to ban at the specified scope.

        Returns Response object on permission denied, None if permitted.
        """
        from lms.djangoapps.discussion.rest_api.permissions import can_take_action_on_spam
        from common.djangoapps.student.roles import GlobalStaff

        if ban_scope == 'course':
            if not can_take_action_on_spam(request.user, course_key):
                return Response(
                    {'error': 'You do not have permission to ban users in this course'},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            if not (GlobalStaff().has_user(request.user) or request.user.is_staff):
                return Response(
                    {'error': 'Organization-level bans require global staff permissions'},
                    status=status.HTTP_403_FORBIDDEN
                )

        if not ENABLE_DISCUSSION_BAN.is_enabled(course_key):
            return Response(
                {'error': 'Discussion ban feature is not enabled for this course'},
                status=status.HTTP_403_FORBIDDEN
            )

        return None

    def _get_or_create_ban(self, user, course_key, ban_scope, reason, request):
        """
        Get existing ban or create new one.

        Returns tuple of (ban, action_type, message) or Response object on error.
        """
        from forum import api as forum_api

        # Check if already banned
        if forum_api.is_user_banned(user, course_key, check_org=(ban_scope == 'organization')):
            existing_ban = forum_api.get_ban(
                user=user,
                course_id=course_key,
                scope=ban_scope
            )
            if existing_ban and existing_ban['is_active']:
                return Response(
                    {
                        'error': f'User {user.username} is already banned at {ban_scope} level',
                        'ban_id': existing_ban['id']
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Use forum API to ban user
        ban_result = forum_api.ban_user(
            user=user,
            banned_by=request.user,
            course_id=course_key,
            scope=ban_scope,
            reason=reason
        )

        # Determine action type and message
        if ban_result.get('reactivated'):
            action_type = 'ban_reactivate'
            message = f'User {user.username} ban reactivated at {ban_scope} level'
        else:
            action_type = 'ban_user'
            message = f'User {user.username} banned at {ban_scope} level'

        return ban_result, action_type, message

    def ban_user(self, request):
        """
        Ban a user from discussions without deleting posts.

        **Use Cases**

            * Ban user directly from UI moderation interface
            * Prevent future posts without removing existing content
            * Apply preventive bans based on behavior patterns

        **Example Requests**

            POST /api/discussion/v1/moderation/ban-user/

            Course-level ban:
            ```json
            {
                "user_id": 12345,
                "course_id": "course-v1:HarvardX+CS50+2024",
                "scope": "course",
                "reason": "Repeated policy violations"
            }
            ```

            Organization-level ban (requires global staff):
            ```json
            {
                "username": "spammer123",
                "course_id": "course-v1:HarvardX+CS50+2024",
                "scope": "organization",
                "reason": "Spam across multiple courses"
            }
            ```

        **Response Values**

            * status: Success status
            * message: Human-readable message
            * ban_id: ID of the created ban record
            * user_id: Banned user's ID
            * username: Banned user's username
            * scope: Scope of the ban
            * course_id: Course ID (if course-level ban)

        **Notes**

            * Creates ban without deleting existing posts
            * Course-level bans require course moderation permissions
            * Organization-level bans require global staff permissions
            * Reactivates existing inactive bans if found
            * All ban actions are logged in ModerationAuditLog
        """
        from forum import api as forum_api
        from lms.djangoapps.discussion.rest_api.serializers import BanUserRequestSerializer

        # Check if ban API is available
        if not hasattr(forum_api, 'ban_user') or not hasattr(forum_api, 'is_user_banned'):
            return Response(
                {'error': 'Ban functionality is not available in this forum version'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

        serializer = BanUserRequestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Validate and get user
        result = self._validate_ban_request_and_get_user(request, serializer.validated_data)
        if isinstance(result, Response):
            return result
        user, course_key, ban_scope, reason = result

        # Check permissions
        permission_error = self._check_ban_permissions(request, ban_scope, course_key)
        if permission_error:
            return permission_error

        # Get or create ban
        result = self._get_or_create_ban(user, course_key, ban_scope, reason, request)
        if isinstance(result, Response):
            return result
        ban, action_type, message = result

        # Audit log
        org_key = course_key.org if ban_scope == 'organization' else None
        forum_api.create_audit_log(
            action_type=action_type,
            target_user=user,
            moderator=request.user,
            course_id=str(course_key),
            scope=ban_scope,
            reason=reason or 'No reason provided',
            metadata={
                'ban_id': ban['id'],
                'organization': org_key
            }
        )

        return Response({
            'status': 'success',
            'message': message,
            'ban_id': ban['id'],
            'user_id': user.id,
            'username': user.username,
            'scope': ban_scope,
            'course_id': str(course_key) if ban_scope == 'course' else None,
        }, status=status.HTTP_201_CREATED)

    @apidocs.schema(
        body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['course_id', 'scope'],
            properties={
                'user_id': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='ID of the user to unban (required if username is not provided)'
                ),
                'username': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Username of the user to unban (required if user_id is not provided)'
                ),
                'course_id': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Course ID (e.g., course-v1:edX+DemoX+Demo_Course)'
                ),
                'scope': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Scope of ban to lift: "course" or "organization"',
                    enum=['course', 'organization']
                ),
                'reason': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Reason for unbanning',
                    max_length=1000
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description='User unbanned successfully',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'status': openapi.Schema(type=openapi.TYPE_STRING, example='success'),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'ban_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'user_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'username': openapi.Schema(type=openapi.TYPE_STRING),
                        'scope': openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            400: 'Invalid request data or user not currently banned.',
            401: 'The requester is not authenticated.',
            403: 'The requester does not have permission to unban users.',
            404: 'The specified user or ban does not exist.',
        },
    )
    def unban_user(self, request):
        """
        Unban a user from discussions.

        **Use Cases**

            * Lift ban after user appeal
            * Remove accidental or temporary bans
            * Restore discussion access

        **Example Requests**

            POST /api/discussion/v1/moderation/unban-user/

            Course-level unban:
            ```json
            {
                "user_id": 12345,
                "course_id": "course-v1:HarvardX+CS50+2024",
                "scope": "course",
                "reason": "User appealed and corrected behavior"
            }
            ```

            Organization-level unban:
            ```json
            {
                "username": "student123",
                "course_id": "course-v1:HarvardX+CS50+2024",
                "scope": "organization",
                "reason": "Ban lifted after review"
            }
            ```

        **Response Values**

            * status: Success status
            * message: Human-readable message
            * ban_id: ID of the unbanned record
            * user_id: Unbanned user's ID
            * username: Unbanned user's username
            * scope: Scope of the ban that was lifted

        **Notes**

            * Deactivates the ban without deleting the record
            * Course-level unbans require course moderation permissions
            * Organization-level unbans require global staff permissions
            * All unban actions are logged in ModerationAuditLog
        """
        from forum import api as forum_api
        from lms.djangoapps.discussion.rest_api.serializers import BanUserRequestSerializer

        # Check if ban API is available
        if not hasattr(forum_api, 'unban_user') or not hasattr(forum_api, 'is_user_banned'):
            return Response(
                {'error': 'Ban functionality is not available in this forum version'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

        serializer = BanUserRequestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Validate and get user
        result = self._validate_ban_request_and_get_user(request, serializer.validated_data)
        if isinstance(result, Response):
            return result

        user, course_key, ban_scope, reason = result

        # Permission check
        permission_error = self._check_ban_permissions(request, ban_scope, course_key)
        if permission_error:
            return permission_error

        # Check if user has an active ban
        if not forum_api.is_user_banned(user, course_key, check_org=(ban_scope == 'organization')):
            return Response(
                {
                    'error': f'User {user.username} does not have an active ban at {ban_scope} level',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get ban details before unbanning
        ban_data = forum_api.get_ban(
            user=user,
            course_id=course_key,
            scope=ban_scope
        )

        # Unban using forum API
        # For org-level bans, pass course_id=None to fully unban across org
        # (passing course_id for org ban creates an exception instead)
        # NOTE: The newer /moderation/{pk}/unban/ endpoint (line ~2912) correctly
        # supports optional course_id for creating exceptions. This older endpoint
        # should always fully unban when scope='organization'.
        unban_result = forum_api.unban_user(
            user=user,
            unbanned_by=request.user,
            course_id=course_key if ban_scope == 'course' else None,
            scope=ban_scope
        )

        # Prepare ban parameters based on scope
        org_key = course_key.org if ban_scope == 'organization' else None

        # Audit log
        forum_api.create_audit_log(
            action_type='unban_user',
            target_user=user,
            moderator=request.user,
            course_id=str(course_key),
            scope=ban_scope,
            reason=reason or 'No reason provided',
            metadata={
                'ban_id': ban_data.get('id') if ban_data else None,
                'organization': org_key
            },
        )

        return Response({
            'status': 'success',
            'message': f'User {user.username} unbanned at {ban_scope} level',
            'ban_id': ban_data.get('id') if ban_data else None,
            'user_id': user.id,
            'username': user.username,
            'scope': ban_scope,
        }, status=status.HTTP_200_OK)

    @apidocs.schema(
        body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['user_id', 'course_id'],
            properties={
                'user_id': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='ID of the user whose posts should be deleted'
                ),
                'course_id': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Course ID (e.g., course-v1:edX+DemoX+Demo_Course)'
                ),
                'ban_user': openapi.Schema(
                    type=openapi.TYPE_BOOLEAN,
                    description='If true, ban the user after deleting posts',
                    default=False
                ),
                'ban_scope': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Scope of ban: "course" or "organization"',
                    enum=['course', 'organization'],
                    default='course'
                ),
                'reason': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Reason for ban (required if ban_user is true)',
                    max_length=1000
                ),
            },
        ),
        responses={
            202: openapi.Response(
                description='Deletion task queued successfully',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'status': openapi.Schema(type=openapi.TYPE_STRING, example='success'),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'task_id': openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            400: 'Invalid request data or missing required parameters.',
            401: 'The requester is not authenticated.',
            403: 'The requester does not have permission to perform bulk delete.',
            404: 'The specified user does not exist.',
        },
    )
    def bulk_delete_ban(self, request):
        """
        Delete all user posts in a course and optionally ban the user.

        **Use Cases**

            * Remove all discussion content from a spam account
            * Ban user from course or organization discussions
            * Bulk cleanup of policy-violating content

        **Example Request**

            POST /api/discussion/v1/moderation/bulk-delete-ban/

            ```json
            {
                "user_id": 12345,
                "course_id": "course-v1:HarvardX+CS50+2024",
                "ban_user": true,
                "ban_scope": "course",
                "reason": "Posting spam and scam content"
            }
            ```

        **Response Values**

            * status: Success status of the request
            * message: Human-readable message about the queued task
            * task_id: Celery task ID for tracking the asynchronous operation

        **Notes**

            * This operation is asynchronous and returns a task ID
            * If ban_user is true, a ban record will be created after content deletion
            * Reason is required when ban_user is true
            * Email notification is sent to partner-support upon ban
            * Staff and privileged users cannot be banned
        """
        from lms.djangoapps.discussion.rest_api.serializers import BulkDeleteBanRequestSerializer
        from lms.djangoapps.discussion.rest_api.utils import _is_privileged_user

        serializer = BulkDeleteBanRequestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        try:
            course_key = CourseKey.from_string(validated_data['course_id'])
        except InvalidKeyError:
            return Response(
                {'error': f"Invalid course_id: {validated_data['course_id']}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_user = User.objects.get(id=validated_data['user_id'])
        except User.DoesNotExist:
            return Response(
                {'error': f'User with ID {validated_data["user_id"]} does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if target user is staff/privileged - they shouldn't be banned
        if validated_data['ban_user'] and _is_privileged_user(target_user, course_key):
            return Response(
                {
                    'error': (
                        f'Cannot ban staff or privileged users. User {target_user.username} '
                        f'has elevated permissions in this course.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if ban feature is enabled for this course
        if validated_data['ban_user']:
            if not ENABLE_DISCUSSION_BAN.is_enabled(course_key):
                return Response(
                    {'error': 'Discussion ban feature is not enabled for this course'},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Enqueue Celery task (backward compatible with new parameters)
        task = delete_course_post_for_user.apply_async(
            kwargs={
                'user_id': validated_data['user_id'],
                'username': target_user.username,
                'course_ids': [validated_data['course_id']],
                'ban_user': validated_data['ban_user'],
                'ban_scope': validated_data.get('ban_scope', 'course'),
                'moderator_id': request.user.id,
                'reason': validated_data.get('reason', ''),
            }
        )

        message = (
            'Deletion task queued. User will be banned upon completion.'
            if validated_data['ban_user']
            else 'Deletion task queued.'
        )
        return Response({
            'status': 'success',
            'message': message,
            'task_id': task.id,
        }, status=status.HTTP_202_ACCEPTED)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description='Course ID to retrieve banned users for (required)'
            ),
            apidocs.string_parameter(
                'scope',
                apidocs.ParameterLocation.QUERY,
                description='Filter by ban scope: "course" or "organization"'
            ),
        ],
        responses={
            200: openapi.Response(
                description='List of banned users',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'count': openapi.Schema(
                            type=openapi.TYPE_INTEGER,
                            description='Total number of banned users'
                        ),
                        'results': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            description='Array of banned user records',
                            items=openapi.Schema(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    'id': openapi.Schema(type=openapi.TYPE_INTEGER),
                                    'username': openapi.Schema(type=openapi.TYPE_STRING),
                                    'email': openapi.Schema(type=openapi.TYPE_STRING),
                                    'user_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                                    'course_id': openapi.Schema(type=openapi.TYPE_STRING),
                                    'organization': openapi.Schema(type=openapi.TYPE_STRING),
                                    'scope': openapi.Schema(type=openapi.TYPE_STRING),
                                    'reason': openapi.Schema(type=openapi.TYPE_STRING),
                                    'banned_at': openapi.Schema(type=openapi.TYPE_STRING, format='date-time'),
                                    'banned_by_username': openapi.Schema(type=openapi.TYPE_STRING),
                                    'is_active': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                                },
                            ),
                        ),
                    },
                ),
            ),
            400: 'Missing required course_id parameter.',
            401: 'The requester is not authenticated.',
            403: 'The requester does not have permission to view banned users.',
        },
    )
    def banned_users(self, request, course_id=None):
        """
        Retrieve list of banned users for a specific course.

        **Use Cases**

            * View all currently banned users in a course
            * Filter banned users by scope (course-level vs organization-level)
            * Audit moderation actions
            * Unban users who were mistakenly banned (including staff)

        **Example Requests**

            GET /api/discussion/v1/moderation/banned-users/course-v1:HarvardX+CS50+2024
            GET /api/discussion/v1/moderation/banned-users/course-v1:edX+DemoX+Demo?scope=course

        **Response Values**

            * count: Total number of active bans for the course
            * results: Array of ban records with user information (deduplicated)

        **Notes**

            * Only returns active bans (is_active=True)
            * Course-level bans are specific to one course
            * Organization-level bans apply to all courses in the organization
            * Shows ALL banned users including staff (so they can be unbanned if mistakenly banned)
            * Deduplicates users with multiple ban records (e.g., course-level + org-level)
            * New bans of staff are prevented by validation in ban endpoints
        """
        from forum import api as forum_api
        from lms.djangoapps.discussion.rest_api.permissions import can_take_action_on_spam

        if not course_id:
            return Response(
                {'error': 'course_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {'error': f'Invalid course_id: {course_id}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Permission check: user must be able to moderate in this course
        if not can_take_action_on_spam(request.user, course_key):
            return Response(
                {'error': 'You do not have permission to view banned users in this course'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if ban feature is enabled for this course
        if not ENABLE_DISCUSSION_BAN.is_enabled(course_key):
            return Response(
                {'error': 'Discussion ban feature is not enabled for this course'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Optional scope filter
        scope = request.query_params.get('scope')

        # Get banned users using forum API
        banned_users_data = forum_api.get_banned_users(
            course_id=course_key,
            scope=scope
        )

        # Deduplicate by user_id (user may have both course-level and org-level bans)
        # Keep the first occurrence (most relevant ban record)
        seen_user_ids = set()
        deduplicated_banned_users = []
        for ban in banned_users_data:
            user_id = ban.get('user', {}).get('id')
            if user_id and user_id not in seen_user_ids:
                seen_user_ids.add(user_id)
                deduplicated_banned_users.append(ban)

        return Response({
            'count': len(deduplicated_banned_users),
            'results': deduplicated_banned_users
        })

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'pk',
                apidocs.ParameterLocation.PATH,
                description='Ban ID to unban'
            ),
        ],
        body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'course_id': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Course ID for organization-level ban exceptions'
                ),
                'reason': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Reason for unbanning'
                ),
            },
            required=['reason'],
        ),
        responses={
            200: openapi.Response(
                description='User unbanned successfully',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'status': openapi.Schema(type=openapi.TYPE_STRING, example='success'),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'exception_created': openapi.Schema(
                            type=openapi.TYPE_BOOLEAN,
                            description='True if org-level ban exception was created'
                        ),
                    },
                ),
            ),
            401: 'The requester is not authenticated.',
            403: 'The requester does not have permission to unban users.',
            404: 'Active ban not found with the specified ID.',
        },
    )
    def unban_user_by_id(self, request, pk=None):
        """
        Unban a user from discussions or create course-level exception (by ban ID).

        **Use Cases**

            * Lift a course-level ban completely
            * Lift an organization-level ban completely
            * Create course-specific exception to organization-level ban
            * Process user appeals

        **Example Requests**

            POST /api/discussion/v1/moderation/123/unban/

            ```json
            {
                "reason": "User appeal approved - first offense"
            }
            ```

            Create exception for org-level ban:

            ```json
            {
                "course_id": "course-v1:HarvardX+CS50+2024",
                "reason": "Exception approved for CS50 only"
            }
            ```

        **Response Values**

            * status: Success status of the operation
            * message: Human-readable message describing the action taken
            * exception_created: Boolean indicating if an org-level exception was created

        **Notes**

            * For course-level bans: Deactivates the ban completely
            * For org-level bans without course_id: Deactivates entire org-level ban
            * For org-level bans with course_id: Creates exception allowing user in that course only
            * All unban actions are logged in ModerationAuditLog
        """
        from forum import api as forum_api
        from lms.djangoapps.discussion.rest_api.permissions import can_take_action_on_spam

        # Get ban using forum API
        try:
            ban = forum_api.get_ban(pk)
        except Exception:  # pylint: disable=broad-exception-caught
            return Response(
                {'error': 'Active ban not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if ban is active
        if not ban.get('is_active'):
            return Response(
                {'error': 'Active ban not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        course_id = request.data.get('course_id')
        reason = request.data.get('reason', '').strip()
        parsed_course_key = None

        if course_id:
            try:
                parsed_course_key = CourseKey.from_string(course_id)
            except InvalidKeyError:
                return Response(
                    {'error': f'Invalid course_id: {course_id}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Import dependencies
        from common.djangoapps.student.roles import GlobalStaff

        # Permission check: depends on ban type and what user is trying to do
        ban_course_id = ban.get('course_id')
        if ban_course_id:
            # Course-level ban - check permissions for that specific course
            course_key_obj = CourseKey.from_string(ban_course_id)
            if not can_take_action_on_spam(request.user, course_key_obj):
                return Response(
                    {'error': 'You do not have permission to unban users in this course'},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            # Org-level ban
            if course_id:
                # Creating exception for specific course - check permissions in that course
                if not can_take_action_on_spam(request.user, parsed_course_key):
                    return Response(
                        {'error': 'You do not have permission to create exceptions in this course'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                # Fully unbanning org-level ban - only global staff can do this
                if not (GlobalStaff().has_user(request.user) or request.user.is_staff):
                    return Response(
                        {'error': 'Only global staff can fully unban organization-level bans'},
                        status=status.HTTP_403_FORBIDDEN
                    )

        # Check if ban feature is enabled
        # Determine which course_key to use for flag check
        if ban_course_id:
            # Course-level ban - use ban's course_id
            course_key_for_flag = CourseKey.from_string(ban_course_id)
        elif course_id:
            # Org-level ban with course exception - use provided course_id
            course_key_for_flag = parsed_course_key
        elif ban.get('scope') == 'organization' and ban.get('org_key'):
            # Org-level ban without course_id - find any course in org to check flag
            from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
            try:
                # Find any course in the organization to check the flag
                org_course = CourseOverview.objects.filter(org=ban['org_key']).first()
                if org_course:
                    course_key_for_flag = org_course.id
                else:
                    # No courses found in org - deny unless global staff
                    if not (GlobalStaff().has_user(request.user) or request.user.is_staff):
                        return Response(
                            {'error': 'Discussion ban feature check requires course context or global staff access'},
                            status=status.HTTP_403_FORBIDDEN
                        )
                    # Global staff can proceed without flag check for org-level operations
                    course_key_for_flag = None
            except Exception:  # pylint: disable=broad-exception-caught
                # Fallback: deny unless global staff
                if not (GlobalStaff().has_user(request.user) or request.user.is_staff):
                    return Response(
                        {'error': 'Discussion ban feature check requires course context or global staff access'},
                        status=status.HTTP_403_FORBIDDEN
                    )
                course_key_for_flag = None
        else:
            course_key_for_flag = None

        # Check flag if we have a course_key
        if course_key_for_flag:
            if not ENABLE_DISCUSSION_BAN.is_enabled(course_key_for_flag):
                return Response(
                    {'error': 'Discussion ban feature is not enabled for this course'},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Validate that reason is provided
        if not reason:
            return Response(
                {'error': 'reason field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Use forum API to unban - it handles both full unban and exceptions
        try:
            unban_result = forum_api.unban_user(
                ban_id=pk,
                unbanned_by=request.user,
                course_id=course_id,
                reason=reason
            )

            return Response({
                'status': unban_result.get('status', 'success'),
                'message': unban_result.get('message', 'User unbanned successfully'),
                'exception_created': unban_result.get('exception_created', False)
            })
        except ValueError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            log.error(f"Error unbanning user: {e}")
            return Response(
                {'error': 'An error occurred while unbanning the user'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
