"""
Utility functions around course dates.

get_expected_duration: return the expected duration of a course (absent any user information)
"""

from datetime import timedelta

from edx_when.api import Assignment
from openedx.core.djangoapps.catalog.utils import get_course_run_details


MIN_DURATION = timedelta(weeks=4)
MAX_DURATION = timedelta(weeks=18)


def to_edx_when_assignments(assignments):
    """
    Convert ``get_course_assignments`` output into ``edx_when.api.Assignment`` instances.

    Arguments:
        assignments: iterable of ``_Assignment`` namedtuples.

    Returns:
        list of ``edx_when.api.Assignment`` instances.
    """
    return [
        Assignment(
            title=assignment.title,
            date=assignment.date,
            block_key=assignment.block_key,
            subsection_name=assignment.title,
        )
        for assignment in assignments
    ]


def get_expected_duration(course_id):
    """
    Return a `datetime.timedelta` defining the expected length of the supplied course.
    """

    access_duration = MIN_DURATION

    # The user course expiration date is the content availability date
    # plus the weeks_to_complete field from course-discovery.
    discovery_course_details = get_course_run_details(course_id, ['weeks_to_complete'])
    expected_weeks = discovery_course_details.get('weeks_to_complete')
    if expected_weeks:
        access_duration = timedelta(weeks=expected_weeks)

    # Course access duration is bounded by the min and max duration.
    access_duration = max(MIN_DURATION, min(MAX_DURATION, access_duration))

    return access_duration


def spaced_out_sections(course):
    """
    Generator that returns sections of the course block with a suggested time to complete for each

    Returns:
        index (int): index of section
        section (block): a section block of the course
        relative time (timedelta): the amount of weeks to complete the section, since start of course
    """
    duration = get_expected_duration(course.id)
    sections = [
        section
        for section
        in course.get_children()
        if not section.visible_to_staff_only
    ]
    weeks_per_section = duration / len(sections)
    for idx, section in enumerate(sections):
        yield idx, section, weeks_per_section * (idx + 1)
