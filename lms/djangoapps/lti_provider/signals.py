"""
Signals handlers for the lti_provider Django app.
"""

import logging

from django.conf import settings
from django.dispatch import receiver
from opaque_keys.edx.keys import LearningContextKey

import lms.djangoapps.lti_provider.outcomes as outcomes
from lms.djangoapps.grades.api import signals as grades_signals
from lms.djangoapps.lti_provider.tasks import send_composite_outcome, send_leaf_outcome
from lms.djangoapps.lti_provider.views import parse_course_and_usage_keys
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


def increment_assignment_versions(course_key, usage_key, user_id):
    """
    Update the version numbers for all assignments that are affected by a score
    change event. Returns a list of all affected assignments.
    """
    problem_block = modulestore().get_item(usage_key)
    # Get all assignments involving the current problem for which the campus LMS
    # is expecting a grade. There may be many possible graded assignments, if
    # a problem has been added several times to a course at different
    # granularities (such as the unit or the vertical).
    assignments = outcomes.get_assignments_for_problem(
        problem_block, user_id, course_key
    )
    for assignment in assignments:
        assignment.version_number += 1
        assignment.save()
    return assignments


@receiver(grades_signals.PROBLEM_WEIGHTED_SCORE_CHANGED)
def score_changed_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Consume signals that indicate score changes. See the definition of
    PROBLEM_WEIGHTED_SCORE_CHANGED for a description of the signal.
    """
    points_possible = kwargs.get('weighted_possible', None)
    points_earned = kwargs.get('weighted_earned', None)
    user_id = kwargs.get('user_id', None)
    course_id = kwargs.get('course_id', None)
    usage_id = kwargs.get('usage_id', None)

    # Make sure this came from a course because this code only works with courses
    if not course_id:
        return
    context_key = LearningContextKey.from_string(course_id)
    if not context_key.is_course:
        return  # This is a content library or something else...

    if None not in (points_earned, points_possible, user_id, course_id):
        course_key, usage_key = parse_course_and_usage_keys(course_id, usage_id)
        assignments = increment_assignment_versions(course_key, usage_key, user_id)
        for assignment in assignments:
            if assignment.usage_key == usage_key:
                send_leaf_outcome.delay(
                    assignment.id, points_earned, points_possible
                )
            else:
                send_composite_outcome.apply_async(
                    (user_id, course_id, assignment.id, assignment.version_number),
                    countdown=settings.LTI_AGGREGATE_SCORE_PASSBACK_DELAY
                )
    else:
        log.error(
            "Outcome Service: Required signal parameter is None. "
            "points_possible: %s, points_earned: %s, user_id: %s, "
            "course_id: %s, usage_id: %s",
            points_possible, points_earned, user_id, course_id, usage_id
        )
