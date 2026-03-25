"""
Test helpers for the course experience.
"""


from datetime import timedelta  # noqa: I001

from django.core.exceptions import ObjectDoesNotExist
from django.utils.timezone import now

from common.djangoapps.course_modes.models import CourseMode

TEST_COURSE_PRICE = 50


def add_course_mode(course, mode_slug=CourseMode.VERIFIED,
                    mode_display_name='Verified Certificate', upgrade_deadline_expired=False):
    """
    Add a course mode to the test course.

    Args:
        course
        mode_slug (str): the slug of the mode to add
        mode_display_name (str): the display name of the mode to add
        upgrade_deadline_expired (bool): whether the upgrade deadline has passed
    """
    upgrade_exp_date = now()
    if upgrade_deadline_expired:
        upgrade_exp_date = upgrade_exp_date - timedelta(days=21)
    else:
        upgrade_exp_date = upgrade_exp_date + timedelta(days=21)

    CourseMode(
        course_id=course.id,
        mode_slug=mode_slug,
        mode_display_name=mode_display_name,
        min_price=TEST_COURSE_PRICE,
        _expiration_datetime=upgrade_exp_date,
    ).save()


def remove_course_mode(course, mode_slug):
    """
    Remove a course mode from the test course if it exists in the course.

    Args:
        course
        mode_slug (str): slug of the mode to remove
    """
    try:
        mode = CourseMode.objects.get(course_id=course.id, mode_slug=mode_slug)
    except ObjectDoesNotExist:
        pass

    mode.delete()
