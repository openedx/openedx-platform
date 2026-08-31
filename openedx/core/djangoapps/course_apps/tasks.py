"""
This file contains celery tasks for course apps.
"""

from celery import shared_task
from celery.utils.log import get_task_logger
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_apps.models import CourseAppStatus
from openedx.core.djangoapps.course_apps.plugins import CourseAppsPluginManager

log = get_task_logger(__name__)


@shared_task(name='openedx.core.djangoapps.course_apps.tasks.cache_all_course_apps_status')
def cache_all_course_apps_status():
    """
    Create CourseAppStatus entries for all course apps, across all courses to speed up queries.
    """
    for idx, course_key in enumerate(CourseOverview.objects.values_list('id', flat=True)):
        if isinstance(course_key, LibraryLocator):
            continue
        update_course_apps_status.delay(str(course_key))
        if (idx + 1) % 100 == 0:
            log.info("Cached course app status for %s courses successfully", idx + 1)
    log.info("Cached course app status for all courses successfully")


@shared_task(name='openedx.core.djangoapps.course_apps.tasks.update_course_apps_status')
def update_course_apps_status(course_key_str: str):
    """
    Create CourseAppStatus entries for apps available for the specified course.
    """
    course_key = CourseKey.from_string(course_key_str)
    course_apps = CourseAppsPluginManager.get_apps_available_for_course(course_key)
    log.info("Caching course apps status for course with id: %s", course_key)
    for course_app in course_apps:
        is_enabled = course_app.is_enabled(course_key=course_key)
        CourseAppStatus.update_status_for_course_app(
            course_key=course_key,
            app_id=course_app.app_id,
            enabled=is_enabled,
        )
