"""
This module contains utility functions for grading.
"""


import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from .config.waffle import ENFORCE_FREEZE_GRADE_AFTER_COURSE_END

log = logging.getLogger(__name__)


def are_grades_frozen(course_key):
    """
    Returns whether grades are frozen for the given course.
    """
    if ENFORCE_FREEZE_GRADE_AFTER_COURSE_END.is_enabled(course_key):
        course = CourseOverview.get_from_id(course_key)
        if course.end:
            freeze_grade_date = course.end + timedelta(settings.GRADEBOOK_FREEZE_DAYS)
            now = timezone.now()
            return now > freeze_grade_date
    return False
