"""
Utils function for notifications app
"""
from typing import Dict, List

from common.djangoapps.student.models import CourseAccessRole
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS
from openedx.core.djangoapps.notifications.models import create_notification_preference, NotificationPreference
from openedx.core.lib.cache_utils import request_cached


def get_show_notifications_tray():
    """
    Returns whether notifications tray is enabled via waffle flag
    """
    return ENABLE_NOTIFICATIONS.is_enabled()


def get_list_in_batches(input_list, batch_size):
    """
    Divides the list of objects into list of list of objects each of length batch_size.
    """
    list_length = len(input_list)
    for index in range(0, list_length, batch_size):
        yield input_list[index: index + batch_size]


@request_cached()
def get_notification_types_with_visibility_settings() -> Dict[str, List[str]]:
    """
    Get notification types with their visibility settings.

    :return: List of dictionaries with notification type names and corresponding visibility settings
    """
    from .base_notification import COURSE_NOTIFICATION_TYPES

    notification_types_with_visibility_settings = {}
    for notification_type in COURSE_NOTIFICATION_TYPES.values():
        if notification_type.get('visible_to'):
            notification_types_with_visibility_settings[notification_type['name']] = notification_type['visible_to']

    return notification_types_with_visibility_settings


def filter_out_visible_notifications(
    user_preferences: dict,
    notifications_with_visibility: Dict[str, List[str]],
    user_forum_roles: List[str],
    user_course_roles: List[str]
) -> dict:
    """
    Filter out notifications visible to forum roles from user preferences.

    :param user_preferences: User preferences dictionary
    :param notifications_with_visibility: List of dictionaries with notification type names and
    corresponding visibility settings
    :param user_forum_roles: List of forum roles for the user
    :param user_course_roles: List of course roles for the user
    :return: Updated user preferences dictionary
    """
    for user_preferences_app, app_config in user_preferences.items():
        if 'notification_types' in app_config:
            # Iterate over the types to remove and pop them from the dictionary
            for notification_type, is_visible_to in notifications_with_visibility.items():
                is_visible = False
                for role in is_visible_to:
                    if role in user_forum_roles or role in user_course_roles:
                        is_visible = True
                        break
                if is_visible:
                    continue
                if notification_type in user_preferences[user_preferences_app]['notification_types']:
                    user_preferences[user_preferences_app]['notification_types'].pop(notification_type)
    return user_preferences


def clean_arguments(kwargs):
    """
    Returns query arguments from command line arguments
    """
    clean_kwargs = {}
    for key in ['app_name', 'notification_type', 'course_id']:
        if kwargs.get(key):
            clean_kwargs[key] = kwargs[key]
    if kwargs.get('created', {}):
        clean_kwargs.update(kwargs.get('created'))
    return clean_kwargs


def get_user_forum_access_roles(user_id: int) -> List[str]:
    """
    Get forum roles for the given user in all course.

    :param user_id: User ID
    :return: List of forum roles
    """
    return list(Role.objects.filter(users__id=user_id).values_list('name', flat=True))


def exclude_inaccessible_preferences(user_preferences: dict, user):
    """
    Exclude notifications from user preferences that the user has no access to,
    based on forum and course roles.

    :param user_preferences: Dictionary of user notification preferences
    :param user: Django User object
    :return: Updated user_preferences dictionary (modified in-place)
    """
    forum_roles = get_user_forum_access_roles(user.id)
    visible_notifications = get_notification_types_with_visibility_settings()
    course_roles = CourseAccessRole.objects.filter(
        user=user
    ).values_list('role', flat=True)

    filter_out_visible_notifications(
        user_preferences,
        visible_notifications,
        forum_roles,
        course_roles
    )
    return user_preferences


def _get_missing_preference_objects(user_ids, existing_prefs, target_types):
    """
    Compares existing data against target needs and returns
    a list of unsaved model instances.
    """
    already_exists = {f"{p.user_id}-{p.type}" for p in existing_prefs}

    to_create = []
    for user_id in user_ids:
        for n_type in target_types:
            key = f"{int(user_id)}-{n_type}"

            if key not in already_exists:
                new_obj = create_notification_preference(
                    user_id=int(user_id),
                    notification_type=n_type
                )
                to_create.append(new_obj)
                already_exists.add(key)

    return to_create


def create_account_notification_pref_if_not_exists(user_ids, existing_preferences, notification_types):
    """
    Ensures that NotificationPreference objects exist for the given user IDs
    and notification types. Creates any missing preferences in bulk.
    Args:
        user_ids: Iterable of user IDs to check/create preferences for.
        existing_preferences: QuerySet of existing NotificationPreference objects.
        notification_types: Iterable of notification type strings to ensure exist.
    Returns:
        List of NotificationPreference objects including both existing and newly created ones.
    """
    new_prefs_to_save = _get_missing_preference_objects(
        user_ids,
        existing_preferences,
        notification_types
    )

    if not new_prefs_to_save:
        return list(existing_preferences)

    NotificationPreference.objects.bulk_create(
        new_prefs_to_save,
        ignore_conflicts=True
    )

    return list(existing_preferences) + new_prefs_to_save
