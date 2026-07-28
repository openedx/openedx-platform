"""
Discussion API views
"""
import logging
import uuid

import edx_api_doc_tools as apidocs
from django.contrib.auth import get_user_model
from django.core.exceptions import BadRequest, ValidationError
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from forum import api as forum_api
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.file import store_uploaded_file
from lms.djangoapps.course_api.blocks.api import get_blocks
from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.discussion.django_comment_client import settings as cc_settings
from lms.djangoapps.discussion.django_comment_client.utils import get_group_id_for_comments_service
from lms.djangoapps.discussion.rate_limit import is_content_creation_rate_limited
from lms.djangoapps.discussion.rest_api.permissions import IsAllowedToBulkDelete
from lms.djangoapps.discussion.rest_api.tasks import delete_course_post_for_user
from lms.djangoapps.discussion.toggles import ONLY_VERIFIED_USERS_CAN_POST
from lms.djangoapps.instructor.access import update_forum_role
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.discussions.serializers import DiscussionSettingsSerializer
from openedx.core.djangoapps.django_comment_common import comment_client
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings, Role
from openedx.core.djangoapps.user_api.accounts.permissions import CanReplaceUsername, CanRetireUser
from openedx.core.djangoapps.user_api.models import UserRetirementStatus
from openedx.core.lib.api.authentication import BearerAuthentication, BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from xmodule.modulestore.django import modulestore

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
    get_learner_active_thread_list,
    get_response_comments,
    get_thread,
    get_thread_list,
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
    get_course_id_from_thread_id,
    is_captcha_enabled,
    is_only_student,
    verify_recaptcha_token,
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

        thread_count = 0
        comment_count = 0
        for cid in course_ids:
            counts = forum_api.get_user_post_counts(user_id=str(user.id), course_id=cid)
            thread_count += counts.get("thread_count", 0)
            comment_count += counts.get("comment_count", 0)
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
