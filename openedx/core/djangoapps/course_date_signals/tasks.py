"""
Celery tasks for the course_date_signals app.
"""
from celery import shared_task
from celery.utils.log import get_task_logger
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute
from edx_when.api import update_or_create_assignments_due_dates
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.courses import get_course_assignments

from .utils import to_edx_when_assignments


User = get_user_model()


log = get_task_logger(__name__)


@shared_task(
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
)
@set_code_owner_attribute
def update_assignment_dates_for_course(course_key_str):
    """
    Sync a course's assignment due dates into edx-when.

    Resolves graded assignments via ``get_course_assignments`` (needs a staff user)
    and writes them through ``update_or_create_assignments_due_dates``.
    """
    course_key = CourseKey.from_string(course_key_str)
    staff_user = User.objects.filter(is_staff=True).first()
    if not staff_user:
        raise RuntimeError(
            "No staff user found to update assignment dates for course %s" % course_key_str
        )
    log.info("Starting to update assignment dates for course %s", course_key_str)
    assignments = get_course_assignments(course_key, staff_user)
    update_or_create_assignments_due_dates(course_key, to_edx_when_assignments(assignments))
    log.info("Successfully updated assignment dates for course %s", course_key_str)
