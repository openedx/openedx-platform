"""
ACE emails for verify_student app
"""
import logging  # noqa: I001

from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.recipient import Recipient

from lms.djangoapps.verify_student.message_types import VerificationApproved, VerificationSubmitted
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request

log = logging.getLogger(__name__)


def send_verification_confirmation_email(context):
    """Send an email confirming that the user submitted photos for initial verification."""
    site = Site.objects.get_current()
    message_context = get_base_template_context(site)
    message_context.update(context)
    user = context['user']
    try:
        with emulate_http_request(site=site, user=user):
            msg = VerificationSubmitted(context=message_context).personalize(
                recipient=Recipient(user.id, user.email),
                language=get_user_preference(user, LANGUAGE_KEY),
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
            log.info('Verification confirmation email sent to user: %r', user.username)
            return True
    except Exception:  # pylint: disable=broad-except
        log.exception('Could not send email for verification confirmation to user %s', user.username)
        return False


def send_verification_approved_email(context):
    """
    Sends email to a learner when ID verification has been approved.
    """
    site = Site.objects.get_current()
    message_context = get_base_template_context(site)
    message_context.update(context)
    user = context['user']
    try:
        with emulate_http_request(site=site, user=user):
            msg = VerificationApproved(context=message_context).personalize(
                recipient=Recipient(user.id, user.email),
                language=get_user_preference(user, LANGUAGE_KEY),
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
            log.info('Verification approved email sent to user: %r', user.username)
            return True
    except Exception:  # pylint: disable=broad-except
        log.exception('Could not send email for verification approved to user %s', user.username)
        return False
