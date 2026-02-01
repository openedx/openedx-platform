"""Signal handlers for writing course dates into edx_when."""


from datetime import timedelta
import logging

from django.db import transaction
from django.dispatch import receiver
from edx_when.api import FIELDS_TO_EXTRACT, set_dates_for_course
from xblock.fields import Scope

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from openedx.core.lib.graph_traversals import get_children, leaf_filter, traverse_pre_order
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import SignalHandler, modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.util.misc import is_xblock_an_assignment  # lint-amnesty, pylint: disable=wrong-import-order

from .models import SelfPacedRelativeDatesConfig
from .utils import spaced_out_sections

log = logging.getLogger(__name__)


def _field_values(fields, xblock):
    """
    Read field values for the specified fields from the supplied xblock.
    """
    result = {}
    for field_name in fields:
        if field_name not in xblock.fields:
            continue
        field = xblock.fields[field_name]
        if field.scope == Scope.settings and field.is_set_on(xblock):
            try:
                result[field.name] = field.read_from(xblock)
            except TypeError as exception:
                exception_message = "{message}, Block-location:{location}, Field-name:{field_name}".format(
                    message=str(exception),
                    location=str(xblock.location),
                    field_name=field.name
                )
                raise TypeError(exception_message)  # lint-amnesty, pylint: disable=raise-missing-from
    return result


def _has_assignment_blocks(item):
    """
    Check if a given block contains children that are assignments.
    Assignments have graded, has_score and nonzero weight attributes.
    """
    return any(
        is_xblock_an_assignment(block)
        for block in traverse_pre_order(item, get_children, leaf_filter)
    )


def _gather_graded_items(root, due):  # lint-amnesty, pylint: disable=missing-function-docstring
    items = [root]
    has_non_ora_scored_content = False
    collected_items = []
    while items:
        next_item = items.pop()
        if next_item.graded:
            # Sequentials can be marked as graded, while only containing ungraded problems
            # To handle this case, we can look at the leaf blocks within a sequential
            # and check that they are a graded assignment
            # (if they have graded/has_score attributes and nonzero weight).
            # Open response assessments (ORA) contain their own set of due dates
            # and we do not want to potentially conflict with due dates that are set from Studio.
            # So here we do not assign a due date to items that are ORA.
            if next_item.category != 'openassessment':
                collected_items.append((
                    next_item.location,
                    {'due': due if _has_assignment_blocks(next_item) else None}
                ))
            # TODO: This is pretty gross, and should maybe be configurable in the future,
            # especially if we find ourselves needing more exceptions.
            has_non_ora_scored_content = (
                has_non_ora_scored_content or
                (next_item.has_score and next_item.category != 'openassessment')
            )

        items.extend(next_item.get_children())

    if has_non_ora_scored_content:
        return collected_items
    return []


def _get_custom_pacing_children(subsection, num_weeks):
    """
    Return relative date items for the subsection and its children
    """
    items = [subsection]
    has_content = False
    all_problems_are_ora = True
    section_date_items = []
    while items:
        next_item = items.pop()
        is_problem = next_item.category not in {'sequential', 'vertical'}
        if is_problem:
            has_content = True
        # Open response assessment problems have their own due dates
        if next_item.category != 'openassessment':
            section_date_items.append((next_item.location, {'due': timedelta(weeks=num_weeks)}))
            items.extend(next_item.get_children())
            if is_problem:
                all_problems_are_ora = False

    # If all the problems are ORA then we return an empty list. This is to avoid potential conflicts with
    # custom relative dates through PLS and Studio since ORA problems have their own due dates.
    if has_content and all_problems_are_ora:
        return []

    # If there are non ORA content problems or if there are no problems at all return the list of date items.
    # Relative dates should apply to subsections and their children if there are other graded assignments
    # in it (i.e. non-ORA problems). The current custom PLS allows for due dates to be set even for empty
    # units.
    return section_date_items


def extract_dates_from_course(course):
    """
    Extract all dates from the supplied course.
    """
    log.info('Extracting course dates for %s', course.id)
    if course.self_paced:
        metadata = _field_values(FIELDS_TO_EXTRACT, course)
        # self-paced courses may accidentally have a course due date
        metadata.pop('due', None)
        date_items = [(course.location, metadata)]

        if SelfPacedRelativeDatesConfig.current(course_key=course.id).enabled:
            # Apply the same relative due date to all content inside a section,
            # unless that item already has a relative date set
            for _, section, weeks_to_complete in spaced_out_sections(course):
                section_date_items = []
                # section_due_date will end up being the max of all due dates of its subsections
                section_due_date = timedelta(weeks=1)
                for subsection in section.get_children():
                    # If custom pacing is set on a subsection, apply the set relative
                    # date to all the content inside the subsection. Otherwise
                    # apply the default Personalized Learner Schedules (PLS)
                    # logic for self paced courses.
                    relative_weeks_due = subsection.fields['relative_weeks_due'].read_from(subsection)
                    if (CUSTOM_RELATIVE_DATES.is_enabled(course.id) and relative_weeks_due):
                        section_due_date = max(section_due_date, timedelta(weeks=relative_weeks_due))
                        section_date_items.extend(_get_custom_pacing_children(subsection, relative_weeks_due))
                    else:
                        section_due_date = max(section_due_date, weeks_to_complete)
                        section_date_items.extend(_gather_graded_items(subsection, weeks_to_complete))
                if section_date_items and (section.graded or CUSTOM_RELATIVE_DATES.is_enabled(course.id)):
                    date_items.append((section.location, {'due': section_due_date}))
                date_items.extend(section_date_items)
    else:
        date_items = []
        store = modulestore()
        with store.branch_setting(ModuleStoreEnum.Branch.published_only, course.id):
            items = store.get_items(course.id)
        log.info('Extracting dates from %d items in %s', len(items), course.id)
        for item in items:
            date_items.append((item.location, _field_values(FIELDS_TO_EXTRACT, item)))
    return date_items


@receiver(SignalHandler.course_published)
def extract_dates(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Extract dates from blocks when publishing a course.
    """
    store = modulestore()
    with store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
        course = store.get_course(course_key)

    if not course:
        log.info("No course found for key %s to extract dates from", course_key)
        return

    date_items = extract_dates_from_course(course)

    try:
        set_dates_for_course(course_key, date_items)
    except Exception:  # pylint: disable=broad-except
        log.exception('Unable to set dates for %s on course publish', course_key)


@receiver(SignalHandler.course_published)
def update_assignment_dates(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Receive the course_published signal and update assignment dates for the course.
    """
    # import here, because signal is registered at startup, but items in tasks are not available yet
    from .tasks import update_assignment_dates_for_course

    course_key_str = str(course_key)
    transaction.on_commit(lambda: update_assignment_dates_for_course.delay(course_key_str))
