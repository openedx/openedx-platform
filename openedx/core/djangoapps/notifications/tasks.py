"""
This file contains celery tasks for notifications.
"""
import uuid
from datetime import datetime, timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.exceptions import ValidationError
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey
from zoneinfo import ZoneInfo

from openedx.core.djangoapps.notifications.audience_filters import NotificationFilter
from openedx.core.djangoapps.notifications.base_notification import (
    COURSE_NOTIFICATION_TYPES,
    get_default_values_of_preference,
    get_notification_content
)

from openedx.core.djangoapps.notifications.email.tasks import send_immediate_cadence_email
from openedx.core.djangoapps.notifications.config.waffle import (
    ENABLE_NOTIFICATIONS,
    ENABLE_PUSH_NOTIFICATIONS
)
from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.events import notification_generated_event
from openedx.core.djangoapps.notifications.grouping_notifications import (
    NotificationRegistry,
    get_user_existing_notifications,
    group_user_notifications
)
from openedx.core.djangoapps.notifications.models import (
    Notification,
    NotificationPreference, create_notification_preference,
)
from openedx.core.djangoapps.notifications.push.tasks import send_ace_msg_to_push_channel
from openedx.core.djangoapps.notifications.utils import (
    clean_arguments,
    get_list_in_batches,
    create_account_notification_pref_if_not_exists
)

logger = get_task_logger(__name__)


@shared_task(ignore_result=True)
@set_code_owner_attribute
def delete_notifications(kwargs):
    """
    Delete notifications
    kwargs: dict {notification_type, app_name, created, course_id}
    """
    batch_size = settings.EXPIRED_NOTIFICATIONS_DELETE_BATCH_SIZE
    total_deleted = 0
    kwargs = clean_arguments(kwargs)
    logger.info(f'Running delete with kwargs {kwargs}')
    while True:
        ids_to_delete = Notification.objects.filter(
            **kwargs
        ).values_list('id', flat=True)[:batch_size]
        ids_to_delete = list(ids_to_delete)
        if not ids_to_delete:
            break
        delete_queryset = Notification.objects.filter(
            id__in=ids_to_delete
        )
        delete_count, _ = delete_queryset.delete()
        total_deleted += delete_count
    logger.info(f'Total deleted: {total_deleted}')


@shared_task(ignore_result=True)
@set_code_owner_attribute
def delete_expired_notifications():
    """
    This task deletes all expired notifications
    """
    batch_size = settings.EXPIRED_NOTIFICATIONS_DELETE_BATCH_SIZE
    expiry_date = datetime.now(ZoneInfo("UTC")) - timedelta(days=settings.NOTIFICATIONS_EXPIRY)
    start_time = datetime.now()
    total_deleted = 0
    delete_count = None
    while delete_count != 0:
        batch_start_time = datetime.now()
        ids_to_delete = Notification.objects.filter(
            created__lte=expiry_date,
        ).values_list('id', flat=True)[:batch_size]
        ids_to_delete = list(ids_to_delete)
        delete_queryset = Notification.objects.filter(
            id__in=ids_to_delete
        )
        delete_count, _ = delete_queryset.delete()
        total_deleted += delete_count
        time_elapsed = datetime.now() - batch_start_time
    time_elapsed = datetime.now() - start_time
    logger.info(f'{total_deleted} Notifications deleted in {time_elapsed} seconds.')


# pylint: disable=too-many-statements
@shared_task
@set_code_owner_attribute
def send_notifications(user_ids, course_key: str, app_name, notification_type, context, content_url):
    """
    Send notifications to the users.
    """
    # pylint: disable=too-many-statements
    course_key = CourseKey.from_string(course_key)
    if not ENABLE_NOTIFICATIONS.is_enabled():
        return

    if not is_notification_valid(notification_type, context):
        raise ValidationError(f"Notification is not valid {app_name} {notification_type} {context}")

    user_ids = list(set(user_ids))
    batch_size = settings.NOTIFICATION_CREATION_BATCH_SIZE
    group_by_id = context.pop('group_by_id', '')
    grouping_function = NotificationRegistry.get_grouper(notification_type)
    grouping_enabled = group_by_id and grouping_function is not None
    generated_notification = None
    sender_id = context.pop('sender_id', None)
    default_web_config = get_default_values_of_preference(app_name, notification_type).get('web', False)
    generated_notification_audience = []
    email_notification_mapping = {}
    push_notification_audience = []
    is_push_notification_enabled = ENABLE_PUSH_NOTIFICATIONS.is_enabled(course_key)
    task_id = str(uuid.uuid4())
    for batch_user_ids in get_list_in_batches(user_ids, batch_size):
        logger.debug(f'Sending notifications to {len(batch_user_ids)} users in {course_key}')
        batch_user_ids = NotificationFilter().apply_filters(batch_user_ids, course_key, notification_type)
        logger.info(f'After applying filters, sending notifications to {len(batch_user_ids)} users in {course_key}')

        existing_notifications = (
            get_user_existing_notifications(batch_user_ids, notification_type, group_by_id, course_key)) \
            if grouping_enabled else {}

        # check if what is preferences of user and make decision to send notification or not

        preferences = NotificationPreference.objects.filter(
            user_id__in=batch_user_ids,
            app=app_name,
            type=notification_type

        )

        preferences = list(preferences)
        if default_web_config:
            preferences = create_account_notification_pref_if_not_exists(
                batch_user_ids, preferences, [notification_type]
            )

        if not preferences:
            continue

        notifications = []
        email_notification_user_ids = []
        for preference in preferences:
            user_id = preference.user_id

            if (
                preference and
                preference.is_enabled_for_any_channel(app_name, notification_type)
            ):
                notification_preferences = preference.get_channels_for_notification_type(app_name, notification_type)
                email_enabled = 'email' in notification_preferences
                email_cadence = preference.get_email_cadence_for_notification_type(app_name, notification_type)
                push_notification = is_push_notification_enabled and 'push' in notification_preferences
                new_notification = Notification(
                    user_id=user_id,
                    app_name=app_name,
                    notification_type=notification_type,
                    content_context={**context, 'uuid': task_id},
                    content_url=content_url,
                    course_id=course_key,
                    web='web' in notification_preferences,
                    email=email_enabled,
                    push=push_notification,
                    group_by_id=group_by_id,
                    email_scheduled=False
                )
                if email_enabled and (email_cadence == EmailCadence.IMMEDIATELY):
                    email_notification_user_ids.append(user_id)

                if push_notification:
                    push_notification_audience.append(user_id)

                if grouping_enabled and existing_notifications.get(user_id, None):
                    group_user_notifications(new_notification, existing_notifications[user_id])
                else:
                    notifications.append(new_notification)

                if not generated_notification:
                    generated_notification = new_notification

                generated_notification_audience.append(user_id)

        # send notification to users but use bulk_create
        Notification.objects.bulk_create(notifications)

        # Get fresh records with pk so it can be used in email sending because there is a need to
        # update the records further down the line.
        if email_notification_user_ids:
            email_notification_mapping = {
                notif.user_id: notif
                for notif in Notification.objects.filter(
                    user_id__in=email_notification_user_ids,
                    content_context__uuid=task_id,
                )
            }
    if email_notification_mapping:
        logger.info(
            f"Email Buffered Digest: Sending immediate email notifications to "
            f"users {list(email_notification_mapping.keys())} "
            f"for notification {notification_type}",
        )
        send_immediate_cadence_email(email_notification_mapping, course_key)

    if generated_notification:
        notification_generated_event(
            generated_notification_audience, app_name, notification_type, course_key, content_url,
            generated_notification.content, sender_id=sender_id
        )
        info_msg = "Sending %s %s notification to ace push channel for user ids %s"
        logger.info(info_msg, generated_notification.app_name,
                    generated_notification.notification_type, push_notification_audience)
        send_ace_msg_to_push_channel(push_notification_audience, generated_notification)


def is_notification_valid(notification_type, context):
    """
    Validates notification before creation
    """
    try:
        get_notification_content(notification_type, context)
    except Exception:  # pylint: disable=broad-except
        return False
    return True


def update_account_user_preference(user_id: int) -> None:
    """
    Update account level user preferences to ensure all notification types are present.
    """
    notification_types = set(COURSE_NOTIFICATION_TYPES.keys())
    # Get existing notification types for the user
    existing_types = set(
        NotificationPreference.objects
        .filter(user_id=user_id, type__in=notification_types)
        .values_list('type', flat=True)
    )

    # Find missing notification types
    missing_types = notification_types - existing_types

    if not missing_types:
        return

    # Create new preferences for missing types
    new_preferences = [
        create_notification_preference(user_id, notification_type)
        for notification_type in missing_types
    ]

    # Bulk create all new preferences
    NotificationPreference.objects.bulk_create(new_preferences)
    return
