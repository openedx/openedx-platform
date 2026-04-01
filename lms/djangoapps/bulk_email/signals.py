"""
Signal handlers for the bulk_email app
"""
import logging

from django.dispatch import receiver
from eventtracking import tracker

from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.user_api.accounts.signals import USER_RETIRE_MAILINGS
from edx_ace.signals import ACE_MESSAGE_SENT

from .models import Optout

log = logging.getLogger(__name__)


@receiver(USER_RETIRE_MAILINGS)
def force_optout_all(sender, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    When a user is retired from all mailings this method will create an Optout
    row for any courses they may be enrolled in.
    """
    user = kwargs.get('user', None)

    if not user:
        raise TypeError('Expected a User type, but received None.')

    for enrollment in CourseEnrollment.objects.filter(user=user):
        try:
            Optout.objects.get_or_create(user=user, course_id=enrollment.course.id)
        except CourseOverview.DoesNotExist:
            log.warning(
                f"CourseOverview not found for enrollment {enrollment.id} (user: {user.id}), "
                f"skipping optout creation. This may mean the course was deleted."
            )
            continue


@receiver(ACE_MESSAGE_SENT)
def ace_email_sent_handler(sender, **kwargs):
    """
    When an email is sent using ACE, this method will create an event to detect ace email success status
    """
    # Fetch the message dictionary from kwargs, defaulting to {} if not present
    message = kwargs.get('message', {})
    recipient = message.get('recipient', {})
    message_name = message.get('name', None)
    context = message.get('context', {})
    email_address = recipient.get('email', None)
    user_id = recipient.get('user_id', None)
    channel = message.get('channel', None)
    course_id = context.get('course_id', None)
    message_language = message.get('message_language', None)
    translation_language = message.get('translation_language', None)
    if not course_id:
        course_email = context.get('course_email', None)
        course_id = course_email.course_id if course_email else None
    log.info(f'Email sent for {message_name} for course {course_id} using channel {channel}')
    tracker.emit(
        'edx.ace.message_sent',
        {
            'message_type': message_name,
            'channel': channel,
            'course_id': course_id,
            'user_id': user_id,
            'user_email': email_address,
            'message_language': message_language,
            'translation_language': translation_language,
        }
    )
