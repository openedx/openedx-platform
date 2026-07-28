"""
Contain celery tasks
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute
from eventtracking import tracker
from forum import api as forum_api
from opaque_keys.edx.locator import CourseKey

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.track import segment
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names
from lms.djangoapps.discussion.rest_api.discussions_notifications import DiscussionNotificationSender
from lms.djangoapps.discussion.rest_api.utils import can_user_notify_all_learners
from openedx.core.djangoapps.django_comment_common.comment_client import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.notifications.config.waffle import DISABLE_NOTIFICATIONS

User = get_user_model()
log = logging.getLogger(__name__)


@shared_task
@set_code_owner_attribute
def send_thread_created_notification(thread_id, course_key_str, user_id, notify_all_learners=False):
    """
    Send notification when a new thread is created
    """
    course_key = CourseKey.from_string(course_key_str)
    if DISABLE_NOTIFICATIONS.is_enabled():
        return
    thread = Thread(id=thread_id).retrieve()
    user = User.objects.get(id=user_id)

    if notify_all_learners:
        is_course_staff = CourseStaffRole(course_key).has_user(user)
        is_course_admin = CourseInstructorRole(course_key).has_user(user)
        user_roles = get_user_role_names(user, course_key)
        if not can_user_notify_all_learners(user_roles, is_course_staff, is_course_admin):
            return

    course = get_course_with_access(user, 'load', course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(thread, course, user)
    notification_sender.send_new_thread_created_notification(notify_all_learners)


@shared_task
@set_code_owner_attribute
def send_response_notifications(thread_id, course_key_str, user_id, comment_id, parent_id=None):
    """
    Send notifications to users who are subscribed to the thread.
    """
    course_key = CourseKey.from_string(course_key_str)
    if DISABLE_NOTIFICATIONS.is_enabled():
        return
    thread = Thread(id=thread_id).retrieve()
    user = User.objects.get(id=user_id)
    course = get_course_with_access(user, 'load', course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(thread, course, user, parent_id, comment_id)
    notification_sender.send_new_comment_notification()
    notification_sender.send_new_response_notification()
    notification_sender.send_new_comment_on_response_notification()
    notification_sender.send_response_on_followed_post_notification()


@shared_task
@set_code_owner_attribute
def send_response_endorsed_notifications(thread_id, response_id, course_key_str, endorsed_by):
    """
    Send notifications when a response is marked answered/ endorsed
    """
    course_key = CourseKey.from_string(course_key_str)
    if DISABLE_NOTIFICATIONS.is_enabled():
        return
    thread = Thread(id=thread_id).retrieve()
    response = Comment(id=response_id).retrieve()
    creator = User.objects.get(id=response.user_id)
    endorser = User.objects.get(id=endorsed_by)
    course = get_course_with_access(creator, 'load', course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(thread, course, creator, comment_id=response_id)
    # skip sending notification to author of thread if they are the same as the author of the response
    if response.user_id != thread.user_id:
        # sends notification to author of thread
        notification_sender.send_response_endorsed_on_thread_notification()
    # sends notification to author of response
    if int(response.user_id) != endorser.id:
        notification_sender.creator = User.objects.get(id=response.user_id)
        notification_sender.send_response_endorsed_notification()


@shared_task
@set_code_owner_attribute
def delete_course_post_for_user(user_id, username, course_ids, event_data=None):
    """
    Deletes all posts for user in a course.
    """
    event_data = event_data or {}
    log.info(f"<<Bulk Delete>> Deleting all posts for {username} in course {course_ids}")
    threads_deleted = 0
    comments_deleted = 0
    for course_id in course_ids:
        result = forum_api.delete_user_posts(user_id=str(user_id), course_id=course_id)
        threads_deleted += result.get("thread_count", 0)
        comments_deleted += result.get("comment_count", 0)
    log.info(f"<<Bulk Delete>> Deleted {threads_deleted} posts and {comments_deleted} comments for {username} "
             f"in course {course_ids}")
    event_data.update({
        "number_of_posts_deleted": threads_deleted,
        "number_of_comments_deleted": comments_deleted,
    })
    event_name = 'edx.discussion.bulk_delete_user_posts'
    tracker.emit(event_name, event_data)
    segment.track('None', event_name, event_data)
