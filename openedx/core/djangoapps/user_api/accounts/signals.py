"""
Django Signal related functionality for user_api accounts
"""

import logging

from django.db.models.signals import pre_delete
from django.dispatch import Signal, receiver
from social_django.models import UserSocialAuth

from .utils import REDACTED_SOCIAL_AUTH_UID_PREFIX, REDACTED_SOCIAL_AUTH_UID_SUFFIX, redact_and_delete_social_auth

logger = logging.getLogger(__name__)


def get_redacted_social_auth_uid(pk):
    """
    Return the redacted uid for a UserSocialAuth record.

    This must match the format used in redact_and_delete_social_auth.
    """
    return f'{REDACTED_SOCIAL_AUTH_UID_PREFIX}{pk}{REDACTED_SOCIAL_AUTH_UID_SUFFIX}'

# Signal to retire a user from LMS-initiated mailings (course mailings, etc)
# providing_args=["user"]
USER_RETIRE_MAILINGS = Signal()

# Signal to retire LMS critical information
# providing_args=["user", "retired_username", "retired_email"]
USER_RETIRE_LMS_CRITICAL = Signal()

# Signal to retire LMS misc information
# providing_args=["user"]
USER_RETIRE_LMS_MISC = Signal()


@receiver(pre_delete, sender=UserSocialAuth)
def redact_social_auth_pii_before_deletion(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
    Safety-net signal handler that redacts PII on any UserSocialAuth before deletion.

    Records deleted via ``redact_and_delete_social_auth`` will already be redacted;
    this handler is a fallback for any missed deletion path.
    """
    redacted_uid = get_redacted_social_auth_uid(instance.pk)

    # Safety-net in case the record wasn't redacted before delete.
    if instance.extra_data or instance.uid != redacted_uid:
        logger.warning(
            'Social auth link for user_id=%s, provider=%s was deleted without first being redacted.'
            ' Redacting in pre_delete.',
            instance.user_id,
            instance.provider,
        )
        redact_and_delete_social_auth(instance.user_id, skip_delete=True)
