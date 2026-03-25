"""
CourseGrade Class
"""


from abc import abstractmethod
from collections import OrderedDict, defaultdict

from ccx_keys.locator import CCXLocator
from django.conf import settings
from lazy import lazy

from openedx.core.lib.grade_utils import round_away_from_zero
from xmodule import block_metadata_utils  # lint-amnesty, pylint: disable=wrong-import-order

from .scores import compute_percent
from .subsection_grade import ZeroSubsectionGrade
from .subsection_grade_factory import SubsectionGradeFactory


class CourseGradeBase:
    """
    Base class for Course Grades.
    """
    def __init__(
        self,
        user,
        course_data,
        percent=0.0,
        letter_grade=None,
        passed=False,
        force_update_subsections=False,
        last_updated=None
    ):
        self.user = user
        self.course_data = course_data

        self.percent = percent
        self.passed = passed

        # Convert empty strings to None when reading from the table
        self.letter_grade = letter_grade or None
        self.force_update_subsections = force_update_subsections

        self.last_updated = last_updated

    def __str__(self):
        return 'Course Grade: percent: {}, letter_grade: {}, passed: {}'.format(
            str(self.percent),
            self.letter_grade,
            self.passed,
        )

    def update(self, visible_grades_only=False, has_staff_access=False):
        """
        Recalculates the grade for the course, with the given parameters.

        Also updates subsection grades if self.force_update_subsections is true.

        Arguments:
            visible_grades_only: Only considers grades the user can see (via show_correctness subsection field)
            has_staff_access: Used to help determine which grades are visible (if visible_grades_only=True)
        """
        return self

    @property
    def attempted(self):
        """
        Returns whether at least one problem was attempted
        by the user in the course.
        """
        return False

    def subsection_grade(self, subsection_key):
        """
        Returns the subsection grade for the given subsection usage key.

        Raises `KeyError` if the course structure does not contain the key.

        If the course structure contains the key, this will always succeed
        (and return a grade) regardless of whether the user can access that section;
        it is up to the caller to ensure that the grade isn't
        shown to users that shouldn't be able to access it
        (e.g. a student shouldn't see a grade for an unreleased subsection);
        """
        # look in the user structure first and fallback to the collected;
        # however, we assume the state of course_data is intentional,
        # so we use effective_structure to avoid additional fetching
        subsection = (
            self.course_data.effective_structure[subsection_key]
            if subsection_key in self.course_data.effective_structure
            else self.course_data.collected_structure[subsection_key]
        )
        return self._get_subsection_grade(subsection)

    def graded_subsections_by_format(self, visible_grades_only=False, has_staff_access=False):
        """
        Returns grades for the subsections in the course in
        a dict keyed by subsection format types.
        """
        subsections_by_format = defaultdict(OrderedDict)
        for chapter in self.chapter_grades.values():
            for subsection_grade in chapter['sections']:
                is_visible = not visible_grades_only or subsection_grade.show_grades(has_staff_access)
                if subsection_grade.graded and is_visible:
                    graded_total = subsection_grade.graded_total
                    if graded_total.possible > 0:
                        subsections_by_format[subsection_grade.format][subsection_grade.location] = subsection_grade
        return subsections_by_format

    @lazy
    def chapter_grades(self):
        """
        Returns a dictionary of dictionaries.
        The primary dictionary is keyed by the chapter's usage_key.
        The secondary dictionary contains the chapter's
        subsection grades, display name, and url name.
        """
        course_structure = self.course_data.structure
        grades = OrderedDict()
        for chapter_key in course_structure.get_children(self.course_data.location):
            grades[chapter_key] = self._get_chapter_grade_info(course_structure[chapter_key], course_structure)
        return grades

    @lazy
    def subsection_grades(self):
        """
        Returns an ordered dictionary of subsection grades,
        keyed by subsection location.
        """
        subsection_grades = defaultdict(OrderedDict)
        for chapter in self.chapter_grades.values():
            for subsection_grade in chapter['sections']:
                subsection_grades[subsection_grade.location] = subsection_grade
        return subsection_grades

    @lazy
    def problem_scores(self):
        """
        Returns a dict of problem scores keyed by their locations.
        """
        problem_scores = {}
        for chapter in self.chapter_grades.values():
            for subsection_grade in chapter['sections']:
                problem_scores.update(subsection_grade.problem_scores)
        return problem_scores

    def chapter_percentage(self, chapter_key):
        """
        Returns the rounded aggregate weighted percentage for the given chapter.
        Raises:
            KeyError if the chapter is not found.
        """
        earned, possible = 0.0, 0.0
        chapter_grade = self.chapter_grades[chapter_key]
        for section in chapter_grade['sections']:
            earned += section.graded_total.earned
            possible += section.graded_total.possible
        return compute_percent(earned, possible)

    def score_for_block(self, location):
        """
        Calculate the aggregate weighted score for any location in the course.
        This method returns a tuple containing (earned_score, possible_score).
        If the location is of 'problem' type, this method will return the
        possible and earned scores for that problem. If the location refers to a
        composite block (a vertical or section ) the scores will be the sums of
        all scored problems that are children of the chosen location.
        """
        if location in self.problem_scores:
            score = self.problem_scores[location]
            return score.earned, score.possible
        children = self.course_data.structure.get_children(location)
        earned, possible = 0.0, 0.0
        for child in children:
            child_earned, child_possible = self.score_for_block(child)
            earned += child_earned
            possible += child_possible
        return earned, possible

    def grader_result(self, visible_grades_only=False, has_staff_access=False):
        """
        Returns the result from the course grader.
        """
        course = self._prep_course_for_grading(self.course_data.course)
        return course.grader.grade(
            self.graded_subsections_by_format(visible_grades_only=visible_grades_only,
                                              has_staff_access=has_staff_access),
            generate_random_scores=settings.GENERATE_PROFILE_SCORES,
        )

    @property
    def summary(self):
        """
        Returns the grade summary as calculated by the course's grader.
        DEPRECATED: To be removed as part of TNL-5291.
        """
        # TODO(TNL-5291) Remove usages of this deprecated property.
        grade_summary = self.grader_result()
        grade_summary['percent'] = self.percent
        grade_summary['grade'] = self.letter_grade
        return grade_summary

    @classmethod
    def get_subsection_type_graders(cls, course):
        """
        Returns a dictionary mapping subsection types to their
        corresponding configured graders, per grading policy.
        """
        course = cls._prep_course_for_grading(course)
        return {
            subsection_type: subsection_type_grader
            for (subsection_type_grader, subsection_type, _)
            in course.grader.subgraders
        }

    @classmethod
    def _prep_course_for_grading(cls, course):
        """
        Make sure any overrides to the grading policy are used.
        This is most relevant for CCX courses.

        Right now, we still access the grading policy from the course
        object. Once we get the grading policy from the BlockStructure
        this will no longer be needed - since BlockStructure correctly
        retrieves/uses all field overrides.
        """
        if isinstance(course.id, CCXLocator):
            # clean out any field values that may have been set from the
            # parent course of the CCX course.
            course._field_data_cache = {}  # pylint: disable=protected-access

            # this is "magic" code that automatically retrieves any overrides
            # to the grading policy and updates the course object.
            course.set_grading_policy(course.grading_policy)
        return course

    def _get_chapter_grade_info(self, chapter, course_structure):
        """
        Helper that returns a dictionary of chapter grade information.
        """
        chapter_subsection_grades = self._get_subsection_grades(course_structure, chapter.location)
        return {
            'display_name': block_metadata_utils.display_name_with_default(chapter),
            'url_name': chapter.usage_key.block_id,
            'sections': chapter_subsection_grades,
        }

    def _get_subsection_grades(self, course_structure, chapter_key):
        """
        Returns a list of subsection grades for the given chapter.
        """
        return [
            self._get_subsection_grade(course_structure[subsection_key], self.force_update_subsections)
            for subsection_key in _uniqueify_and_keep_order(course_structure.get_children(chapter_key))
        ]

    @abstractmethod
    def _get_subsection_grade(self, subsection, force_update_subsections=False):
        """
        Abstract method to be implemented by subclasses for returning
        the grade of the given subsection.
        """
        raise NotImplementedError


class ZeroCourseGrade(CourseGradeBase):
    """
    Course Grade class for Zero-value grades when no problems were
    attempted in the course.
    """
    def _get_subsection_grade(self, subsection, force_update_subsections=False):
        return ZeroSubsectionGrade(subsection, self.course_data)


class CourseGrade(CourseGradeBase):
    """
    Course Grade class when grades are updated or read from storage.
    """
    def __init__(self, user, course_data, *args, **kwargs):
        super().__init__(user, course_data, *args, **kwargs)
        self._subsection_grade_factory = SubsectionGradeFactory(user, course_data=course_data)

    def update(self, visible_grades_only=False, has_staff_access=False):
        """
        Updates the grade for the course. Also updates subsection grades
        if self.force_update_subsections is true, via the call to self.grader_result.
        """
        # TODO update this code to be more functional and readable.
        # Currently, it is hard to follow since there are plenty of
        # side-effects. Once functional, force_update_subsections
        # can be passed through and not confusingly stored and used
        # at a later time.
        grade_cutoffs = self.course_data.course.grade_cutoffs
        grader_result = self.grader_result(visible_grades_only=visible_grades_only, has_staff_access=has_staff_access)
        self.percent = self._compute_percent(grader_result)
        self.letter_grade = self._compute_letter_grade(grade_cutoffs, self.percent)
        self.passed = self._compute_passed(grade_cutoffs, self.percent)
        return self

    @lazy
    def attempted(self):  # lint-amnesty, pylint: disable=invalid-overridden-method
        """
        Returns whether any of the subsections in this course
        have been attempted by the student.
        """
        return True

    def _get_subsection_grade(self, subsection, force_update_subsections=False):
        if self.force_update_subsections:
            return self._subsection_grade_factory.update(subsection, force_update_subsections=force_update_subsections)
        else:
            # Pass read_only here so the subsection grades can be persisted in bulk at the end.
            return self._subsection_grade_factory.create(subsection, read_only=True)

    @staticmethod
    def _compute_percent(grader_result):
        """
        Computes and returns the grade percentage from the given
        result from the grader.
        """

        # Confused about the addition of .05 here?  See https://openedx.atlassian.net/browse/TNL-6972
        return round_away_from_zero(grader_result['percent'] * 100 + 0.05) / 100

    @staticmethod
    def _compute_letter_grade(grade_cutoffs, percent):
        """
        Computes and returns the course letter grade given the
        inputs, as defined in the grading_policy (e.g. 'A' 'B' 'C')
        or None if not passed.
        """
        letter_grade = None

        # Possible grades, sorted in descending order of score
        descending_grades = sorted(grade_cutoffs, key=lambda x: grade_cutoffs[x], reverse=True)
        for possible_grade in descending_grades:
            if percent >= grade_cutoffs[possible_grade]:
                letter_grade = possible_grade
                break

        return letter_grade

    @staticmethod
    def _compute_passed(grade_cutoffs, percent):
        """
        Computes and returns whether the given percent value
        is a passing grade according to the given grade cutoffs.
        """
        nonzero_cutoffs = [cutoff for cutoff in grade_cutoffs.values() if cutoff > 0]
        success_cutoff = min(nonzero_cutoffs) if nonzero_cutoffs else None
        return success_cutoff and percent >= success_cutoff


def _uniqueify_and_keep_order(iterable):
    return list(OrderedDict([(item, None) for item in iterable]).keys())
