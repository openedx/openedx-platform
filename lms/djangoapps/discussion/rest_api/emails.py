"""
Email notifications for discussion moderation actions.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model

log = logging.getLogger(__name__)
User = get_user_model()

# Try to import ACE at module level for easier testing
try:
    from edx_ace import ace
    from edx_ace.recipient import Recipient
    from edx_ace.message import Message
    ACE_AVAILABLE = True
except ImportError:
    ace = None
    Recipient = None
    Message = None
    ACE_AVAILABLE = False


def send_ban_escalation_email(
    banned_user_id,
    moderator_id,
    course_id,
    scope,
    reason,
    threads_deleted,
    comments_deleted
):
    """
    Send email to partner-support when user is banned.

    Uses ACE (Automated Communications Engine) for templated emails if available,
    otherwise falls back to Django's email system.

    Args:
        banned_user_id: ID of the banned user
        moderator_id: ID of the moderator who applied the ban
        course_id: Course ID where ban was applied
        scope: 'course' or 'organization'
        reason: Reason for the ban
        threads_deleted: Number of threads deleted
        comments_deleted: Number of comments deleted
    """
    # Check if email notifications are enabled
    if not getattr(settings, 'DISCUSSION_MODERATION_BAN_EMAIL_ENABLED', True):
        log.info(
            "Ban email notifications disabled by settings. "
            "User %s banned in course %s (scope: %s)",
            banned_user_id, course_id, scope
        )
        return

    try:
        banned_user = User.objects.get(id=banned_user_id)
        moderator = User.objects.get(id=moderator_id)

        # Get escalation email from settings
        escalation_email = getattr(
            settings,
            'DISCUSSION_MODERATION_ESCALATION_EMAIL',
            'partner-support@edx.org'
        )

        # Try using ACE first (preferred method for edX)
        if ACE_AVAILABLE and ace is not None:
            message = Message(
                app_label='discussion',
                name='ban_escalation',
                recipient=Recipient(lms_user_id=None, email_address=escalation_email),
                context={
                    'banned_username': banned_user.username,
                    'banned_email': banned_user.email,
                    'banned_user_id': banned_user_id,
                    'moderator_username': moderator.username,
                    'moderator_email': moderator.email,
                    'moderator_id': moderator_id,
                    'course_id': str(course_id),
                    'scope': scope,
                    'reason': reason or 'No reason provided',
                    'threads_deleted': threads_deleted,
                    'comments_deleted': comments_deleted,
                    'total_deleted': threads_deleted + comments_deleted,
                }
            )

            ace.send(message)
            log.info(
                "Ban escalation email sent via ACE to %s for user %s in course %s",
                escalation_email, banned_user.username, course_id
            )

        else:
            # Fallback to Django's email system if ACE is not available
            from django.core.mail import send_mail
            from django.template.loader import render_to_string
            from django.template import TemplateDoesNotExist

            context = {
                'banned_username': banned_user.username,
                'banned_email': banned_user.email,
                'banned_user_id': banned_user_id,
                'moderator_username': moderator.username,
                'moderator_email': moderator.email,
                'moderator_id': moderator_id,
                'course_id': str(course_id),
                'scope': scope,
                'reason': reason or 'No reason provided',
                'threads_deleted': threads_deleted,
                'comments_deleted': comments_deleted,
                'total_deleted': threads_deleted + comments_deleted,
            }

            # Try to render template, fall back to plain text if template doesn't exist
            try:
                email_body = render_to_string(
                    'discussion/ban_escalation_email.txt',
                    context
                )
            except TemplateDoesNotExist:
                # Plain text fallback
                banned_user_info = "{} ({})".format(banned_user.username, banned_user.email)
                moderator_info = "{} ({})".format(moderator.username, moderator.email)
                email_body = """
A user has been banned from discussions:

Banned User: {}
Moderator: {}
Course: {}
Scope: {}
Reason: {}
Content Deleted: {} threads, {} comments

Please review this moderation action and follow up as needed.
""".format(
                    banned_user_info,
                    moderator_info,
                    course_id,
                    scope,
                    reason or 'No reason provided',
                    threads_deleted,
                    comments_deleted
                )

            subject = f'Discussion Ban Alert: {banned_user.username} in {course_id}'
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com')

            send_mail(
                subject=subject,
                message=email_body,
                from_email=from_email,
                recipient_list=[escalation_email],
                fail_silently=False,
            )

            log.info(
                "Ban escalation email sent via Django mail to %s for user %s in course %s",
                escalation_email, banned_user.username, course_id
            )

    except User.DoesNotExist as e:
        log.error("Failed to send ban escalation email: User not found - %s", str(e))
        raise
    except Exception as exc:
        log.error("Failed to send ban escalation email: %s", str(exc), exc_info=True)
        raise
