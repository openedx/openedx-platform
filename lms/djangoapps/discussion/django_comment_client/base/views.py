"""Views for discussion forums."""
import functools
import json
import logging
import random
import time

import six
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core import exceptions
from django.http import Http404, HttpResponse, HttpResponseServerError
from django.utils.translation import gettext as _
from django.views.decorators import csrf
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey
from openedx_events.learning.data import DiscussionThreadData, UserData, UserPersonalData
from openedx_events.learning.signals import (
    FORUM_RESPONSE_COMMENT_CREATED,
    FORUM_THREAD_CREATED,
    FORUM_THREAD_RESPONSE_CREATED
)

import lms.djangoapps.discussion.django_comment_client.settings as cc_settings
import openedx.core.djangoapps.django_comment_common.comment_client as cc
from openedx.core.djangoapps.django_comment_common.models import has_permission
from common.djangoapps.student.roles import GlobalStaff
from common.djangoapps.track import contexts
from common.djangoapps.util.file import store_uploaded_file
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_overview_with_access, get_course_with_access
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.discussion.django_comment_client.permissions import (
    check_permissions_by_view,
    get_team
)
from lms.djangoapps.discussion.django_comment_client.utils import (
    JsonError,
    JsonResponse,
    add_courseware_context,
    discussion_category_id_access,
    get_ability,
    get_annotated_content_info,
    get_cached_discussion_id_map,
    get_group_id_for_comments_service,
    get_user_group_ids,
    is_comment_too_deep,
    prepare_content,
    sanitize_body
)
from lms.djangoapps.discussion.rest_api.utils import send_signal_after_commit
from openedx.core.djangoapps.django_comment_common.signals import (
    comment_created,
    comment_deleted,
    comment_edited,
    comment_endorsed,
    comment_flagged,
    comment_voted,
    thread_created,
    thread_deleted,
    thread_edited,
    thread_flagged,
    thread_followed,
    thread_unfollowed,
    thread_voted
)
from openedx.core.djangoapps.django_comment_common.utils import ThreadContext
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)

TRACKING_MAX_FORUM_BODY = 2000
TRACKING_MAX_FORUM_TITLE = 1000
_EVENT_NAME_TEMPLATE = 'edx.forum.{obj_type}.{action_name}'

TRACKING_LOG_TO_EVENT_MAPS = {
    'edx.forum.thread.created': FORUM_THREAD_CREATED,
    'edx.forum.response.created': FORUM_THREAD_RESPONSE_CREATED,
    'edx.forum.comment.created': FORUM_RESPONSE_COMMENT_CREATED,
}


def track_forum_event(request, event_name, course, obj, data, id_map=None):
    """
    Send out an analytics event when a forum event happens. Works for threads,
    responses to threads, and comments on those responses.
    """
    user = request.user
    data['id'] = obj.id
    commentable_id = data['commentable_id']

    team = get_team(commentable_id)
    if team is not None:
        data.update(team_id=team.team_id)

    if id_map is None:
        id_map = get_cached_discussion_id_map(course, [commentable_id], user)
    if commentable_id in id_map:
        data['category_name'] = id_map[commentable_id]["title"]
        data['category_id'] = commentable_id
    data['url'] = request.META.get('HTTP_REFERER', '')
    data['user_forums_roles'] = [
        role.name for role in user.roles.filter(course_id=course.id)
    ]
    data['user_course_roles'] = [
        role.role for role in user.courseaccessrole_set.filter(course_id=course.id)
    ]

    context = contexts.course_context_from_course_id(course.id)
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(event_name, data)

    forum_event = TRACKING_LOG_TO_EVENT_MAPS.get(event_name, None)
    if forum_event is not None:
        # .. event_implemented_name: FORUM_THREAD_CREATED
        # .. event_type: org.openedx.learning.forum.thread.created.v1

        # .. event_implemented_name: FORUM_THREAD_RESPONSE_CREATED
        # .. event_type: org.openedx.learning.forum.thread.response.created.v1

        # .. event_implemented_name: FORUM_RESPONSE_COMMENT_CREATED
        # .. event_type: org.openedx.learning.forum.thread.response.comment.created.v1
        forum_event.send_event(
            thread=DiscussionThreadData(
                anonymous=data.get('anonymous'),
                anonymous_to_peers=data.get('anonymous_to_peers'),
                body=data.get('body'),
                category_id=data.get('category_id'),
                category_name=data.get('category_name'),
                commentable_id=data.get('commentable_id'),
                group_id=data.get('group_id'),
                id=data.get('id'),
                team_id=data.get('team_id'),
                thread_type=data.get('thread_type'),
                title=data.get('title'),
                title_truncated=data.get('title_truncated'),
                truncated=data.get('truncated'),
                url=data.get('url'),
                discussion=data.get('discussion'),
                user_course_roles=data.get('user_course_roles'),
                user_forums_roles=data.get('user_forums_roles'),
                user=UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
                course_id=str(course.id),
                options=data.get('options'),
            )
        )


def track_created_event(request, event_name, course, obj, data):
    """
    Send analytics event for a newly created thread, response or comment.
    """
    data['truncated'] = len(obj.body) > TRACKING_MAX_FORUM_BODY
    data['body'] = obj.body[:TRACKING_MAX_FORUM_BODY]
    track_forum_event(request, event_name, course, obj, data)


def add_truncated_title_to_event_data(event_data, full_title):
    event_data['title_truncated'] = (len(full_title) > TRACKING_MAX_FORUM_TITLE)
    event_data['title'] = full_title[:TRACKING_MAX_FORUM_TITLE]


def track_thread_created_event(request, course, thread, followed, from_mfe_sidebar=False, notify_all_learners=False):
    """
    Send analytics event for a newly created thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='created')
    event_data = {
        'commentable_id': thread.commentable_id,
        'group_id': thread.get("group_id"),
        'thread_type': thread.thread_type,
        'anonymous': thread.anonymous,
        'anonymous_to_peers': thread.anonymous_to_peers,
        'options': {
            'followed': followed,
            'notify_all_learners': notify_all_learners
        },
        'from_mfe_sidebar': from_mfe_sidebar,
        # There is a stated desire for an 'origin' property that will state
        # whether this thread was created via courseware or the forum.
        # However, the view does not contain that data, and including it will
        # likely require changes elsewhere.
    }
    add_truncated_title_to_event_data(event_data, thread.title)
    track_created_event(request, event_name, course, thread, event_data)


def track_thread_followed_event(request, course, thread, followed):
    """
    Send analytics event for a newly followed/unfollowed thread.
    """
    action_name = 'followed' if followed else 'unfollowed'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name=action_name)
    event_data = {
        'commentable_id': thread.commentable_id,
        'id': thread.id,
        'followed': followed,
    }
    track_forum_event(request, event_name, course, thread, event_data)


def track_comment_created_event(request, course, comment, commentable_id, followed, from_mfe_sidebar=False):
    """
    Send analytics event for a newly created response or comment.
    """
    obj_type = 'comment' if comment.get("parent_id") else 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='created')
    event_data = {
        'discussion': {'id': comment.thread_id},
        'commentable_id': commentable_id,
        'options': {'followed': followed},
        'from_mfe_sidebar': from_mfe_sidebar,
    }
    parent_id = comment.get('parent_id')
    if parent_id:
        event_data['response'] = {'id': parent_id}
    track_created_event(request, event_name, course, comment, event_data)


def track_voted_event(request, course, obj, vote_value, undo_vote=False):
    """
    Send analytics event for a vote on a thread or response.
    """
    if isinstance(obj, cc.Thread):
        obj_type = 'thread'
    else:
        obj_type = 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='voted')
    event_data = {
        'commentable_id': obj.commentable_id,
        'target_username': obj.get('username'),
        'undo_vote': undo_vote,
        'vote_value': vote_value,
    }
    track_forum_event(request, event_name, course, obj, event_data)


def track_forum_search_event(request, course, search_event_data):
    """
    Send analytics event for discussions related search.
    """
    event_name = 'edx.forum.searched'

    context = contexts.course_context_from_course_id(course.id)
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(event_name, search_event_data)


def track_thread_viewed_event(request, course, thread, from_mfe_sidebar=False):
    """
    Send analytics event for a viewed thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='viewed')
    event_data = {}
    event_data['commentable_id'] = thread.get('commentable_id', '')
    event_data['from_mfe_sidebar'] = from_mfe_sidebar
    if hasattr(thread, 'username'):
        event_data['target_username'] = thread.get('username', '')
    add_truncated_title_to_event_data(event_data, thread.get('title', ''))
    track_forum_event(request, event_name, course, thread, event_data)


def track_thread_lock_unlock_event(request, course, thread, close_reason_code, locked=True):
    """
    Triggers edx.forum.thread.locked  and edx.forum.thread.unlocked tracking event
    """
    event_name = f'edx.forum.thread.{"locked" if locked else "unlocked"}'
    event_data = {
        'target_username': thread.get('username'),
        'lock_reason': close_reason_code,
        'commentable_id': thread.get('commentable_id', ''),
        'team_id': get_team(thread.commentable_id),
    }
    track_forum_event(request, event_name, course, thread, event_data)


def track_thread_edited_event(request, course, thread, edit_reason_code):
    """
    Send analytics event for an edited thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='edited')
    own_content = str(request.user.id) == thread['user_id']
    event_data = {
        'target_username': thread.get('username'),
        'content_type': 'Post',
        'own_content': own_content,
        'edit_reason': edit_reason_code,
        'commentable_id': thread.get('commentable_id', ''),
    }
    track_forum_event(request, event_name, course, thread, event_data)


def track_comment_edited_event(request, course, comment, edit_reason_code):
    """
    Send analytics event for an edited response or comment.
    """
    obj_type = 'comment' if comment.get('parent_id') else 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='edited')
    own_content = str(request.user.id) == comment['user_id']
    event_data = {
        'target_username': comment.get('username'),
        'own_content': own_content,
        'content_type': obj_type.capitalize(),
        'edit_reason': edit_reason_code,
        'commentable_id': comment.get('commentable_id', ''),
    }
    track_forum_event(request, event_name, course, comment, event_data)


def track_thread_deleted_event(request, course, thread):
    """
    Send analytics event for a deleted thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='deleted')
    own_content = str(request.user.id) == thread['user_id']
    event_data = {
        'body': thread.body[:TRACKING_MAX_FORUM_BODY],
        'content_type': 'Post',
        'own_content': own_content,
        'commentable_id': thread.get('commentable_id', ''),
    }
    if hasattr(thread, 'username'):
        event_data['target_username'] = thread.get('username', '')
    add_truncated_title_to_event_data(event_data, thread.get('title', ''))
    track_forum_event(request, event_name, course, thread, event_data)


def track_comment_deleted_event(request, course, comment):
    """
    Send analytics event for a deleted response or comment.
    """
    obj_type = 'comment' if comment.get('parent_id') else 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='deleted')
    own_content = str(request.user.id) == comment["user_id"]
    event_data = {
        'body': comment.body[:TRACKING_MAX_FORUM_BODY],
        'commentable_id': comment.get('commentable_id', ''),
        'content_type': obj_type.capitalize(),
        'own_content': own_content,
    }
    if hasattr(comment, 'username'):
        event_data['target_username'] = comment.get('username', '')
    track_forum_event(request, event_name, course, comment, event_data)


def track_thread_reported_event(request, course, thread):
    """
    Send analytics event for a reported thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='reported')
    event_data = {
        'body': thread.body[:TRACKING_MAX_FORUM_BODY],
        'truncated': len(thread.body) > TRACKING_MAX_FORUM_BODY,
        'content_type': 'Post',
        'commentable_id': thread.get('commentable_id', ''),
        'thread_type': thread.get('thread_type', ''),
        'group_id': thread.get('group_id', ''),
    }
    if hasattr(thread, 'username'):
        event_data['target_username'] = thread.get('username', '')
    add_truncated_title_to_event_data(event_data, thread.get('title', ''))
    track_forum_event(request, event_name, course, thread, event_data)


def track_comment_reported_event(request, course, comment):
    """
    Send analytics event for a reported response or comment.
    """
    obj_type = 'comment' if comment.get('parent_id') else 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='reported')
    event_data = {
        'discussion': {'id': comment.thread_id},
        'body': comment.body[:TRACKING_MAX_FORUM_BODY],
        'truncated': len(comment.body) > TRACKING_MAX_FORUM_BODY,
        'commentable_id': comment.get('commentable_id', ''),
        'content_type': obj_type.capitalize(),
    }
    if hasattr(comment, 'username'):
        event_data['target_username'] = comment.get('username', '')
    track_forum_event(request, event_name, course, comment, event_data)


def track_thread_unreported_event(request, course, thread):
    """
    Send analytics event for a unreported thread.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='thread', action_name='unreported')
    event_data = {
        'body': thread.body[:TRACKING_MAX_FORUM_BODY],
        'truncated': len(thread.body) > TRACKING_MAX_FORUM_BODY,
        'content_type': 'Post',
        'commentable_id': thread.get('commentable_id', ''),
        'reported_status_cleared': not bool(thread.get('abuse_flaggers', [])),
        'thread_type': thread.get('thread_type', ''),
        'group_id': thread.get('group_id', ''),

    }
    if hasattr(thread, 'username'):
        event_data['target_username'] = thread.get('username', '')
    add_truncated_title_to_event_data(event_data, thread.get('title', ''))
    track_forum_event(request, event_name, course, thread, event_data)


def track_comment_unreported_event(request, course, comment):
    """
    Send analytics event for a unreported response or comment.
    """
    obj_type = 'comment' if comment.get('parent_id') else 'response'
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type=obj_type, action_name='unreported')
    event_data = {
        'discussion': {'id': comment.thread_id},
        'body': comment.body[:TRACKING_MAX_FORUM_BODY],
        'truncated': len(comment.body) > TRACKING_MAX_FORUM_BODY,
        'commentable_id': comment.get('commentable_id', ''),
        'content_type': obj_type.capitalize(),
        'reported_status_cleared': not bool(comment.get('abuse_flaggers', [])),
    }
    if hasattr(comment, 'username'):
        event_data['target_username'] = comment.get('username', '')
    track_forum_event(request, event_name, course, comment, event_data)


def track_response_mark_event(request, course, comment, thread, action_name):
    """
    Send analytics event for response that is marked as endorsed or answered.
    """
    event_name = _EVENT_NAME_TEMPLATE.format(obj_type='response', action_name=action_name)
    if getattr(thread, 'thread_type', '') == 'question':
        mark_type = 'Answer'
    else:
        mark_type = 'Endorse'
    event_data = {
        'discussion': {'id': comment.thread_id},
        'commentable_id': getattr(thread, 'commentable_id', None),
        'is_thread_author': getattr(thread, 'user_id', None) == str(request.user.id),
        'is_global_staff': GlobalStaff().has_user(request.user),
        'mark_type': mark_type,
        'target_username': comment.get('username')
    }

    track_forum_event(request, event_name, course, comment, event_data)


def track_discussion_reported_event(request, course, cc_content):
    """
    Helper method for discussion reported events.
    """
    if cc_content.type == 'thread':
        track_thread_reported_event(request, course, cc_content)
    else:
        track_comment_reported_event(request, course, cc_content)


def track_discussion_unreported_event(request, course, cc_content):
    """
    Helper method for discussion unreported events.
    """
    if cc_content.type == 'thread':
        track_thread_unreported_event(request, course, cc_content)
    else:
        track_comment_unreported_event(request, course, cc_content)


def track_forum_response_mark_event(request, course, cc_content, value):
    """
    Helper method for discussions response mark event
    """
    thread = cc.Thread.find(cc_content.thread_id)
    action_name = 'mark' if value else 'unmark'
    track_response_mark_event(request, course, cc_content, thread, action_name)


def permitted(func):
    """
    View decorator to verify the user is authorized to access this endpoint.
    """
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        """
        Wrapper for the view that only calls the view if the user is authorized.
        """
        def fetch_content():
            """
            Extract the forum object from the keyword arguments to the view.
            """
            user_group_id = None
            content_user_group_id = None
            if "thread_id" in kwargs:
                content = cc.Thread.find(kwargs["thread_id"]).to_dict()
            elif "comment_id" in kwargs:
                content = cc.Comment.find(kwargs["comment_id"]).to_dict()
            elif "commentable_id" in kwargs:
                content = cc.Commentable.find(kwargs["commentable_id"]).to_dict()
            else:
                content = None

            if 'username' in content:
                (user_group_id, content_user_group_id) = get_user_group_ids(course_key, content, request.user)
            return content, user_group_id, content_user_group_id

        course_key = CourseKey.from_string(kwargs['course_id'])
        content, user_group_id, content_user_group_id = fetch_content()

        if check_permissions_by_view(request.user, course_key, content,
                                     request.view_name, user_group_id, content_user_group_id):
            return func(request, *args, **kwargs)
        else:
            return JsonError("unauthorized", status=401)
    return wrapper


def ajax_content_response(request, course_key, content):
    """
    Standard AJAX response returning the content hierarchy of the current thread.
    """
    user_info = cc.User.from_django_user(request.user).to_dict()
    annotated_content_info = get_annotated_content_info(course_key, content, request.user, user_info)
    return JsonResponse({
        'content': prepare_content(content, course_key),
        'annotated_content_info': annotated_content_info,
    })


@require_POST
@login_required
@permitted
def create_thread(request, course_id, commentable_id):
    """
    Given a course and commentable ID, create the thread
    """

    log.debug("Creating new thread in %r, id %r", course_id, commentable_id)
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    post = request.POST
    user = request.user

    if course.allow_anonymous:
        anonymous = post.get('anonymous', 'false').lower() == 'true'
    else:
        anonymous = False

    if course.allow_anonymous_to_peers:
        anonymous_to_peers = post.get('anonymous_to_peers', 'false').lower() == 'true'
    else:
        anonymous_to_peers = False

    if 'title' not in post or not post['title'].strip():
        return JsonError(_("Title can't be empty"))
    if 'body' not in post or not post['body'].strip():
        return JsonError(_("Body can't be empty"))

    params = {
        'anonymous': anonymous,
        'anonymous_to_peers': anonymous_to_peers,
        'commentable_id': commentable_id,
        'course_id': str(course_key),
        'user_id': user.id,
        'thread_type': post["thread_type"],
        'body': sanitize_body(post["body"]),
        'title': post["title"],
    }

    # Check for whether this commentable belongs to a team, and add the right context
    if get_team(commentable_id) is not None:
        params['context'] = ThreadContext.STANDALONE
    else:
        params['context'] = ThreadContext.COURSE
    thread = cc.Thread(**params)

    # Divide the thread if required
    try:
        group_id = get_group_id_for_comments_service(request, course_key, commentable_id)
    except ValueError:
        return HttpResponseServerError("Invalid group id for commentable")
    if group_id is not None:
        thread.group_id = group_id

    thread.save()

    # Use send_signal_after_commit() to ensure the signal is sent only after the transaction commits.
    send_signal_after_commit(
        lambda: thread_created.send(sender=None, user=user, post=thread)
    )

    # patch for backward compatibility to comments service
    if 'pinned' not in thread.attributes:
        thread['pinned'] = False

    follow = post.get('auto_subscribe', 'false').lower() == 'true'

    if follow:
        cc_user = cc.User.from_django_user(user)
        cc_user.follow(thread, course_id)
        send_signal_after_commit(
            lambda: thread_followed.send(sender=None, user=user, post=thread)
        )

    data = thread.to_dict()

    add_courseware_context([data], course, user)

    track_thread_created_event(request, course, thread, follow)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return ajax_content_response(request, course_key, data)
    else:
        return JsonResponse(prepare_content(data, course_key))


@require_POST
@login_required
@permitted
def update_thread(request, course_id, thread_id):
    """
    Given a course id and thread id, update a existing thread, used for both static and ajax submissions
    """
    if 'title' not in request.POST or not request.POST['title'].strip():
        return JsonError(_("Title can't be empty"))
    if 'body' not in request.POST or not request.POST['body'].strip():
        return JsonError(_("Body can't be empty"))

    course_key = CourseKey.from_string(course_id)
    thread = cc.Thread.find(thread_id)
    # Get thread context first in order to be safe from reseting the values of thread object later
    thread_context = getattr(thread, "context", "course")
    thread.body = sanitize_body(request.POST["body"])
    thread.title = request.POST["title"]
    user = request.user
    # The following checks should avoid issues we've seen during deploys, where end users are hitting an updated server
    # while their browser still has the old client code. This will avoid erasing present values in those cases.
    course = get_course_with_access(user, 'load', course_key)
    if "thread_type" in request.POST:
        thread.thread_type = request.POST["thread_type"]
    if "commentable_id" in request.POST:
        commentable_id = request.POST["commentable_id"]
        if thread_context == "course" and not discussion_category_id_access(course, user, commentable_id):
            return JsonError(_("Topic doesn't exist"))
        else:
            thread.commentable_id = commentable_id

    thread.save()

    send_signal_after_commit(
        lambda: thread_edited.send(sender=None, user=user, post=thread)
    )

    track_thread_edited_event(request, course, thread, None)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return ajax_content_response(request, course_key, thread.to_dict())
    else:
        return JsonResponse(prepare_content(thread.to_dict(), course_key))


def _create_comment(request, course_key, thread_id=None, parent_id=None):
    """
    given a course_key, thread_id, and parent_id, create a comment,
    called from create_comment to do the actual creation
    """
    assert isinstance(course_key, CourseKey)
    post = request.POST
    user = request.user

    if 'body' not in post or not post['body'].strip():
        return JsonError(_("Body can't be empty"))

    course = get_course_with_access(user, 'load', course_key)
    if course.allow_anonymous:
        anonymous = post.get('anonymous', 'false').lower() == 'true'
    else:
        anonymous = False

    if course.allow_anonymous_to_peers:
        anonymous_to_peers = post.get('anonymous_to_peers', 'false').lower() == 'true'
    else:
        anonymous_to_peers = False

    comment = cc.Comment(
        anonymous=anonymous,
        anonymous_to_peers=anonymous_to_peers,
        user_id=user.id,
        course_id=str(course_key),
        thread_id=thread_id,
        parent_id=parent_id,
        body=sanitize_body(post["body"]),
    )
    comment.save(params={"course_id": str(course_key)})

    send_signal_after_commit(
        lambda: comment_created.send(sender=None, user=user, post=comment)
    )

    followed = post.get('auto_subscribe', 'false').lower() == 'true'

    if followed:
        cc_user = cc.User.from_django_user(request.user)
        cc_user.follow(comment.thread)

    track_comment_created_event(request, course, comment, comment.thread.commentable_id, followed)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return ajax_content_response(request, course_key, comment.to_dict())
    else:
        return JsonResponse(prepare_content(comment.to_dict(), course.id))


@require_POST
@login_required
@permitted
def create_comment(request, course_id, thread_id):
    """
    given a course_id and thread_id, test for comment depth. if not too deep,
    call _create_comment to create the actual comment.
    """
    if is_comment_too_deep(parent=None):
        return JsonError(_("Comment level too deep"))
    return _create_comment(request, CourseKey.from_string(course_id), thread_id=thread_id)


@require_POST
@login_required
@permitted
def delete_thread(request, course_id, thread_id):
    """
    given a course_id and thread_id, delete this thread
    this is ajax only
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    thread = cc.Thread.find(thread_id)
    thread.delete(course_id=course_id)
    send_signal_after_commit(
        lambda: thread_deleted.send(sender=None, user=request.user, post=thread)
    )

    track_thread_deleted_event(request, course, thread)
    return JsonResponse(prepare_content(thread.to_dict(), course_key))


@require_POST
@login_required
@permitted
def update_comment(request, course_id, comment_id):
    """
    given a course_id and comment_id, update the comment with payload attributes
    handles static and ajax submissions
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    comment = cc.Comment.find(comment_id)
    if 'body' not in request.POST or not request.POST['body'].strip():
        return JsonError(_("Body can't be empty"))
    comment.body = sanitize_body(request.POST["body"])
    comment.save(params={"course_id": course_id})

    send_signal_after_commit(
        lambda: comment_edited.send(sender=None, user=request.user, post=comment)
    )

    track_comment_edited_event(request, course, comment, None)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return ajax_content_response(request, course_key, comment.to_dict())
    else:
        return JsonResponse(prepare_content(comment.to_dict(), course_key))


@require_POST
@login_required
@permitted
def endorse_comment(request, course_id, comment_id):
    """
    given a course_id and comment_id, toggle the endorsement of this comment,
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    comment = cc.Comment.find(comment_id)
    course = get_course_with_access(request.user, 'load', course_key)
    user = request.user
    endorsed = request.POST.get('endorsed', 'false').lower() == 'true'
    comment.endorsed = endorsed
    comment.endorsement_user_id = user.id
    comment.save(params={"course_id": course_id})
    send_signal_after_commit(
        lambda: comment_endorsed.send(sender=None, user=user, post=comment)
    )
    track_forum_response_mark_event(request, course, comment, endorsed)
    return JsonResponse(prepare_content(comment.to_dict(), course_key))


@require_POST
@login_required
@permitted
def openclose_thread(request, course_id, thread_id):
    """
    given a course_id and thread_id, toggle the status of this thread
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    thread = cc.Thread.find(thread_id)
    close_thread = request.POST.get('closed', 'false').lower() == 'true'
    thread.closed = close_thread
    thread.save(params={"course_id": course_id})

    track_thread_lock_unlock_event(request, course, thread, None, close_thread)
    return JsonResponse({
        'content': prepare_content(thread.to_dict(), course_key),
        'ability': get_ability(course_key, thread.to_dict(), request.user),
    })


@require_POST
@login_required
@permitted
def create_sub_comment(request, course_id, comment_id):
    """
    given a course_id and comment_id, create a response to a comment
    after checking the max depth allowed, if allowed
    """
    if is_comment_too_deep(parent=cc.Comment(comment_id)):
        return JsonError(_("Comment level too deep"))
    return _create_comment(request, CourseKey.from_string(course_id), parent_id=comment_id)


@require_POST
@login_required
@permitted
def delete_comment(request, course_id, comment_id):
    """
    given a course_id and comment_id delete this comment
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    comment = cc.Comment.find(comment_id)
    comment.delete(course_id=course_id)
    send_signal_after_commit(
        lambda: comment_deleted.send(sender=None, user=request.user, post=comment)
    )
    track_comment_deleted_event(request, course, comment)
    return JsonResponse(prepare_content(comment.to_dict(), course_key))


def _vote_or_unvote(request, course_id, obj, value='up', undo_vote=False):
    """
    Vote or unvote for a thread or a response.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    user = cc.User.from_django_user(request.user)
    if undo_vote:
        user.unvote(obj, course_id)
        # TODO(smarnach): Determine the value of the vote that is undone.  Currently, you can
        # only cast upvotes in the user interface, so it is assumed that the vote value is 'up'.
        # (People could theoretically downvote by handcrafting AJAX requests.)
    else:
        user.vote(obj, value, course_id)
    send_signal_after_commit(
        lambda: thread_voted.send(sender=None, user=request.user, post=obj)
    )
    track_voted_event(request, course, obj, value, undo_vote)
    return JsonResponse(prepare_content(obj.to_dict(), course_key))


@require_POST
@login_required
@permitted
def vote_for_comment(request, course_id, comment_id, value):
    """
    Given a course_id and comment_id, vote for this response.  AJAX only.
    """
    comment = cc.Comment.find(comment_id)
    result = _vote_or_unvote(request, course_id, comment, value)
    send_signal_after_commit(
        lambda: comment_voted.send(sender=None, user=request.user, post=comment)
    )
    return result


@require_POST
@login_required
@permitted
def undo_vote_for_comment(request, course_id, comment_id):
    """
    given a course id and comment id, remove vote
    ajax only
    """
    return _vote_or_unvote(request, course_id, cc.Comment.find(comment_id), undo_vote=True)


@require_POST
@login_required
@permitted
def vote_for_thread(request, course_id, thread_id, value):
    """
    given a course id and thread id vote for this thread
    ajax only
    """
    thread = cc.Thread.find(thread_id)
    result = _vote_or_unvote(request, course_id, thread, value)
    return result


@require_POST
@login_required
@permitted
def undo_vote_for_thread(request, course_id, thread_id):
    """
    given a course id and thread id, remove users vote for thread
    ajax only
    """
    return _vote_or_unvote(request, course_id, cc.Thread.find(thread_id), undo_vote=True)


@require_POST
@login_required
@permitted
def flag_abuse_for_thread(request, course_id, thread_id):
    """
    given a course_id and thread_id flag this thread for abuse
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    user = cc.User.from_django_user(request.user)
    course = get_course_by_id(course_key)
    thread = cc.Thread.find(thread_id)
    thread.flagAbuse(user, thread, course_id)
    track_discussion_reported_event(request, course, thread)
    send_signal_after_commit(
        lambda: thread_flagged.send(sender='flag_abuse_for_thread', user=request.user, post=thread)
    )
    return JsonResponse(prepare_content(thread.to_dict(), course_key))


@require_POST
@login_required
@permitted
def un_flag_abuse_for_thread(request, course_id, thread_id):
    """
    given a course id and thread id, remove abuse flag for this thread
    ajax only
    """
    user = cc.User.from_django_user(request.user)
    course_key = CourseKey.from_string(course_id)
    course = get_course_by_id(course_key)
    thread = cc.Thread.find(thread_id)
    remove_all = bool(
        has_permission(request.user, 'openclose_thread', course_key) or
        has_access(request.user, 'staff', course)
    )
    thread.unFlagAbuse(user, thread, remove_all, course_id)
    track_discussion_unreported_event(request, course, thread)
    return JsonResponse(prepare_content(thread.to_dict(), course_key))


@require_POST
@login_required
@permitted
def flag_abuse_for_comment(request, course_id, comment_id):
    """
    given a course and comment id, flag comment for abuse
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    user = cc.User.from_django_user(request.user)
    course = get_course_by_id(course_key)
    comment = cc.Comment.find(comment_id)
    comment.flagAbuse(user, comment, course_id)
    track_discussion_reported_event(request, course, comment)
    send_signal_after_commit(
        lambda: comment_flagged.send(sender='flag_abuse_for_comment', user=request.user, post=comment)
    )
    return JsonResponse(prepare_content(comment.to_dict(), course_key))


@require_POST
@login_required
@permitted
def un_flag_abuse_for_comment(request, course_id, comment_id):
    """
    given a course_id and comment id, unflag comment for abuse
    ajax only
    """
    user = cc.User.from_django_user(request.user)
    course_key = CourseKey.from_string(course_id)
    course = get_course_by_id(course_key)
    remove_all = bool(
        has_permission(request.user, 'openclose_thread', course_key) or
        has_access(request.user, 'staff', course)
    )
    comment = cc.Comment.find(comment_id)
    comment.unFlagAbuse(user, comment, remove_all, course_id)
    track_discussion_unreported_event(request, course, comment)
    return JsonResponse(prepare_content(comment.to_dict(), course_key))


@require_POST
@login_required
@permitted
def pin_thread(request, course_id, thread_id):
    """
    given a course id and thread id, pin this thread
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    user = cc.User.from_django_user(request.user)
    thread = cc.Thread.find(thread_id)
    thread.pin(user, thread_id, course_id)

    return JsonResponse(prepare_content(thread.to_dict(), course_key))


@require_POST
@login_required
@permitted
def un_pin_thread(request, course_id, thread_id):
    """
    given a course id and thread id, remove pin from this thread
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    user = cc.User.from_django_user(request.user)
    thread = cc.Thread.find(thread_id)
    thread.un_pin(user, thread_id, course_id)

    return JsonResponse(prepare_content(thread.to_dict(), course_key))


@require_POST
@login_required
@permitted
def follow_thread(request, course_id, thread_id):  # lint-amnesty, pylint: disable=missing-function-docstring, unused-argument
    user = cc.User.from_django_user(request.user)
    course_key = CourseKey.from_string(course_id)
    course = get_course_by_id(course_key)
    thread = cc.Thread.find(thread_id)
    user.follow(thread, course_id=course_id)
    send_signal_after_commit(
        lambda: thread_followed.send(sender=None, user=request.user, post=thread)
    )
    track_thread_followed_event(request, course, thread, True)
    return JsonResponse({})


@require_POST
@login_required
@permitted
def follow_commentable(request, course_id, commentable_id):  # lint-amnesty, pylint: disable=unused-argument
    """
    given a course_id and commentable id, follow this commentable
    ajax only
    """
    user = cc.User.from_django_user(request.user)
    commentable = cc.Commentable.find(commentable_id)
    user.follow(commentable, course_id=course_id)
    return JsonResponse({})


@require_POST
@login_required
@permitted
def unfollow_thread(request, course_id, thread_id):  # lint-amnesty, pylint: disable=unused-argument
    """
    given a course id and thread id, stop following this thread
    ajax only
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_by_id(course_key)
    user = cc.User.from_django_user(request.user)
    thread = cc.Thread.find(thread_id)
    user.unfollow(thread, course_id=course_id)
    send_signal_after_commit(
        lambda: thread_unfollowed.send(sender=None, user=request.user, post=thread)
    )
    track_thread_followed_event(request, course, thread, False)
    return JsonResponse({})


@require_POST
@login_required
@permitted
def unfollow_commentable(request, course_id, commentable_id):  # lint-amnesty, pylint: disable=unused-argument
    """
    given a course id and commentable id stop following commentable
    ajax only
    """
    user = cc.User.from_django_user(request.user)
    commentable = cc.Commentable.find(commentable_id)
    user.unfollow(commentable, course_id=course_id)
    return JsonResponse({})


@require_POST
@login_required
@csrf.csrf_exempt
@xframe_options_exempt
def upload(request, course_id):  # ajax upload file to a question or answer  # lint-amnesty, pylint: disable=unused-argument
    """view that handles file upload via Ajax
    """

    # check upload permission
    error = ''
    new_file_name = ''
    try:
        # TODO authorization
        #may raise exceptions.PermissionDenied
        #if request.user.is_anonymous:
        #    msg = _('Sorry, anonymous users cannot upload files')
        #    raise exceptions.PermissionDenied(msg)

        #request.user.assert_can_upload_file()

        base_file_name = str(time.time()).replace('.', str(random.randint(0, 100000)))
        file_storage, new_file_name = store_uploaded_file(
            request, 'file-upload', cc_settings.ALLOWED_UPLOAD_FILE_TYPES, base_file_name,
            max_file_size=cc_settings.MAX_UPLOAD_FILE_SIZE
        )

    except exceptions.PermissionDenied as err:
        error = str(err)
    except Exception as err:      # pylint: disable=broad-except
        print(err)
        logging.critical(str(err))
        error = _('Error uploading file. Please contact the site administrator. Thank you.')

    if error == '':
        result = _('Good')
        file_url = file_storage.url(new_file_name)
        parsed_url = six.moves.urllib.parse.urlparse(file_url)
        file_url = six.moves.urllib.parse.urlunparse(
            six.moves.urllib.parse.ParseResult(
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                '', '', ''
            )
        )
    else:
        result = ''
        file_url = ''

    # Using content-type of text/plain here instead of JSON because
    # IE doesn't know how to handle the JSON response and prompts the
    # user to save the JSON as a file instead of passing it to the callback.
    return HttpResponse(json.dumps({  # lint-amnesty, pylint: disable=http-response-with-json-dumps
        'result': {
            'msg': result,
            'error': error,
            'file_url': file_url,
        }
    }), content_type="text/plain")


@require_GET
@login_required
def users(request, course_id):
    """
    Given a `username` query parameter, find matches for users in the forum for this course.

    Only exact matches are supported here, so the length of the result set will either be 0 or 1.
    """

    course_key = CourseKey.from_string(course_id)
    try:
        get_course_overview_with_access(request.user, 'load', course_key, check_if_enrolled=True)
    except Http404:
        # course didn't exist, or requesting user does not have access to it.
        return JsonError(status=404)
    except CourseAccessRedirect:
        # user does not have access to the course.
        return JsonError(status=404)

    try:
        username = request.GET['username']
    except KeyError:
        # 400 is default status for JsonError
        return JsonError(["username parameter is required"])

    user_objs = []
    try:
        matched_user = User.objects.get(username=username)
        cc_user = cc.User.from_django_user(matched_user)
        cc_user.course_id = course_key
        cc_user.retrieve(complete=False)
        if (cc_user['threads_count'] + cc_user['comments_count']) > 0:
            user_objs.append({
                'id': matched_user.id,
                'username': matched_user.username,
            })
    except User.DoesNotExist:
        pass
    return JsonResponse({"users": user_objs})
