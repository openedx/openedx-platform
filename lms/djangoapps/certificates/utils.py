"""
Certificates utilities
"""
import logging
from datetime import datetime

from django.conf import settings
from django.urls import reverse
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey
from pytz import utc

from common.djangoapps.student import models_api as student_api
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service
from xmodule.data import CertificatesDisplayBehaviors  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


def emit_certificate_event(event_name, user, course_id, course_overview=None, event_data=None):
    """
    Utility function responsible for emitting certificate events.

    We currently track the following events:
    - `edx.certificate.created` - Emit when a course certificate with the `downloadable` status has been awarded to a
                                  learner.
    - `edx.certificate.revoked`- Emit when a course certificate with the `downloadable` status has been taken away from
                                 a learner.
    - `edx.certificate.shared` - Emit when a learner shares their course certificate to social media (LinkedIn,
                                 Facebook, or Twitter).
    - `edx.certificate.evidence_visited` - Emit when a user (other than the learner who owns a certificate) views a
                                           course certificate (e.g., someone views a course certificate shared on a
                                           LinkedIn profile).

    Args:
        event_name (String) - Text describing the action/event that we are tracking. Examples include `revoked`,
                              `created`, etc.
        user (User) - The User object of the learner associated with this event.
        course_id (CourseLocator) - The course-run key associated with this event.
        course_overview (CourseOverview) - Optional. The CourseOverview of the course-run associated with this event.
        event_data (dictionary) - Optional. Dictionary containing any additional data we want to be associated with an
                                  event.
    """
    event_name = '.'.join(['edx', 'certificate', event_name])

    if not course_overview:
        course_overview = get_course_overview_or_none(course_id)

    context = {
        'org_id': course_overview.org,
        'course_id': str(course_id)
    }

    data = {
        'user_id': user.id,
        'course_id': str(course_id),
        'certificate_url': get_certificate_url(user.id, course_id, uuid=event_data['certificate_id'])
    }
    event_data = event_data or {}
    event_data.update(data)

    with tracker.get_tracker().context(event_name, context):
        tracker.emit(event_name, event_data)


def get_certificate_url(user_id=None, course_id=None, uuid=None, user_certificate=None):
    """
    Returns the certificate URL
    """
    url = ''

    course_overview = get_course_overview_or_none(_safe_course_key(course_id))
    if not course_overview:
        return url

    if has_html_certificates_enabled(course_overview):
        url = _certificate_html_url(uuid)
    else:
        url = _certificate_download_url(user_id, course_id, user_certificate=user_certificate)
    return url


def has_html_certificates_enabled(course_overview):
    """
    Returns True if HTML certificates are enabled in a course run.
    """
    if not settings.FEATURES.get('CERTIFICATES_HTML_VIEW', False):
        return False
    return course_overview.cert_html_view_enabled


def _certificate_html_url(uuid):
    """
    Returns uuid based certificate URL.
    """
    return reverse(
        'certificates:render_cert_by_uuid', kwargs={'certificate_uuid': uuid}
    ) if uuid else ''


def _certificate_download_url(user_id, course_id, user_certificate=None):
    """
    Returns the certificate download URL
    """
    if not user_certificate:
        try:
            user_certificate = GeneratedCertificate.eligible_certificates.get(
                user=user_id,
                course_id=_safe_course_key(course_id)
            )
        except GeneratedCertificate.DoesNotExist:
            log.critical(
                'Unable to lookup certificate\n'
                'user id: %s\n'
                'course: %s', str(user_id), str(course_id)
            )

    if user_certificate:
        return user_certificate.download_url

    return ''


def _safe_course_key(course_key):
    """
    Returns the course key
    """
    if not isinstance(course_key, CourseKey):
        return CourseKey.from_string(course_key)
    return course_key


def should_certificate_be_visible(
    certificates_display_behavior,
    certificates_show_before_end,
    has_ended,
    certificate_available_date,
    self_paced
):
    """
    Returns whether it is acceptable to show the student a certificate download
    link for a course, based on provided attributes of the course.
    Arguments:
        certificates_display_behavior (str): string describing the course's
            certificate display behavior.
            See CourseFields.certificates_display_behavior.help for more detail.
        certificates_show_before_end (bool): whether user can download the
            course's certificates before the course has ended.
        has_ended (bool): Whether the course has ended.
        certificate_available_date (datetime): the date the certificate is available on for the course.
        self_paced (bool): Whether the course is self-paced.
    """
    show_early = (
        certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO
        or certificates_show_before_end
    )
    past_available_date = (
        certificates_display_behavior == CertificatesDisplayBehaviors.END_WITH_DATE
        and certificate_available_date
        and certificate_available_date < datetime.now(utc)
    )
    ended_without_available_date = (
        certificates_display_behavior == CertificatesDisplayBehaviors.END
        and has_ended
    )

    return any((self_paced, show_early, past_available_date, ended_without_available_date))


def certificate_status(generated_certificate):
    """
    This returns a dictionary with a key for status, and other information.

    If the status is "downloadable", the dictionary also contains
    "download_url".

    If the student has been graded, the dictionary also contains their
    grade for the course with the key "grade".
    """
    # Import here instead of top of file since this module gets imported before
    # the course_modes app is loaded, resulting in a Django deprecation warning.
    from common.djangoapps.course_modes.models import CourseMode  # pylint: disable=redefined-outer-name, reimported

    if generated_certificate:
        cert_status = {
            'status': generated_certificate.status,
            'mode': generated_certificate.mode,
            'uuid': generated_certificate.verify_uuid,
        }
        if generated_certificate.grade:
            cert_status['grade'] = generated_certificate.grade

        if generated_certificate.mode == 'audit':
            course_mode_slugs = [mode.slug for mode in CourseMode.modes_for_course(generated_certificate.course_id)]
            # Short term fix to make sure old audit users with certs still see their certs
            # only do this if there if no honor mode
            if 'honor' not in course_mode_slugs:
                cert_status['status'] = CertificateStatuses.auditing
                return cert_status

        if generated_certificate.status == CertificateStatuses.downloadable:
            cert_status['download_url'] = generated_certificate.download_url

        return cert_status
    else:
        return {'status': CertificateStatuses.unavailable, 'mode': GeneratedCertificate.MODES.honor, 'uuid': None}


def certificate_status_for_student(student, course_id):
    """
    This returns a dictionary with a key for status, and other information.
    See certificate_status for more information.
    """
    try:
        generated_certificate = GeneratedCertificate.objects.get(user=student, course_id=course_id)
    except GeneratedCertificate.DoesNotExist:
        generated_certificate = None
    return certificate_status(generated_certificate)


def get_preferred_certificate_name(user):
    """
    If the verified name feature is enabled and the user has their preference set to use their
    verified name for certificates, return their verified name. Else, return the user's profile
    name, or an empty string if it doesn't exist.
    """
    name_to_use = student_api.get_name(user.id)
    name_affirmation_service = get_name_affirmation_service()

    if name_affirmation_service and name_affirmation_service.should_use_verified_name_for_certs(user):
        verified_name_obj = name_affirmation_service.get_verified_name(user, is_verified=True)
        if verified_name_obj:
            name_to_use = verified_name_obj.verified_name

    if not name_to_use:
        name_to_use = ''

    return name_to_use
