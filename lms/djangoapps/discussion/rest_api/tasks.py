"""
Contain celery tasks
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from edx_django_utils.monitoring import set_code_owner_attribute
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.roles import CourseStaffRole, CourseInstructorRole
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


@shared_task(
    bind=True,  # Enable retry context and access to task instance
    max_retries=3,  # Retry up to 3 times on failure
    default_retry_delay=60,  # Wait 60 seconds between retries
    autoretry_for=(OSError, TimeoutError),  # Only retry on transient network/IO errors
    retry_backoff=True,  # Exponential backoff between retries
    retry_jitter=True,   # Add randomization to retry delays
)
@set_code_owner_attribute
def delete_course_post_for_user(  # pylint: disable=too-many-statements
    self,
    user_id,
    username=None,
    course_ids=None,
    event_data=None,
    # NEW PARAMETERS (backward compatible - all have defaults):
    ban_user=False,
    ban_scope='course',
    moderator_id=None,
    reason=None,
):
    """
    Delete all discussion posts for a user and optionally ban them.

    BACKWARD COMPATIBLE: Existing callers without ban_user parameter
    will experience no change in behavior.

    Args:
        self: Task instance (when bind=True)
        user_id: User whose posts to delete
        username: Username of the user (optional, will be fetched if not provided)
        course_ids: List of course IDs (API sends single course wrapped in array)
        event_data: Event tracking metadata
        ban_user: If True, create ban record (NEW)
        ban_scope: 'course' or 'organization' (NEW)
        moderator_id: Moderator applying ban (NEW)
        reason: Ban reason (NEW)
    """
    from django.db.utils import OperationalError, InterfaceError

    event_data = event_data or {}

    try:
        user = User.objects.get(id=user_id)
        if username is None:
            username = user.username

        log.info(
            f"<<Bulk Delete>> Deleting all posts for {username} in course {course_ids}"
        )

        # Phase 1: Delete content with audit trail
        deleted_by_user_id = event_data.get("triggered_by_user_id") if event_data else None
        threads_deleted = Thread.delete_user_threads(
            user_id, course_ids, deleted_by=deleted_by_user_id
        )
        comments_deleted = Comment.delete_user_comments(
            user_id, course_ids, deleted_by=deleted_by_user_id
        )

        log.info(
            f"<<Bulk Delete>> Deleted {threads_deleted} posts and {comments_deleted} comments for {username} "
            f"in course {course_ids}"
        )

        # Phase 2: Create ban record (only if ban_user=True)
        ban_id = None
        ban_error = None
        if ban_user and moderator_id:
            try:
                from forum import api as forum_api

                with transaction.atomic():
                    banned_user = User.objects.get(id=user_id)
                    moderator = User.objects.get(id=moderator_id)

                    # Extract organization from course for consistency
                    course_key = CourseKey.from_string(course_ids[0])

                    # Use forum API to ban user
                    ban_result = forum_api.ban_user(
                        user=banned_user,
                        banned_by=moderator,
                        course_id=course_key,
                        scope=ban_scope,
                        reason=reason or 'Bulk delete and ban operation'
                    )

                    ban_id = ban_result.get('id')

                    log.info(
                        f"<<Bulk Delete>> Created {ban_scope}-level ban (ID: {ban_id}) "
                        f"for user {username} (ID: {user_id})"
                    )

            except Exception as e:  # pylint: disable=broad-except
                ban_error = str(e)
                log.error(
                    f"<<Bulk Delete>> Failed to create ban for user {username} (ID: {user_id}): {e}",
                    exc_info=True
                )
                # Don't fail the entire task if ban creation fails

        # Phase 3: Audit logging
        if ban_user and moderator_id and ban_id:
            from forum import api as forum_api

            try:
                with transaction.atomic():
                    forum_api.create_audit_log(
                        action_type='ban_user',
                        target_user=User.objects.get(id=user_id),
                        moderator=User.objects.get(id=moderator_id),
                        course_id=course_ids[0],
                        scope=ban_scope,
                        reason=reason,
                        metadata={
                            'threads_deleted': threads_deleted,
                            'comments_deleted': comments_deleted,
                            'task_id': self.request.id,
                            'ban_id': ban_id,
                        }
                    )
            except Exception as e:  # pylint: disable=broad-except
                log.error(f"Failed to create audit log: {e}", exc_info=True)

        # Phase 4: Event tracking
        event_data.update({
            "number_of_posts_deleted": threads_deleted,
            "number_of_comments_deleted": comments_deleted,
            'ban_applied': ban_user,
            'ban_scope': ban_scope if ban_user else None,
            'ban_id': ban_id if ban_id else None,
            'ban_error': ban_error if ban_error else None,
        })
        event_name = 'edx.discussion.bulk_delete_user_posts'
        tracker.emit(event_name, event_data)
        segment.track('None', event_name, event_data)

        # Phase 5: Email notification
        if ban_user and moderator_id:
            # Check if email notifications are enabled before attempting to send
            from django.conf import settings as django_settings
            if getattr(django_settings, 'DISCUSSION_MODERATION_BAN_EMAIL_ENABLED', True):
                from lms.djangoapps.discussion.rest_api.emails import send_ban_escalation_email

                try:
                    send_ban_escalation_email(
                        banned_user_id=user_id,
                        moderator_id=moderator_id,
                        course_id=course_ids[0],
                        scope=ban_scope,
                        reason=reason,
                        threads_deleted=threads_deleted,
                        comments_deleted=comments_deleted,
                    )
                except (OSError, ValueError, TypeError) as email_exc:
                    # Log but don't fail the task if email fails
                    # Catches: SMTP errors (OSError), template errors (ValueError), data errors (TypeError)
                    log.error(
                        "Task %s: Failed to send ban escalation email: %s",
                        self.request.id, str(email_exc)
                    )
            else:
                log.info(
                    "Task %s: Email notifications disabled, skipping ban escalation email",
                    self.request.id
                )

        log.info(
            "Task %s completed: user=%s, threads=%d, comments=%d, ban=%s",
            self.request.id, username, threads_deleted, comments_deleted, ban_user
        )

        return {
            'threads_deleted': threads_deleted,
            'comments_deleted': comments_deleted,
            'ban_applied': ban_user,
            'task_id': self.request.id,
        }

    except (OperationalError, InterfaceError, OSError, TimeoutError) as exc:
        # Transient errors - let Celery retry
        log.warning(
            "Task %s retrying due to transient error: user_id=%s, error=%s",
            self.request.id, user_id, str(exc)
        )
        raise
    except Exception as exc:
        # Permanent errors - log and fail immediately
        log.error(
            "Task %s failed permanently: user_id=%s, error=%s",
            self.request.id, user_id, str(exc), exc_info=True
        )
        raise
