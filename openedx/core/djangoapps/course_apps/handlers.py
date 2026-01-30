"""
Signal handlers for course apps.
"""
from django.db import transaction
from django.dispatch import receiver
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import SignalHandler

from .models import CourseAppStatus
from .signals import COURSE_APP_STATUS_INIT
from .tasks import update_course_apps_status


@receiver(SignalHandler.course_published)
def update_course_apps(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Whenever the course is published, update the status of course apps in the
    django models to match their status in the course.
    """
    # Adding transaction.on_commit to ensure that the course publish operations
    # are fully complete before we attempt to read the course data.
    transaction.on_commit(lambda: update_course_apps_status.delay(str(course_key)))


# pylint: disable=unused-argument
@receiver(COURSE_APP_STATUS_INIT)
def initialize_course_app_status(sender: str, course_key: CourseKey, is_enabled: bool, **kwargs):
    """
    Create a new CourseAppStatus object when a course app is accessed for the first time.

    For existing courses CourseAppStatus object might not exist. This handler creates a new
    CourseAppStatus object in such cases.

    Args:
        sender (str): The sender is the app_id for the app being accessed for the first time.
        course_key (CourseKey): The key for the course run the app is associated with
        is_enabled (bool): The status of the course app
    """
    CourseAppStatus.update_status_for_course_app(course_key=course_key, app_id=sender, enabled=is_enabled)
