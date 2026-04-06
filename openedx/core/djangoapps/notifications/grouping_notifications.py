"""
Notification grouping utilities for notifications
"""
import datetime
from abc import ABC, abstractmethod
from typing import Dict, Type, Union  # noqa: UP035

from pytz import utc

from openedx.core.djangoapps.notifications.base_notification import COURSE_NOTIFICATION_TYPES
from openedx.core.djangoapps.notifications.models import Notification

from .exceptions import InvalidNotificationTypeError


class BaseNotificationGrouper(ABC):
    """
    Base class for notification groupers.
    """

    @abstractmethod
    def group(self, new_notification, old_notification):
        pass


class NotificationRegistry:
    """
    Registry for notification groupers.
    """
    _groupers: Dict[str, Type[BaseNotificationGrouper]] = {}  # noqa: UP006

    @classmethod
    def register(cls, notification_type: str):
        """
        Registers a notification grouper for the given notification type.
        Args
            notification_type: The type of notification for which to register the grouper.

        Returns:
            A decorator that registers the grouper class for the given notification type.
        """

        def decorator(grouper_class: Type[BaseNotificationGrouper]) -> Type[BaseNotificationGrouper]:  # noqa: UP006
            """
            Registers the grouper class for the given notification type.
            """
            if notification_type not in COURSE_NOTIFICATION_TYPES:
                raise InvalidNotificationTypeError(
                    f"'{notification_type}' is not a valid notification type."
                )
            cls._groupers[notification_type] = grouper_class
            return grouper_class

        return decorator

    @classmethod
    def get_grouper(cls, notification_type: str) -> Union[BaseNotificationGrouper, None]:  # noqa: UP007
        """Retrieves the appropriate notification grouper based on the given notification type.

        Args:
            notification_type: The type of notification for which to retrieve the grouper.

        Returns:
            The corresponding BaseNotificationGrouper instance or None if no grouper is found.
        """
        grouper_class = cls._groupers.get(notification_type)
        if not grouper_class:
            return None
        return grouper_class()


@NotificationRegistry.register('new_discussion_post')
class NewPostGrouper(BaseNotificationGrouper):
    """
    Groups new post notifications based on the author name.
    """

    def group(self, new_notification, old_notification):
        """
        Groups new post notifications based on the author name.
        """
        if (
            old_notification.content_context['username'] == new_notification.content_context['username']
            and not old_notification.content_context.get('grouped', False)
        ):
            return {**new_notification.content_context}
        return {
            **old_notification.content_context,
            "grouped": True,
            "replier_name": new_notification.content_context["replier_name"]
        }


@NotificationRegistry.register('ora_staff_notifications')
class OraStaffGrouper(BaseNotificationGrouper):
    """
    Grouper for new ora staff notifications.
    """

    def group(self, new_notification, old_notification):
        """
        Groups new ora staff notifications based on the xblock ID.
        """
        content_context = old_notification.content_context
        content_context.setdefault("grouped", True)
        return content_context


@NotificationRegistry.register('new_response')
class NewResponseGrouper(BaseNotificationGrouper):
    """
    Grouper for new response on post.
    """

    def group(self, new_notification, old_notification):
        """
        Groups new ora staff notifications based on the xblock ID.
        """
        content_context = old_notification.content_context
        content_context.setdefault("grouped", True)
        content_context["replier_name"] = new_notification.content_context["replier_name"]
        return content_context


@NotificationRegistry.register('response_on_followed_post')
class NewResponseOnFollowedPostGrouper(BaseNotificationGrouper):
    """
    Grouper for new response on post.
    """

    def group(self, new_notification, old_notification):
        """
        Groups new ora staff notifications based on the xblock ID.
        """
        content_context = old_notification.content_context
        content_context.setdefault("grouped", True)
        content_context["replier_name"] = new_notification.content_context["replier_name"]
        return content_context


def group_user_notifications(new_notification: Notification, old_notification: Notification):
    """
    Groups user notification based on notification type and group_id
    """
    notification_type = new_notification.notification_type
    grouper_class = NotificationRegistry.get_grouper(notification_type)

    if grouper_class:
        old_notification.content_context = grouper_class.group(new_notification, old_notification)
        old_notification.web = old_notification.web or new_notification.web
        old_notification.email = old_notification.email or new_notification.email
        old_notification.content_url = new_notification.content_url
        old_notification.last_read = None
        old_notification.last_seen = None
        old_notification.created = utc.localize(datetime.datetime.now())
        old_notification.save()


def get_user_existing_notifications(user_ids, notification_type, group_by_id, course_id):
    """
    Returns user last group able notification
    """
    notifications = Notification.objects.filter(
        user__in=user_ids,
        notification_type=notification_type,
        group_by_id=group_by_id,
        course_id=course_id,
        last_seen__isnull=True,
    )
    notifications_mapping = {user_id: [] for user_id in user_ids}
    for notification in notifications:
        notifications_mapping[notification.user_id].append(notification)

    for user_id, notifications in notifications_mapping.items():
        notifications.sort(key=lambda elem: elem.created, reverse=True)
        notifications_mapping[user_id] = notifications[0] if notifications else None
    return notifications_mapping
