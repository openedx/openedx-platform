"""
Signal handlers for the contentstore shim.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from openedx_catalog.models_api import CourseRun

from .api import clear_course_asset_backend_status_cache
from .models import CourseAssetBackend


@receiver([post_save, post_delete], sender=CourseAssetBackend)
def _clear_cached_status(sender, instance: CourseAssetBackend, **kwargs):  # pylint: disable=unused-argument
    """
    Clear the cached status of a course run when its status changes.
    """
    try:
        course_key = instance.course_run.course_key
    except CourseRun.DoesNotExist:
        return  # The course run is being deleted; _clear_cached_status_for_course_run will handle it.
    clear_course_asset_backend_status_cache(course_key)


@receiver(post_delete, sender=CourseRun)
def _clear_cached_status_for_course_run(sender, instance: CourseRun, **kwargs):  # pylint: disable=unused-argument
    """
    Clear the cached status when a course run is deleted.

    Otherwise, a new course run created with the same key could get the deleted
    course run's cached status.
    """
    clear_course_asset_backend_status_cache(instance.course_key)
