"""
Notification utilities for instructor tasks.

This module contains functions for sending email notifications about completed
instructor tasks (enrollments, grades, certificates, etc.).
"""

import json
import logging

from django.conf import settings
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.recipient import Recipient
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import EnrollStatusChange
from lms.djangoapps.instructor.message_types import BatchEnrollment
from lms.djangoapps.instructor_task.models import InstructorTask
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.core.lib.courses import get_course_by_id

TASK_LOG = logging.getLogger("edx.celery.task")


def _get_current_site() -> Site | None:
    """
    Get the current Django Site instance with fallback logic.

    Returns:
        Site | None: The current Site object or None if unavailable
    """
    # Try to get current site if method exists
    if hasattr(Site.objects, "get_current"):
        site = Site.objects.get_current()
        if site:
            return site

    # Fallback to SITE_ID from settings
    try:
        return Site.objects.get(id=settings.SITE_ID)
    except Site.DoesNotExist:
        pass

    # Last resort: get first site
    try:
        return Site.objects.first()
    except Exception:  # pylint: disable=broad-except
        return None


def _parse_task_input(instructor_task: InstructorTask) -> dict:
    """
    Parse and return the task input JSON from InstructorTask.

    Args:
        instructor_task (InstructorTask): The InstructorTask instance

    Returns:
        dict: Parsed task input or empty dict if parsing fails
    """
    try:
        return json.loads(instructor_task.task_input)
    except (json.JSONDecodeError, ValueError):
        return {}


def _get_action_display_name(action: str) -> str:
    """
    Get the localized display name for an enrollment action.

    Args:
        action (str): The enrollment action ('enroll' or 'unenroll')

    Returns:
        str: Localized action name
    """
    from django.utils.translation import gettext_lazy as _

    return _("enrollment") if action == EnrollStatusChange.enroll else _("unenrollment")


def _build_enrollment_email_context(
    course_key: CourseKey,
    requester,
    action: str,
    task_result: dict,
    site: Site | None,
    task_input: dict,
) -> dict:
    """
    Build the email context dictionary for enrollment completion email.

    Args:
        course_key (CourseKey): The course key
        requester (User): The user who initiated the task
        action (str): The enrollment action
        task_result (dict): Dictionary with task results
        site (Site | None): The current site object
        task_input (dict): Parsed task input dictionary

    Returns:
        dict: Complete context for email template
    """
    course = get_course_by_id(course_key)
    site_name = configuration_helpers.get_value("SITE_NAME", settings.SITE_NAME)
    secure = task_input.get("secure", True)
    protocol = "https" if secure else "http"

    context = {
        "action_name": _get_action_display_name(action),
        "course_name": course.display_name_with_default,
        "total_processed": task_result.get("total_processed", 0),
        "successful": task_result.get("successful", 0),
        "failed": task_result.get("failed", 0),
        "user_name": requester.username,
        "platform_name": settings.PLATFORM_NAME,
        "course_url": f"{protocol}://{site_name}/courses/{course_key}/",
    }

    # Add base template context
    context.update(get_base_template_context(site))

    return context


def send_enrollment_task_completion_email(
    course_key: CourseKey, instructor_task: InstructorTask, action: str, task_result: dict
) -> None:
    """
    Send a completion email to the user who initiated the enrollment batch task.

    Args:
        course_key (CourseKey): The course key
        instructor_task (InstructorTask): The InstructorTask object
        action (str): The action (e.g., 'enroll', 'unenroll')
        task_result (dict): Dictionary containing task completion results with keys:
            - total_processed: Total number of students processed
            - successful: Number of successful operations
            - failed: Number of failed operations
    """
    requester = instructor_task.requester
    site = _get_current_site()
    task_input = _parse_task_input(instructor_task)
    user_language = get_user_preference(requester, LANGUAGE_KEY)

    # Build email context
    user_context = _build_enrollment_email_context(
        course_key=course_key,
        requester=requester,
        action=action,
        task_result=task_result,
        site=site,
        task_input=task_input,
    )

    # Create and send message
    message = BatchEnrollment().personalize(
        recipient=Recipient(lms_user_id=requester.id, email_address=requester.email),
        language=user_language,
        user_context=user_context,
    )

    with emulate_http_request(site=site, user=requester):
        ace.send(message)

    TASK_LOG.info(
        "Enrollment task completion email sent via ACE to user %s (%s) for course %s. "
        "Action: %s, Results: %d successful, %d failed out of %d total",
        requester.username,
        requester.email,
        course_key,
        action,
        user_context["successful"],
        user_context["failed"],
        user_context["total_processed"],
    )
