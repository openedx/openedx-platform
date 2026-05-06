"""
This file contains celery tasks for credit course views.
"""


from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

from openedx.core.djangoapps.credit.api import set_credit_requirements
from openedx.core.djangoapps.credit.exceptions import InvalidCreditRequirements
from openedx.core.djangoapps.credit.models import CreditCourse
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

LOGGER = get_task_logger(__name__)


@shared_task(default_retry_delay=settings.CREDIT_TASK_DEFAULT_RETRY_DELAY, max_retries=settings.CREDIT_TASK_MAX_RETRIES)
@set_code_owner_attribute
def update_credit_course_requirements(course_id):
    """
    Updates course requirements table for a course.

     Args:
        course_id(str): A string representation of course identifier

    Returns:
        None

    """
    try:
        course_key = CourseKey.from_string(course_id)
        is_credit_course = CreditCourse.is_credit_course(course_key)
        if is_credit_course:
            requirements = _get_course_credit_requirements(course_key)
            set_credit_requirements(course_key, requirements)
    except (InvalidKeyError, ItemNotFoundError, InvalidCreditRequirements) as exc:
        LOGGER.error('Error on adding the requirements for course %s - %s', course_id, str(exc))
        raise update_credit_course_requirements.retry(args=[course_id], exc=exc)  # noqa: B904
    LOGGER.info('Requirements added for course %s', course_id)


def _get_course_credit_requirements(course_key):
    """
    Returns the list of credit requirements for the given course.

    This will also call into the edx-proctoring subsystem to also
    produce proctored exam requirements for credit bearing courses

    It returns the minimum_grade_credit and also the ICRV checkpoints
    if any were added in the course

    Args:
        course_key (CourseKey): Identifier for the course.

    Returns:
        List of credit requirements (dictionaries)

    """
    min_grade_requirement = _get_min_grade_requirement(course_key)
    proctored_exams_requirements = _get_proctoring_requirements(course_key)
    sorted_exam_requirements = sorted(
        proctored_exams_requirements, key=lambda x: (x['start_date'] is None, x['start_date'], x['display_name'])
    )

    credit_requirements = (
        min_grade_requirement + sorted_exam_requirements
    )
    return credit_requirements


def _get_min_grade_requirement(course_key):
    """
    Get list of 'minimum_grade_credit' requirement for the given course.

    Args:
        course_key (CourseKey): Identifier for the course.

    Returns:
        The list of minimum_grade_credit requirements

    """
    course = modulestore().get_course(course_key, depth=0)
    try:
        return [
            {
                "namespace": "grade",
                "name": "grade",
                "display_name": "Minimum Grade",
                "criteria": {
                    "min_grade": course.minimum_grade_credit
                },
            }
        ]
    except AttributeError:
        LOGGER.error("The course %s does not has minimum_grade_credit attribute", str(course.id))
    else:
        return []


def _get_proctoring_requirements(course_key):
    """
    Will return list of requirements regarding any exams that have been
    marked as proctored exams. For credit-bearing courses, all
    proctored exams must be validated and confirmed from a proctoring
    standpoint. The passing grade on an exam is not enough.

    Args:
        course_key: The key of the course in question

    Returns:
        list of requirements dictionary, one per active proctored exam

    """

    # Note: Need to import here as there appears to be
    # a circular reference happening when launching Studio
    # process
    from edx_proctoring.api import get_all_exams_for_course

    requirements = []
    for exam in get_all_exams_for_course(str(course_key)):
        if exam['is_proctored'] and exam['is_active'] and not exam['is_practice_exam']:
            try:
                usage_key = UsageKey.from_string(exam['content_id'])
                proctor_block = modulestore().get_item(usage_key)
            except (InvalidKeyError, ItemNotFoundError):
                LOGGER.info("Invalid content_id '%s' for proctored block '%s'", exam['content_id'], exam['exam_name'])
                proctor_block = None

            if proctor_block:
                requirements.append(
                    {
                        'namespace': 'proctored_exam',
                        'name': exam['content_id'],
                        'display_name': exam['exam_name'],
                        'start_date': proctor_block.start if proctor_block.start else None,
                        'criteria': {},
                    })

    if requirements:
        log_msg = (
            'Registering the following as \'proctored_exam\' credit requirements: {log_msg}'.format(  # noqa: UP032
                log_msg=requirements
            )
        )
        LOGGER.info(log_msg)

    return requirements
