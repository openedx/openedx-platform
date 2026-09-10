"""
Celery tasks used by cms_user_tasks
"""
import json

import botocore
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core import mail

from common.djangoapps.edxmako.shortcuts import render_to_string
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

LOGGER = get_task_logger(__name__)
TASK_COMPLETE_EMAIL_MAX_RETRIES = 3
TASK_COMPLETE_EMAIL_TIMEOUT = 60


@shared_task(bind=True)
def send_task_complete_email(self, task_name, task_state_text, dest_addr, detail_url,
                             olx_validation_text=None, is_course_optimizer_task=False):
    """
    Sending an email to the users when an async task completes.
    """
    retries = self.request.retries
    context = {
        'task_name': task_name,
        'task_status': task_state_text,
        'detail_url': detail_url,
        'olx_validation_errors': {},
        'is_course_optimizer_task': is_course_optimizer_task,
    }
    if olx_validation_text:
        try:
            context['olx_validation_errors'] = json.loads(olx_validation_text)
        except ValueError:  # includes simplejson.decoder.JSONDecodeError
            LOGGER.error(f'Unable to parse CourseOlx validation text: {olx_validation_text}')

    subject = render_to_string('emails/user_task_complete_email_subject.txt', context)
    # Eliminate any newlines
    subject = ''.join(subject.splitlines())
    message = render_to_string('emails/user_task_complete_email.txt', context)

    from_address = configuration_helpers.get_value(
        'email_from_address',
        settings.DEFAULT_FROM_EMAIL
    )

    try:
        mail.send_mail(subject, message, from_address, [dest_addr], fail_silently=False)
        LOGGER.info("Task complete email has been sent to User %s", dest_addr)
    except botocore.exceptions.ClientError:
        LOGGER.info(
            'Retrying sending email to user %s, attempt # %s of %s',
            dest_addr,
            retries,
            TASK_COMPLETE_EMAIL_MAX_RETRIES
        )
        try:
            self.retry(countdown=TASK_COMPLETE_EMAIL_TIMEOUT, max_retries=TASK_COMPLETE_EMAIL_MAX_RETRIES)
        except MaxRetriesExceededError:
            LOGGER.error(
                'Unable to send task completion email to user from "%s" to "%s"',
                from_address,
                dest_addr,
                exc_info=True
            )
    except Exception:  # pylint: disable=broad-except
        LOGGER.exception(
            'Unable to send task completion email to user from "%s" to "%s"',
            from_address,
            dest_addr,
            exc_info=True
        )
