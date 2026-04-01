"""
SubsectionGrade Class
"""


from abc import ABCMeta
from collections import OrderedDict
from datetime import datetime, timezone
from logging import getLogger
from lazy import lazy
from xblock.scorable import ShowCorrectness

from lms.djangoapps.grades.models import BlockRecord, PersistentSubsectionGrade
from lms.djangoapps.grades.scores import compute_percent, get_score, possibly_scored
from xmodule import block_metadata_utils, graders  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.graders import AggregatedScore  # lint-amnesty, pylint: disable=wrong-import-order

log = getLogger(__name__)


class SubsectionGradeBase(metaclass=ABCMeta):
    """
    Abstract base class for Subsection Grades.
    """

    def __init__(self, subsection):
        self.location = subsection.location
        self.display_name = block_metadata_utils.display_name_with_default(subsection)
        self.url_name = subsection.usage_key.block_id

        self.due = block_metadata_utils.get_datetime_field(subsection, 'due', None)
        self.end = getattr(subsection, 'end', None)
        self.format = getattr(subsection, 'format', '')
        self.graded = getattr(subsection, 'graded', False)
        transformer_data = getattr(subsection, 'transformer_data', None)
        hidden_content_data = transformer_data and subsection.transformer_data.get('hidden_content')
        self.hide_after_due = hidden_content_data and hidden_content_data.fields.get('merged_hide_after_due')
        self.self_paced = subsection.self_paced
        self.show_correctness = getattr(subsection, 'show_correctness', '')

        self.course_version = getattr(subsection, 'course_version', None)
        self.subtree_edited_timestamp = getattr(subsection, 'subtree_edited_on', None)

        self.override = None

    @property
    def attempted(self):
        """
        Returns whether any problem in this subsection
        was attempted by the student.
        """
        # pylint: disable=no-member
        assert self.all_total is not None, (
            "SubsectionGrade not fully populated yet.  Call init_from_structure or init_from_model "
            "before use."
        )
        return self.all_total.attempted

    def show_grades(self, has_staff_access):
        """
        Returns whether subsection scores are currently available to users with or without staff access.
        """
        if self.show_correctness == ShowCorrectness.NEVER_BUT_INCLUDE_GRADE:
            # show_grades fn is used to determine if the grade should be included in final calculation.
            # For NEVER_BUT_INCLUDE_GRADE, show_grades returns True if the due date has passed,
            # but correctness_available always returns False as we do not want to show correctness
            # of problems to the users.
            return (self.due is None or
                    self.due < datetime.now(timezone.utc))
        return ShowCorrectness.correctness_available(self.show_correctness, self.due, has_staff_access)

    @property
    def attempted_graded(self):
        """
        Returns whether the user had attempted a graded problem in this subsection.
        """
        raise NotImplementedError

    @property
    def percent_graded(self):
        """
        Returns the percent score of the graded problems in this subsection.
        """
        raise NotImplementedError


class ZeroSubsectionGrade(SubsectionGradeBase):
    """
    Class for Subsection Grades with Zero values.
    """

    def __init__(self, subsection, course_data):
        super().__init__(subsection)
        self.course_data = course_data

    @property
    def attempted_graded(self):
        return False

    @property
    def percent_graded(self):
        return 0.0

    @property
    def all_total(self):
        """
        Returns the total score (earned and possible) amongst all problems (graded and ungraded) in this subsection.
        NOTE: This will traverse this subsection's subtree to determine
        problem scores.  If self.course_data.structure is currently null, this means
        we will first fetch the user-specific course structure from the data store!
        """
        return self._aggregate_scores[0]

    @property
    def graded_total(self):
        """
        Returns the total score (earned and possible) amongst all graded problems in this subsection.
        NOTE: This will traverse this subsection's subtree to determine
        problem scores.  If self.course_data.structure is currently null, this means
        we will first fetch the user-specific course structure from the data store!
        """
        return self._aggregate_scores[1]

    @lazy
    def _aggregate_scores(self):
        return graders.aggregate_scores(list(self.problem_scores.values()))

    @lazy
    def problem_scores(self):
        """
        Overrides the problem_scores member variable in order
        to return empty scores for all scorable problems in the
        course.
        NOTE: The use of `course_data.structure` here is very intentional.
        It means we look through the user-specific subtree of this subsection,
        taking into account which problems are visible to the user.
        """
        locations = OrderedDict()  # dict of problem locations to ProblemScore
        for block_key in self.course_data.structure.post_order_traversal(
                filter_func=possibly_scored,
                start_node=self.location,
        ):
            block = self.course_data.structure[block_key]
            if getattr(block, 'has_score', False):
                problem_score = get_score(
                    submissions_scores={}, csm_scores={}, persisted_block=None, block=block,
                )
                if problem_score is not None:
                    locations[block_key] = problem_score
        return locations


class NonZeroSubsectionGrade(SubsectionGradeBase, metaclass=ABCMeta):
    """
    Abstract base class for Subsection Grades with
    possibly NonZero values.
    """

    def __init__(self, subsection, all_total, graded_total, override=None):
        super().__init__(subsection)
        self.all_total = all_total
        self.graded_total = graded_total
        self.override = override

    @property
    def attempted_graded(self):
        return self.graded_total.first_attempted is not None

    @property
    def percent_graded(self):
        return compute_percent(self.graded_total.earned, self.graded_total.possible)

    @staticmethod
    def _compute_block_score(  # lint-amnesty, pylint: disable=missing-function-docstring
            block_key,
            course_structure,
            submissions_scores,
            csm_scores,
            persisted_block=None,
    ):
        try:
            block = course_structure[block_key]
        except KeyError:
            # It's possible that the user's access to that
            # block has changed since the subsection grade
            # was last persisted.
            pass
        else:
            if getattr(block, 'has_score', False):
                return get_score(
                    submissions_scores,
                    csm_scores,
                    persisted_block,
                    block,
                )

    @staticmethod
    def _aggregated_score_from_model(grade_model, is_graded):
        """
        Helper method that returns `AggregatedScore` objects based on
        the values in the given `grade_model`.  If the given model
        has an associated override, the values from the override are
        used instead.
        """
        score_type = 'graded' if is_graded else 'all'
        earned_value = getattr(grade_model, f'earned_{score_type}')
        possible_value = getattr(grade_model, f'possible_{score_type}')
        if hasattr(grade_model, 'override'):
            score_type = 'graded_override' if is_graded else 'all_override'

            earned_override = getattr(grade_model.override, f'earned_{score_type}')
            if earned_override is not None:
                earned_value = earned_override

            possible_override = getattr(grade_model.override, f'possible_{score_type}')
            if possible_override is not None:
                possible_value = possible_override

        return AggregatedScore(
            tw_earned=earned_value,
            tw_possible=possible_value,
            graded=is_graded,
            first_attempted=grade_model.first_attempted,
        )


class ReadSubsectionGrade(NonZeroSubsectionGrade):
    """
    Class for Subsection grades that are read from the database.
    """
    def __init__(self, subsection, model, factory):
        all_total = self._aggregated_score_from_model(model, is_graded=False)
        graded_total = self._aggregated_score_from_model(model, is_graded=True)
        override = model.override if hasattr(model, 'override') else None

        # save these for later since we compute problem_scores lazily
        self.model = model
        self.factory = factory

        super().__init__(subsection, all_total, graded_total, override)

    @lazy
    def problem_scores(self):
        """
        Returns the scores of the problem blocks that compose this subsection.
        NOTE: The use of `course_data.structure` here is very intentional.
        It means we look through the user-specific subtree of this subsection,
        taking into account which problems are visible to the user.
        """
        # pylint: disable=protected-access
        problem_scores = OrderedDict()
        for block in self.model.visible_blocks.blocks:
            problem_score = self._compute_block_score(
                block.locator,
                self.factory.course_data.structure,
                self.factory._submissions_scores,
                self.factory._csm_scores,
                block,
            )
            if problem_score:
                problem_scores[block.locator] = problem_score
        return problem_scores


class CreateSubsectionGrade(NonZeroSubsectionGrade):
    """
    Class for Subsection grades that are newly created or updated.
    """
    def __init__(self, subsection, course_structure, submissions_scores, csm_scores):
        self.problem_scores = OrderedDict()
        for block_key in course_structure.post_order_traversal(
                filter_func=possibly_scored,
                start_node=subsection.location,
        ):
            problem_score = self._compute_block_score(block_key, course_structure, submissions_scores, csm_scores)
            if problem_score:
                self.problem_scores[block_key] = problem_score

        all_total, graded_total = graders.aggregate_scores(list(self.problem_scores.values()))

        super().__init__(subsection, all_total, graded_total)

    def update_or_create_model(self, student, score_deleted=False, force_update_subsections=False):
        """
        Saves or updates the subsection grade in a persisted model.
        """
        if self._should_persist_per_attempted(score_deleted, force_update_subsections):
            model = PersistentSubsectionGrade.update_or_create_grade(**self._persisted_model_params(student))

            if hasattr(model, 'override'):
                # When we're doing an update operation, the PersistentSubsectionGrade model
                # will be updated based on the problem_scores, but if a grade override
                # exists that's related to the updated persistent grade, we need to update
                # the aggregated scores for this object to reflect the override.
                self.all_total = self._aggregated_score_from_model(model, is_graded=False)
                self.graded_total = self._aggregated_score_from_model(model, is_graded=True)

            return model

    @classmethod
    def bulk_create_models(cls, student, subsection_grades, course_key):
        """
        Saves the subsection grade in a persisted model.
        """
        params = [
            subsection_grade._persisted_model_params(student)  # pylint: disable=protected-access
            for subsection_grade in subsection_grades
            if subsection_grade
            if subsection_grade._should_persist_per_attempted()  # pylint: disable=protected-access
        ]
        return PersistentSubsectionGrade.bulk_create_grades(params, student.id, course_key)

    def _should_persist_per_attempted(self, score_deleted=False, force_update_subsections=False):
        """
        Returns whether the SubsectionGrade's model should be
        persisted based on settings and attempted status.

        If the learner's score was just deleted, they will have
        no attempts but the grade should still be persisted.

        If the learner's enrollment track has changed, and the
        subsection *only* contains track-specific problems that the
        user has attempted, a re-grade will not occur. Should force
        a re-grade in this case. See EDUCATOR-1280.
        """
        return (
            self.all_total.first_attempted is not None or
            score_deleted or
            force_update_subsections
        )

    def _persisted_model_params(self, student):
        """
        Returns the parameters for creating/updating the
        persisted model for this subsection grade.
        """
        return dict(
            user_id=student.id,
            usage_key=self.location,
            course_version=self.course_version,
            subtree_edited_timestamp=self.subtree_edited_timestamp,
            earned_all=self.all_total.earned,
            possible_all=self.all_total.possible,
            earned_graded=self.graded_total.earned,
            possible_graded=self.graded_total.possible,
            visible_blocks=self._get_visible_blocks,
            first_attempted=self.all_total.first_attempted,
        )

    @property
    def _get_visible_blocks(self):
        """
        Returns the list of visible blocks.
        """
        return [
            BlockRecord(location, score.weight, score.raw_possible, score.graded)
            for location, score in
            self.problem_scores.items()
        ]
