"""Disable User Email OptOut Policy"""

import logging

from django.contrib.auth import get_user_model
from edx_ace.channel import ChannelType
from edx_ace.policy import Policy, PolicyResult

User = get_user_model()
log = logging.getLogger(__name__)


class DisableUserOptout(Policy):
    """
    Skips sending ace messages to disabled users
    """
    def check(self, message):
        """
        Checks if the user is disabled and if so, skips sending the message
        """
        skip_disable_user_policy = message.options.get('skip_disable_user_policy', False)
        if skip_disable_user_policy:
            return PolicyResult(deny=set())
        try:
            user = User.objects.get(id=message.recipient.lms_user_id)
        except User.DoesNotExist:
            log.info(f"Disable User Policy - User not found - {message.recipient.lms_user_id} - {message.name}")
            return PolicyResult(deny=set())
        if user.has_usable_password():
            return PolicyResult(deny=set())
        log.info(f"===> User is disabled - {user.email} - {message.name}")
        return PolicyResult(deny=set(ChannelType))
