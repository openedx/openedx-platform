"""
Common Utilities for the verify_student application.
"""

import datetime
import logging

from django.conf import settings
from django.utils.timezone import now

from lms.djangoapps.verify_student.tasks import send_request_to_ss_for_user

log = logging.getLogger(__name__)


def submit_request_to_ss(user_verification, copy_id_photo_from):
    """
    Submit our verification attempt to Software Secure for validation.

    Submits the task to software secure and If the task creation fails,
    set the verification status to "must_retry".
    """
    try:
        send_request_to_ss_for_user.delay(
            user_verification_id=user_verification.id, copy_id_photo_from=copy_id_photo_from
        )
    except Exception as error:  # pylint: disable=broad-except
        log.error(
            "Software Secure submit request %r failed, result: %s", user_verification.user.username, str(error)
        )
        user_verification.mark_must_retry()


def is_verification_expiring_soon(expiration_datetime):
    """
    Returns True if verification is expiring within EXPIRING_SOON_WINDOW.
    """
    if expiration_datetime:
        if (expiration_datetime - now()).days <= settings.VERIFY_STUDENT.get("EXPIRING_SOON_WINDOW"):
            return True

    return False


def earliest_allowed_verification_date():
    """
    Returns the earliest allowed date given the settings
    """
    days_good_for = settings.VERIFY_STUDENT["DAYS_GOOD_FOR"]
    return now() - datetime.timedelta(days=days_good_for)


def active_verifications(candidates, deadline):
    """
    Based on the deadline, only return verification attempts
    that are considered active (non-expired and wasn't created for future)
    """
    relevant_attempts = []
    if not candidates:
        return relevant_attempts

    # Look for a verification that was in effect at the deadline,
    # preferring recent verifications.
    # If no such verification is found, implicitly return empty array
    for verification in candidates:
        if verification.active_at_datetime(deadline):
            relevant_attempts.append(verification)

    return relevant_attempts


def verification_for_datetime(deadline, candidates):
    """Find a verification in a set that applied during a particular datetime.

    A verification is considered "active" during a datetime if:
    1) The verification was created before the datetime, and
    2) The verification is set to expire after the datetime.

    Note that verification status is *not* considered here,
    just the start/expire dates.

    If multiple verifications were active at the deadline,
    returns the most recently created one.

    Arguments:
        deadline (datetime): The datetime at which the verification applied.
            If `None`, then return the most recently created candidate.
        candidates (list of `PhotoVerification`s): Potential verifications to search through.

    Returns:
        PhotoVerification: A photo verification that was active at the deadline.
            If no verification was active, return None.

    """
    if not candidates:
        return None

    if not deadline:
        return candidates[0]

    attempts = active_verifications(candidates, deadline)
    if attempts:
        return attempts[0]
    else:
        return None


def most_recent_verification(verification_sets):
    """
    Return the most recent verification (by updated date) given querysets for multiple types of verifications.
    Photo, sso and manual are the current use.

    Arguments:
        tuple or other iterable of verification sets

    Returns:
        The most recent verification.
    """
    most_recent = None
    for s in verification_sets:
        for v in s:
            if not most_recent or v.updated_at > most_recent.updated_at:
                most_recent = v
    return most_recent


def auto_verify_for_testing_enabled(override=None):
    """
    If AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING is True, we want to skip posting
    anything to Software Secure.

    Bypass posting anything to Software Secure if auto verify feature for testing is enabled.
    We actually don't even create the message because that would require encryption and message
    signing that rely on settings.VERIFY_STUDENT values that aren't set in dev. So we just
    pretend like we successfully posted.
    """
    if override is not None:
        return override
    return settings.AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING


def can_verify_now(verification_status, expiration_datetime):
    """
    Returns whether one is eligible for verification now based on status and expiration.

    Arguments:
        verification_status (str)
        expiration_datetime (datetime)

    Returns: bool
    """
    return (
        # If the user has no initial verification or if the verification
        # process is still ongoing 'pending' or expired then allow the user to
        # submit the photo verification.
        # A photo verification is marked as 'pending' if its status is either
        # 'submitted' or 'must_retry'.
        verification_status['status'] in {"none", "must_reverify", "expired", "pending"}
        or (
            # The user has an active verification, but the verification
            # is set to expire within "EXPIRING_SOON_WINDOW" days (default is 4 weeks).
            # In this case user can resubmit photos for reverification.
            expiration_datetime
            and is_verification_expiring_soon(expiration_datetime)
        )
    )
