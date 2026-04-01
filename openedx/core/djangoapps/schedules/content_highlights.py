"""
Contains methods for accessing course highlights. Course highlights is a
schedule experience built on the Schedules app.
"""


import logging

from openedx.core.djangoapps.course_date_signals.utils import spaced_out_sections
from openedx.core.djangoapps.schedules.exceptions import CourseUpdateDoesNotExist
from openedx.core.lib.request_utils import get_request_or_stub
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


def get_all_course_highlights(course_key):
    """
    This ignores access checks, since highlights may be lurking in currently
    inaccessible content.
    Returns a list of all the section highlights in the course
    """
    try:
        course = _get_course_with_highlights(course_key)

    except CourseUpdateDoesNotExist:
        return []
    else:
        highlights = [section.highlights for section in course.get_children() if not section.hide_from_toc]
        return highlights


def course_has_highlights(course):
    """
    Does the course have any highlights for any section/week in it?
    This ignores access checks, since highlights may be lurking in currently
    inaccessible content.

    Arguments:
        course (CourseBlock): course block to check
    """
    if not course.highlights_enabled_for_messaging:
        return False

    else:
        highlights_are_available = any(
            section.highlights
            for section in course.get_children()
            if not section.hide_from_toc
        )

        if not highlights_are_available:
            log.warning(
                f'Course team enabled highlights and provided no highlights in {course.id}'
            )

        return highlights_are_available


def course_has_highlights_from_store(course_key):
    """
    Does the course have any highlights for any section/week in it?
    This ignores access checks, since highlights may be lurking in currently
    inaccessible content.

    Arguments:
        course_key (CourseKey): course to lookup from the modulestore
    """
    try:
        course = _get_course_descriptor(course_key)
    except CourseUpdateDoesNotExist:
        return False
    return course_has_highlights(course)


def get_week_highlights(user, course_key, week_num):
    """
    Get highlights (list of unicode strings) for a given week.
    week_num starts at 1.

    Raises:
        CourseUpdateDoesNotExist: if highlights do not exist for
            the requested week_num.
    """
    course_descriptor = _get_course_with_highlights(course_key)
    course_block = _get_course_block(course_descriptor, user)
    sections_with_highlights = _get_sections_with_highlights(course_block)
    highlights = _get_highlights_for_week(
        sections_with_highlights,
        week_num,
        course_key,
    )
    return highlights


def get_next_section_highlights(user, course_key, start_date, target_date):
    """
    Get highlights (list of unicode strings) for a week, based upon the current date.

    Raises:
        CourseUpdateDoeNotExist: if highlights do not exist for the requested date
    """
    course_descriptor = _get_course_with_highlights(course_key)
    course_block = _get_course_block(course_descriptor, user)
    return _get_highlights_for_next_section(course_block, start_date, target_date)


def _get_course_with_highlights(course_key):
    """ Gets Course descriptor if highlights are enabled for the course """
    course_descriptor = _get_course_descriptor(course_key)
    if not course_descriptor.highlights_enabled_for_messaging:
        raise CourseUpdateDoesNotExist(
            f'{course_key} Course Update Messages are disabled.'
        )

    return course_descriptor


def _get_course_descriptor(course_key):
    """ Gets course descriptor from modulestore """
    descriptor = modulestore().get_course(course_key, depth=1)
    if descriptor is None:
        raise CourseUpdateDoesNotExist(
            f'Course {course_key} not found.'
        )
    return descriptor


def _get_course_block(course_descriptor, user):
    """ Gets course block that takes into account user state and permissions """
    # Adding courseware imports here to insulate other apps (e.g. schedules) to
    # avoid import errors.
    # TODO remove the LMS dependency https://github.com/openedx/edx-platform/issues/37659
    from lms.djangoapps.courseware.model_data import FieldDataCache
    from lms.djangoapps.courseware.block_render import get_block_for_descriptor

    # Fake a request to fool parts of the courseware that want to inspect it.
    request = get_request_or_stub()
    request.user = user

    # Now evil modulestore magic to inflate our block with user state and
    # permissions checks.
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        course_descriptor.id, user, course_descriptor, depth=1, read_only=True,
    )
    course_block = get_block_for_descriptor(
        user, request, course_descriptor, field_data_cache, course_descriptor.id, course=course_descriptor,
    )
    if not course_block:
        raise CourseUpdateDoesNotExist(f'Course block {course_descriptor.id} not found')
    return course_block


def _section_has_highlights(section):
    """ Returns if the section has highlights """
    return section.highlights and not section.hide_from_toc


def _get_sections_with_highlights(course_block):
    """ Returns all sections that have highlights in a course """
    return list(filter(_section_has_highlights, course_block.get_children()))


def _get_highlights_for_week(sections, week_num, course_key):
    """ Gets highlights from the section at week num """
    # assume each provided section maps to a single week
    num_sections = len(sections)
    if not 1 <= week_num <= num_sections:
        raise CourseUpdateDoesNotExist(
            'Requested week {} but {} has only {} sections.'.format(
                week_num, course_key, num_sections
            )
        )

    section = sections[week_num - 1]
    return section.highlights


def _get_highlights_for_next_section(course, start_date, target_date):
    """ Using the target date, retrieves highlights for the next section. """
    use_next_sections_highlights = False
    for index, section, weeks_to_complete in spaced_out_sections(course):
        # We calculate section due date ourselves (rather than grabbing the due attribute),
        # since not every section has a real due date (i.e. not all are graded), but we still
        # want to know when this section should have been completed by the learner.
        section_due_date = start_date + weeks_to_complete

        if section_due_date.date() == target_date:
            use_next_sections_highlights = True
        elif use_next_sections_highlights and not _section_has_highlights(section):
            raise CourseUpdateDoesNotExist(
                f'Next section [{section.display_name}] has no highlights for {course.id}'
            )
        elif use_next_sections_highlights:
            return section.highlights, index + 1

    if use_next_sections_highlights:
        raise CourseUpdateDoesNotExist(
            f'Last section was reached. There are no more highlights for {course.id}'
        )

    return None, None
