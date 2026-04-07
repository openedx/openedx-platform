"""
Course certificate generation handler.

These methods check to see if a certificate can be generated (created if it does not already exist, or updated if it
exists but its state can be altered). If so, a celery task is launched to do the generation. If the certificate
cannot be generated, a message is logged and no further action is taken.
"""

import logging

from django.conf import settings
from openedx_filters.learning.filters import CertificateCreationRequested

from common.djangoapps.course_modes import api as modes_api
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import CertificateAllowlist, CertificateInvalidation, GeneratedCertificate
from lms.djangoapps.certificates.tasks import CERTIFICATE_DELAY_SECONDS, generate_certificate
from lms.djangoapps.certificates.utils import has_html_certificates_enabled
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor.access import is_beta_tester
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none

log = logging.getLogger(__name__)


class GeneratedCertificateException(Exception):
    pass


class CertificateGenerationNotAllowed(GeneratedCertificateException):
    pass


def generate_certificate_task(user, course_key, generation_mode=None, delay_seconds=CERTIFICATE_DELAY_SECONDS):
    """
    Create a task to generate a certificate for this user in this course run, if the user is eligible and a certificate
    can be generated.

    If the allowlist is enabled for this course run and the user is on the allowlist, the allowlist logic will be used.
    Otherwise, the regular course certificate generation logic will be used.
    """
    if is_on_certificate_allowlist(user, course_key):
        log.info(f'User {user.id} is on the allowlist for {course_key}. Attempt will be made to generate an allowlist '
                 f'certificate.')
        return generate_allowlist_certificate_task(user, course_key, generation_mode=generation_mode,
                                                   delay_seconds=delay_seconds)

    log.info(f'Attempt will be made to generate course certificate for user {user.id} : {course_key}')
    return _generate_regular_certificate_task(user, course_key, generation_mode=generation_mode,
                                              delay_seconds=delay_seconds)


def generate_allowlist_certificate_task(user, course_key, generation_mode=None,
                                        delay_seconds=CERTIFICATE_DELAY_SECONDS):
    """
    Create a task to generate an allowlist certificate for this user in this course run.
    """
    enrollment_mode = _get_enrollment_mode(user, course_key)
    course_grade = _get_course_grade(user, course_key)
    if _can_generate_allowlist_certificate(user, course_key, enrollment_mode):
        try:
            return _generate_certificate_task(
                user=user, course_key=course_key, enrollment_mode=enrollment_mode, course_grade=course_grade,
                generation_mode=generation_mode, delay_seconds=delay_seconds,
            )
        except CertificateGenerationNotAllowed:
            # Catch exception to contain error message in console.
            log.error(
                "Certificate generation not allowed for user %s in course %s",
                user.id,
                course_key,
            )
            return False

    status = _set_allowlist_cert_status(user, course_key, enrollment_mode, course_grade)
    if status is not None:
        return True

    return False


def _generate_regular_certificate_task(user, course_key, generation_mode=None, delay_seconds=CERTIFICATE_DELAY_SECONDS):
    """
    Create a task to generate a regular (non-allowlist) certificate for this user in this course run, if the user is
    eligible and a certificate can be generated.
    """
    enrollment_mode = _get_enrollment_mode(user, course_key)
    course_grade = _get_course_grade(user, course_key)
    if _can_generate_regular_certificate(user, course_key, enrollment_mode, course_grade):
        return _generate_certificate_task(user=user, course_key=course_key, enrollment_mode=enrollment_mode,
                                          course_grade=course_grade, generation_mode=generation_mode,
                                          delay_seconds=delay_seconds)

    status = _set_regular_cert_status(user, course_key, enrollment_mode, course_grade)
    if status is not None:
        return True

    return False


def _generate_certificate_task(user, course_key, enrollment_mode, course_grade, status=None, generation_mode=None,
                               delay_seconds=CERTIFICATE_DELAY_SECONDS):
    """
    Create a task to generate a certificate
    """
    log.info(f'About to create a regular certificate task for {user.id} : {course_key}')

    course_grade_val = _get_grade_value(course_grade)

    try:
        # .. filter_implemented_name: CertificateCreationRequested
        # .. filter_type: org.openedx.learning.certificate.creation.requested.v1
        user, course_key, enrollment_mode, status, course_grade, generation_mode = CertificateCreationRequested.run_filter(  # pylint: disable=line-too-long
            user=user,
            course_key=course_key,
            mode=enrollment_mode,
            status=status,
            grade=course_grade,
            generation_mode=generation_mode,
        )
    except CertificateCreationRequested.PreventCertificateCreation as exc:
        raise CertificateGenerationNotAllowed(str(exc)) from exc

    kwargs = {
        'student': str(user.id),
        'course_key': str(course_key),
        'enrollment_mode': str(enrollment_mode),
        'course_grade': str(course_grade_val)
    }
    if status is not None:
        kwargs['status'] = status
    if generation_mode is not None:
        kwargs['generation_mode'] = generation_mode

    generate_certificate.apply_async(countdown=delay_seconds, kwargs=kwargs)
    return True


def _can_generate_allowlist_certificate(user, course_key, enrollment_mode):
    """
    Check if an allowlist certificate can be generated (created if it doesn't already exist, or updated if it does
    exist) for this user, in this course run.
    """
    if not is_on_certificate_allowlist(user, course_key):
        log.info(f'{user.id} : {course_key} is not on the certificate allowlist. Allowlist certificate cannot be '
                 f'generated.')
        return False

    log.info(f'{user.id} : {course_key} is on the certificate allowlist')

    if not _can_generate_certificate_common(user, course_key, enrollment_mode):
        log.info(f'One of the common checks failed. Allowlist certificate cannot be generated for {user.id} : '
                 f'{course_key}.')
        return False

    log.info(f'Allowlist certificate can be generated for {user.id} : {course_key}')
    return True


def _can_generate_regular_certificate(user, course_key, enrollment_mode, course_grade):
    """
    Check if a regular (non-allowlist) course certificate can be generated (created if it doesn't already exist, or
    updated if it does exist) for this user, in this course run.
    """
    if _is_ccx_course(course_key):
        log.info(f'{course_key} is a CCX course. Certificate cannot be generated for {user.id}.')
        return False

    if is_beta_tester(user, course_key):
        log.info(f'{user.id} is a beta tester in {course_key}. Certificate cannot be generated.')
        return False

    if not _is_passing_grade(course_grade):
        log.info(f'{user.id} does not have a passing grade in {course_key}. Certificate cannot be generated.')
        return False

    if not _can_generate_certificate_common(user, course_key, enrollment_mode):
        log.info(f'One of the common checks failed. Certificate cannot be generated for {user.id} : {course_key}.')
        return False

    log.info(f'Regular certificate can be generated for {user.id} : {course_key}')
    return True


def _can_generate_certificate_common(user, course_key, enrollment_mode):
    """
    Check if a course certificate can be generated (created if it doesn't already exist, or updated if it does
    exist) for this user, in this course run.

    This method contains checks that are common to both allowlist and regular course certificates.
    """
    if CertificateInvalidation.has_certificate_invalidation(user, course_key):
        # The invalidation list prevents certificate generation
        log.info(f'{user.id} : {course_key} is on the certificate invalidation list. Certificate cannot be generated.')
        return False

    if enrollment_mode is None:
        log.info(f'{user.id} : {course_key} does not have an enrollment. Certificate cannot be generated.')
        return False

    is_eligible_for_cert = modes_api.is_eligible_for_certificate(enrollment_mode)
    if not is_eligible_for_cert:
        log.info(f'{user.id} : {course_key} has an enrollment mode of {enrollment_mode}, which is not eligible for a '
                 f'certificate. Certificate cannot be generated.')
        return False

    # If the IDV check fails we then check if the course-run requires ID verification. Honor and Professional-No-ID
    # modes do not require IDV for certificate generation.
    if _id_verification_enforced_and_missing(user):
        if enrollment_mode not in CourseMode.NON_VERIFIED_MODES:
            log.info(f'{user.id} does not have a verified id. Certificate cannot be generated for {course_key}.')
            return False

        log.info(f'{user.id} : {course_key} is eligible for a certificate without requiring a verified ID. '
                 'Skipping results of the ID verification check.')

    if not _can_generate_certificate_for_status(user, course_key, enrollment_mode):
        return False

    course_overview = get_course_overview_or_none(course_key)
    if not course_overview:
        log.info(f'{course_key} does not a course overview. Certificate cannot be generated for {user.id}.')
        return False

    if not has_html_certificates_enabled(course_overview):
        log.info(f'{course_key} does not have HTML certificates enabled. Certificate cannot be generated for '
                 f'{user.id}.')
        return False

    return True


def _set_allowlist_cert_status(user, course_key, enrollment_mode, course_grade):
    """
    Determine the allowlist certificate status for this user, in this course run and update the cert.

    This is used when a downloadable cert cannot be generated, but we want to provide more info about why it cannot
    be generated.
    """
    if not _can_set_allowlist_cert_status(user, course_key, enrollment_mode):
        return None

    cert = GeneratedCertificate.certificate_for_student(user, course_key)
    return _get_cert_status_common(user, course_key, enrollment_mode, course_grade, cert)


def _set_regular_cert_status(user, course_key, enrollment_mode, course_grade):
    """
    Determine the regular (non-allowlist) certificate status for this user, in this course run.

    This is used when a downloadable cert cannot be generated, but we want to provide more info about why it cannot
    be generated.
    """
    if not _can_set_regular_cert_status(user, course_key, enrollment_mode):
        return None

    cert = GeneratedCertificate.certificate_for_student(user, course_key)
    status = _get_cert_status_common(user, course_key, enrollment_mode, course_grade, cert)
    if status is not None:
        return status

    if not _id_verification_enforced_and_missing(user) \
            and not _is_passing_grade(course_grade) \
            and cert is not None:
        if cert.status != CertificateStatuses.notpassing:
            course_grade_val = _get_grade_value(course_grade)
            cert.mark_notpassing(mode=enrollment_mode, grade=course_grade_val, source='certificate_generation')
        return CertificateStatuses.notpassing

    return None


def _get_cert_status_common(user, course_key, enrollment_mode, course_grade, cert):
    """
    Determine the certificate status for this user, in this course run.

    This is used when a downloadable cert cannot be generated, but we want to provide more info about why it cannot
    be generated.
    """
    if CertificateInvalidation.has_certificate_invalidation(user, course_key) and cert is not None:
        if cert.status != CertificateStatuses.unavailable:
            cert.invalidate(mode=enrollment_mode, source='certificate_generation')
        return CertificateStatuses.unavailable

    if _id_verification_enforced_and_missing(user) and _has_passing_grade_or_is_allowlisted(
        user, course_key, course_grade
    ):
        if cert is None:
            _generate_certificate_task(user=user, course_key=course_key, enrollment_mode=enrollment_mode,
                                       course_grade=course_grade, status=CertificateStatuses.unverified,
                                       generation_mode='batch')
        elif cert.status != CertificateStatuses.unverified:
            cert.mark_unverified(mode=enrollment_mode, source='certificate_generation')
        return CertificateStatuses.unverified

    return None


def _can_set_allowlist_cert_status(user, course_key, enrollment_mode):
    """
    Determine whether we can set a custom (non-downloadable) cert status for an allowlist certificate
    """
    if not is_on_certificate_allowlist(user, course_key):
        return False

    return _can_set_cert_status_common(user, course_key, enrollment_mode)


def _can_set_regular_cert_status(user, course_key, enrollment_mode):
    """
    Determine whether we can set a custom (non-downloadable) cert status for a regular (non-allowlist) certificate
    """
    if _is_ccx_course(course_key):
        return False

    if is_beta_tester(user, course_key):
        return False

    return _can_set_cert_status_common(user, course_key, enrollment_mode)


def _can_set_cert_status_common(user, course_key, enrollment_mode):
    """
    Determine whether we can set a custom (non-downloadable) cert status
    """
    if _is_cert_downloadable(user, course_key):
        return False

    if enrollment_mode is None:
        return False

    if not modes_api.is_eligible_for_certificate(enrollment_mode):
        return False

    course_overview = get_course_overview_or_none(course_key)
    if not course_overview:
        return False

    if not has_html_certificates_enabled(course_overview):
        return False

    return True


def is_on_certificate_allowlist(user, course_key):
    """
    Check if the user is on the allowlist, and is enabled for the allowlist, for this course run
    """
    return CertificateAllowlist.objects.filter(user=user, course_id=course_key, allowlist=True).exists()


def _can_generate_certificate_for_status(user, course_key, enrollment_mode):
    """
    Check if the user's certificate status can handle regular (non-allowlist) certificate generation
    """
    cert = GeneratedCertificate.certificate_for_student(user, course_key)
    if cert is None:
        return True

    if cert.status == CertificateStatuses.downloadable:
        if not _is_mode_now_eligible(enrollment_mode, cert):
            log.info(f'Certificate with status {cert.status} already exists for {user.id} : {course_key}, and is not '
                     f'eligible for generation. Certificate cannot be generated as it is already in a final state. The '
                     f'current enrollment mode is {enrollment_mode} and the existing cert mode is {cert.mode}')
            return False

    log.info(f'Certificate with status {cert.status} already exists for {user.id} : {course_key}, and is eligible for '
             f'generation. The current enrollment mode is {enrollment_mode} and the existing cert mode is {cert.mode}')
    return True


def _is_ccx_course(course_key):
    """
    Check if the course is a CCX (custom edX course)
    """
    return hasattr(course_key, 'ccx')


def _has_passing_grade_or_is_allowlisted(user, course_key, course_grade):
    """
    Check if the user has a passing grade in this course run, or is on the allowlist and so is exempt from needing
    a passing grade.
    """
    if is_on_certificate_allowlist(user, course_key):
        return True

    return _is_passing_grade(course_grade)


def _is_passing_grade(course_grade):
    """
    Check if the grade is a passing grade
    """
    if course_grade:
        return course_grade.passed
    return False


def _get_grade_value(course_grade):
    """
    Get the user's course grade as a percent, or an empty string if there is no grade
    """
    if course_grade:
        return course_grade.percent
    return ''


def _get_course_grade(user, course_key):
    """
    Get the user's course grade in this course run. Note that this may be None.
    """
    return CourseGradeFactory().read(user, course_key=course_key)


def _get_enrollment_mode(user, course_key):
    """
    Get the user's enrollment mode for this course run. Note that this may be None.
    """
    enrollment_mode, __ = CourseEnrollment.enrollment_mode_for_user(user, course_key)
    return enrollment_mode


def _is_cert_downloadable(user, course_key):
    """
    Check if cert already exists, has a downloadable status, and has not been invalidated
    """
    cert = GeneratedCertificate.certificate_for_student(user, course_key)
    if cert is None:
        return False
    if cert.status != CertificateStatuses.downloadable:
        return False
    if CertificateInvalidation.has_certificate_invalidation(user, course_key):
        return False

    return True


def _is_mode_now_eligible(enrollment_mode, cert):
    """
    Check if the current enrollment mode is now eligible, while the enrollment mode on the cert is NOT eligible
    """
    if modes_api.is_eligible_for_certificate(enrollment_mode) and not modes_api.is_eligible_for_certificate(cert.mode):
        return True
    return False


def _id_verification_enforced_and_missing(user):
    """
    Return true if IDV is required for this course and the user does not have it
    """
    return settings.FEATURES.get(
        'ENABLE_CERTIFICATES_IDV_REQUIREMENT') and not IDVerificationService.user_is_verified(user)
