"""
This module contains signals / handlers related to programs.
"""

import logging

from django.dispatch import receiver

from openedx.core.djangoapps.content.course_overviews.signals import COURSE_PACING_CHANGED
from openedx.core.djangoapps.credentials.api import is_credentials_enabled
from openedx.core.djangoapps.credentials.helpers import is_learner_records_enabled_for_org
from openedx.core.djangoapps.signals.signals import (
    COURSE_CERT_AWARDED,
    COURSE_CERT_CHANGED,
    COURSE_CERT_DATE_CHANGE,
    COURSE_CERT_REVOKED,
)

LOGGER = logging.getLogger(__name__)


@receiver(COURSE_CERT_AWARDED)
def handle_course_cert_awarded(sender, user, course_key, mode, status, **kwargs):  # pylint: disable=unused-argument
    """
    If use of the Credentials IDA is enabled and a learner is awarded a course certificate, schedule a celery task to
    determine if the learner is also eligible to be awarded any program certificates.

    Args:
        sender: class of the object instance that sent this signal
        user(User): The user to whom a course certificate was awarded
        course_key(CourseLocator): The course run key for which the course certificate was awarded
        mode(str): The "mode" of the course (e.g. Audit, Honor, Verified, etc.)
        status(str): The status of the course certificate that was awarded (e.g. "downloadable")

    Returns:
        None
    """
    if not is_credentials_enabled():
        return

    LOGGER.debug(f"Handling COURSE_CERT_AWARDED: user={user}, course_key={course_key}, mode={mode}, status={status}")
    # import here, because signal is registered at startup, but items in tasks are not yet able to be loaded
    from openedx.core.djangoapps.programs.tasks import award_program_certificates

    award_program_certificates.delay(user.username)


@receiver(COURSE_CERT_CHANGED)
def handle_course_cert_changed(sender, user, course_key, mode, status, **kwargs):  # pylint: disable=unused-argument
    """
    When the system updates a course certificate, enqueue a celery task responsible for syncing this change in the
    Credentials IDA

    ***** Important *****
    While the current name of the enqueue'd task is `award_course_certificate` it is *actually* responsible for both
    awarding and revocation of course certificates in Credentials.
    *********************

    Args:
        sender: class of the object instance that sent this signal
        user(User): The user to whom a course certificate was awarded
        course_key(CourseLocator): The course run key for which the course certificate was awarded
        mode(str): The "mode" of the course (e.g. Audit, Honor, Verified, etc.)
        status(str): The status of the course certificate that was awarded (e.g. "downloadable")

    Returns:
        None
    """
    verbose = kwargs.get("verbose", False)
    if verbose:
        LOGGER.info(
            f"Starting handle_course_cert_changed with params: sender [{sender}], user [{user}], course_key "
            f"[{course_key}], mode [{mode}], status [{status}], kwargs [{kwargs}]"
        )

    if not is_credentials_enabled():
        return

    # Avoid scheduling new tasks if learner records are disabled for this site (right now, course certs are only
    # used for learner records -- when that changes, we can remove this bit and always send course certs).
    if not is_learner_records_enabled_for_org(course_key.org):
        LOGGER.warning(f"Skipping send cert: the Learner Record feature is disabled for org [{course_key.org}]")
        return

    LOGGER.debug(f"Handling COURSE_CERT_CHANGED: user={user}, course_key={course_key}, mode={mode}, status={status}")
    # import here, because signal is registered at startup, but items in tasks are not yet able to be loaded
    from openedx.core.djangoapps.programs.tasks import award_course_certificate

    award_course_certificate.delay(user.username, str(course_key))


@receiver(COURSE_CERT_REVOKED)
def handle_course_cert_revoked(sender, user, course_key, mode, status, **kwargs):  # pylint: disable=unused-argument
    """
    If use of the Credentials IDA is enabled and a learner has a course certificate revoked, schedule a celery task
    to determine if there are any program certificates that must be revoked too.

    Args:
        sender: class of the object instance that sent this signal
        user(User): The user to whom a course certificate was revoked
        course_key(CourseLocator): The course run key for which the course certificate was revoked
        mode(str): The "mode" of the course (e.g. "audit", "honor", "verified", etc.)
        status(str): The status of the course certificate that was revoked (e.g. "revoked")

    Returns:
        None
    """
    if not is_credentials_enabled():
        return

    LOGGER.info(f"Handling COURSE_CERT_REVOKED: user={user}, course_key={course_key}, mode={mode}, status={status}")
    # import here, because signal is registered at startup, but items in tasks are not yet able to be loaded
    from openedx.core.djangoapps.programs.tasks import revoke_program_certificates

    revoke_program_certificates.delay(user.username, str(course_key))


@receiver(COURSE_CERT_DATE_CHANGE, dispatch_uid="course_certificate_date_change_handler")
def handle_course_cert_date_change(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    When a course run's configuration has been updated, and the system has detected an update related to the display
    behavior or availability date of the certificates issued in that course, we should enqueue celery tasks responsible
    for:
        - updating the certificate available date of the course run's course certificate configuration in Credentials

    Args:
        sender: class of the object instance that sent this signal
        course_key(CourseLocator): The course run key of the course run which was updated

    Returns:
        None
    """
    if not is_credentials_enabled():
        return

    LOGGER.info(f"Handling COURSE_CERT_DATE_CHANGE for course {course_key}")
    # import here, because signal is registered at startup, but items in tasks are not yet loaded
    from openedx.core.djangoapps.programs.tasks import update_certificate_available_date_on_course_update

    update_certificate_available_date_on_course_update.delay(str(course_key))


@receiver(COURSE_PACING_CHANGED, dispatch_uid="update_credentials_on_pacing_change")
def handle_course_pacing_change(sender, updated_course_overview, **kwargs):  # pylint: disable=unused-argument
    """
    If the pacing of a course run has been updated, we should enqueue the tasks responsible for updating the certificate
    available date (CAD) stored in the Credentials IDA's internal records. This ensures that we are correctly managing
    the visibiltiy of certificates on learners' program records.

    Args:
        sender: class of the object instance that sent this signal
        updated_course_overview(CourseOverview): The course overview of the course run which was just updated

    Returns:
        None
    """
    if not is_credentials_enabled():
        return

    course_id = str(updated_course_overview.id)
    LOGGER.info(f"Handling COURSE_PACING_CHANGED for course {course_id}")
    # import here, because signal is registered at startup, but items in tasks are not yet loaded
    from openedx.core.djangoapps.programs.tasks import update_certificate_available_date_on_course_update

    update_certificate_available_date_on_course_update.delay(course_id)
