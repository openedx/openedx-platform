"""
Defines asynchronous celery task for sending email notification (through edx-ace)
pertaining to new discussion forum comments.
"""


import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.conf import settings  # pylint: disable=unused-import
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.channel import ChannelType
from edx_ace.recipient import Recipient
from edx_ace.utils import date
from edx_django_utils.monitoring import set_code_owner_attribute
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey
from six.moves.urllib.parse import urljoin

import openedx.core.djangoapps.django_comment_common.comment_client as cc
from common.djangoapps.track import segment
from lms.djangoapps.discussion.django_comment_client.utils import get_users_with_moderator_roles, permalink
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from lms.djangoapps.discussion.toggles_utils import reported_content_email_notification_enabled
from openedx.core.djangoapps.ace_common.message import BaseMessageType
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.discussions.url_helpers import get_discussions_mfe_url
from openedx.core.djangoapps.discussions.utils import get_accessible_discussion_xblocks_by_course_id
from openedx.core.djangoapps.django_comment_common.models import DiscussionsIdMapping
from openedx.core.lib.celery.task_utils import emulate_http_request
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


DEFAULT_LANGUAGE = 'en'


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def update_discussions_map(context):
    """
    Updates the mapping between discussion_id to discussion block usage key
    for all discussion blocks in the given course.

    context is a dict that contains:
        course_id (string): identifier of the course
    """
    course_key = CourseKey.from_string(context['course_id'])
    discussion_blocks = get_accessible_discussion_xblocks_by_course_id(course_key, include_all=True)
    discussions_id_map = {
        discussion_block.discussion_id: str(discussion_block.location)
        for discussion_block in discussion_blocks
    }
    DiscussionsIdMapping.update_mapping(course_key, discussions_id_map)


class ResponseNotification(BaseMessageType):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True


class ReportedContentNotification(BaseMessageType):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True


class CommentNotification(BaseMessageType):
    """
    Notify discussion participants of new comments.
    """


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def send_ace_message(context):  # pylint: disable=missing-function-docstring
    context['course_id'] = CourseKey.from_string(context['course_id'])

    if _should_send_message(context):
        context['site'] = Site.objects.get(id=context['site_id'])
        thread_author = User.objects.get(id=context['thread_author_id'])
        comment_author = User.objects.get(id=context['comment_author_id'])
        with emulate_http_request(site=context['site'], user=comment_author):
            message_context = _build_message_context(context, notification_type='forum_response')
            message = ResponseNotification().personalize(
                Recipient(thread_author.id, thread_author.email),
                _get_course_language(context['course_id']),
                message_context
            )
            log.info('Sending forum comment notification with context %s', message_context)
            if _is_first_comment(context['comment_id'], context['thread_id']):
                limit_to_channels = None
            else:
                limit_to_channels = [ChannelType.PUSH]
            ace.send(message, limit_to_channels=limit_to_channels)
            _track_notification_sent(message, context)

    elif _should_send_subcomment_message(context):
        context['site'] = Site.objects.get(id=context['site_id'])
        comment_author = User.objects.get(id=context['comment_author_id'])
        thread_author = User.objects.get(id=context['thread_author_id'])

        with emulate_http_request(site=context['site'], user=comment_author):
            message_context = _build_message_context(context)
            message = CommentNotification().personalize(
                Recipient(thread_author.id, thread_author.email),
                _get_course_language(context['course_id']),
                message_context
            )
            log.info('Sending forum comment notification with context %s', message_context)
            ace.send(message, limit_to_channels=[ChannelType.PUSH])
            _track_notification_sent(message, context)


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def send_ace_message_for_reported_content(context):  # pylint: disable=missing-function-docstring
    context['course_id'] = CourseKey.from_string(context['course_id'])
    context['course_name'] = modulestore().get_course(context['course_id']).display_name
    if not reported_content_email_notification_enabled(context['course_id']):
        return

    moderators = get_users_with_moderator_roles(context)
    context['site'] = Site.objects.get(id=context['site_id'])
    if not _is_content_still_reported(context):
        log.info('Reported content is no longer in reported state. Email to moderators will not be sent.')
        return
    for moderator in moderators:
        with emulate_http_request(site=context['site'], user=User.objects.get(id=context['user_id'])):
            message_context = _build_message_context_for_reported_content(context, moderator)
            message = ReportedContentNotification().personalize(
                Recipient(moderator.id, moderator.email),
                _get_course_language(context['course_id']),
                message_context
            )
            log.info(f'Sending forum reported content email notification with context {message_context}')
            ace.send(message)


def _track_notification_sent(message, context):
    """
    Send analytics event for a sent email
    """
    properties = {
        'app_label': 'discussion',
        'name': 'responsenotification',  # This is 'Campaign' in GA
        'language': message.language,
        'uuid': str(message.uuid),
        'send_uuid': str(message.send_uuid),
        'thread_id': context['thread_id'],
        'course_id': str(context['course_id']),
        'thread_created_at': date.deserialize(context['thread_created_at']),
        'nonInteraction': 1,
    }
    tracking_context = {
        'host': context['site'].domain,
        'path': '/',  # make up a value, in order to allow the host to be passed along.
    }
    # The event used to specify the user_id as being the recipient of the email (i.e. the thread_author_id).
    # This has the effect of interrupting the actual chain of events for that author, if any, while the
    # email-sent event should really be associated with the sender, since that is what triggers the event.
    with tracker.get_tracker().context(properties['app_label'], tracking_context):
        segment.track(
            user_id=context['thread_author_id'],
            event_name='edx.bi.email.sent',
            properties=properties
        )


def _should_send_message(context):
    cc_thread_author = cc.User(id=context['thread_author_id'], course_id=context['course_id'])
    return (
        _is_user_subscribed_to_thread(cc_thread_author, context['thread_id']) and
        _is_not_subcomment(context['comment_id']) and
        not _comment_author_is_thread_author(context)
    )


def _should_send_subcomment_message(context):
    cc_thread_author = cc.User(id=context['thread_author_id'], course_id=context['course_id'])
    return (
        _is_user_subscribed_to_thread(cc_thread_author, context['thread_id']) and
        _is_subcomment(context['comment_id']) and
        not _comment_author_is_thread_author(context)
    )


def _comment_author_is_thread_author(context):
    return context.get('comment_author_id', '') == context['thread_author_id']


def _is_content_still_reported(context):
    if context.get('comment_id') is not None:
        return len(cc.Comment.find(context['comment_id']).abuse_flaggers) > 0
    return len(cc.Thread.find(context['thread_id']).abuse_flaggers) > 0


def _is_subcomment(comment_id):
    comment = cc.Comment.find(id=comment_id).retrieve()
    return getattr(comment, 'parent_id', None)


def _is_not_subcomment(comment_id):
    return not _is_subcomment(comment_id)


def _is_first_comment(comment_id, thread_id):  # pylint: disable=missing-function-docstring
    thread = cc.Thread.find(id=thread_id).retrieve(with_responses=True)

    if thread.get('thread_type') == 'question':
        endorsed_comments = getattr(thread, 'endorsed_responses', [])
        non_endorsed_comments = getattr(thread, 'non_endorsed_responses', [])
        comments = endorsed_comments + non_endorsed_comments
    else:
        comments = getattr(thread, 'children', [])

    if comments:
        first_comment = sorted(comments, key=lambda c: c['created_at'])[0]
        return first_comment.get('id') == comment_id
    else:
        return False


def _is_user_subscribed_to_thread(cc_user, thread_id):  # pylint: disable=missing-function-docstring
    paginated_result = cc_user.subscribed_threads()
    thread_ids = {thread['id'] for thread in paginated_result.collection}

    while paginated_result.page < paginated_result.num_pages:
        next_page = paginated_result.page + 1
        paginated_result = cc_user.subscribed_threads(query_params={'page': next_page})
        thread_ids.update(thread['id'] for thread in paginated_result.collection)

    return thread_id in thread_ids


def _get_course_language(course_id):
    course_overview = CourseOverview.objects.get(id=course_id)
    language = course_overview.language or DEFAULT_LANGUAGE
    return language


def _build_message_context(context, notification_type='forum_comment'):  # pylint: disable=missing-function-docstring
    message_context = get_base_template_context(context['site'])
    message_context.update(context)
    thread_author = User.objects.get(id=context['thread_author_id'])
    comment_author = User.objects.get(id=context['comment_author_id'])
    show_mfe_post_link = ENABLE_DISCUSSIONS_MFE.is_enabled(
        context['course_id']
    )
    post_link = _get_mfe_thread_url(context) if show_mfe_post_link else _get_thread_url(context)

    message_context.update({
        'thread_username': thread_author.username,
        'comment_username': comment_author.username,
        'post_link': post_link,
        'push_notification_extra_context': {
            'course_id': str(context['course_id']),
            'parent_id': str(context['comment_parent_id']),
            'notification_type': notification_type,
            'topic_id': str(context['thread_commentable_id']),
            'thread_id': context['thread_id'],
            'comment_id': context['comment_id'],
        },
        'comment_created_at': date.deserialize(context['comment_created_at']),
        'thread_created_at': date.deserialize(context['thread_created_at'])
    })
    return message_context


def _build_message_context_for_reported_content(context, moderator):  # pylint: disable=missing-function-docstring
    message_context = get_base_template_context(context['site'])
    message_context.update(context)
    use_mfe_url = ENABLE_DISCUSSIONS_MFE.is_enabled(context['course_id'])

    message_context.update({
        'post_link': _get_mfe_thread_url(context) if use_mfe_url else _get_thread_url(context, settings.LMS_BASE),
        'moderator_email': moderator.email,
    })
    return message_context


def _get_mfe_thread_url(context):
    """
    Get thread url for new MFE
    """
    forum_url = get_discussions_mfe_url(course_key=context['course_id'])
    mfe_post_link = f"posts/{context['thread_id']}"
    return urljoin(forum_url, mfe_post_link)


def _get_thread_url(context, domain_url=None):  # pylint: disable=missing-function-docstring
    scheme = 'https' if settings.HTTPS == 'on' else 'http'
    if domain_url is None:
        domain_url = context['site'].domain
    base_url = '{}://{}'.format(scheme, domain_url)  # noqa: UP032
    thread_content = {
        'type': 'thread',
        'course_id': context['course_id'],
        'commentable_id': context['thread_commentable_id'],
        'id': context['thread_id'],
    }
    return urljoin(base_url, permalink(thread_content))
