"""
API for the gating djangoapp
"""


import logging  # noqa: I001
from collections import defaultdict

from opaque_keys.edx.keys import UsageKey

from common.djangoapps.util import milestones_helpers
from lms.djangoapps.courseware.entrance_exams import get_entrance_exam_content
from openedx.core.lib.gating import api as gating_api
from openedx.core.toggles import ENTRANCE_EXAMS

log = logging.getLogger(__name__)


@gating_api.gating_enabled(default=False)
def evaluate_prerequisite(course, subsection_grade, user):
    """
    Evaluates any gating milestone relationships attached to the given
    subsection. If the subsection_grade and subsection_completion meets
    the minimum score required by dependent subsections, the related
    milestone will be marked fulfilled for the user.
    """
    prereq_milestone = gating_api.get_gating_milestone(course.id, subsection_grade.location, 'fulfills')
    if prereq_milestone:
        gated_content_milestones = defaultdict(list)
        for milestone in gating_api.find_gating_milestones(course.id, content_key=None, relationship='requires'):
            gated_content_milestones[milestone['id']].append(milestone)

        gated_content = gated_content_milestones.get(prereq_milestone['id'])
        if gated_content:
            grade_percentage = subsection_grade.percent_graded * 100.0 \
                if hasattr(subsection_grade, 'percent_graded') else None

            for milestone in gated_content:
                gating_api.update_milestone(
                    milestone, subsection_grade.location, prereq_milestone, user, grade_percentage
                )


def evaluate_entrance_exam(course_grade, user):
    """
    Evaluates any entrance exam milestone relationships attached
    to the given course. If the course_grade meets the
    minimum score required, the dependent milestones will be marked
    fulfilled for the user.
    """
    course = course_grade.course_data.course
    if ENTRANCE_EXAMS.is_enabled() and getattr(course, 'entrance_exam_enabled', False):
        if get_entrance_exam_content(user, course):
            exam_chapter_key = get_entrance_exam_usage_key(course)
            exam_score = get_entrance_exam_score(course_grade, exam_chapter_key)
            if exam_score >= course.entrance_exam_minimum_score_pct:
                relationship_types = milestones_helpers.get_milestone_relationship_types()
                content_milestones = milestones_helpers.get_course_content_milestones(
                    course.id,
                    exam_chapter_key,
                    relationship=relationship_types['FULFILLS']
                )
                # Mark each entrance exam dependent milestone as fulfilled by the user.
                for milestone in content_milestones:
                    milestones_helpers.add_user_milestone({'id': user.id}, milestone)


def get_entrance_exam_usage_key(course):
    """
    Returns the UsageKey of the entrance exam for the course.
    """
    return course.entrance_exam_id and UsageKey.from_string(course.entrance_exam_id).replace(course_key=course.id)


def get_entrance_exam_score(course_grade, exam_chapter_key):
    """
    Returns the score for the given chapter as a ratio of the
    aggregated earned over the possible points, resulting in a
    decimal value less than 1.
    """
    try:
        entrance_exam_score = course_grade.chapter_percentage(exam_chapter_key)
    except KeyError:
        entrance_exam_score = 0.0
        log.warning('Gating: Unexpectedly failed to find chapter_grade for %s.', exam_chapter_key)
    return entrance_exam_score
