"""
Utility functions around course dates.

get_expected_duration: return the expected duration of a course (absent any user information)
"""

from datetime import timedelta

from django.conf import settings
from edx_when.api import Assignment

from openedx.core.djangoapps.catalog.models import CatalogIntegration
from openedx.core.djangoapps.catalog.utils import get_course_run_details
from xmodule.block_metadata_utils import display_name_with_default  # pylint: disable=wrong-import-order

MIN_DURATION = timedelta(weeks=settings.COURSE_ACCESS_DURATION_MIN_WEEKS)
MAX_DURATION = timedelta(weeks=settings.COURSE_ACCESS_DURATION_MAX_WEEKS)


def _catalog_integration_enabled():
    """
    Check if catalog integration is enabled
    """
    catalog_integration = CatalogIntegration.current()
    return catalog_integration.is_enabled()


def get_subsection_assignments(course):
    """
    Collect ``edx_when.api.Assignment`` instances for the graded subsections of a course.

    Reads the course block tree directly, so the result does not depend on any user's
    schedule or date overrides, nor on the block-structure cache. Only subsections with an
    explicitly set ``due`` are returned: these are exactly the dates ``extract_dates``
    stores for instructor-paced courses, so passing the same values to edx-when keeps the
    existing date policies unchanged.

    Arguments:
        course: the published course block.

    Returns:
        list of ``edx_when.api.Assignment`` instances.
    """
    assignments = []
    for section in course.get_children():
        for subsection in section.get_children():
            due_field = subsection.fields['due']
            if not (subsection.graded and due_field.is_set_on(subsection)):
                continue
            title = display_name_with_default(subsection)
            assignments.append(Assignment(
                title=title,
                date=due_field.read_from(subsection),
                block_key=subsection.location,
                subsection_name=title,
            ))
    return assignments


def get_expected_duration(course_id):
    """
    Return a `datetime.timedelta` defining the expected length of the supplied course.
    """

    access_duration = MIN_DURATION

    if _catalog_integration_enabled():
        discovery_course_details = get_course_run_details(
            course_id, ['weeks_to_complete']
        )
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
