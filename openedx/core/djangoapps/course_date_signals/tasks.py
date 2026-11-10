"""
Celery tasks for the course_date_signals app.
"""
from celery import shared_task
from celery.utils.log import get_task_logger
from edx_when.api import update_or_create_assignments_due_dates
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .utils import get_subsection_assignments

log = get_task_logger(__name__)


@shared_task(
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
)
def update_assignment_dates_for_course(course_key_str):
    """
    Sync a course's graded subsections into edx-when.

    ``course_published`` fires in Studio, so this task runs on the CMS worker. It reads the
    published course from the modulestore rather than the block-structure cache, which is
    rebuilt asynchronously and may still hold the previous course version, and needs
    neither a user nor LMS-only URLs. Assignments are written through
    ``update_or_create_assignments_due_dates``.
    """
    course_key = CourseKey.from_string(course_key_str)
    store = modulestore()
    with store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
        course = store.get_course(course_key)
        if not course:
            log.info("No course found for key %s to update assignment dates", course_key_str)
            return
        if course.self_paced:
            # extract_dates stores relative (PLS) due dates for self-paced courses, which
            # the absolute dates written below would replace for every learner.
            log.info("Skipping assignment dates update for self-paced course %s", course_key_str)
            return
        assignments = get_subsection_assignments(course)

    log.info("Starting to update assignment dates for course %s", course_key_str)
    update_or_create_assignments_due_dates(course_key, assignments)
    log.info("Successfully updated assignment dates for course %s", course_key_str)
