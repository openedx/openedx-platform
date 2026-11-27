"""Service layer for the v3 course grading endpoint."""

from django.conf import settings

from cms.djangoapps.contentstore.rest_api.v3.serializers.grading import GRADING_POLICY_FIELDS
from cms.djangoapps.contentstore.utils import get_course_grading
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.djangoapps.credit.api import is_credit_course
from openedx.core.djangoapps.credit.tasks import update_credit_course_requirements
from xmodule.modulestore.django import modulestore


def get_grading_policy(course_key):
    """Return the stored grading policy of the course ``course_key``."""
    return CourseGradingModel.fetch(course_key)


def get_full_grading_settings(course_key):
    """
    Return the grading policy of ``course_key`` with the context the Studio grading page shows beside it.

    The returned mapping holds the four policy fields plus the graded subsections per
    assignment type, the default letter grade names, and the credit and proctoring context.
    """
    with modulestore().bulk_operations(course_key):
        show_credit_eligibility = is_credit_course(course_key) and settings.ENABLE_CREDIT_ELIGIBILITY
        context = get_course_grading(course_key)
    policy = context["course_details"]
    return {
        "graders": policy.graders,
        "grade_cutoffs": policy.grade_cutoffs,
        "grace_period": policy.grace_period,
        "minimum_grade_credit": policy.minimum_grade_credit,
        "course_assignment_lists": context["course_assignment_lists"],
        "default_grade_designations": context["default_grade_designations"],
        "is_credit_course": context["is_credit_course"],
        "show_credit_eligibility": show_credit_eligibility,
        "mfe_proctored_exam_settings_url": context["mfe_proctored_exam_settings_url"],
    }


def update_grading_policy(course_key, changes, user):
    """
    Apply ``changes`` to the grading policy of ``course_key`` and return the stored policy.

    Only the fields present in ``changes`` are written. A body holding all four fields is
    applied exactly as a whole-policy update. When ``graders`` is absent the stored graders
    are never written back: their weights are fractions that do not survive a round trip
    through the percentages the API exchanges, so rewriting them would alter weights
    nobody changed. Grade cutoffs are written, and a grading policy change announced,
    only when they differ from the stored ones.

    The credit requirements of the course are refreshed whenever ``minimum_grade_credit``
    is present, whether or not its value changes.
    """
    if "minimum_grade_credit" in changes:
        update_credit_course_requirements.delay(str(course_key))

    if all(field in changes for field in GRADING_POLICY_FIELDS):
        return CourseGradingModel.update_from_json(course_key, changes, user)

    if "graders" in changes:
        # The whole-policy update needs every field. The stored cutoffs leave them
        # unchanged, and None leaves the grace period and credit minimum unchanged.
        stored_cutoffs = modulestore().get_course(course_key).grade_cutoffs
        policy = {
            "grade_cutoffs": stored_cutoffs,
            "grace_period": None,
            "minimum_grade_credit": None,
            **changes,
        }
        return CourseGradingModel.update_from_json(course_key, policy, user)

    if "grace_period" in changes:
        CourseGradingModel.update_grace_period_from_json(course_key, changes["grace_period"], user)
    if "minimum_grade_credit" in changes:
        CourseGradingModel.update_minimum_grade_credit_from_json(course_key, changes["minimum_grade_credit"], user)
    # Last, so the grading policy change is announced after every other field is stored,
    # as the whole-policy update does.
    if "grade_cutoffs" in changes:
        if modulestore().get_course(course_key).grade_cutoffs != changes["grade_cutoffs"]:
            CourseGradingModel.update_cutoffs_from_json(course_key, changes["grade_cutoffs"], user)
    return CourseGradingModel.fetch(course_key)
