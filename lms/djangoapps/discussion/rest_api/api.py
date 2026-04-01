"""
Discussion API internal interface
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from datetime import datetime

from enum import Enum
from typing import Dict, Iterable, List, Literal, Optional, Set, Tuple
from urllib.parse import urlencode, urlunparse
from pytz import UTC

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from edx_django_utils.monitoring import function_trace
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import CourseKey
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
)

from lms.djangoapps.course_api.blocks.api import get_blocks
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.discussion.rate_limit import is_content_creation_rate_limited
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE, ONLY_VERIFIED_USERS_CAN_POST
from lms.djangoapps.discussion.views import is_privileged_user
from openedx.core.djangoapps.discussions.models import (
    DiscussionsConfiguration,
    DiscussionTopicLink,
    Provider,
)
from openedx.core.djangoapps.discussions.utils import get_accessible_discussion_xblocks
from openedx.core.djangoapps.django_comment_common import comment_client
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.course import (
    get_course_commentable_counts,
    get_course_user_stats
)
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.comment_client.utils import (
    CommentClient500Error,
    CommentClientRequestError
)
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    CourseDiscussionSettings,
    Role
)
from openedx.core.djangoapps.django_comment_common.signals import (
    comment_created,
    comment_deleted,
    comment_endorsed,
    comment_edited,
    comment_flagged,
    comment_voted,
    thread_created,
    thread_deleted,
    thread_edited,
    thread_flagged,
    thread_followed,
    thread_voted,
    thread_unfollowed
)
from openedx.core.djangoapps.user_api.accounts.api import get_account_settings
from openedx.core.lib.exceptions import CourseNotFoundError, DiscussionNotFoundError, PageNotFoundError
from xmodule.course_block import CourseBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.tabs import CourseTabList

from ..django_comment_client.base.views import (
    track_comment_created_event,
    track_comment_deleted_event,
    track_thread_created_event,
    track_thread_deleted_event,
    track_thread_viewed_event,
    track_voted_event,
    track_discussion_reported_event,
    track_discussion_unreported_event,
    track_forum_search_event, track_thread_followed_event
)
from ..django_comment_client.utils import (
    get_group_id_for_user,
    get_user_role_names,
    has_discussion_privileges,
    is_commentable_divided
)
from .exceptions import CommentNotFoundError, DiscussionBlackOutException, DiscussionDisabledError, ThreadNotFoundError
from .forms import CommentActionsForm, ThreadActionsForm, UserOrdering
from .pagination import DiscussionAPIPagination
from .permissions import (
    can_delete,
    can_take_action_on_spam,
    get_editable_fields,
    get_initializable_comment_fields,
    get_initializable_thread_fields
)
from .serializers import (
    CommentSerializer,
    DiscussionTopicSerializer,
    DiscussionTopicSerializerV2,
    ThreadSerializer,
    TopicOrdering,
    UserStatsSerializer,
    get_context
)
from .utils import (
    AttributeDict,
    add_stats_for_users_with_no_discussion_content,
    create_blocks_params,
    discussion_open_for_user,
    get_usernames_for_course,
    get_usernames_from_search_string,
    send_signal_after_commit,
    set_attribute,
    is_posting_allowed,
    can_user_notify_all_learners, is_captcha_enabled, get_captcha_site_key_by_platform
)

User = get_user_model()

ThreadType = Literal["discussion", "question"]
ViewType = Literal["unread", "unanswered"]
ThreadOrderingType = Literal["last_activity_at", "comment_count", "vote_count"]


class DiscussionTopic:
    """
    Class for discussion topic structure
    """

    def __init__(
        self,
        topic_id: Optional[str],
        name: str,
        thread_list_url: str,
        children: Optional[List[DiscussionTopic]] = None,
        thread_counts: Dict[str, int] = None,
    ):
        self.id = topic_id  # pylint: disable=invalid-name
        self.name = name
        self.thread_list_url = thread_list_url
        self.children = children or []  # children are of same type i.e. DiscussionTopic
        if not children and not thread_counts:
            thread_counts = {"discussion": 0, "question": 0}
        self.thread_counts = thread_counts


class DiscussionEntity(Enum):
    """
    Enum for different types of discussion related entities
    """
    thread = 'thread'
    comment = 'comment'


def _get_course(course_key: CourseKey, user: User, check_tab: bool = True) -> CourseBlock:
    """
    Get the course block, raising CourseNotFoundError if the course is not found or
    the user cannot access forums for the course, and DiscussionDisabledError if the
    discussion tab is disabled for the course.

    Using the ``check_tab`` parameter, tab checking can be skipped to perform other
    access checks only.

    Args:
        course_key (CourseKey): course key of course to fetch
        user (User): user for access checks
        check_tab (bool): Whether the discussion tab should be checked

    Returns:
        CourseBlock: course object
    """
    try:
        course = get_course_with_access(user, 'load', course_key, check_if_enrolled=True)
    except (Http404, CourseAccessRedirect) as err:
        # Convert 404s into CourseNotFoundErrors.
        # Raise course not found if the user cannot access the course
        raise CourseNotFoundError("Course not found.") from err

    if check_tab:
        discussion_tab = CourseTabList.get_tab_by_type(course.tabs, 'discussion')
        if not (discussion_tab and discussion_tab.is_enabled(course, user)):
            raise DiscussionDisabledError("Discussion is disabled for the course.")

    return course


def _get_thread_and_context(request, thread_id, retrieve_kwargs=None, course_id=None):
    """
    Retrieve the given thread and build a serializer context for it, returning
    both. This function also enforces access control for the thread (checking
    both the user's access to the course and to the thread's cohort if
    applicable). Raises ThreadNotFoundError if the thread does not exist or the
    user cannot access it.
    """
    retrieve_kwargs = retrieve_kwargs or {}
    try:
        if "with_responses" not in retrieve_kwargs:
            retrieve_kwargs["with_responses"] = False
        if "mark_as_read" not in retrieve_kwargs:
            retrieve_kwargs["mark_as_read"] = False
        cc_thread = Thread(id=thread_id).retrieve(course_id=course_id, **retrieve_kwargs)
        course_key = CourseKey.from_string(cc_thread["course_id"])
        course = _get_course(course_key, request.user)
        context = get_context(course, request, cc_thread)

        if retrieve_kwargs.get("flagged_comments") and not context["has_moderation_privilege"]:
            raise ValidationError("Only privileged users can request flagged comments")

        course_discussion_settings = CourseDiscussionSettings.get(course_key)
        if (
            not context["has_moderation_privilege"] and
            cc_thread["group_id"] and
            is_commentable_divided(course.id, cc_thread["commentable_id"], course_discussion_settings)
        ):
            requester_group_id = get_group_id_for_user(request.user, course_discussion_settings)
            if requester_group_id is not None and cc_thread["group_id"] != requester_group_id:
                raise ThreadNotFoundError("Thread not found.")
        return cc_thread, context
    except CommentClientRequestError as err:
        # params are validated at a higher level, so the only possible request
        # error is if the thread doesn't exist
        raise ThreadNotFoundError("Thread not found.") from err


def _get_comment_and_context(request, comment_id):
    """
    Retrieve the given comment and build a serializer context for it, returning
    both. This function also enforces access control for the comment (checking
    both the user's access to the course and to the comment's thread's cohort if
    applicable). Raises CommentNotFoundError if the comment does not exist or the
    user cannot access it.
    """
    try:
        cc_comment = Comment(id=comment_id).retrieve()
        _, context = _get_thread_and_context(request, cc_comment["thread_id"])
        return cc_comment, context
    except CommentClientRequestError as err:
        raise CommentNotFoundError("Comment not found.") from err


def _is_user_author_or_privileged(cc_content, context):
    """
    Check if the user is the author of a content object or a privileged user.

    Returns:
        Boolean
    """
    return (
        context["has_moderation_privilege"] or
        context["cc_requester"]["id"] == cc_content["user_id"]
    )


def get_thread_list_url(request, course_key, topic_id_list=None, following=False):
    """
    Returns the URL for the thread_list_url field, given a list of topic_ids
    """
    path = reverse("thread-list")
    query_list = (
        [("course_id", str(course_key))] +
        [("topic_id", topic_id) for topic_id in topic_id_list or []] +
        ([("following", following)] if following else [])
    )
    return request.build_absolute_uri(urlunparse(("", "", path, "", urlencode(query_list), "")))


def get_course(request, course_key, check_tab=True):
    """
    Return general discussion information for the course.

    Parameters:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        course_key: The key of the course to get information for
        check_tab: Whether to check if the discussion tab is enabled for the course

    Returns:

        The course information; see discussion.rest_api.views.CourseView for more
        detail.

    Raises:

        CourseNotFoundError: if the course does not exist or is not accessible
        to the requesting user
    """

    def _format_datetime(dt):
        """
        Provide backwards compatible datetime formatting.

        Technically, both "2020-10-20T23:59:00Z" and "2020-10-20T23:59:00+00:00"
        are ISO-8601 compliant, though the latter is preferred. We've always
        just passed back whatever datetime.isoformat() generated for the
        blackout dates in the get_course function (the "+00:00" format). At some
        point, this broke the expectation of the mobile app code, which expects
        these dates to be formatted in the same way that DRF formats the other
        datetimes in this API (the "Z" format).

        For the sake of compatibility, we're doing a manual substitution back to
        the old format here. This is done with a replacement because it's
        possible (though really not recommended) to enter blackout dates in
        something other than the UTC timezone, in which case we should not do
        the substitution... though really, that would probably break mobile
        client parsing of the dates as well. :-P
        """
        return dt.isoformat().replace('+00:00', 'Z')

    course = _get_course(course_key, request.user, check_tab=check_tab)
    user_roles = get_user_role_names(request.user, course_key)
    course_config = DiscussionsConfiguration.get(course_key)
    EDIT_REASON_CODES = getattr(settings, "DISCUSSION_MODERATION_EDIT_REASON_CODES", {})
    CLOSE_REASON_CODES = getattr(settings, "DISCUSSION_MODERATION_CLOSE_REASON_CODES", {})
    is_posting_enabled = is_posting_allowed(
        course_config.posting_restrictions,
        course.get_discussion_blackout_datetimes()
    )
    discussion_tab = CourseTabList.get_tab_by_type(course.tabs, 'discussion')
    is_course_staff = CourseStaffRole(course_key).has_user(request.user)
    is_course_admin = CourseInstructorRole(course_key).has_user(request.user)
    return {
        "id": str(course_key),
        "is_posting_enabled": is_posting_enabled,
        "blackouts": [
            {
                "start": _format_datetime(blackout["start"]),
                "end": _format_datetime(blackout["end"]),
            }
            for blackout in course.get_discussion_blackout_datetimes()
        ],
        "thread_list_url": get_thread_list_url(request, course_key),
        "following_thread_list_url": get_thread_list_url(request, course_key, following=True),
        "topics_url": request.build_absolute_uri(
            reverse("course_topics", kwargs={"course_id": course_key})
        ),
        "allow_anonymous": course.allow_anonymous,
        "allow_anonymous_to_peers": course.allow_anonymous_to_peers,
        "user_roles": user_roles,
        "has_bulk_delete_privileges": can_take_action_on_spam(request.user, course_key),
        "has_moderation_privileges": bool(user_roles & {
            FORUM_ROLE_ADMINISTRATOR,
            FORUM_ROLE_MODERATOR,
            FORUM_ROLE_COMMUNITY_TA,
        }),
        "is_group_ta": bool(user_roles & {FORUM_ROLE_GROUP_MODERATOR}),
        "is_user_admin": request.user.is_staff,
        "is_course_staff": is_course_staff,
        "is_course_admin": is_course_admin,
        "provider": course_config.provider_type,
        "enable_in_context": course_config.enable_in_context,
        "group_at_subsection": course_config.plugin_configuration.get("group_at_subsection", False),
        "edit_reasons": [
            {"code": reason_code, "label": label}
            for (reason_code, label) in EDIT_REASON_CODES.items()
        ],
        "post_close_reasons": [
            {"code": reason_code, "label": label}
            for (reason_code, label) in CLOSE_REASON_CODES.items()
        ],
        'show_discussions': bool(discussion_tab and discussion_tab.is_enabled(course, request.user)),
        'is_notify_all_learners_enabled': can_user_notify_all_learners(
            user_roles, is_course_staff, is_course_admin
        ),
        'captcha_settings': {
            'enabled': is_captcha_enabled(course_key),
            'site_key': get_captcha_site_key_by_platform('web'),
        },
        "is_email_verified": request.user.is_active,
        "only_verified_users_can_post": ONLY_VERIFIED_USERS_CAN_POST.is_enabled(course_key),
        "content_creation_rate_limited": is_content_creation_rate_limited(request, course_key, increment=False),
    }


def get_courseware_topics(
    request: Request,
    course_key: CourseKey,
    course: CourseBlock,
    topic_ids: Optional[List[str]],
    thread_counts: Dict[str, Dict[str, int]],
) -> Tuple[List[Dict], Set[str]]:
    """
    Returns a list of topic trees for courseware-linked topics.

    Parameters:

        request: The django request objects used for build_absolute_uri.
        course_key: The key of the course to get discussion threads for.
        course: The course for which topics are requested.
        topic_ids: A list of topic IDs for which details are requested.
            This is optional. If None then all course topics are returned.
        thread_counts: A map of the thread ids to the count of each type of thread in them
           e.g. discussion, question

    Returns:
        A list of courseware topics and a set of existing topics among
        topic_ids.

    """
    courseware_topics = []
    existing_topic_ids = set()

    now = datetime.now(UTC)

    discussion_xblocks = get_accessible_discussion_xblocks(course, request.user)
    xblocks_by_category = defaultdict(list)
    for xblock in discussion_xblocks:
        if course.self_paced or (xblock.start and xblock.start < now):
            xblocks_by_category[xblock.discussion_category].append(xblock)

    def sort_categories(category_list):
        """
        Sorts the given iterable containing alphanumeric correctly.
        Required arguments:
        category_list -- list of categories.
        """

        def convert(text):
            if text.isdigit():
                return int(text)
            return text

        def alphanum_key(key):
            return [convert(c) for c in re.split('([0-9]+)', key)]

        return sorted(category_list, key=alphanum_key)

    for category in sort_categories(xblocks_by_category.keys()):
        children = []
        for xblock in xblocks_by_category[category]:
            if not topic_ids or xblock.discussion_id in topic_ids:
                discussion_topic = DiscussionTopic(
                    xblock.discussion_id,
                    xblock.discussion_target,
                    get_thread_list_url(request, course_key, [xblock.discussion_id]),
                    None,
                    thread_counts.get(xblock.discussion_id),
                )
                children.append(discussion_topic)

                if topic_ids and xblock.discussion_id in topic_ids:
                    existing_topic_ids.add(xblock.discussion_id)

        if not topic_ids or children:
            discussion_topic = DiscussionTopic(
                None,
                category,
                get_thread_list_url(
                    request,
                    course_key,
                    [item.discussion_id for item in xblocks_by_category[category]],
                ),
                children,
                None,
            )
            courseware_topics.append(DiscussionTopicSerializer(discussion_topic).data)

    return courseware_topics, existing_topic_ids


def get_non_courseware_topics(
    request: Request,
    course_key: CourseKey,
    course: CourseBlock,
    topic_ids: Optional[List[str]],
    thread_counts: Dict[str, Dict[str, int]]
) -> Tuple[List[Dict], Set[str]]:
    """
    Returns a list of topic trees that are not linked to courseware.

    Parameters:

        request: The django request objects used for build_absolute_uri.
        course_key: The key of the course to get discussion threads for.
        course: The course for which topics are requested.
        topic_ids: A list of topic IDs for which details are requested.
            This is optional. If None then all course topics are returned.
        thread_counts: A map of the thread ids to the count of each type of thread in them
           e.g. discussion, question

    Returns:
        A list of non-courseware topics and a set of existing topics among
        topic_ids.

    """
    non_courseware_topics = []
    existing_topic_ids = set()
    topics = list(course.discussion_topics.items())
    for name, entry in topics:
        if not topic_ids or entry['id'] in topic_ids:
            discussion_topic = DiscussionTopic(
                entry["id"], name, get_thread_list_url(request, course_key, [entry["id"]]),
                None,
                thread_counts.get(entry["id"])
            )
            non_courseware_topics.append(DiscussionTopicSerializer(discussion_topic).data)

            if topic_ids and entry["id"] in topic_ids:
                existing_topic_ids.add(entry["id"])

    return non_courseware_topics, existing_topic_ids


def get_course_topics(request: Request, course_key: CourseKey, topic_ids: Optional[Set[str]] = None):
    """
    Returns the course topic listing for the given course and user; filtered
    by 'topic_ids' list if given.

    Parameters:

        course_key: The key of the course to get topics for
        topic_ids: A list of topic IDs for which topic details are requested

    Returns:

        A course topic listing dictionary; see discussion.rest_api.views.CourseTopicViews
        for more detail.

    Raises:
        DiscussionNotFoundError: If topic/s not found for given topic_ids.
    """
    course = _get_course(course_key, request.user)
    thread_counts = get_course_commentable_counts(course.id)

    courseware_topics, existing_courseware_topic_ids = get_courseware_topics(
        request, course_key, course, topic_ids, thread_counts
    )
    non_courseware_topics, existing_non_courseware_topic_ids = get_non_courseware_topics(
        request, course_key, course, topic_ids, thread_counts,
    )

    if topic_ids:
        not_found_topic_ids = topic_ids - (existing_courseware_topic_ids | existing_non_courseware_topic_ids)
        if not_found_topic_ids:
            raise DiscussionNotFoundError(
                "Discussion not found for '{}'.".format(", ".join(str(id) for id in not_found_topic_ids))
            )

    return {
        "courseware_topics": courseware_topics,
        "non_courseware_topics": non_courseware_topics,
    }


def get_v2_non_courseware_topics_as_v1(request, course_key, topics):
    """
    Takes v2 topics list and returns v1 list of non courseware topics
    """
    non_courseware_topics = []
    for topic in topics:
        if topic.get('usage_key', '') is None:
            for key in ['usage_key', 'enabled_in_context']:
                topic.pop(key)
            topic.update({
                'children': [],
                'thread_list_url': get_thread_list_url(
                    request,
                    course_key,
                    topic.get('id'),
                )
            })
            non_courseware_topics.append(topic)
    return non_courseware_topics


def get_v2_courseware_topics_as_v1(request, course_key, sequentials, topics):
    """
    Returns v2 courseware topics list as v1 structure
    """
    courseware_topics = []
    for sequential in sequentials:
        children = []
        for child in sequential.get('children', []):
            for topic in topics:
                if child == topic.get('usage_key'):
                    topic.update({
                        'children': [],
                        'thread_list_url': get_thread_list_url(
                            request,
                            course_key,
                            [topic.get('id')],
                        )
                    })
                    topic.pop('enabled_in_context')
                    children.append(AttributeDict(topic))

        discussion_topic = DiscussionTopic(
            None,
            sequential.get('display_name'),
            get_thread_list_url(
                request,
                course_key,
                [child.id for child in children],
            ),
            children,
            None,
        )
        courseware_topics.append(DiscussionTopicSerializer(discussion_topic).data)
    courseware_topics = [
        courseware_topic
        for courseware_topic in courseware_topics
        if courseware_topic.get('children', [])
    ]
    return courseware_topics


def get_v2_course_topics_as_v1(
    request: Request,
    course_key: CourseKey,
    topic_ids: Optional[Iterable[str]] = None,
):
    """
    Returns v2 topics in v1 structure
    """
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

    sequentials = [value for _, value in blocks.items()
                   if value.get('type') == "sequential"]

    topics = get_course_topics_v2(course_key, request.user, topic_ids)
    non_courseware_topics = get_v2_non_courseware_topics_as_v1(
        request,
        course_key,
        topics,
    )
    courseware_topics = get_v2_courseware_topics_as_v1(
        request,
        course_key,
        sequentials,
        topics,
    )
    return {
        "courseware_topics": courseware_topics,
        "non_courseware_topics": non_courseware_topics,
    }


def get_course_topics_v2(
    course_key: CourseKey,
    user: User,
    topic_ids: Optional[Iterable[str]] = None,
    order_by: TopicOrdering = TopicOrdering.COURSE_STRUCTURE,
) -> List[Dict]:
    """
    Returns the course topic listing for the given course and user; filtered
    by 'topic_ids' list if given.

    Parameters:

        course_key: The key of the course to get topics for
        user: The requesting user, for access control
        topic_ids: A list of topic IDs for which topic details are requested
        order_by: The sort ordering for the returned list of topics

    Returns:

        A list of discussion topics for the course.

    Raises:
        ValidationError: If unsupported ordering is used.
    """
    provider_type = DiscussionsConfiguration.get(context_key=course_key).provider_type

    if provider_type in [Provider.OPEN_EDX, Provider.LEGACY]:
        thread_counts = get_course_commentable_counts(course_key)
    else:
        thread_counts = {}
        # For other providers we can't sort by activity since we don't have activity information.
        if order_by == TopicOrdering.ACTIVITY:
            raise ValidationError("Topic ordering type not supported")

    # Check access to the course
    store = modulestore()
    _get_course(course_key, user=user, check_tab=False)
    user_is_privileged = user.is_staff or user.roles.filter(
        course_id=course_key,
        name__in=[
            FORUM_ROLE_MODERATOR,
            FORUM_ROLE_COMMUNITY_TA,
            FORUM_ROLE_ADMINISTRATOR,
        ]
    ).exists()

    with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
        blocks = store.get_items(
            course_key,
            qualifiers={'category': 'vertical'},
            fields=['usage_key', 'discussion_enabled', 'display_name'],
        )
        accessible_vertical_keys = []
        for block in blocks:
            if block.discussion_enabled and (not block.visible_to_staff_only or user_is_privileged):
                accessible_vertical_keys.append(block.usage_key)
        accessible_vertical_keys.append(None)

    topics_query = DiscussionTopicLink.objects.filter(
        context_key=course_key,
        provider_id=provider_type,
    )

    if user_is_privileged:
        topics_query = topics_query.filter(Q(usage_key__in=accessible_vertical_keys) | Q(enabled_in_context=False))
    else:
        topics_query = topics_query.filter(usage_key__in=accessible_vertical_keys, enabled_in_context=True)

    if topic_ids:
        topics_query = topics_query.filter(external_id__in=topic_ids)

    if order_by == TopicOrdering.ACTIVITY:
        topics_query = sorted(
            topics_query,
            key=lambda topic: sum(thread_counts.get(topic.external_id, {}).values()),
            reverse=True,
        )
    elif order_by == TopicOrdering.NAME:
        topics_query = topics_query.order_by('title')
    else:
        topics_query = topics_query.order_by('ordering')

    topics_data = DiscussionTopicSerializerV2(topics_query, many=True, context={"thread_counts": thread_counts}).data
    return [
        topic_data
        for topic_data in topics_data
        if topic_data["enabled_in_context"] or sum(topic_data["thread_counts"].values())
    ]


def _get_user_profile_dict(request, usernames):
    """
    Gets user profile details for a list of usernames and creates a dictionary with
    profile details against username.

    Parameters:

        request: The django request object.
        usernames: A string of comma separated usernames.

    Returns:

        A dict with username as key and user profile details as value.
    """
    if usernames:
        username_list = usernames.split(",")
    else:
        username_list = []
    user_profile_details = get_account_settings(request, username_list)
    return {user['username']: user for user in user_profile_details}


def _user_profile(user_profile):
    """
    Returns the user profile object. For now, this just comprises the
    profile_image details.
    """
    return {
        'profile': {
            'image': user_profile['profile_image']
        }
    }


def _get_users(discussion_entity_type, discussion_entity, username_profile_dict):
    """
    Returns users with profile details for given discussion thread/comment.

    Parameters:

        discussion_entity_type: DiscussionEntity Enum value for Thread or Comment.
        discussion_entity: Serialized thread/comment.
        username_profile_dict: A dict with user profile details against username.

    Returns:

        A dict of users with username as key and user profile details as value.
    """
    users = {}
    if discussion_entity['author']:
        user_profile = username_profile_dict.get(discussion_entity['author'])
        if user_profile:
            users[discussion_entity['author']] = _user_profile(user_profile)

    if (
        discussion_entity_type == DiscussionEntity.comment
        and discussion_entity['endorsed']
        and discussion_entity['endorsed_by']
    ):
        users[discussion_entity['endorsed_by']] = _user_profile(username_profile_dict[discussion_entity['endorsed_by']])
    return users


def _add_additional_response_fields(
    request, serialized_discussion_entities, usernames, discussion_entity_type, include_profile_image
):
    """
    Adds additional data to serialized discussion thread/comment.

    Parameters:

        request: The django request object.
        serialized_discussion_entities: A list of serialized Thread/Comment.
        usernames: A list of usernames involved in threads/comments (e.g. as author or as comment endorser).
        discussion_entity_type: DiscussionEntity Enum value for Thread or Comment.
        include_profile_image: (boolean) True if requested_fields has 'profile_image' else False.

    Returns:

        A list of serialized discussion thread/comment with additional data if requested.
    """
    if include_profile_image:
        username_profile_dict = _get_user_profile_dict(request, usernames=','.join(usernames))
        for discussion_entity in serialized_discussion_entities:
            discussion_entity['users'] = _get_users(discussion_entity_type, discussion_entity, username_profile_dict)

    return serialized_discussion_entities


def _include_profile_image(requested_fields):
    """
    Returns True if requested_fields list has 'profile_image' entity else False
    """
    return requested_fields and 'profile_image' in requested_fields


def _serialize_discussion_entities(request, context, discussion_entities, requested_fields, discussion_entity_type):
    """
    It serializes Discussion Entity (Thread or Comment) and add additional data if requested.

    For a given list of Thread/Comment; it serializes and add additional information to the
    object as per requested_fields list (i.e. profile_image).

    Parameters:

        request: The django request object
        context: The context appropriate for use with the thread or comment
        discussion_entities: List of Thread or Comment objects
        requested_fields: Indicates which additional fields to return
            for each thread.
        discussion_entity_type: DiscussionEntity Enum value for Thread or Comment

    Returns:

        A list of serialized discussion entities
    """
    results = []
    usernames = []
    include_profile_image = _include_profile_image(requested_fields)
    for entity in discussion_entities:
        if discussion_entity_type == DiscussionEntity.thread:
            serialized_entity = ThreadSerializer(entity, context=context).data
        elif discussion_entity_type == DiscussionEntity.comment:
            serialized_entity = CommentSerializer(entity, context=context).data
        results.append(serialized_entity)

        if include_profile_image:
            if serialized_entity['author'] and serialized_entity['author'] not in usernames:
                usernames.append(serialized_entity['author'])
            if (
                'endorsed' in serialized_entity and serialized_entity['endorsed'] and
                'endorsed_by' in serialized_entity and
                serialized_entity['endorsed_by'] and serialized_entity['endorsed_by'] not in usernames
            ):
                usernames.append(serialized_entity['endorsed_by'])

    results = _add_additional_response_fields(
        request, results, usernames, discussion_entity_type, include_profile_image
    )
    return results


def get_thread_list(
    request: Request,
    course_key: CourseKey,
    page: int,
    page_size: int,
    topic_id_list: List[str] = None,
    text_search: Optional[str] = None,
    following: Optional[bool] = False,
    author: Optional[str] = None,
    thread_type: Optional[ThreadType] = None,
    flagged: Optional[bool] = None,
    view: Optional[ViewType] = None,
    order_by: ThreadOrderingType = "last_activity_at",
    order_direction: Literal["desc"] = "desc",
    requested_fields: Optional[List[Literal["profile_image"]]] = None,
    count_flagged: bool = None,
):
    """
    Return the list of all discussion threads pertaining to the given course

    Parameters:

    request: The django request objects used for build_absolute_uri
    course_key: The key of the course to get discussion threads for
    page: The page number (1-indexed) to retrieve
    page_size: The number of threads to retrieve per page
    count_flagged: If true, fetch the count of flagged items in each thread
    topic_id_list: The list of topic_ids to get the discussion threads for
    text_search A text search query string to match
    following: If true, retrieve only threads the requester is following
    author: If provided, retrieve only threads by this author
    thread_type: filter for "discussion" or "question threads
    flagged: filter for only threads that are flagged
    view: filters for either "unread" or "unanswered" threads
    order_by: The key in which to sort the threads by. The only values are
        "last_activity_at", "comment_count", and "vote_count". The default is
        "last_activity_at".
    order_direction: The direction in which to sort the threads by. The default
        and only value is "desc". This will be removed in a future major
        version.
    requested_fields: Indicates which additional fields to return
        for each thread. (i.e. ['profile_image'])

    Note that topic_id_list, text_search, and following are mutually exclusive.

    Returns:

    A paginated result containing a list of threads; see
    discussion.rest_api.views.ThreadViewSet for more detail.

    Raises:

    PermissionDenied: If count_flagged is set but the user isn't privileged
    ValidationError: if an invalid value is passed for a field.
    ValueError: if more than one of the mutually exclusive parameters is
      provided
    CourseNotFoundError: if the requesting user does not have access to the requested course
    PageNotFoundError: if page requested is beyond the last
    """
    exclusive_param_count = sum(1 for param in [topic_id_list, text_search, following] if param)
    if exclusive_param_count > 1:  # pragma: no cover
        raise ValueError("More than one mutually exclusive param passed to get_thread_list")

    cc_map = {"last_activity_at": "activity", "comment_count": "comments", "vote_count": "votes"}
    if order_by not in cc_map:
        raise ValidationError({
            "order_by":
                [f"Invalid value. '{order_by}' must be 'last_activity_at', 'comment_count', or 'vote_count'"]
        })
    if order_direction != "desc":
        raise ValidationError({
            "order_direction": [f"Invalid value. '{order_direction}' must be 'desc'"]
        })

    course = _get_course(course_key, request.user)
    context = get_context(course, request)

    author_id = None
    if author:
        try:
            author_id = User.objects.get(username=author).id
        except User.DoesNotExist:
            # Raising an error for a missing user leaks the presence of a username,
            # so just return an empty response.
            return DiscussionAPIPagination(request, 0, 1).get_paginated_response({
                "results": [],
                "text_search_rewrite": None,
            })

    if count_flagged and not context["has_moderation_privilege"]:
        raise PermissionDenied("`count_flagged` can only be set by users with moderator access or higher.")

    group_id = None
    allowed_roles = [
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_MODERATOR,
    ]

    if request.GET.get("group_id", None):
        if Role.user_has_role_for_course(request.user, course_key, allowed_roles):
            try:
                group_id = int(request.GET.get("group_id", None))
            except ValueError:
                pass

    if (group_id is None) and (
        not context["has_moderation_privilege"]
        or request.user.id in context["ta_user_ids"]
    ):
        group_id = get_group_id_for_user(request.user, CourseDiscussionSettings.get(course.id))

    query_params = {
        "user_id": str(request.user.id),
        "group_id": group_id,
        "page": page,
        "per_page": page_size,
        "sort_key": cc_map.get(order_by),
        "author_id": author_id,
        "flagged": flagged,
        "thread_type": thread_type,
        "count_flagged": count_flagged,
    }

    if view:
        if view in ["unread", "unanswered", "unresponded"]:
            query_params[view] = "true"
        else:
            raise ValidationError({
                "view": [f"Invalid value. '{view}' must be 'unread' or 'unanswered'"]
            })

    if following:
        paginated_results = context["cc_requester"].subscribed_threads(query_params)
    else:
        query_params["course_id"] = str(course.id)
        query_params["commentable_ids"] = ",".join(topic_id_list) if topic_id_list else None
        query_params["text"] = text_search
        paginated_results = Thread.search(query_params)
    # The comments service returns the last page of results if the requested
    # page is beyond the last page, but we want be consistent with DRF's general
    # behavior and return a PageNotFoundError in that case
    if paginated_results.page != page:
        raise PageNotFoundError("Page not found (No results on this page).")

    results = _serialize_discussion_entities(
        request, context, paginated_results.collection, requested_fields, DiscussionEntity.thread
    )

    paginator = DiscussionAPIPagination(
        request,
        paginated_results.page,
        paginated_results.num_pages,
        paginated_results.thread_count
    )
    return paginator.get_paginated_response({
        "results": results,
        "text_search_rewrite": paginated_results.corrected_text,
    })


def get_learner_active_thread_list(request, course_key, query_params):
    """
    Returns a list of active threads for a particular user

    Parameters:

    request: The django request objects used for build_absolute_uri
    course_key: The key of the course
    query_params: Parameters to fetch data from comments service. It must contain
                        user_id, course_id, page, per_page, group_id, count_flagged

    Returns:

    A paginated result containing a list of threads.

    ** Sample Response
    {
        "results": [
            {
                "id": "thread_id",
                "author": "author_username",
                "author_label": "Staff",
                "created_at": "2010-01-01T12:00:00Z",
                "updated_at": "2010-01-01T12:00:00Z",
                "raw_body": "<p></p>",
                "rendered_body": "<p></p>",
                "abuse_flagged": false,
                "voted": false,
                "vote_count": 0,
                "editable_fields": [
                    "abuse_flagged", "anonymous", "close_reason_code", "closed",
                    "edit_reason_code", "following", "pinned", "raw_body", "read",
                    "title", "topic_id", "type", "voted"
                ],
                "can_delete": true,
                "anonymous": false,
                "anonymous_to_peers": false,
                "last_edit": {
                    "original_body": "<p></p>",
                    "reason_code": null,
                    "editor_username": "author_username",
                    "created_at": "2010-01-01T12:00:00Z"
                },
                "course_id": "course-v1:edX+DemoX+Demo_Course",
                "topic_id": "i4x-edx-eiorguegnru-course-foobarbaz",
                "group_id": null,
                "group_name": null,
                "type": "discussion",
                "preview_body": "",
                "abuse_flagged_count": null,
                "title": "Post Title",
                "pinned": false,
                "closed": false,
                "following": false,
                "comment_count": 1,
                "unread_comment_count": 0,
                "comment_list_url": "http://localhost:18000/api/discussion/v1/comments/?thread_id=thread_id",
                "endorsed_comment_list_url": null,
                "non_endorsed_comment_list_url": null,
                "read": true,
                "has_endorsed": false,
                "close_reason": null,
                "closed_by": null,
                "users": {
                    "username": {
                        "profile": {
                            "image": {
                                "has_image": false,
                                "image_url_full": "http://localhost:18000/static/images/profiles/default_500.png",
                                "image_url_large": "http://localhost:18000/static/images/profiles/default_120.png",
                                "image_url_medium": "http://localhost:18000/static/images/profiles/default_50.png",
                                "image_url_small": "http://localhost:18000/static/images/profiles/default_30.png"
                            }
                        }
                    }
                }
            },
            ...
        ],
        "pagination": {
            "next": None,
            "previous": None,
            "count": 10,
            "num_pages": 1
        }
    }

    """

    course = _get_course(course_key, request.user)
    context = get_context(course, request)

    group_id = query_params.get('group_id', None)
    user_id = query_params.get('user_id', None)
    count_flagged = query_params.get('count_flagged', None)
    if user_id is None:
        return Response({'detail': 'Invalid user id'}, status=status.HTTP_400_BAD_REQUEST)

    if count_flagged and not context["has_moderation_privilege"]:
        raise PermissionDenied("count_flagged can only be set by users with moderation roles.")
    if "flagged" in query_params.keys() and not context["has_moderation_privilege"]:
        raise PermissionDenied("Flagged filter is only available for moderators")

    if group_id is None:
        comment_client_user = comment_client.User(id=user_id, course_id=course_key)
    else:
        comment_client_user = comment_client.User(id=user_id, course_id=course_key, group_id=group_id)

    try:
        threads, page, num_pages = comment_client_user.active_threads(query_params)
        threads = set_attribute(threads, "pinned", False)
        results = _serialize_discussion_entities(
            request, context, threads, {'profile_image'}, DiscussionEntity.thread
        )
        paginator = DiscussionAPIPagination(
            request,
            page,
            num_pages,
            len(threads)
        )
        return paginator.get_paginated_response({
            "results": results,
        })
    except CommentClient500Error:
        return DiscussionAPIPagination(
            request,
            page_num=1,
            num_pages=0,
        ).get_paginated_response({
            "results": [],
        })


def get_comment_list(request, thread_id, endorsed, page, page_size, flagged=False, requested_fields=None,
                     merge_question_type_responses=False):
    """
    Return the list of comments in the given thread.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        thread_id: The id of the thread to get comments for.

        endorsed: Boolean indicating whether to get endorsed or non-endorsed
          comments (or None for all comments). Must be None for a discussion
          thread and non-None for a question thread.

        page: The page number (1-indexed) to retrieve

        page_size: The number of comments to retrieve per page

        flagged: Filter comments by flagged for abuse status.

        requested_fields: Indicates which additional fields to return for
        each comment. (i.e. ['profile_image'])

    Returns:

        A paginated result containing a list of comments; see
        discussion.rest_api.views.CommentViewSet for more detail.
    """
    response_skip = page_size * (page - 1)
    reverse_order = request.GET.get('reverse_order', False)
    from_mfe_sidebar = request.GET.get("enable_in_context_sidebar", False)
    cc_thread, context = _get_thread_and_context(
        request,
        thread_id,
        retrieve_kwargs={
            "with_responses": True,
            "recursive": False,
            "user_id": request.user.id,
            "flagged_comments": flagged,
            "response_skip": response_skip,
            "response_limit": page_size,
            "reverse_order": reverse_order,
            "merge_question_type_responses": merge_question_type_responses
        }
    )
    # Responses to discussion threads cannot be separated by endorsed, but
    # responses to question threads must be separated by endorsed due to the
    # existing comments service interface
    if cc_thread["thread_type"] == "question" and not merge_question_type_responses:
        if endorsed is None:  # lint-amnesty, pylint: disable=no-else-raise
            raise ValidationError({"endorsed": ["This field is required for question threads."]})
        elif endorsed:
            # CS does not apply resp_skip and resp_limit to endorsed responses
            # of a question post
            responses = cc_thread["endorsed_responses"][response_skip:(response_skip + page_size)]
            resp_total = len(cc_thread["endorsed_responses"])
        else:
            responses = cc_thread["non_endorsed_responses"]
            resp_total = cc_thread["non_endorsed_resp_total"]
    else:
        if not merge_question_type_responses:
            if endorsed is not None:
                raise ValidationError(
                    {"endorsed": ["This field may not be specified for discussion threads."]}
                )
        responses = cc_thread["children"]
        resp_total = cc_thread["resp_total"]

    # The comments service returns the last page of results if the requested
    # page is beyond the last page, but we want be consistent with DRF's general
    # behavior and return a PageNotFoundError in that case
    if not responses and page != 1:
        raise PageNotFoundError("Page not found (No results on this page).")
    num_pages = (resp_total + page_size - 1) // page_size if resp_total else 1

    results = _serialize_discussion_entities(request, context, responses, requested_fields, DiscussionEntity.comment)

    paginator = DiscussionAPIPagination(request, page, num_pages, resp_total)
    track_thread_viewed_event(request, context["course"], cc_thread, from_mfe_sidebar)
    return paginator.get_paginated_response(results)


def _check_fields(allowed_fields, data, message):
    """
    Checks that the keys given in data is in allowed_fields

    Arguments:
        allowed_fields (set): A set of allowed fields
        data (dict): The data to compare the allowed_fields against
        message (str): The message to return if there are any invalid fields

    Raises:
        ValidationError if the given data contains a key that is not in
            allowed_fields
    """
    non_allowed_fields = {field: [message] for field in data.keys() if field not in allowed_fields}
    if non_allowed_fields:
        raise ValidationError(non_allowed_fields)


def _check_initializable_thread_fields(data, context):
    """
    Checks if the given data contains a thread field that is not initializable
    by the requesting user

    Arguments:
        data (dict): The data to compare the allowed_fields against
        context (dict): The context appropriate for use with the thread which
            includes the requesting user

    Raises:
        ValidationError if the given data contains a thread field that is not
            initializable by the requesting user
    """
    _check_fields(
        get_initializable_thread_fields(context),
        data,
        "This field is not initializable."
    )


def _check_initializable_comment_fields(data, context):
    """
    Checks if the given data contains a comment field that is not initializable
    by the requesting user

    Arguments:
        data (dict): The data to compare the allowed_fields against
        context (dict): The context appropriate for use with the comment which
            includes the requesting user

    Raises:
        ValidationError if the given data contains a comment field that is not
            initializable by the requesting user
    """
    _check_fields(
        get_initializable_comment_fields(context),
        data,
        "This field is not initializable."
    )


def _check_editable_fields(cc_content, data, context):
    """
    Raise ValidationError if the given update data contains a field that is not
    editable by the requesting user
    """
    _check_fields(
        get_editable_fields(cc_content, context),
        data,
        "This field is not editable."
    )


def _do_extra_actions(api_content, cc_content, request_fields, actions_form, context, request):
    """
    Perform any necessary additional actions related to content creation or
    update that require a separate comments service request.
    """
    for field, form_value in actions_form.cleaned_data.items():
        if field in request_fields and field in api_content and form_value != api_content[field]:
            api_content[field] = form_value
            if field == "following":
                _handle_following_field(form_value, context["cc_requester"], cc_content, request)
            elif field == "abuse_flagged":
                _handle_abuse_flagged_field(form_value, context["cc_requester"], cc_content, request)
            elif field == "voted":
                _handle_voted_field(form_value, cc_content, api_content, request, context)
            elif field == "read":
                _handle_read_field(api_content, form_value, context["cc_requester"], cc_content)
            elif field == "pinned":
                _handle_pinned_field(form_value, cc_content, context["cc_requester"])
            else:
                raise ValidationError({field: ["Invalid Key"]})


def _handle_following_field(form_value, user, cc_content, request):
    """follow/unfollow thread for the user"""
    course_key = CourseKey.from_string(cc_content.course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    if form_value:
        user.follow(cc_content)
    else:
        user.unfollow(cc_content)
    signal = thread_followed if form_value else thread_unfollowed
    send_signal_after_commit(
        lambda: signal.send(sender=None, user=user, post=cc_content)
    )
    track_thread_followed_event(request, course, cc_content, form_value)


def _handle_abuse_flagged_field(form_value, user, cc_content, request):
    """mark or unmark thread/comment as abused"""
    course_key = CourseKey.from_string(cc_content.course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    if form_value:
        cc_content.flagAbuse(user, cc_content)
        track_discussion_reported_event(request, course, cc_content)
        if ENABLE_DISCUSSIONS_MFE.is_enabled(course_key):
            if cc_content.type == 'thread':
                send_signal_after_commit(
                    lambda: thread_flagged.send(sender='flag_abuse_for_thread', user=user, post=cc_content)
                )
            else:
                send_signal_after_commit(
                    lambda: comment_flagged.send(sender='flag_abuse_for_comment', user=user, post=cc_content)
                )
    else:
        remove_all = bool(is_privileged_user(course_key, User.objects.get(id=user.id)))
        cc_content.unFlagAbuse(user, cc_content, remove_all)
        track_discussion_unreported_event(request, course, cc_content)


def _handle_voted_field(form_value, cc_content, api_content, request, context):
    """vote or undo vote on thread/comment"""
    signal = thread_voted if cc_content.type == 'thread' else comment_voted
    send_signal_after_commit(
        lambda: signal.send(sender=None, user=context["request"].user, post=cc_content)
    )
    if form_value:
        context["cc_requester"].vote(cc_content, "up")
        api_content["vote_count"] += 1
    else:
        context["cc_requester"].unvote(cc_content)
        api_content["vote_count"] -= 1
    track_voted_event(
        request, context["course"], cc_content, vote_value="up", undo_vote=not form_value
    )


def _handle_read_field(api_content, form_value, user, cc_content):
    """
    Marks thread as read for the user
    """
    if form_value and not cc_content['read']:
        user.read(cc_content)
        # When a thread is marked as read, all of its responses and comments
        # are also marked as read.
        api_content["unread_comment_count"] = 0


def _handle_pinned_field(pin_thread: bool, cc_content: Thread, user: User):
    """
    Pins or unpins a thread

    Arguments:

        pin_thread (bool): Value of field from API
        cc_content (Thread): The thread on which to operate
        user (User): The user performing the action
    """
    if pin_thread:
        cc_content.pin(user, cc_content.id)
    else:
        cc_content.un_pin(user, cc_content.id)


def _handle_comment_signals(update_data, comment, user, sender=None):
    """
    Send signals depending upon the the patch (update_data)
    """
    for key, value in update_data.items():
        if key == "endorsed" and value is True:
            send_signal_after_commit(
                lambda: comment_endorsed.send(sender=sender, user=user, post=comment)
            )


def create_thread(request, thread_data):
    """
    Create a thread.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        thread_data: The data for the created thread.

    Returns:

        The created thread; see discussion.rest_api.views.ThreadViewSet for more
        detail.
    """
    course_id = thread_data.get("course_id")
    from_mfe_sidebar = thread_data.pop("enable_in_context_sidebar", False)
    user = request.user
    if not course_id:
        raise ValidationError({"course_id": ["This field is required."]})
    try:
        course_key = CourseKey.from_string(course_id)
        course = _get_course(course_key, user)
    except InvalidKeyError as err:
        raise ValidationError({"course_id": ["Invalid value."]}) from err

    if not discussion_open_for_user(course, user):
        raise DiscussionBlackOutException

    notify_all_learners = thread_data.pop("notify_all_learners", False)

    context = get_context(course, request)
    _check_initializable_thread_fields(thread_data, context)
    discussion_settings = CourseDiscussionSettings.get(course_key)
    if (
        "group_id" not in thread_data and
        is_commentable_divided(course_key, thread_data.get("topic_id"), discussion_settings)
    ):
        thread_data = thread_data.copy()
        thread_data["group_id"] = get_group_id_for_user(user, discussion_settings)
    serializer = ThreadSerializer(data=thread_data, context=context)
    actions_form = ThreadActionsForm(thread_data)
    if not (serializer.is_valid() and actions_form.is_valid()):
        raise ValidationError(dict(list(serializer.errors.items()) + list(actions_form.errors.items())))
    serializer.save()
    cc_thread = serializer.instance
    # Use send_signal_after_commit() to ensure the signal is sent only after the transaction commits.
    send_signal_after_commit(
        lambda: thread_created.send(sender=None, user=user, post=cc_thread, notify_all_learners=notify_all_learners)
    )
    api_thread = serializer.data
    _do_extra_actions(api_thread, cc_thread, list(thread_data.keys()), actions_form, context, request)

    track_thread_created_event(request, course, cc_thread, actions_form.cleaned_data["following"],
                               from_mfe_sidebar, notify_all_learners)

    return api_thread


def create_comment(request, comment_data):
    """
    Create a comment.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        comment_data: The data for the created comment.

    Returns:

        The created comment; see discussion.rest_api.views.CommentViewSet for more
        detail.
    """
    thread_id = comment_data.get("thread_id")
    from_mfe_sidebar = comment_data.pop("enable_in_context_sidebar", False)
    if not thread_id:
        raise ValidationError({"thread_id": ["This field is required."]})
    cc_thread, context = _get_thread_and_context(request, thread_id)

    course = context["course"]
    if not discussion_open_for_user(course, request.user):
        raise DiscussionBlackOutException

    # if a thread is closed; no new comments could be made to it
    if cc_thread["closed"]:
        raise PermissionDenied

    _check_initializable_comment_fields(comment_data, context)
    serializer = CommentSerializer(data=comment_data, context=context)
    actions_form = CommentActionsForm(comment_data)
    if not (serializer.is_valid() and actions_form.is_valid()):
        raise ValidationError(dict(list(serializer.errors.items()) + list(actions_form.errors.items())))
    context["cc_requester"].follow(cc_thread)
    serializer.save()
    cc_comment = serializer.instance
    send_signal_after_commit(
        lambda: comment_created.send(sender=None, user=request.user, post=cc_comment)
    )
    api_comment = serializer.data
    _do_extra_actions(api_comment, cc_comment, list(comment_data.keys()), actions_form, context, request)
    track_comment_created_event(request, course, cc_comment, cc_thread["commentable_id"], followed=False,
                                from_mfe_sidebar=from_mfe_sidebar)
    return api_comment


def update_thread(request, thread_id, update_data):
    """
    Update a thread.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        thread_id: The id for the thread to update.

        update_data: The data to update in the thread.

    Returns:

        The updated thread; see discussion.rest_api.views.ThreadViewSet for more
        detail.
    """
    cc_thread, context = _get_thread_and_context(request, thread_id, retrieve_kwargs={"with_responses": True})
    _check_editable_fields(cc_thread, update_data, context)
    serializer = ThreadSerializer(cc_thread, data=update_data, partial=True, context=context)
    actions_form = ThreadActionsForm(update_data)
    if not (serializer.is_valid() and actions_form.is_valid()):
        raise ValidationError(dict(list(serializer.errors.items()) + list(actions_form.errors.items())))
    # Only save thread object if some of the edited fields are in the thread data, not extra actions
    if set(update_data) - set(actions_form.fields):
        serializer.save()
        # signal to update Teams when a user edits a thread
        send_signal_after_commit(
            lambda: thread_edited.send(sender=None, user=request.user, post=cc_thread)
        )
    api_thread = serializer.data
    _do_extra_actions(api_thread, cc_thread, list(update_data.keys()), actions_form, context, request)

    # always return read as True (and therefore unread_comment_count=0) as reasonably
    # accurate shortcut, rather than adding additional processing.
    api_thread['read'] = True
    api_thread['unread_comment_count'] = 0
    return api_thread


def update_comment(request, comment_id, update_data):
    """
    Update a comment.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        comment_id: The id for the comment to update.

        update_data: The data to update in the comment.

    Returns:

        The updated comment; see discussion.rest_api.views.CommentViewSet for more
        detail.

    Raises:

        CommentNotFoundError: if the comment does not exist or is not accessible
        to the requesting user

        PermissionDenied: if the comment is accessible to but not editable by
          the requesting user

        ValidationError: if there is an error applying the update (e.g. raw_body
          is empty or thread_id is included)
    """
    cc_comment, context = _get_comment_and_context(request, comment_id)
    _check_editable_fields(cc_comment, update_data, context)
    serializer = CommentSerializer(cc_comment, data=update_data, partial=True, context=context)
    actions_form = CommentActionsForm(update_data)
    if not (serializer.is_valid() and actions_form.is_valid()):
        raise ValidationError(dict(list(serializer.errors.items()) + list(actions_form.errors.items())))
    # Only save comment object if some of the edited fields are in the comment data, not extra actions
    if set(update_data) - set(actions_form.fields):
        serializer.save()
        send_signal_after_commit(
            lambda: comment_edited.send(sender=None, user=request.user, post=cc_comment)
        )
    api_comment = serializer.data
    _do_extra_actions(api_comment, cc_comment, list(update_data.keys()), actions_form, context, request)
    _handle_comment_signals(update_data, cc_comment, request.user)
    return api_comment


def get_thread(request, thread_id, requested_fields=None, course_id=None):
    """
    Retrieve a thread.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        thread_id: The id for the thread to retrieve

        course_id: the id of the course the threads belongs to

        requested_fields: Indicates which additional fields to return for
        thread. (i.e. ['profile_image'])
    """
    # Possible candidate for optimization with caching:
    #   Param with_responses=True required only to add "response_count" to response.
    cc_thread, context = _get_thread_and_context(
        request,
        thread_id,
        retrieve_kwargs={
            "with_responses": True,
            "user_id": str(request.user.id),
        },
        course_id=course_id,
    )
    if course_id and course_id != cc_thread.course_id:
        raise ThreadNotFoundError("Thread not found.")
    return _serialize_discussion_entities(request, context, [cc_thread], requested_fields, DiscussionEntity.thread)[0]


def get_response_comments(request, comment_id, page, page_size, requested_fields=None):
    """
    Return the list of comments for the given thread response.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        comment_id: The id of the comment/response to get child comments for.

        page: The page number (1-indexed) to retrieve

        page_size: The number of comments to retrieve per page

        requested_fields: Indicates which additional fields to return for
        each child comment. (i.e. ['profile_image'])

    Returns:

        A paginated result containing a list of comments

    """
    try:
        cc_comment = Comment(id=comment_id).retrieve()
        reverse_order = request.GET.get('reverse_order', False)
        cc_thread, context = _get_thread_and_context(
            request,
            cc_comment["thread_id"],
            retrieve_kwargs={
                "with_responses": True,
                "recursive": True,
                "reverse_order": reverse_order,
            }
        )
        if cc_thread["thread_type"] == "question":
            thread_responses = itertools.chain(cc_thread["endorsed_responses"], cc_thread["non_endorsed_responses"])
        else:
            thread_responses = cc_thread["children"]
        response_comments = []
        for response in thread_responses:
            if response["id"] == comment_id:
                response_comments = response["children"]
                break

        response_skip = page_size * (page - 1)
        paged_response_comments = response_comments[response_skip:(response_skip + page_size)]
        if not paged_response_comments and page != 1:
            raise PageNotFoundError("Page not found (No results on this page).")

        results = _serialize_discussion_entities(
            request, context, paged_response_comments, requested_fields, DiscussionEntity.comment
        )

        comments_count = len(response_comments)
        num_pages = (comments_count + page_size - 1) // page_size if comments_count else 1
        paginator = DiscussionAPIPagination(request, page, num_pages, comments_count)
        return paginator.get_paginated_response(results)
    except CommentClientRequestError as err:
        raise CommentNotFoundError("Comment not found") from err


def get_user_comments(
    request: Request,
    author: User,
    course_key: CourseKey,
    flagged: bool = False,
    page: int = 1,
    page_size: int = 10,
    requested_fields: Optional[List[str]] = None,
):
    """
    Returns the list of comments made by the user in the requested course.

    Arguments:

        request: The django request object.

        author: The user to get comments from.

        course_key: The course locator to filter the comments.

        flagged: Filter comments by flagged status.

        page: The page number (1-indexed) to retrieve

        page_size: The number of comments to retrieve per page

        requested_fields: Indicates which additional fields to return for
        each child comment. (i.e. ['profile_image'])

    Returns:

        A paginated result containing a list of comments.
    """
    course = _get_course(course_key, request.user)
    context = get_context(course, request)

    if flagged and not context["has_moderation_privilege"]:
        raise ValidationError("Only privileged users can filter comments by flagged status")

    try:
        response = Comment.retrieve_all({
            'user_id': author.id,
            'course_id': str(course_key),
            'flagged': flagged,
            'page': page,
            'per_page': page_size,
        })
    except CommentClientRequestError as err:
        raise CommentNotFoundError("Comment not found") from err

    response_comments = response["collection"]
    if not response_comments and page != 1:
        raise PageNotFoundError("Page not found (No results on this page).")

    results = _serialize_discussion_entities(
        request,
        context,
        response_comments,
        requested_fields,
        DiscussionEntity.comment,
    )

    comment_count = response["comment_count"]
    num_pages = response["num_pages"]
    paginator = DiscussionAPIPagination(request, page, num_pages, comment_count)
    return paginator.get_paginated_response(results)


def delete_thread(request, thread_id):
    """
    Delete a thread.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        thread_id: The id for the thread to delete

    Raises:

        PermissionDenied: if user does not have permission to delete thread

    """
    cc_thread, context = _get_thread_and_context(request, thread_id)
    if can_delete(cc_thread, context):
        cc_thread.delete()
        send_signal_after_commit(
            lambda: thread_deleted.send(sender=None, user=request.user, post=cc_thread)
        )
        track_thread_deleted_event(request, context["course"], cc_thread)
    else:
        raise PermissionDenied


def delete_comment(request, comment_id):
    """
    Delete a comment.

    Arguments:

        request: The django request object used for build_absolute_uri and
          determining the requesting user.

        comment_id: The id of the comment to delete

    Raises:

        PermissionDenied: if user does not have permission to delete thread

    """
    cc_comment, context = _get_comment_and_context(request, comment_id)
    if can_delete(cc_comment, context):
        cc_comment.delete()
        send_signal_after_commit(
            lambda: comment_deleted.send(sender=None, user=request.user, post=cc_comment)
        )
        track_comment_deleted_event(request, context["course"], cc_comment)
    else:
        raise PermissionDenied


@function_trace("get_course_discussion_user_stats")
def get_course_discussion_user_stats(
    request,
    course_key_str: str,
    page: int,
    page_size: int,
    order_by: UserOrdering = None,
    username_search_string: str = None,
) -> Dict:
    """
    Get paginated course discussion stats for users in the course.

    Args:
        request (Request): DRF request
        course_key_str (str): course key string
        page (int): Page number to fetch
        page_size (int): Number of items in each page
        order_by (UserOrdering): The ordering to use for the user stats
        username_search_string (str): Partial string to match user names

    Returns:
        Paginated data of a user's discussion stats sorted based on the specified ordering.

    """
    course_key = CourseKey.from_string(course_key_str)
    is_privileged = has_discussion_privileges(user=request.user, course_id=course_key) or request.user.is_staff
    if is_privileged:
        order_by = order_by or UserOrdering.BY_FLAGS
    else:
        order_by = order_by or UserOrdering.BY_ACTIVITY
        if order_by == UserOrdering.BY_FLAGS:
            raise ValidationError({"order_by": "Invalid value"})

    params = {
        'sort_key': str(order_by),
        'page': page,
        'per_page': page_size,
    }
    comma_separated_usernames = matched_users_count = matched_users_pages = None
    if username_search_string:
        comma_separated_usernames, matched_users_count, matched_users_pages = get_usernames_from_search_string(
            course_key, username_search_string, page, page_size
        )
        search_event_data = {
            'query': username_search_string,
            'search_type': 'Learner',
            'page': params.get('page'),
            'sort_key': params.get('sort_key'),
            'total_results': matched_users_count,
        }
        course = _get_course(course_key, request.user)
        track_forum_search_event(request, course, search_event_data)
        if not comma_separated_usernames:
            return DiscussionAPIPagination(request, 0, 1).get_paginated_response({
                "results": [],
            })

        params['usernames'] = comma_separated_usernames

    course_stats_response = get_course_user_stats(course_key, params)

    if comma_separated_usernames:
        updated_course_stats = add_stats_for_users_with_no_discussion_content(
            course_stats_response["user_stats"],
            comma_separated_usernames,
        )
        course_stats_response["user_stats"] = updated_course_stats

    serializer = UserStatsSerializer(
        course_stats_response["user_stats"],
        context={"is_privileged": is_privileged},
        many=True,
    )

    paginator = DiscussionAPIPagination(
        request,
        course_stats_response["page"],
        matched_users_pages if username_search_string else course_stats_response["num_pages"],
        matched_users_count if username_search_string else course_stats_response["count"],
    )
    return paginator.get_paginated_response({
        "results": serializer.data,
    })


def get_users_without_stats(
    username_search_string,
    course_key,
    page_number,
    page_size,
    request,
    is_privileged
):
    """
    This return users with no user stats.
    This function will be deprecated when this ticket DOS-3414 is resolved
    """
    if username_search_string:
        comma_separated_usernames, matched_users_count, matched_users_pages = get_usernames_from_search_string(
            course_key, username_search_string, page_number, page_size
        )
        if not comma_separated_usernames:
            return DiscussionAPIPagination(request, 0, 1).get_paginated_response({
                "results": [],
            })

    else:
        comma_separated_usernames, matched_users_count, matched_users_pages = get_usernames_for_course(
            course_key, page_number, page_size
        )

    if comma_separated_usernames:
        updated_course_stats = add_stats_for_users_with_null_values([], comma_separated_usernames)

        serializer = UserStatsSerializer(updated_course_stats, context={"is_privileged": is_privileged}, many=True)
        paginator = DiscussionAPIPagination(
            request,
            page_number,
            matched_users_pages,
            matched_users_count,
        )
        return paginator.get_paginated_response({
            "results": serializer.data,
        })


def add_stats_for_users_with_null_values(course_stats, users_in_course):
    """
    Update users stats for users with no discussion stats available in course
    """
    users_returned_from_api = [user['username'] for user in course_stats]
    user_list = users_in_course.split(',')
    users_with_no_discussion_content = set(user_list) ^ set(users_returned_from_api)
    updated_course_stats = course_stats
    for user in users_with_no_discussion_content:
        updated_course_stats.append({
            'username': user,
            'threads': None,
            'replies': None,
            'responses': None,
            'active_flags': None,
            'inactive_flags': None,
        })
    updated_course_stats = sorted(updated_course_stats, key=lambda d: len(d['username']))
    return updated_course_stats
