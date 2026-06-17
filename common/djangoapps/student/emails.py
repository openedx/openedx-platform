"""
ACE emails for Student app
"""
import logging

from django.conf import settings
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.recipient import Recipient

from common.djangoapps.student.message_types import ProctoringRequirements
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context

log = logging.getLogger(__name__)


def send_proctoring_requirements_email(context):
    """Send an email with proctoring requirements for a course enrollment"""
    site = Site.objects.get_current()
    message_context = get_base_template_context(site)
    message_context.update(context)
    user = context['user']
    try:
        msg = ProctoringRequirements(context=message_context).personalize(
            recipient=Recipient(user.id, user.email),
            language=settings.LANGUAGE_CODE,
            user_context={'full_name': user.profile.name}
        )
        ace.send(msg)
        if settings.FEATURES['SQUELCH_PII_IN_LOGS']:
            log.info('Proctoring requirements email sent to user ID: %r', user.id)
        else:
            log.info('Proctoring requirements email sent to user: %r', user.username)
        return True
    except Exception:  # pylint: disable=broad-except
        if settings.FEATURES['SQUELCH_PII_IN_LOGS']:
            log.exception('Could not send email for proctoring requirements to user ID %s', user.id)
        else:
            log.exception('Could not send email for proctoring requirements to user %s', user.username)
        return False
