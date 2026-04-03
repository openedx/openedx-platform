"""
This file contains celery tasks for ccxcon
"""


from celery import shared_task
from celery.utils.log import get_task_logger
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey
from requests.exceptions import (  # lint-amnesty, pylint: disable=redefined-builtin
    ConnectionError,
    HTTPError,
    RequestException,
    TooManyRedirects,
)

from openedx.core.djangoapps.ccxcon import api

log = get_task_logger(__name__)


@shared_task(name='openedx.core.djangoapps.ccxcon.tasks.update_ccxcon')
@set_code_owner_attribute
def update_ccxcon(course_id, cur_retry=0):
    """
    Pass through function to update course information on CCXCon.
    Takes care of retries in case of some specific exceptions.

    Args:
        course_id (str): string representing a course key
        cur_retry (int): integer representing the current task retry
    """
    course_key = CourseKey.from_string(course_id)
    try:
        api.course_info_to_ccxcon(course_key)
        log.info('Course update to CCXCon returned no errors. Course key: %s', course_id)
    except (ConnectionError, HTTPError, RequestException, TooManyRedirects, api.CCXConnServerError) as exp:
        log.error('Course update to CCXCon failed for course_id %s with error: %s', course_id, exp)
        # in case the maximum amount of retries has not been reached,
        # insert another task delayed exponentially up to 5 retries
        if cur_retry < 5:
            update_ccxcon.apply_async(
                kwargs={'course_id': course_id, 'cur_retry': cur_retry + 1},
                countdown=10 ** cur_retry  # number of seconds the task should be delayed
            )
            log.info('Requeued celery task for course key %s ; retry # %s', course_id, cur_retry + 1)
