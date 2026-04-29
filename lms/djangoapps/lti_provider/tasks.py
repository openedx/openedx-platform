"""
Asynchronous tasks for the LTI provider app.
"""


import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

import lms.djangoapps.lti_provider.outcomes as outcomes
from lms import CELERY_APP
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.lti_provider.models import GradedAssignment
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@CELERY_APP.task(name='lms.djangoapps.lti_provider.tasks.send_composite_outcome')
@set_code_owner_attribute
def send_composite_outcome(user_id, course_id, assignment_id, version):
    """
    Calculate and transmit the score for a composite module (such as a
    vertical).

    A composite module may contain multiple problems, so we need to
    calculate the total points earned and possible for all child problems. This
    requires calculating the scores for the whole course, which is an expensive
    operation.

    Callers should be aware that the score calculation code accesses the latest
    scores from the database. This can lead to a race condition between a view
    that updates a user's score and the calculation of the grade. If the Celery
    task attempts to read the score from the database before the view exits (and
    its transaction is committed), it will see a stale value. Care should be
    taken that this task is not triggered until the view exits.

    The GradedAssignment model has a version_number field that is incremented
    whenever the score is updated. It is used by this method for two purposes.
    First, it allows the task to exit if it detects that it has been superseded
    by another task that will transmit the score for the same assignment.
    Second, it prevents a race condition where two tasks calculate different
    scores for a single assignment, and may potentially update the campus LMS
    in the wrong order.
    """
    assignment = GradedAssignment.objects.get(id=assignment_id)
    if version != assignment.version_number:
        log.info(
            "Score passback for GradedAssignment %s skipped. More recent score available.",
            assignment.id
        )
        return
    course_key = CourseKey.from_string(course_id)
    mapped_usage_key = assignment.usage_key.map_into_course(course_key)
    user = User.objects.get(id=user_id)
    course = modulestore().get_course(course_key, depth=0)
    course_grade = CourseGradeFactory().read(user, course)
    earned, possible = course_grade.score_for_block(mapped_usage_key)
    if possible == 0:
        weighted_score = 0
    else:
        weighted_score = float(earned) / float(possible)

    assignment = GradedAssignment.objects.get(id=assignment_id)
    if assignment.version_number == version:
        outcomes.send_score_update(assignment, weighted_score)


@CELERY_APP.task
@set_code_owner_attribute
def send_leaf_outcome(assignment_id, points_earned, points_possible):
    """
    Calculate and transmit the score for a single problem. This method assumes
    that the individual problem was the source of a score update, and so it
    directly takes the points earned and possible values. As such it does not
    have to calculate the scores for the course, making this method far faster
    than send_outcome_for_composite_assignment.
    """
    assignment = GradedAssignment.objects.get(id=assignment_id)
    if points_possible == 0:
        weighted_score = 0
    else:
        weighted_score = float(points_earned) / float(points_possible)
    outcomes.send_score_update(assignment, weighted_score)
