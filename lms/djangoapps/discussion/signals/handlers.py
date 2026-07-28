"""
Signal handlers related to discussions.
"""

import logging

from django.conf import settings
from django.dispatch import receiver
from django.utils.html import strip_tags
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator

from lms.djangoapps.discussion import tasks
from lms.djangoapps.discussion.rest_api.discussions_notifications import DiscussionNotificationSender
from lms.djangoapps.discussion.rest_api.tasks import (
    send_response_endorsed_notifications,
    send_response_notifications,
    send_thread_created_notification,
)
from openedx.core.djangoapps.django_comment_common import signals
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.djangoapps.theming.helpers import get_current_site
from xmodule.modulestore.django import SignalHandler, modulestore

log = logging.getLogger(__name__)

ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY = 'enable_forum_notifications'


@receiver(SignalHandler.course_published)
def update_discussions_on_course_publish(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been published in the module
    store and creates/updates the corresponding cache entry.
    Ignores publish signals from content libraries.
    """
    if isinstance(course_key, LibraryLocator):
        return

    context = {
        'course_id': str(course_key),
    }
    tasks.update_discussions_map.apply_async(
        args=[context],
        countdown=settings.DISCUSSION_SETTINGS['COURSE_PUBLISH_TASK_DELAY'],
    )


@receiver(signals.comment_created)
def send_discussion_email_notification(sender, user, post,
                                       **kwargs):  # pylint: disable=missing-function-docstring, unused-argument
    current_site = get_current_site()
    if current_site is None:
        log.info('Discussion: No current site, not sending notification about post: %s.', post.id)
        return

    try:
        if not current_site.configuration.get_value(ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY, False):
            log_message = 'Discussion: notifications not enabled for site: %s. Not sending message about post: %s.'
            log.info(log_message, current_site, post.id)
            return
    except SiteConfiguration.DoesNotExist:
        log_message = 'Discussion: No SiteConfiguration for site %s. Not sending message about post: %s.'
        log.info(log_message, current_site, post.id)
        return

    send_message(post, current_site)


@receiver(signals.comment_flagged)
@receiver(signals.thread_flagged)
def send_reported_content_email_notification(sender, user, post, **kwargs):
    """
    Sends email notification for reported content.
    """
    current_site = get_current_site()
    if current_site is None:
        log.info('Discussion: No current site, not sending notification about post: %s.', post.id)
        return

    try:
        if not current_site.configuration.get_value(ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY, False):
            log_message = 'Discussion: reported content notifications not enabled for site: %s. ' \
                          'Not sending message about post: %s.'
            log.info(log_message, current_site, post.id)
            return
    except SiteConfiguration.DoesNotExist:
        log_message = 'Discussion: No SiteConfiguration for site %s. Not sending message about post: %s.'
        log.info(log_message, current_site, post.id)
        return

    send_message_for_reported_content(user, post, current_site, sender)


@receiver(signals.comment_flagged)
@receiver(signals.thread_flagged)
def send_reported_content_notification(sender, user, post, **kwargs):
    """
    Sends notification for reported content.
    """
    course_key = CourseKey.from_string(post.course_id)
    course = modulestore().get_course(course_key)
    DiscussionNotificationSender(post, course, user).send_reported_content_notification()


def create_message_context(comment, site):
    thread = comment.thread
    return {
        'course_id': str(thread.course_id),
        'comment_id': comment.id,
        'comment_body': comment.body,
        'comment_body_text': comment.body_text,
        'comment_author_id': comment.user_id,
        'comment_created_at': comment.created_at,  # comment_client models dates are already serialized
        'comment_parent_id': comment.parent_id,
        'thread_id': thread.id,
        'thread_title': thread.title,
        'thread_author_id': thread.user_id,
        'thread_created_at': thread.created_at,  # comment_client models dates are already serialized
        'thread_commentable_id': thread.commentable_id,
        'site_id': site.id
    }


def create_message_context_for_reported_content(user, post, site, sender):
    """
    Create message context for reported content.
    """

    def get_comment_type(comment):
        """
        Returns type of comment.
        """
        return 'response' if comment.get('parent_id', None) is None else 'comment'

    context = {
        'user_id': user.id,
        'course_id': str(post.course_id),
        'thread_id': post.thread.id if sender == 'flag_abuse_for_comment' else post.id,
        'title': post.thread.title if sender == 'flag_abuse_for_comment' else post.title,
        'content_type': 'post' if sender == 'flag_abuse_for_thread' else get_comment_type(post),
        'content_body': strip_tags(post.body),
        'thread_created_at': post.created_at,
        'thread_commentable_id': post.commentable_id,
        'site_id': site.id,
        'comment_id': post.id if sender == 'flag_abuse_for_comment' else None,
    }
    return context


def send_message(comment, site):  # pylint: disable=missing-function-docstring
    context = create_message_context(comment, site)
    tasks.send_ace_message.apply_async(args=[context])


def send_message_for_reported_content(user, post, site,
                                      sender):  # pylint: disable=missing-function-docstring
    context = create_message_context_for_reported_content(user, post, site, sender)
    tasks.send_ace_message_for_reported_content.apply_async(args=[context], countdown=120)


@receiver(signals.thread_created)
def create_thread_created_notification(*args, **kwargs):
    """
    Creates a notification when new thread is created
    """
    user = kwargs['user']
    post = kwargs['post']
    notify_all_learners = kwargs.get('notify_all_learners', False)
    send_thread_created_notification.apply_async(
        args=[post.id, post.attributes['course_id'], user.id, notify_all_learners]
    )


@receiver(signals.comment_created)
def create_comment_created_notification(*args, **kwargs):
    """
    Creates a notification when new response or comment is created
    """
    user = kwargs['user']
    comment = kwargs['post']
    thread_id = comment.attributes['thread_id']
    parent_id = comment.attributes['parent_id']
    comment_id = comment.attributes['id']
    course_key_str = comment.attributes['course_id']
    send_response_notifications.apply_async(args=[thread_id, course_key_str, user.id, comment_id, parent_id])


@receiver(signals.comment_endorsed)
def create_response_endorsed_on_thread_notification(*args, **kwargs):
    """
    Creates  a notification for thread author when response on thread is endorsed
    and another notification for response author when response is endorsed
    """
    comment = kwargs['post']
    thread_id = comment.attributes['thread_id']
    course_key_str = comment.attributes['course_id']
    endorsed_by = kwargs['user'].id
    send_response_endorsed_notifications.apply_async(args=[thread_id, kwargs['post'].id, course_key_str, endorsed_by])
