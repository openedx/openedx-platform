"""Policies for the notifications app."""

from edx_ace.channel import ChannelType
from edx_ace.policy import Policy, PolicyResult

from .models import NotificationPreference


class CoursePushNotificationOptout(Policy):
    """
    Course Push Notification optOut Policy.
    """

    def check(self, message):
        """
        Check if the user has opted out of push notifications for the given course.
        :param message:
        :return: PolicyResult
        """
        app_label = message.context.get('app_label')

        if not (app_label or message.context.get('push_notification_extra_context', {})):
            return PolicyResult(deny={ChannelType.PUSH})

        notification_preference = NotificationPreference.objects.get_or_create(
            user_id=message.recipient.lms_user_id,
            app=app_label
        )
        if not notification_preference.push:
            return PolicyResult(deny={ChannelType.PUSH})

        return PolicyResult(deny=frozenset())
