"""
API module.
"""
import logging
from datetime import datetime
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from lms.djangoapps.verify_student.emails import send_verification_approved_email
from lms.djangoapps.verify_student.exceptions import VerificationAttemptInvalidStatus
from lms.djangoapps.verify_student.models import VerificationAttempt
from lms.djangoapps.verify_student.signals.signals import (
    emit_idv_attempt_approved_event,
    emit_idv_attempt_created_event,
    emit_idv_attempt_denied_event,
    emit_idv_attempt_pending_event,
)
from lms.djangoapps.verify_student.statuses import VerificationAttemptStatus
from lms.djangoapps.verify_student.tasks import send_verification_status_email

log = logging.getLogger(__name__)

User = get_user_model()


def send_approval_email(attempt):
    """
    Send an approval email to the learner associated with the IDV attempt.
    """
    verification_status_email_vars = {
        'platform_name': settings.PLATFORM_NAME,
    }

    expiration_datetime = attempt.expiration_datetime.date()
    if settings.VERIFY_STUDENT.get('USE_DJANGO_MAIL'):
        verification_status_email_vars['expiration_datetime'] = expiration_datetime.strftime("%m/%d/%Y")
        verification_status_email_vars['full_name'] = attempt.user.profile.name
        subject = _("Your {platform_name} ID verification was approved!").format(
            platform_name=settings.PLATFORM_NAME
        )
        context = {
            'subject': subject,
            'template': 'emails/passed_verification_email.txt',
            'email': attempt.user.email,
            'email_vars': verification_status_email_vars
        }
        send_verification_status_email.delay(context)
    else:
        email_context = {'user': attempt.user, 'expiration_datetime': expiration_datetime.strftime("%m/%d/%Y")}
        send_verification_approved_email(context=email_context)


def create_verification_attempt(
    user: User,
    name: str,
    status: str,
    expiration_datetime: Optional[datetime] = None,
    hide_status_from_user: Optional[bool] = False,
):
    """
    Create a verification attempt.

    This method is intended to be used by IDV implementation plugins to create VerificationAttempt instances.

    Args:
        user (User): the user (usually a learner) performing the verification attempt
        name (string): the name being ID verified
        status (string): the initial status of the verification attempt
        expiration_datetime (datetime, optional): When the verification attempt expires. Defaults to None.

    Returns:
        id (int): The id of the created VerificationAttempt instance
    """
    verification_attempt = VerificationAttempt.objects.create(
        user=user,
        name=name,
        status=status,
        expiration_datetime=expiration_datetime,
        hide_status_from_user=hide_status_from_user,
    )

    emit_idv_attempt_created_event(
        attempt_id=verification_attempt.id,
        user=user,
        status=status,
        name=name,
        expiration_date=expiration_datetime,
    )

    return verification_attempt.id


def update_verification_attempt(
    attempt_id: int,
    name: Optional[str] = None,
    status: Optional[str] = None,
    expiration_datetime: Optional[datetime] = None,
):
    """
    Update a verification attempt.

    This method is intended to be used by IDV implementation plugins to update VerificationAttempt instances.

    Arguments:
        * attempt_id (int): the verification attempt id of the attempt to update
        * name (string, optional): the new name being ID verified
        * status (string, optional): the new status of the verification attempt
        * expiration_datetime (datetime, optional): The new expiration date and time

    Returns:
        * None
    """
    try:
        attempt = VerificationAttempt.objects.get(id=attempt_id)
    except VerificationAttempt.DoesNotExist:
        log.error(
            f'VerificationAttempt with id {attempt_id} was not found '
            f'when updating the attempt to status={status}',
        )
        raise

    if name is not None:
        attempt.name = name

    if status is not None:
        attempt.status = status

        status_list = list(VerificationAttemptStatus)
        if status not in status_list:
            log.error(
                'Attempted to call update_verification_attempt called with invalid status: %(status)s. '
                'Status must be one of: %(status_list)s',
                {
                    'status': status,
                    'status_list': VerificationAttempt.STATUS,
                },
            )
            raise VerificationAttemptInvalidStatus

    # NOTE: Generally, we only set the expiration date from the time that an IDV attempt is marked approved,
    # so we allow expiration_datetime to = None for other status updates (e.g. pending).
    attempt.expiration_datetime = expiration_datetime

    attempt.save()

    user = attempt.user
    if status == VerificationAttemptStatus.PENDING:
        emit_idv_attempt_pending_event(
            attempt_id=attempt_id,
            user=user,
            status=status,
            name=name,
            expiration_date=expiration_datetime,
        )
    elif status == VerificationAttemptStatus.APPROVED:
        emit_idv_attempt_approved_event(
            attempt_id=attempt_id,
            user=user,
            status=status,
            name=name,
            expiration_date=expiration_datetime,
        )
    elif status == VerificationAttemptStatus.DENIED:
        emit_idv_attempt_denied_event(
            attempt_id=attempt_id,
            user=user,
            status=status,
            name=name,
            expiration_date=expiration_datetime,
        )
