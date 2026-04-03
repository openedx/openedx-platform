# pylint: disable=unused-import,wildcard-import
"""
Python APIs exposed by the grades app to other in-process apps.
"""


from datetime import datetime

import pytz
from django.core.exceptions import ObjectDoesNotExist
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.track.event_transaction_utils import create_new_event_transaction_id, set_event_transaction_type

# Public Grades Modules
from lms.djangoapps.grades import constants, context, course_data, events

# Grades APIs that should NOT belong within the Grades subsystem
# TODO move Gradebook to be an external feature outside of core Grades
from lms.djangoapps.grades.config.waffle import gradebook_bulk_management_enabled, is_writable_gradebook_enabled

# Public Grades Factories
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.models_api import *
from lms.djangoapps.grades.signals import signals

# TODO exposing functionality from Grades handlers seems fishy.
from lms.djangoapps.grades.signals.handlers import disconnect_submissions_signal_receiver
from lms.djangoapps.grades.subsection_grade import CreateSubsectionGrade
from lms.djangoapps.grades.subsection_grade_factory import SubsectionGradeFactory
from lms.djangoapps.grades.tasks import compute_all_grades_for_course as task_compute_all_grades_for_course
from lms.djangoapps.grades.util_services import GradesUtilService
from lms.djangoapps.utils import _get_key


def graded_subsections_for_course_id(course_id):
    """
    Return graded subsections for the course.
    """
    from lms.djangoapps.grades.context import graded_subsections_for_course
    return graded_subsections_for_course(course_data.CourseData(user=None, course_key=course_id).collected_structure)


def override_subsection_grade(
        user_id, course_key_or_id, usage_key_or_id, overrider=None, earned_all=None, earned_graded=None,
        feature=constants.GradeOverrideFeatureEnum.proctoring, comment=None,
):
    """
    Creates a PersistentSubsectionGradeOverride corresponding to the given
    user, course, and usage_key.
    Will also create a ``PersistentSubsectionGrade`` for this (user, course, usage_key)
    if none currently exists.

    Fires off a recalculate_subsection_grade async task to update the PersistentCourseGrade table.
    Will not override ``earned_all`` or ``earned_graded`` value if they are ``None``.
    Both of these parameters have ``None`` as their default value.
    """
    course_key = _get_key(course_key_or_id, CourseKey)
    usage_key = _get_key(usage_key_or_id, UsageKey)

    try:
        grade = get_subsection_grade(user_id, usage_key.course_key, usage_key)
    except ObjectDoesNotExist:
        grade = _create_subsection_grade(user_id, course_key, usage_key)

    override = update_or_create_override(
        grade,
        requesting_user=overrider,
        subsection_grade_model=grade,
        feature=feature,
        system=feature,
        earned_all_override=earned_all,
        earned_graded_override=earned_graded,
        comment=comment,
    )

    # Cache a new event id and event type which the signal handler will use to emit a tracking log event.
    create_new_event_transaction_id()
    set_event_transaction_type(events.SUBSECTION_OVERRIDE_EVENT_TYPE)

    # This will eventually trigger a re-computation of the course grade,
    # taking the new PersistentSubsectionGradeOverride into account.
    signals.SUBSECTION_OVERRIDE_CHANGED.send(
        sender=None,
        user_id=user_id,
        course_id=str(course_key),
        usage_id=str(usage_key),
        only_if_higher=False,
        modified=override.modified,
        score_deleted=False,
        score_db_table=constants.ScoreDatabaseTableEnum.overrides
    )


def undo_override_subsection_grade(user_id, course_key_or_id, usage_key_or_id, feature=''):
    """
    Delete the override subsection grade row (the PersistentSubsectionGrade model must already exist)

    Fires off a recalculate_subsection_grade async task to update the PersistentSubsectionGrade table. If the
    override does not exist, no error is raised, it just triggers the recalculation.

    feature: if specified, the deletion will only occur if the
             override to be deleted was created by the corresponding
             subsystem
    """
    course_key = _get_key(course_key_or_id, CourseKey)
    usage_key = _get_key(usage_key_or_id, UsageKey)

    try:
        override = get_subsection_grade_override(user_id, course_key, usage_key)
    except ObjectDoesNotExist:
        return

    if override is not None and (
            not feature or not override.system or feature == override.system):
        override.delete()
    else:
        return

    # Cache a new event id and event type which the signal handler will use to emit a tracking log event.
    create_new_event_transaction_id()
    set_event_transaction_type(events.SUBSECTION_OVERRIDE_EVENT_TYPE)

    # Signal will trigger subsection recalculation which will call PersistentSubsectionGrade.update_or_create_grade
    # which will no longer use the above deleted override, and instead return the grade to the original score from
    # the actual problem responses before writing to the table.
    signals.SUBSECTION_OVERRIDE_CHANGED.send(
        sender=None,
        user_id=user_id,
        course_id=str(course_key),
        usage_id=str(usage_key),
        only_if_higher=False,
        modified=datetime.now().replace(tzinfo=pytz.UTC),  # Not used when score_deleted=True
        score_deleted=True,
        score_db_table=constants.ScoreDatabaseTableEnum.overrides
    )


def should_override_grade_on_rejected_exam(course_key_or_id):
    """
    Convenience function to return the state of the CourseWaffleFlag REJECTED_EXAM_OVERRIDES_GRADE
    """
    from .config.waffle import REJECTED_EXAM_OVERRIDES_GRADE
    course_key = _get_key(course_key_or_id, CourseKey)
    return REJECTED_EXAM_OVERRIDES_GRADE.is_enabled(course_key)


def _create_subsection_grade(user_id, course_key, usage_key):
    """
    Given a user_id, course_key, and subsection usage_key,
    creates a new ``PersistentSubsectionGrade``.
    """
    from django.contrib.auth import get_user_model

    from lms.djangoapps.courseware.courses import get_course
    course = get_course(course_key, depth=None)
    subsection = course.get_child(usage_key)
    if not subsection:
        raise Exception('Subsection with given usage_key does not exist.')
    user = get_user_model().objects.get(id=user_id)
    subsection_grade = CreateSubsectionGrade(subsection, course_data.CourseData(user, course=course).structure, {}, {})
    return subsection_grade.update_or_create_model(user, force_update_subsections=True)
