"""
Handlers for instructor
"""
import logging  # noqa: I001

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.dispatch import receiver
from openedx_events.learning.signals import EXAM_ATTEMPT_RESET, EXAM_ATTEMPT_SUBMITTED

from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor import enrollment
from lms.djangoapps.instructor.tasks import update_exam_completion_task

User = get_user_model()

log = logging.getLogger(__name__)


@receiver(EXAM_ATTEMPT_SUBMITTED)
def handle_exam_completion(sender, signal, **kwargs):
    """
    exam completion event from the event bus
    """
    event_data = kwargs.get('exam_attempt')
    user_data = event_data.student_user
    usage_key = event_data.usage_key

    update_exam_completion_task.apply_async((user_data.pii.username, str(usage_key), 1.0))


@receiver(EXAM_ATTEMPT_RESET)
def handle_exam_reset(sender, signal, **kwargs):
    """
    exam reset event from the event bus
    """
    event_data = kwargs.get('exam_attempt')
    user_data = event_data.student_user
    requesting_user_data = event_data.requesting_user
    usage_key = event_data.usage_key
    course_key = event_data.course_key
    content_id = str(usage_key)

    try:
        student = User.objects.get(id=user_data.id)
    except ObjectDoesNotExist:
        log.error(
            'Error occurred while attempting to reset student attempt for user_id '
            f'{user_data.id} for content_id {content_id}. '
            'User does not exist!'
        )
        return

    try:
        requesting_user = User.objects.get(id=requesting_user_data.id)
    except ObjectDoesNotExist:
        log.error(
            'Error occurred while attempting to reset student attempt. Requesting user_id '
            f'{requesting_user_data.id} does not exist!'
        )
        return

    # reset problem state
    try:
        enrollment.reset_student_attempts(
            course_key,
            student,
            usage_key,
            requesting_user=requesting_user,
            delete_module=True,
        )
    except (StudentModule.DoesNotExist, enrollment.sub_api.SubmissionError):
        log.error(
            'Error occurred while attempting to reset module state for user_id '
            f'{student.id} for content_id {content_id}.'
        )

    # In some cases, reset_student_attempts does not clear the entire exam's completion state.
    # One example of this is an exam with multiple units (verticals) within it and the learner
    # never viewing one of the units. All of the content in that unit will still be marked complete,
    # but the reset code is unable to handle clearing the completion in that scenario.
    update_exam_completion_task.apply_async((student.username, content_id, 0.0))
