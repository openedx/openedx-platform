"""
This file contains celery tasks for sending email
"""

import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.errors import RecoverableChannelDeliveryError
from edx_ace.message import Message
from edx_django_utils.monitoring import set_code_owner_attribute

from common.djangoapps.track import segment
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_authn.utils import check_pwned_password
from openedx.core.lib.celery.task_utils import emulate_http_request

log = logging.getLogger('edx.celery.task')


@shared_task
@set_code_owner_attribute
def check_pwned_password_and_send_track_event(
    user_id, password,
    internal_user=False,
    is_new_user=False,
    request_page=''
):
    """
    Check the Pwned Databases and send its event to Segment.
    """
    try:
        pwned_properties = check_pwned_password(password)
        if pwned_properties:
            pwned_properties['internal_user'] = internal_user
            pwned_properties['new_user'] = is_new_user
            pwned_properties['user_request_page'] = request_page
            segment.track(user_id, 'edx.bi.user.pwned.password.status', pwned_properties)
        return pwned_properties
    except Exception:  # pylint: disable=W0703
        log.exception(
            'Unable to get response from pwned password api for user_id: "%s"',
            user_id,
        )
        return {}  # pylint: disable=raise-missing-from


@shared_task(bind=True, default_retry_delay=30, max_retries=2)
@set_code_owner_attribute
def send_activation_email(self, msg_string, from_address=None, site_id=None):
    """
    Sending an activation email to the user.
    """
    msg = Message.from_string(msg_string)

    max_retries = settings.RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS
    retries = self.request.retries

    if from_address is None:
        from_address = configuration_helpers.get_value('ACTIVATION_EMAIL_FROM_ADDRESS') or (
            configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
        )
    msg.options['from_address'] = from_address

    dest_addr = msg.recipient.email_address

    site = Site.objects.get(id=site_id) if site_id else Site.objects.get_current()
    user = User.objects.get(id=msg.recipient.lms_user_id)

    try:
        with emulate_http_request(site=site, user=user):
            ace.send(msg)
    except RecoverableChannelDeliveryError:
        log.info('Retrying sending email to user {dest_addr}, attempt # {attempt} of {max_attempts}'.format(  # noqa: UP032  # pylint: disable=line-too-long
            dest_addr=dest_addr,
            attempt=retries,
            max_attempts=max_retries
        ))
        try:
            self.retry(countdown=settings.RETRY_ACTIVATION_EMAIL_TIMEOUT, max_retries=max_retries)
        except MaxRetriesExceededError:
            log.error(
                'Unable to send activation email to user from "%s" to "%s"',
                from_address,
                dest_addr,
                exc_info=True
            )
    except Exception:
        log.exception(
            'Unable to send activation email to user from "%s" to "%s"',
            from_address,
            dest_addr,
        )
        raise Exception  # pylint: disable=raise-missing-from  # noqa: B904
