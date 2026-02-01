"""
Python APIs exposed for the progress tracking functionality of the course home API.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from opaque_keys.edx.keys import CourseKey
from openedx.core.lib.grade_utils import round_away_from_zero
from xblock.scorable import ShowCorrectness
from datetime import datetime, timezone

from lms.djangoapps.courseware.courses import get_course_blocks_completion_summary
from dataclasses import dataclass, field

User = get_user_model()


@dataclass
class _AssignmentBucket:
    """Holds scores and visibility info for one assignment type.

    Attributes:
        assignment_type: Full assignment type name from the grading policy (for example, "Homework").
        num_total: The total number of assignments expected to contribute to the grade before any
            drop-lowest rules are applied.
        last_grade_publish_date: The most recent date when grades for all assignments of assignment_type
            are released and included in the final grade.
        scores: Per-subsection fractional scores (each value is ``earned / possible`` and falls in
            the range 0–1). While awaiting published content we pad the list with zero placeholders
            so that its length always matches ``num_total`` until real scores replace them.
        visibilities: Mirrors ``scores`` index-for-index and records whether each subsection's
            correctness feedback is visible to the learner (``True``), hidden (``False``), or not
            yet populated (``None`` when the entry is a placeholder).
        included: Tracks whether each subsection currently counts toward the learner's grade as
            determined by ``SubsectionGrade.show_grades``. Values follow the same convention as
            ``visibilities`` (``True`` / ``False`` / ``None`` placeholders).
        assignments_created: Count of real subsections inserted into the bucket so far. Once this
            reaches ``num_total``, all placeholder entries have been replaced with actual data.
    """
    assignment_type: str
    num_total: int
    last_grade_publish_date: datetime | None
    scores: list[float] = field(default_factory=list)
    visibilities: list[bool | None] = field(default_factory=list)
    included: list[bool | None] = field(default_factory=list)
    assignments_created: int = 0

    @classmethod
    def with_placeholders(cls, assignment_type: str, num_total: int, now: datetime):
        """Create a bucket prefilled with placeholder (empty) entries."""
        return cls(
            assignment_type=assignment_type,
            num_total=num_total,
            last_grade_publish_date=None,
            scores=[0] * num_total,
            visibilities=[None] * num_total,
            included=[None] * num_total,
        )

    def add_subsection(self, score: float, is_visible: bool, is_included: bool):
        """Add a subsection’s score and visibility, replacing a placeholder if space remains."""
        if self.assignments_created < self.num_total:
            if self.scores:
                self.scores.pop(0)
            if self.visibilities:
                self.visibilities.pop(0)
            if self.included:
                self.included.pop(0)
        self.scores.append(score)
        self.visibilities.append(is_visible)
        self.included.append(is_included)
        self.assignments_created += 1

    def drop_lowest(self, num_droppable: int):
        """Remove the lowest scoring subsections, up to the provided num_droppable."""
        while num_droppable > 0 and self.scores:
            idx = self.scores.index(min(self.scores))
            self.scores.pop(idx)
            self.visibilities.pop(idx)
            self.included.pop(idx)
            num_droppable -= 1

    def hidden_state(self) -> str:
        """Return whether kept scores are all, some, or none hidden."""
        if not self.visibilities:
            return 'none'
        all_hidden = all(v is False for v in self.visibilities)
        some_hidden = any(v is False for v in self.visibilities)
        if all_hidden:
            return 'all'
        if some_hidden:
            return 'some'
        return 'none'

    def averages(self) -> tuple[float, float]:
        """Compute visible and included averages over kept scores.

        Visible average uses only grades with visibility flag True in numerator; denominator is total
        number of kept scores (mirrors legacy behavior). Included average uses only scores that are
        marked included (show_grades True) in numerator with same denominator.

        Returns:
            (earned_visible, earned_all) tuple of floats (0-1 each).
        """
        if not self.scores:
            return 0.0, 0.0
        visible_scores = [s for i, s in enumerate(self.scores) if self.visibilities[i]]
        included_scores = [s for i, s in enumerate(self.scores) if self.included[i]]
        earned_visible = (sum(visible_scores) / len(self.scores)) if self.scores else 0.0
        earned_all = (sum(included_scores) / len(self.scores)) if self.scores else 0.0
        return earned_visible, earned_all


class _AssignmentTypeGradeAggregator:
    """Collects and aggregates subsection grades by assignment type."""

    def __init__(self, course_grade, grading_policy: dict, has_staff_access: bool):
        """Initialize with course grades, grading policy, and staff access flag."""
        self.course_grade = course_grade
        self.grading_policy = grading_policy
        self.has_staff_access = has_staff_access
        self.now = datetime.now(timezone.utc)
        self.policy_map = self._build_policy_map()
        self.buckets: dict[str, _AssignmentBucket] = {}

    def _build_policy_map(self) -> dict:
        """Convert grading policy into a lookup of assignment type → policy info."""
        policy_map = {}
        for policy in self.grading_policy.get('GRADER', []):
            policy_map[policy.get('type')] = {
                'weight': policy.get('weight', 0.0),
                'short_label': policy.get('short_label', ''),
                'num_droppable': policy.get('drop_count', 0),
                'num_total': policy.get('min_count', 0),
            }
        return policy_map

    def _bucket_for(self, assignment_type: str) -> _AssignmentBucket:
        """Get or create a score bucket for the given assignment type."""
        bucket = self.buckets.get(assignment_type)
        if bucket is None:
            num_total = self.policy_map.get(assignment_type, {}).get('num_total', 0) or 0
            bucket = _AssignmentBucket.with_placeholders(assignment_type, num_total, self.now)
            self.buckets[assignment_type] = bucket
        return bucket

    def collect(self):
        """Gather subsection grades into their respective assignment buckets."""
        for chapter in self.course_grade.chapter_grades.values():
            for subsection_grade in chapter.get('sections', []):
                if not getattr(subsection_grade, 'graded', False):
                    continue
                assignment_type = getattr(subsection_grade, 'format', '') or ''
                if not assignment_type:
                    continue
                graded_total = getattr(subsection_grade, 'graded_total', None)
                earned = getattr(graded_total, 'earned', 0.0) if graded_total else 0.0
                possible = getattr(graded_total, 'possible', 0.0) if graded_total else 0.0
                earned = 0.0 if earned is None else earned
                possible = 0.0 if possible is None else possible
                score = (earned / possible) if possible else 0.0
                is_visible = ShowCorrectness.correctness_available(
                    subsection_grade.show_correctness, subsection_grade.due, self.has_staff_access
                )
                is_included = subsection_grade.show_grades(self.has_staff_access)
                bucket = self._bucket_for(assignment_type)
                bucket.add_subsection(score, is_visible, is_included)
                visibilities_with_due_dates = [ShowCorrectness.PAST_DUE, ShowCorrectness.NEVER_BUT_INCLUDE_GRADE]
                if subsection_grade.show_correctness in visibilities_with_due_dates:
                    if subsection_grade.due:
                        should_update = (
                            bucket.last_grade_publish_date is None or
                            subsection_grade.due > bucket.last_grade_publish_date
                        )
                        if should_update:
                            bucket.last_grade_publish_date = subsection_grade.due

    def build_results(self) -> dict:
        """Apply drops, compute averages, and return aggregated results and total grade."""
        final_grades = 0.0
        rows = []
        for assignment_type, bucket in self.buckets.items():
            policy = self.policy_map.get(assignment_type, {})
            bucket.drop_lowest(policy.get('num_droppable', 0))
            earned_visible, earned_all = bucket.averages()
            weight = policy.get('weight', 0.0)
            short_label = policy.get('short_label', '')
            row = {
                'type': assignment_type,
                'weight': weight,
                'average_grade': round_away_from_zero(earned_visible, 4),
                'weighted_grade': round_away_from_zero(earned_visible * weight, 4),
                'short_label': short_label,
                'num_droppable': policy.get('num_droppable', 0),
                'last_grade_publish_date': bucket.last_grade_publish_date,
                'has_hidden_contribution': bucket.hidden_state(),
            }
            final_grades += earned_all * weight
            rows.append(row)
        rows.sort(key=lambda r: r['weight'])
        return {'results': rows, 'final_grades': round_away_from_zero(final_grades, 4)}

    def run(self) -> dict:
        """Execute full pipeline (collect + aggregate) returning final payload."""
        self.collect()
        return self.build_results()


def aggregate_assignment_type_grade_summary(
    course_grade,
    grading_policy: dict,
    has_staff_access: bool = False,
) -> dict:
    """
    Aggregate subsection grades by assignment type and return summary data.
    Args:
        course_grade: CourseGrade object containing chapter and subsection grades.
        grading_policy: Dictionary representing the course's grading policy.
        has_staff_access: Boolean indicating if the user has staff access to view all grades.
    Returns:
        Dictionary with keys:
            results: list of per-assignment-type summary dicts
            final_grades: overall weighted contribution (float, 4 decimal rounding)
    """
    aggregator = _AssignmentTypeGradeAggregator(course_grade, grading_policy, has_staff_access)
    return aggregator.run()


def calculate_progress_for_learner_in_course(course_key: CourseKey, user: User) -> dict:
    """
    Calculate a given learner's progress in the specified course run.
    """
    summary = get_course_blocks_completion_summary(course_key, user)
    if not summary:
        return {}

    complete_count = summary.get("complete_count", 0)
    locked_count = summary.get("locked_count", 0)
    incomplete_count = summary.get("incomplete_count", 0)

    # This completion calculation mirrors the logic used in the CompletionDonutChart component on the Learning MFE's
    # Progress tab. It's duplicated here to enable backend reporting on learner progress. Ideally, this logic should be
    # refactored in the future so that the calculation is handled solely on the backend, eliminating the need for it to
    # be done in the frontend.
    num_total_units = complete_count + incomplete_count + locked_count
    if num_total_units == 0:
        complete_percentage = locked_percentage = incomplete_percentage = 0.0
    else:
        complete_percentage = round(complete_count / num_total_units, 2)
        locked_percentage = round(locked_count / num_total_units, 2)
        incomplete_percentage = 1.00 - complete_percentage - locked_percentage

    return {
        "complete_count": complete_count,
        "locked_count": locked_count,
        "incomplete_count": incomplete_count,
        "total_count": num_total_units,
        "complete_percentage": complete_percentage,
        "locked_percentage": locked_percentage,
        "incomplete_percentage": incomplete_percentage
    }
