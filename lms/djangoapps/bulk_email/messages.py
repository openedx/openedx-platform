"""
Module to define email message related classes and methods
"""
from abc import ABC, abstractmethod

from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from edx_ace import ace
from edx_ace.recipient import Recipient

from common.djangoapps.util.keyword_substitution import substitute_keywords_with_data
from lms.djangoapps.bulk_email.message_types import BulkEmail
from openedx.core.lib.celery.task_utils import emulate_http_request

User = get_user_model()


class CourseEmailMessage(ABC):
    """
    Abstract base class for course email messages
    """

    @abstractmethod
    def send(self):
        """
        Triggers sending of email message
        """


class DjangoEmail(CourseEmailMessage):
    """
    Email message class to send email directly using django mail API.
    """
    def __init__(self, connection, course_email, email_context):
        """
        Construct message content using course_email model and context
        """
        self.connection = connection
        template_context = email_context.copy()
        # use the CourseEmailTemplate that was associated with the CourseEmail
        course_email_template = course_email.get_template()

        plaintext_msg = course_email_template.render_plaintext(course_email.text_message, template_context)
        html_msg = course_email_template.render_htmltext(course_email.html_message, template_context)

        # Create email:
        message = EmailMultiAlternatives(
            course_email.subject,
            plaintext_msg,
            email_context['from_address'],
            [email_context['email']]
        )
        message.attach_alternative(html_msg, 'text/html')
        self.message = message

    def send(self):
        """
        send email using already opened connection
        """
        self.connection.send_messages([self.message])


class ACEEmail(CourseEmailMessage):
    """
    Email message class to send email using edx-ace.
    """
    def __init__(self, site, email_context):
        """
        Construct edx-ace message using email_context
        """
        self.site = site
        self.user = User.objects.get(email=email_context['email'])
        text_message = email_context['course_email'].text_message
        html_message = email_context['course_email'].html_message
        formatted_text_message = substitute_keywords_with_data(text_message, email_context)
        # The ACE body template renders ``formatted_html_message`` inside
        # ``{% autoescape off %}``. Force HTML escaping of substituted user
        # data (e.g. ``%%USER_FULLNAME%%``) to prevent stored XSS via
        # profile fields. The plain-text branch above stays unescaped so
        # the ``text/plain`` alternative is not polluted with ``&lt;``.
        formatted_html_message = substitute_keywords_with_data(
            html_message, email_context, html_escape=True,
        )
        email_context.update({
            'formatted_text_message': formatted_text_message,
            'formatted_html_message': formatted_html_message,
        })
        message = BulkEmail(context=email_context).personalize(
            recipient=Recipient(email_context['user_id'], email_context['email']),
            language=email_context['course_language'],
            user_context={"name": email_context['name']},
        )
        message.options['skip_disable_user_policy'] = True
        self.message = message

    def send(self):
        """
        Send message by emulating request in the context of site and user
        """
        with emulate_http_request(site=self.site, user=self.user):
            ace.send(self.message)
