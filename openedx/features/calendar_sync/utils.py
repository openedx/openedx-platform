# lint-amnesty, pylint: disable=missing-module-docstring
import logging
import os  # lint-amnesty, pylint: disable=wrong-import-order
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from django.conf import settings
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


def calendar_sync_initial_email_content(course_name):  # lint-amnesty, pylint: disable=missing-function-docstring
    subject = _('Sync {course} to your calendar').format(course=course_name)
    body_paragraph_1 = _('Sticking to a schedule is the best way to ensure that you successfully complete your '
                         'self-paced course. This schedule for {course} will help you stay on track!'
                         ).format(course=course_name)
    body_paragraph_2 = _('Once you sync your course schedule to your calendar, any updates to the course from your '
                         'instructor will be automatically reflected. You can remove the course from your calendar '
                         'at any time.')
    body = format_html(
        '<div style="margin-bottom:10px">{bp1}</div><div>{bp2}</div>',
        bp1=body_paragraph_1,
        bp2=body_paragraph_2
    )

    return subject, body


def calendar_sync_update_email_content(course_name):  # lint-amnesty, pylint: disable=missing-function-docstring
    subject = _('{course} dates have been updated on your calendar').format(course=course_name)
    body_paragraph = _('You have successfully shifted your course schedule and your calendar is up to date.'
                       ).format(course=course_name)
    body = format_html('<div>{text}</div>', text=body_paragraph)

    return subject, body


def prepare_attachments(attachment_data, file_ext=''):
    """
    Helper function to create a list contain file attachment objects
    for use with MIMEMultipart
    Returns a list of MIMEApplication objects
    """
    attachments = []
    for filename, data in attachment_data.items():
        msg_attachment = MIMEApplication(data)
        msg_attachment.add_header(
            'Content-Disposition',
            'attachment',
            filename=os.path.basename(filename) + file_ext
        )
        msg_attachment.set_type('text/calendar')
        attachments.append(msg_attachment)

    return attachments


def send_email_with_attachment(to_emails, attachment_data, course_name, is_initial):  # lint-amnesty, pylint: disable=missing-function-docstring
    # connect to SES
    client = boto3.client('ses', region_name=settings.AWS_SES_REGION_NAME)

    subject, body = (calendar_sync_initial_email_content(course_name) if is_initial else
                     calendar_sync_update_email_content(course_name))

    # build email body as html
    msg_body = MIMEText(body, 'html')

    attachments = prepare_attachments(attachment_data, '.ics')

    # iterate over each email in the list to send emails independently
    for email in to_emails:
        msg = MIMEMultipart()
        msg['Subject'] = str(subject)
        msg['From'] = settings.BULK_EMAIL_DEFAULT_FROM_EMAIL
        msg['To'] = email

        # attach the message body and attachment
        msg.attach(msg_body)
        for msg_attachment in attachments:
            msg.attach(msg_attachment)

        # send the email
        result = client.send_raw_email(Source=msg['From'], Destinations=[email], RawMessage={'Data': msg.as_string()})
        logger.debug(result)
