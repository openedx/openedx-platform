""" Celery Tasks for the Instructor App. """

import logging
from datetime import datetime

from celery import shared_task
from completion.models import BlockCompletion
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.recipient import Recipient
from edx_django_utils.monitoring import set_code_owner_attribute

from common.djangoapps.student.models.course_enrollment import CourseEnrollment
from common.djangoapps.student.models.user import get_user_by_username_or_email
from lms.djangoapps.courseware.courses import get_course
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.api import clear_user_course_grades
from lms.djangoapps.instructor.enrollment import reset_student_attempts
from lms.djangoapps.support.message_types import WholeCourseReset
from lms.djangoapps.support.models import CourseResetAudit
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request

log = logging.getLogger(__name__)


def update_audit_status(audit_instance, status):
    audit_instance.status = status
    if status == CourseResetAudit.CourseResetStatus.COMPLETE:
        audit_instance.completed_at = datetime.now()
    audit_instance.save()


def get_blocks(course):
    """ Get a list of problem xblock for the course."""
    blocks = []
    for section in course.get_children():
        for subsection in section.get_children():
            for vertical in subsection.get_children():
                for block in vertical.get_children():
                    blocks.append(block)
    return blocks


@shared_task
@set_code_owner_attribute
def send_reset_course_completion_email(course, user):
    """
    Sends email to a learner when whole course reset is complete.
    """
    site = Site.objects.get_current()

    message_context = get_base_template_context(site)
    message_context.update({
        'course_title': course.display_name,
    })

    try:
        log.info(
            f"Sending whole course reset email to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id})"
        )
        with emulate_http_request(site=site, user=user):
            msg = WholeCourseReset(context=message_context).personalize(
                recipient=Recipient(user.id, user.email),
                language=get_user_preference(user, LANGUAGE_KEY),
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            f"Whole course reset email to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id}) failed."
            f"Error: {exc.response['Error']['Code']}"
        )
        return False
    else:
        log.info(
            f"Whole course reset email sent successfully to {user.profile.name} (Email: {user.email}) "
            f"from course {course.display_name} (CourseId: {course.id})"
        )
        return True


@shared_task
@set_code_owner_attribute
def reset_student_course(course_id, learner_email, reset_by_user_email):
    """
    Resets a learner's course progress
    """
    user = get_user_by_username_or_email(learner_email)
    reset_by_user = get_user_by_username_or_email(reset_by_user_email)
    enrollment = CourseEnrollment.objects.get(
        course=course_id,
        user=user,
        is_active=True,
    )
    course_overview = enrollment.course_overview
    course_reset_audit = CourseResetAudit.objects.get(
        course_enrollment=enrollment,
        status=CourseResetAudit.CourseResetStatus.ENQUEUED
    )
    update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.IN_PROGRESS)

    try:
        course = get_course(course_overview.id, depth=4)
        blocks = get_blocks(course)

        # Clear student state and score
        for data in blocks:
            try:
                reset_student_attempts(
                    course.id,
                    user,
                    data.scope_ids.usage_id,
                    reset_by_user,
                    delete_module=True,
                    emit_signals_and_events=False
                )
            except StudentModule.DoesNotExist:
                pass

        # Clear block completion data
        BlockCompletion.objects.clear_learning_context_completion(user, course.id)
        # Clear a student grades for a course
        clear_user_course_grades(user.id, course.id)

        update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.COMPLETE)

        # Send email upon completion
        send_reset_course_completion_email(course, user)

    except Exception as e:  # pylint: disable=broad-except
        logging.exception(f'Error occurred for Course Audit with ID {course_reset_audit.id}: {e}.')
        update_audit_status(course_reset_audit, CourseResetAudit.CourseResetStatus.FAILED)
