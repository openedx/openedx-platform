"""
Course certificate generation

These methods generate course certificates (they create a new course certificate if it does not yet exist, or update the
existing cert if it does already exist).

These methods should be called from tasks.
"""

import logging  # noqa: I001
from uuid import uuid4

from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.utils import emit_certificate_event, get_preferred_certificate_name

log = logging.getLogger(__name__)


def generate_course_certificate(user, course_key, status, enrollment_mode, course_grade, generation_mode):
    """
    Generate a course certificate for this user, in this course run. If the certificate has a passing status, also emit
    a certificate event.

    Note that the certificate could be either an allowlist certificate or a "regular" course certificate; the content
    will be the same either way.

    Args:
        user: user for whom to generate a certificate
        course_key: course run key for which to generate a certificate
        status: certificate status (value from the CertificateStatuses model)
        enrollment_mode: user's enrollment mode (ex. verified)
        course_grade: user's course grade
        generation_mode: used when emitting an event. Options are "self" (implying the user generated the cert
            themself) and "batch" for everything else.
    """
    cert = _generate_certificate(user=user, course_key=course_key, status=status, enrollment_mode=enrollment_mode,
                                 course_grade=course_grade)

    if CertificateStatuses.is_passing_status(cert.status):
        # Emit a certificate event
        event_data = {
            'user_id': user.id,
            'course_id': str(course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': cert.mode,
            'generation_mode': generation_mode
        }
        emit_certificate_event(event_name='created', user=user, course_id=course_key, event_data=event_data)

    elif CertificateStatuses.unverified == cert.status:
        cert.mark_unverified(mode=enrollment_mode, source='certificate_generation')

    return cert


def _generate_certificate(user, course_key, status, enrollment_mode, course_grade):
    """
    Generate a certificate for this user, in this course run.

    This method takes things like grade and enrollment mode as parameters because these are used to determine if the
    user is eligible for a certificate, and they're also saved in the cert itself. We want the cert to reflect the
    values that were used when determining if it was eligible for generation.
    """
    # Retrieve the existing certificate for the learner if it exists
    existing_certificate = GeneratedCertificate.certificate_for_student(user, course_key)

    preferred_name = get_preferred_certificate_name(user)

    # Retain the `verify_uuid` from an existing certificate if possible, this will make it possible for the learner to
    # keep the existing URL to their certificate
    if existing_certificate and existing_certificate.verify_uuid:
        uuid = existing_certificate.verify_uuid
    else:
        uuid = uuid4().hex

    cert, created = GeneratedCertificate.objects.update_or_create(
        user=user,
        course_id=course_key,
        defaults={
            'user': user,
            'course_id': course_key,
            'mode': enrollment_mode,
            'name': preferred_name,
            'status': status,
            'grade': course_grade,
            'download_url': '',
            'key': '',
            'verify_uuid': uuid,
            'error_reason': ''
        }
    )

    if created:
        created_msg = 'Certificate was created.'
    else:
        created_msg = 'Certificate already existed and was updated.'
    log.info(f'Generated certificate with status {cert.status}, mode {cert.mode} and grade {cert.grade} for {user.id} '
             f': {course_key}. {created_msg}')
    return cert
