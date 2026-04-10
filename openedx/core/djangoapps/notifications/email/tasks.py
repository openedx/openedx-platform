"""
Celery tasks for sending email notifications
"""
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import override as translation_override
from edx_ace import ace
from edx_ace.recipient import Recipient
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.models import Notification, NotificationPreference

from ..base_notification import COURSE_NOTIFICATION_APPS
from ..config.waffle import DISABLE_EMAIL_NOTIFICATIONS
from .events import send_immediate_email_digest_sent_event, send_user_email_digest_sent_event
from .message_type import EmailNotificationMessageType
from .utils import (
    add_headers_to_email_message,
    create_app_notifications_dict,
    create_email_digest_context,
    create_email_template_context,
    filter_email_enabled_notifications,
    get_course_info,
    get_language_preference_for_users,
    get_start_end_date,
    get_text_for_notification_type,
)

User = get_user_model()
logger = get_task_logger(__name__)


def get_audience_for_cadence_email(cadence_type):
    """
    Returns users that are eligible to receive cadence email
    """
    if cadence_type not in [EmailCadence.DAILY, EmailCadence.WEEKLY]:
        raise ValueError("Invalid value for parameter cadence_type")
    start_date, end_date = get_start_end_date(cadence_type)
    user_ids = Notification.objects.filter(
        email=True,
        created__gte=start_date,
        created__lte=end_date
    ).values_list('user__id', flat=True).distinct()
    users = User.objects.filter(id__in=user_ids)
    return users


def get_buffer_minutes() -> int:
    """Get configured buffer period in minutes."""
    return getattr(settings, 'NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES', 0)


def send_digest_email_to_user(
    user: User,
    cadence_type: str,
    start_date: datetime,
    end_date: datetime,
    user_language: str = 'en',
    courses_data: dict = None
):
    """
    Send [cadence_type] email to user.
    Cadence Type can be EmailCadence.DAILY or EmailCadence.WEEKLY
    start_date: Datetime object
    end_date: Datetime object
    """
    if DISABLE_EMAIL_NOTIFICATIONS.is_enabled():
        return

    if cadence_type not in [EmailCadence.IMMEDIATELY, EmailCadence.DAILY, EmailCadence.WEEKLY]:
        raise ValueError('Invalid cadence_type')
    logger.info(f'<Email Cadence> Sending email to user {user.username} ==Temp Log==')
    if not user.has_usable_password():
        logger.info(f'<Email Cadence> User is disabled {user.username} ==Temp Log==')
        return

    notifications = Notification.objects.filter(user=user, email=True,
                                                created__gte=start_date, created__lte=end_date)
    if not notifications:
        logger.info(f'<Email Cadence> No notification for {user.username} ==Temp Log==')
        return

    with translation_override(user_language):
        preferences = NotificationPreference.objects.filter(user=user)
        notifications_list = filter_email_enabled_notifications(
            notifications,
            preferences,
            user,
            cadence_type=cadence_type
        )
        if not notifications_list:
            logger.info(f'<Email Cadence> No filtered notification for {user.username} ==Temp Log==')
            return

        apps_dict = create_app_notifications_dict(notifications_list)
        message_context = create_email_digest_context(apps_dict, user.username, start_date, end_date,
                                                      cadence_type, courses_data=courses_data)
        recipient = Recipient(user.id, user.email)
        message = EmailNotificationMessageType(
            app_label="notifications", name="email_digest"
        ).personalize(recipient, user_language, message_context)
        message = add_headers_to_email_message(message, message_context)
        message.options['skip_disable_user_policy'] = True
        ace.send(message)
        notifications.update(email_sent_on=datetime.now())
        send_user_email_digest_sent_event(user, cadence_type, notifications_list, message_context)
        logger.info(f'<Email Cadence> Email sent to {user.username} ==Temp Log==')


@shared_task(ignore_result=True)
@set_code_owner_attribute
def send_digest_email_to_all_users(cadence_type):
    """
    Send email digest to all eligible users
    """
    logger.info(f'<Email Cadence> Sending cadence email of type {cadence_type}')
    users = get_audience_for_cadence_email(cadence_type)
    language_prefs = get_language_preference_for_users([user.id for user in users])
    courses_data = {}
    start_date, end_date = get_start_end_date(cadence_type)
    logger.info(f'<Email Cadence> Email Cadence Audience {len(users)}')
    for user in users:
        user_language = language_prefs.get(user.id, 'en')
        send_digest_email_to_user(user, cadence_type, start_date, end_date, user_language=user_language,
                                  courses_data=courses_data)


def send_immediate_cadence_email(email_notification_mapping, course_key):
    """
    Send immediate cadence email to users
    Parameters:
        email_notification_mapping: Dictionary of user_id and Notification object
        course_key: Course key for which the email is sent
    1. First notification → Send immediately
    2. Second notification → Schedule buffer job (15 min)
    3. Third+ notifications → Just mark as scheduled (no new job)
    """
    if DISABLE_EMAIL_NOTIFICATIONS.is_enabled():
        return

    if not email_notification_mapping:
        return
    user_list = email_notification_mapping.keys()
    users = list(User.objects.filter(id__in=user_list))
    language_prefs = get_language_preference_for_users(user_list)
    course_name = get_course_info(course_key).get("name", course_key)

    for user in users:
        if not user.has_usable_password():
            logger.info(f'<Immediate Email> User is disabled {user.username}')
            continue

        notification = email_notification_mapping.get(user.id, None)
        if not notification:
            logger.info(f'<Immediate Email> No notification for {user.username}')
            continue
        # THE CORE DECISION LOGIC
        decision = decide_email_action(user, course_key, notification)
        user_language = language_prefs.get(user.id, 'en')

        if decision == 'send_immediate':
            # CASE 1: First notification - send immediately
            logger.info(
                f"Email Buffered Digest: Sending immediate email for notification IDs: {notification.id}",
            )
            send_immediate_email(
                user=user,
                notification=notification,
                course_key=course_key,
                course_name=course_name,
                user_language=user_language
            )

        elif decision == 'schedule_buffer':
            # CASE 2: Second notification - schedule buffer job
            logger.info(
                f"Email Buffered Digest: Scheduling buffer for notification IDs: {notification.id}",
            )
            schedule_digest_buffer(
                user=user,
                notification=notification,
                course_key=course_key,
                user_language=user_language
            )

        elif decision == 'add_to_buffer':
            logger.info(
                f"Email Buffered Digest: "
                f"Email Buffered Digest:Adding to existing buffer for notification IDs: {notification.id}\n",
            )
            # CASE 3: Third+ notification - just mark as scheduled
            add_to_existing_buffer(notification)


@transaction.atomic
def decide_email_action(user: User, course_key: str, notification: Notification) -> str:
    """
    Decide what to do with this notification.

    Logic:
    - No recent email? → send_immediate (1st)
    - Recent email + no buffer? → schedule_buffer (2nd)
    - Recent email + buffer exists? → add_to_buffer (3rd+)

    Returns:
        'send_immediate', 'schedule_buffer', or 'add_to_buffer'
    """
    buffer_minutes = get_buffer_minutes()
    buffer_threshold = datetime.now() - timedelta(minutes=buffer_minutes)

    # Use select_for_update to prevent race conditions
    recent_notifications = Notification.objects.select_for_update().filter(
        user=user,
        course_id=course_key,
        created__gte=buffer_threshold
    )

    # Check if any email was sent recently
    has_recent_email = recent_notifications.filter(
        email_sent_on__isnull=False,
        email_sent_on__gte=buffer_threshold
    ).exists()

    if not has_recent_email:
        # CASE 1: No recent email → First notification
        logger.info(f'[{user.username}] CASE 1: First notification, sending immediately')
        return 'send_immediate'

    # Check if buffer job already exists
    # Buffer exists if there are notifications marked as scheduled
    has_scheduled_buffer = recent_notifications.filter(
        email_scheduled=True
    ).exists()

    if not has_scheduled_buffer:
        # CASE 2: Recent email but no buffer → Second notification
        logger.info(f'[{user.username}] CASE 2: Second notification, scheduling buffer')
        return 'schedule_buffer'

    # CASE 3: Buffer already exists → Third+ notification
    logger.info(f'[{user.username}] CASE 3: Third+ notification, adding to buffer')
    return 'add_to_buffer'


def send_immediate_email(
    user: User,
    notification: Notification,
    course_key: str,
    course_name: str,
    user_language: str
) -> None:
    """Send immediate email for the first notification."""
    with translation_override(user_language):
        soup = BeautifulSoup(notification.content, "html.parser")
        title = (
            _("New Course Update")
            if notification.notification_type == "course_updates"
            else soup.get_text()
        )

        message_context = create_email_template_context(user.username)
        message_context.update({
            "course_id": course_key,
            "course_name": course_name,
            "content_url": notification.content_url,
            "content_title": title,
            "footer_email_reason": _(
                "You are receiving this email because you are enrolled in "
                "the edX course "
            ) + str(course_name),
            "content": notification.content_context.get(
                "email_content",
                notification.content
            ),
            "view_text": get_text_for_notification_type(
                notification.notification_type
            ),
        })

        message = EmailNotificationMessageType(
            app_label="notifications",
            name="immediate_email"
        ).personalize(
            Recipient(user.id, user.email),
            user_language,
            message_context
        )

        message = add_headers_to_email_message(message, message_context)
        ace.send(message)

        # Mark as sent - this starts the buffer period
        notification.email_sent_on = datetime.now()
        notification.save(update_fields=["email_sent_on"])

        logger.info(f'Email Buffered Digest: ✓ Sent immediate email to {user.username}')

        send_immediate_email_digest_sent_event(
            user,
            EmailCadence.IMMEDIATELY,
            notification
        )


def schedule_digest_buffer(
    user: User,
    notification: Notification,
    course_key: str,
    user_language: str
) -> None:
    """
    Schedule a buffer job for digest email.
    Called for the SECOND notification only.
    """
    buffer_minutes = get_buffer_minutes()

    # Find when we last sent an email
    last_sent = Notification.objects.filter(
        user=user,
        course_id=course_key,
        email_sent_on__isnull=False
    ).order_by('-email_sent_on').first()

    if not last_sent:
        logger.error(f'No last_sent found for {user.username}')
        return

    start_date = last_sent.email_sent_on
    scheduled_time = datetime.now() + timedelta(minutes=buffer_minutes)

    # Mark this notification as scheduled FIRST
    notification.email_scheduled = True
    notification.save(update_fields=['email_scheduled'])

    # Then schedule the digest task
    send_buffered_digest.apply_async(
        kwargs={
            'user_id': user.id,
            'course_key': str(course_key),
            'start_date': start_date,
            'user_language': user_language,
        },
        eta=scheduled_time
    )

    logger.info(
        f'Email Buffered Digest: ✓ Scheduled digest for {user.username} at {scheduled_time}, '
        f'marked notification {notification.id} as scheduled'
    )


def add_to_existing_buffer(notification: Notification) -> None:
    """
    Add notification to existing buffer.
    Just mark as scheduled - the existing job will find it!

    Called for THIRD+ notifications.
    """
    notification.email_scheduled = True
    notification.save(update_fields=['email_scheduled'])

    logger.info(
        f'✓ Marked notification {notification.id} as scheduled '
        f'(will be picked up by existing buffer job)'
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
@set_code_owner_attribute
def send_buffered_digest(
    self,
    user_id: int,
    course_key: str,
    start_date: datetime,
    user_language: str
) -> None:
    """
    Send digest email with all buffered notifications.

    This collects ALL notifications where email_scheduled=True
    for this user+course within the buffer period.

    Simple! No task ID tracking needed.
    """
    try:
        # Re-check feature flags
        if DISABLE_EMAIL_NOTIFICATIONS.is_enabled():
            logger.info('Email notifications disabled, cancelling digest')
            return

        user = User.objects.get(id=user_id)

        if not user.has_usable_password():
            logger.info(f'User {user.username} disabled')
            return

        end_date = datetime.now()

        # Get ALL scheduled notifications
        # Simple query: just find where email_scheduled=True
        scheduled_notifications = Notification.objects.filter(
            user=user,
            course_id=course_key,
            email_scheduled=True,  # This is all we need!
            created__gte=start_date,
            created__lte=end_date,
            app_name__in=COURSE_NOTIFICATION_APPS
        )

        if not scheduled_notifications.exists():
            logger.info(f'Email Buffered Digest: No scheduled notifications for {user.username}')
            return
        logger.info(
            "Email Buffered Digest: "
            f'Found {scheduled_notifications.count()} scheduled '
            f'notifications for {user.username}'
        )
        with translation_override(user_language):
            # Filter based on preferences
            preferences = NotificationPreference.objects.filter(user=user)
            notifications_list = filter_email_enabled_notifications(
                scheduled_notifications,
                preferences,
                user,
                cadence_type=EmailCadence.IMMEDIATELY
            )

            if not notifications_list:
                logger.info(f'No email-enabled notifications for {user.username}')
                # Reset flags even if we don't send
                scheduled_notifications.update(email_scheduled=False)
                return

            # Build digest email
            apps_dict = create_app_notifications_dict(notifications_list)
            course_key = CourseKey.from_string(course_key)
            course_name = get_course_info(course_key).get("name", course_key)

            message_context = create_email_digest_context(
                apps_dict,
                user.username,
                start_date,
                end_date,
                EmailCadence.IMMEDIATELY,
                courses_data={course_key: {'name': course_name}}
            )

            # Send digest
            recipient = Recipient(user.id, user.email)
            message = EmailNotificationMessageType(
                app_label="notifications",
                name="batched_email"
            ).personalize(recipient, user_language, message_context)

            message = add_headers_to_email_message(message, message_context)
            ace.send(message)

            # Mark ALL as sent and clear scheduled flag
            notification_ids = [n.id for n in notifications_list]
            logger.info(
                f'Email Buffered Digest: Sent buffered digest to {user.username} for '""
                f'notifications IDs: {notification_ids}'

            )
            updated_count = scheduled_notifications.filter(
                id__in=notification_ids
            ).update(
                email_sent_on=datetime.now(),
                email_scheduled=False  # Clear the flag
            )

            logger.info(
                f'Email Buffered Digest: ✓ Sent buffered digest to {user.username} with '
                f'{updated_count} notifications'
            )

            send_user_email_digest_sent_event(
                user,
                EmailCadence.IMMEDIATELY,
                notifications_list,
                message_context
            )

    except User.DoesNotExist:
        logger.error(f'Email Buffered Digest: User {user_id} not found')

    except Exception as exc:
        logger.exception(f'Email Buffered Digest: Failed to send buffered digest: {exc}')
        retry_countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=retry_countdown)  # noqa: B904
