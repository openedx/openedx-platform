"""
Signal definitions and functions to send those signals for the verify_student application.
"""

from django.dispatch import Signal
from openedx_events.learning.data import UserData, UserPersonalData, VerificationAttemptData
from openedx_events.learning.signals import (
    IDV_ATTEMPT_APPROVED,
    IDV_ATTEMPT_CREATED,
    IDV_ATTEMPT_DENIED,
    IDV_ATTEMPT_PENDING,
)

# Signal for emitting IDV submission and review updates
# providing_args = ["attempt_id", "user_id", "status", "full_name", "profile_name"]
idv_update_signal = Signal()


def _create_user_data(user):
    """
    Helper function to create a UserData object.
    """
    user_data = UserData(
        id=user.id,
        is_active=user.is_active,
        pii=UserPersonalData(
            username=user.username,
            email=user.email,
            name=user.get_full_name() or user.profile.name
        )
    )

    return user_data


def emit_idv_attempt_created_event(attempt_id, user, status, name, expiration_date):
    """
    Emit the IDV_ATTEMPT_CREATED Open edX event.
    """
    user_data = _create_user_data(user)

    # .. event_implemented_name: IDV_ATTEMPT_CREATED
    # .. event_type: org.openedx.learning.idv_attempt.created.v1
    IDV_ATTEMPT_CREATED.send_event(
        idv_attempt=VerificationAttemptData(
            attempt_id=attempt_id,
            user=user_data,
            status=status,
            name=name,
            expiration_date=expiration_date,
        )
    )
    return user_data


def emit_idv_attempt_pending_event(attempt_id, user, status, name, expiration_date):
    """
    Emit the IDV_ATTEMPT_PENDING Open edX event.
    """
    user_data = _create_user_data(user)

    # .. event_implemented_name: IDV_ATTEMPT_PENDING
    # .. event_type: org.openedx.learning.idv_attempt.pending.v1
    IDV_ATTEMPT_PENDING.send_event(
        idv_attempt=VerificationAttemptData(
            attempt_id=attempt_id,
            user=user_data,
            status=status,
            name=name,
            expiration_date=expiration_date,
        )
    )
    return user_data


def emit_idv_attempt_approved_event(attempt_id, user, status, name, expiration_date):
    """
    Emit the IDV_ATTEMPT_APPROVED Open edX event.
    """
    user_data = _create_user_data(user)

    # .. event_implemented_name: IDV_ATTEMPT_APPROVED
    # .. event_type: org.openedx.learning.idv_attempt.approved.v1
    IDV_ATTEMPT_APPROVED.send_event(
        idv_attempt=VerificationAttemptData(
            attempt_id=attempt_id,
            user=user_data,
            status=status,
            name=name,
            expiration_date=expiration_date,
        )
    )
    return user_data


def emit_idv_attempt_denied_event(attempt_id, user, status, name, expiration_date):
    """
    Emit the IDV_ATTEMPT_DENIED Open edX event.
    """
    user_data = _create_user_data(user)

    # .. event_implemented_name: IDV_ATTEMPT_DENIED
    # .. event_type: org.openedx.learning.idv_attempt.denied.v1
    IDV_ATTEMPT_DENIED.send_event(
        idv_attempt=VerificationAttemptData(
            attempt_id=attempt_id,
            user=user_data,
            status=status,
            name=name,
            expiration_date=expiration_date,
        )
    )
