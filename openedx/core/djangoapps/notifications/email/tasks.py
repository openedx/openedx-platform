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
from django.utils import timezone as django_timezone
from django.utils.translation import gettext as _
from django.utils.translation import override as translation_override
from edx_ace import ace
from edx_ace.recipient import Recipient
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.models import (
    DigestSchedule,
    Notification,
    NotificationPreference,
)

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

    notifications = Notification.objects.filter(
        user=user,
        email=True,
        created__gte=start_date,
        created__lte=end_date,
        email_sent_on__isnull=True,
    )
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
        notifications.update(email_sent_on=django_timezone.now())
        send_user_email_digest_sent_event(user, cadence_type, notifications_list, message_context)
        logger.info(f'<Email Cadence> Email sent to {user.username} ==Temp Log==')


def get_next_digest_delivery_time(cadence_type):
    """
    Calculate the next delivery time for a digest email based on cadence type.

    Uses Django settings for configurable delivery time/day:
    - NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR (default: 17)
    - NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE (default: 0)
    - NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY (default: 0 = Monday)
    - NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR (default: 17)
    - NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE (default: 0)

    Returns:
        datetime: The next scheduled delivery time in UTC.
    """
    now = django_timezone.now()

    if cadence_type == EmailCadence.DAILY:
        delivery_hour = max(0, min(23, getattr(settings, 'NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR', 17)))
        delivery_minute = max(0, min(59, getattr(settings, 'NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE', 0)))

        # Calculate next delivery time
        delivery_time = now.replace(
            hour=delivery_hour,
            minute=delivery_minute,
            second=0,
            microsecond=0
        )
        # If the delivery time has already passed today, schedule for tomorrow
        if delivery_time <= now:
            delivery_time += timedelta(days=1)

        return delivery_time

    elif cadence_type == EmailCadence.WEEKLY:
        delivery_day = max(0, min(6, getattr(settings, 'NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY', 0)))  # 0=Monday
        delivery_hour = max(0, min(23, getattr(settings, 'NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR', 17)))
        delivery_minute = max(0, min(59, getattr(settings, 'NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE', 0)))

        # Calculate next delivery day
        days_ahead = delivery_day - now.weekday()
        if days_ahead < 0:
            days_ahead += 7

        delivery_time = now.replace(
            hour=delivery_hour,
            minute=delivery_minute,
            second=0,
            microsecond=0
        ) + timedelta(days=days_ahead)

        # If the delivery time is today but has already passed, schedule for next week
        if delivery_time <= now:
            delivery_time += timedelta(days=7)

        return delivery_time

    raise ValueError(f"Invalid cadence_type for digest scheduling: {cadence_type}")


def get_digest_dedupe_key(user_id, cadence_type, delivery_time):
    """
    Generate a deduplication key for a digest email task.

    This key ensures that only one digest task is scheduled per user per cadence period.

    Returns:
        str: A unique key based on user_id, cadence_type, and delivery window.
    """
    window_key = delivery_time.strftime('%Y-%m-%d-%H-%M')
    return f"digest:{user_id}:{cadence_type}:{window_key}"


def is_digest_already_scheduled(user_id, cadence_type, delivery_time):
    """
    Check if a digest email is already scheduled for this user in the current cadence window.

    This prevents duplicate scheduling when multiple notifications arrive
    in the same digest window.

    Uses DigestSchedule model for an exact (user, cadence_type, delivery_time) lookup —
    one record represents one pending Celery task. This is intentionally separate from
    Notification.email_scheduled, which tracks the immediate/buffer cadence flow and
    operates at the notification row level rather than the task level.
    """
    if cadence_type not in [EmailCadence.DAILY, EmailCadence.WEEKLY]:
        return False

    return DigestSchedule.objects.filter(
        user_id=user_id,
        cadence_type=cadence_type,
        delivery_time=delivery_time,
    ).exists()


def is_digest_already_sent_in_window(user_id, cadence_type, delivery_time):
    """
    Check if a digest email has already been sent for this user in the current cadence window.

    This prevents duplicate emails when both cron jobs and delayed tasks co-exist.
    """
    if cadence_type == EmailCadence.DAILY:
        window_start = delivery_time - timedelta(days=1, minutes=15)
    elif cadence_type == EmailCadence.WEEKLY:
        window_start = delivery_time - timedelta(days=7, minutes=15)
    else:
        return False

    return Notification.objects.filter(
        user_id=user_id,
        email=True,
        email_sent_on__gte=window_start,
        email_sent_on__lte=delivery_time,
    ).exists()


def schedule_bulk_digest_emails(user_cadence_map):
    """
    Bulk-schedule delayed Celery tasks for digest emails for multiple users.

    This avoids N+1 query issues when scheduling digests for many users at once
    (e.g. during send_notifications) by batching DB operations.

    Runs ~3 queries per cadence type regardless of user count:
    1. SELECT existing DigestSchedule records (1 query)
    2. Bulk INSERT new DigestSchedule records (1 query)
    3. Bulk UPDATE Notification.email_scheduled (1 query)

    Args:
        user_cadence_map: dict mapping user_id -> cadence_type
            (EmailCadence.DAILY or EmailCadence.WEEKLY)
    """
    if not user_cadence_map:
        return

    # Group users by cadence type
    cadence_groups = {}
    for uid, cadence in user_cadence_map.items():
        if cadence not in (EmailCadence.DAILY, EmailCadence.WEEKLY):
            logger.warning(f'<Digest Schedule Bulk> Invalid cadence_type {cadence} for user {uid}')
            continue
        cadence_groups.setdefault(cadence, []).append(uid)

    def _enqueue_bulk_digest_tasks(uids, ctype, dtime):
        for uid in uids:
            task_id = get_digest_dedupe_key(uid, ctype, dtime)
            send_user_digest_email_task.apply_async(
                kwargs={
                    'user_id': uid,
                    'cadence_type': ctype,
                },
                eta=dtime,
                task_id=task_id,
            )
        logger.info(
            f'<Digest Schedule Bulk> Scheduled {ctype} digest for {len(uids)} users '
            f'at {dtime}'
        )

    for cadence_type, user_ids in cadence_groups.items():
        delivery_time = get_next_digest_delivery_time(cadence_type)

        # 1. Find users who already have a DigestSchedule for this window (1 query)
        already_scheduled_user_ids = set(
            DigestSchedule.objects.filter(
                user_id__in=user_ids,
                cadence_type=cadence_type,
                delivery_time=delivery_time,
            ).values_list('user_id', flat=True)
        )

        new_user_ids = [uid for uid in user_ids if uid not in already_scheduled_user_ids]
        if not new_user_ids:
            logger.info(
                f'<Digest Schedule Bulk> All {len(user_ids)} users already scheduled '
                f'for cadence={cadence_type}, delivery_time={delivery_time}'
            )
            continue

        # 2. Bulk create DigestSchedule records for new users (1 query)
        new_schedules = [
            DigestSchedule(
                user_id=uid,
                cadence_type=cadence_type,
                delivery_time=delivery_time,
                task_id=get_digest_dedupe_key(uid, cadence_type, delivery_time),
            )
            for uid in new_user_ids
        ]
        DigestSchedule.objects.bulk_create(new_schedules, ignore_conflicts=True)

        # 3. Bulk mark unscheduled notifications as scheduled (1 query)
        if cadence_type == EmailCadence.DAILY:
            window_start = delivery_time - timedelta(days=1)
        else:
            window_start = delivery_time - timedelta(days=7)

        Notification.objects.filter(
            user_id__in=new_user_ids,
            email=True,
            email_scheduled=False,
            email_sent_on__isnull=True,
            created__gte=window_start,
        ).update(email_scheduled=True)

        # 4. Enqueue Celery tasks for each new user (no DB queries)
        transaction.on_commit(
            lambda uids=new_user_ids, ctype=cadence_type, dtime=delivery_time:
            _enqueue_bulk_digest_tasks(uids, ctype, dtime)
        )


@shared_task(bind=True, ignore_result=True, max_retries=3, default_retry_delay=300)
@set_code_owner_attribute
def send_user_digest_email_task(self, user_id, cadence_type):
    """
    Delayed Celery task to send a digest email to a single user.

    This task is scheduled with apply_async(eta=...) for the configured
    delivery time. When it fires:
    1. Atomically claims the DigestSchedule record (prevents duplicate
       execution from Redis visibility-timeout redeliveries)
    2. Checks if email was already sent (by cron job) to avoid duplicates
    3. Gathers all unsent notifications for the cadence window
    4. Sends the digest email
    5. Marks notifications as sent
    """
    try:
        claimed = _claim_digest_schedule(user_id, cadence_type)
        if not claimed:
            logger.info(
                f'<Digest Task> Duplicate task for user {user_id}, '
                f'cadence {cadence_type} — DigestSchedule already '
                f'claimed by another task. Skipping.'
            )
            return

        user = User.objects.get(id=user_id)

        if not user.has_usable_password():
            logger.info(f'<Digest Task> User {user.username} is disabled, skipping')
            return
        start_date, end_date = get_start_end_date(cadence_type)

        already_sent = Notification.objects.filter(
            user_id=user_id,
            email=True,
            email_sent_on__gte=start_date,
            email_sent_on__lte=end_date,
        ).exists()

        if already_sent:
            logger.info(
                f'<Digest Task> Digest already sent for user {user.username} '
                f'in window {start_date} to {end_date}. Clearing scheduled flags.'
            )
            # Clear scheduled flags so they're not picked up again
            Notification.objects.filter(
                user_id=user_id,
                email=True,
                email_scheduled=True,
                created__gte=start_date,
                created__lte=end_date,
            ).update(email_scheduled=False)
            return

        language_prefs = get_language_preference_for_users([user_id])
        user_language = language_prefs.get(user_id, 'en')
        courses_data = {}

        send_digest_email_to_user(
            user, cadence_type, start_date, end_date,
            user_language=user_language,
            courses_data=courses_data
        )

        # Clear scheduled flags after successful send
        Notification.objects.filter(
            user_id=user_id,
            email=True,
            email_scheduled=True,
            created__gte=start_date,
            created__lte=end_date,
        ).update(email_scheduled=False)

        logger.info(f'<Digest Task> Successfully sent {cadence_type} digest to user {user.username}')

    except User.DoesNotExist:
        logger.error(f'<Digest Task> User {user_id} not found')

    except Exception as exc:
        current_retries = getattr(self.request, "retries", 0)
        max_retries = getattr(self, "max_retries", None)
        if max_retries and current_retries >= max_retries - 1:
            logger.error(
                f'<Digest Task> Giving up sending {cadence_type} digest to user {user_id} '
                f'after {current_retries} retries.'
            )
            return
        retry_countdown = 300 * (2 ** current_retries)
        raise self.retry(exc=exc, countdown=retry_countdown) from exc


def _claim_digest_schedule(user_id, cadence_type):
    """
    Atomically claim (delete) the DigestSchedule record for the current
    delivery window.

    Returns ``True`` if this call deleted the row (we are the rightful
    owner). Returns ``False`` if the row was already gone (another copy
    of the redelivered task got there first).
    """
    now = django_timezone.now()

    if cadence_type == EmailCadence.DAILY:
        window_cutoff = now - timedelta(days=1, hours=1)
    elif cadence_type == EmailCadence.WEEKLY:
        window_cutoff = now - timedelta(days=7, hours=1)
    else:
        return True  # non-digest cadences don't use DigestSchedule

    with transaction.atomic():
        rows_deleted, _ = DigestSchedule.objects.filter(
            user_id=user_id,
            cadence_type=cadence_type,
            delivery_time__lte=now,
            delivery_time__gte=window_cutoff,
        ).delete()

    return rows_deleted > 0


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
