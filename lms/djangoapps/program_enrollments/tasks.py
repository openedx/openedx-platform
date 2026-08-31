""" Tasks for program enrollments """


import logging
from datetime import timedelta

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.utils import timezone

from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment, ProgramEnrollment

log = logging.getLogger(__name__)


@shared_task(base=LoggedTask)
def expire_waiting_enrollments(expiration_days):
    """
    Remove all ProgramEnrollments and related ProgramCourseEnrollments for
    ProgramEnrollments not modified for <expiration_days>
    """
    expiry_date = timezone.now() - timedelta(days=expiration_days)

    program_enrollments = ProgramEnrollment.objects.filter(
        user=None,
        modified__lte=expiry_date
    ).prefetch_related('program_course_enrollments')

    program_enrollment_ids = []
    program_course_enrollment_ids = []
    for program_enrollment in program_enrollments:
        program_enrollment_ids.append(program_enrollment.id)
        log.info(
            'Found expired program_enrollment (id=%s) for program_uuid=%s',
            program_enrollment.id,
            program_enrollment.program_uuid,
        )
        for course_enrollment in program_enrollment.program_course_enrollments.all():
            program_course_enrollment_ids.append(course_enrollment.id)
            log.info(
                'Found expired program_course_enrollment (id=%s) for program_uuid=%s, course_key=%s',
                course_enrollment.id,
                program_enrollment.program_uuid,
                course_enrollment.course_key,
            )

    deleted_enrollments = program_enrollments.delete()
    log.info('Removed %s expired records: %s', deleted_enrollments[0], deleted_enrollments[1])

    deleted_hist_program_enroll = ProgramEnrollment.historical_records.filter(  # pylint: disable=no-member
        id__in=program_enrollment_ids
    ).delete()
    deleted_hist_course_enroll = ProgramCourseEnrollment.historical_records.filter(  # pylint: disable=no-member
        id__in=program_course_enrollment_ids
    ).delete()
    log.info(
        'Removed %s historical program_enrollment records with id in %s',
        deleted_hist_program_enroll[0], program_enrollment_ids
    )
    log.info(
        'Removed %s historical program_course_enrollment records with id in %s',
        deleted_hist_course_enroll[0], program_course_enrollment_ids
    )
