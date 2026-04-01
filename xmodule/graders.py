"""
Code used to calculate learner grades.
"""


import abc
import inspect
import logging
import random
import sys
from collections import OrderedDict

from django.utils.translation import gettext_lazy as _

from xmodule.util.misc import get_short_labeler


log = logging.getLogger("edx.courseware")


class ScoreBase(metaclass=abc.ABCMeta):
    """
    Abstract base class for encapsulating fields of values scores.
    """

    def __init__(self, graded, first_attempted):
        """
        Fields common to all scores include:

            :param graded: Whether or not this block is graded
            :type graded: bool

            :param first_attempted: When the block was first attempted, or None
            :type first_attempted: datetime|None
        """
        self.graded = graded
        self.first_attempted = first_attempted

    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return f"{self.__class__.__name__}({self.__dict__})"


class ProblemScore(ScoreBase):
    """
    Encapsulates the fields of a Problem's score.
    """
    def __init__(self, raw_earned, raw_possible, weighted_earned, weighted_possible, weight, *args, **kwargs):
        """
        In addition to the fields in ScoreBase, arguments include:

            :param raw_earned: Raw points earned on this problem
            :type raw_earned: int|float|None

            :param raw_possible: Raw points possible to earn on this problem
            :type raw_possible: int|float|None

            :param weighted_earned: Weighted value of the points earned
            :type weighted_earned: int|float|None

            :param weighted_possible: Weighted possible points on this problem
            :type weighted_possible: int|float|None

            :param weight: Weight of this problem
            :type weight: int|float|None
        """
        super().__init__(*args, **kwargs)
        self.raw_earned = float(raw_earned) if raw_earned is not None else None
        self.raw_possible = float(raw_possible) if raw_possible is not None else None
        self.earned = float(weighted_earned) if weighted_earned is not None else None
        self.possible = float(weighted_possible) if weighted_possible is not None else None
        self.weight = weight


class AggregatedScore(ScoreBase):
    """
    Encapsulates the fields of a Subsection's score.
    """
    def __init__(self, tw_earned, tw_possible, *args, **kwargs):
        """
        In addition to the fields in ScoreBase, also includes:

            :param tw_earned: Total aggregated sum of all weighted earned values
            :type tw_earned: int|float|None

            :param tw_possible: Total aggregated sum of all weighted possible values
            :type tw_possible: int|float|None
        """
        super().__init__(*args, **kwargs)
        self.earned = float(tw_earned) if tw_earned is not None else None
        self.possible = float(tw_possible) if tw_possible is not None else None


def float_sum(iterable):
    """
    Sum the elements of the iterable, and return the result as a float.
    """
    return float(sum(iterable))


def aggregate_scores(scores):
    """
    scores: A list of ProblemScore objects
    returns: A tuple (all_total, graded_total).
        all_total: An AggregatedScore representing the total score summed over all input scores
        graded_total: An AggregatedScore representing the score summed over all graded input scores
    """
    total_correct_graded = float_sum(score.earned for score in _iter_graded(scores))
    total_possible_graded = float_sum(score.possible for score in _iter_graded(scores))
    first_attempted_graded = _min_or_none(
        score.first_attempted for score in _iter_graded(scores) if score.first_attempted
    )

    total_correct = float_sum(score.earned for score in scores)
    total_possible = float_sum(score.possible for score in scores)
    first_attempted = _min_or_none(
        score.first_attempted for score in scores if score.first_attempted
    )

    # regardless of whether it is graded
    all_total = AggregatedScore(total_correct, total_possible, False, first_attempted=first_attempted)

    # selecting only graded things
    graded_total = AggregatedScore(
        total_correct_graded,
        total_possible_graded,
        True,
        first_attempted=first_attempted_graded
    )

    return all_total, graded_total


def invalid_args(func, argdict):
    """
    Given a function and a dictionary of arguments, returns a set of arguments
    from argdict that aren't accepted by func
    """
    args, _, keywords, _, _, _, _ = inspect.getfullargspec(func)
    if keywords:
        return set()  # All accepted
    return set(argdict) - set(args)


def grader_from_conf(conf):
    """
    This creates a CourseGrader from a configuration (such as in course_settings.py).
    The conf can simply be an instance of CourseGrader, in which case no work is done.
    More commonly, the conf is a list of dictionaries. A WeightedSubsectionsGrader
    with AssignmentFormatGraders will be generated. Every dictionary should contain
    the parameters for making an AssignmentFormatGrader, in addition to a 'weight' key.
    """
    if isinstance(conf, CourseGrader):
        return conf

    subgraders = []
    for subgraderconf in conf:
        subgraderconf = subgraderconf.copy()
        weight = subgraderconf.pop("weight", 0)
        try:
            if 'min_count' in subgraderconf:
                subgrader_class = AssignmentFormatGrader
            else:
                raise ValueError("Configuration has no appropriate grader class.")

            bad_args = invalid_args(subgrader_class.__init__, subgraderconf)
            if bad_args:
                log.warning("Invalid arguments for a subgrader: %s", bad_args)
                for key in bad_args:
                    del subgraderconf[key]

            subgrader = subgrader_class(**subgraderconf)
            subgraders.append((subgrader, subgrader.category, weight))

        except (TypeError, ValueError) as error:
            # Add info and re-raise
            msg = ("Unable to parse grader configuration:\n    " +
                   str(subgraderconf) +
                   "\n    Error was:\n    " + str(error))
            raise ValueError(msg).with_traceback(sys.exc_info()[2])

    return WeightedSubsectionsGrader(subgraders)


class CourseGrader(metaclass=abc.ABCMeta):
    """
    A course grader takes the totaled scores for each graded section (that a student has
    started) in the course. From these scores, the grader calculates an overall percentage
    grade. The grader should also generate information about how that score was calculated,
    to be displayed in graphs or charts.

    A grader has one required method, grade(), which is passed a grade_sheet. The grade_sheet
    contains scores for all graded section that the student has started. If a student has
    a score of 0 for that section, it may be missing from the grade_sheet. The grade_sheet
    is keyed by section format. Each value is a list of Score namedtuples for each section
    that has the matching section format.

    The grader outputs a dictionary with the following keys:
    - percent: Contains a float value, which is the final percentage score for the student.
    - section_breakdown: This is a list of dictionaries which provide details on sections
    that were graded. These are used for display in a graph or chart. The format for a
    section_breakdown dictionary is explained below.
    - grade_breakdown: This is a dict of dictionaries, keyed by category, which provide details on
    the contributions of the final percentage grade. This is a higher level breakdown, for when the
    grade is constructed of a few very large sections (such as Homeworks, Labs, a Midterm, and a Final).
    The format for a grade_breakdown is explained below. This section is optional.

    A dictionary in the section_breakdown list has the following keys:
    percent: A float percentage for the section.
    label: A short string identifying the section. Preferably fixed-length. E.g. "HW  3".
    detail: A string explanation of the score. E.g. "Homework 1 - Ohms Law - 83% (5/6)"
    category: A string identifying the category. Items with the same category are grouped together
    in the display (for example, by color).
    prominent: A boolean value indicating that this section should be displayed as more prominent
    than other items.

    A dictionary in the grade_breakdown dict has the following keys:
    percent: A float percentage in the breakdown. All percents should add up to the final percentage.
    detail: A string explanation of this breakdown. E.g. "Homework - 10% of a possible 15%"
    category: A string identifying the category. Items with the same category are grouped together
    in the display (for example, by color).


    """

    @abc.abstractmethod
    def grade(self, grade_sheet, generate_random_scores=False):
        '''Given a grade sheet, return a dict containing grading information'''
        raise NotImplementedError


class WeightedSubsectionsGrader(CourseGrader):
    """
    This grader takes a list of tuples containing (grader, category_name, weight) and computes
    a final grade by totalling the contribution of each sub grader and multiplying it by the
    given weight. For example, the sections may be

    [ (homeworkGrader, "Homework", 0.15), (labGrader, "Labs", 0.15), (midtermGrader, "Midterm", 0.30),
      (finalGrader, "Final", 0.40) ]

    All items in section_breakdown for each subgrader will be combined. A grade_breakdown will be
    composed using the score from each grader.

    Note that the sum of the weights is not taken into consideration. If the weights add up to
    a value > 1, the student may end up with a percent > 100%. This allows for sections that
    are extra credit.
    """
    def __init__(self, subgraders):  # pylint: disable=super-init-not-called
        self.subgraders = subgraders

    @property
    def sum_of_weights(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        result = 0
        for _, _, weight in self.subgraders:
            result += weight
        return result

    def grade(self, grade_sheet, generate_random_scores=False):
        total_percent = 0.0
        section_breakdown = []
        grade_breakdown = OrderedDict()

        for subgrader, assignment_type, weight in self.subgraders:
            subgrade_result = subgrader.grade(grade_sheet, generate_random_scores)

            weighted_percent = subgrade_result['percent'] * weight
            section_detail = _("{assignment_type} = {weighted_percent:.2%} of a possible {weight:.2%}").format(
                assignment_type=assignment_type,
                weighted_percent=weighted_percent,
                weight=weight)

            total_percent += weighted_percent
            section_breakdown += subgrade_result['section_breakdown']
            grade_breakdown[assignment_type] = {
                'percent': weighted_percent,
                'detail': section_detail,
                'category': assignment_type,
            }

        return {
            'percent': total_percent,
            'section_breakdown': section_breakdown,
            'grade_breakdown': grade_breakdown
        }


class AssignmentFormatGrader(CourseGrader):
    """
    Grades all sections matching the format 'type' with an equal weight. A specified
    number of lowest scores can be dropped from the calculation. The minimum number of
    sections in this format must be specified (even if those sections haven't been
    written yet).

    min_count defines how many assignments are expected throughout the course. Placeholder
    scores (of 0) will be inserted if the number of matching sections in the course is < min_count.
    If there number of matching sections in the course is > min_count, min_count will be ignored.

    show_only_average is to suppress the display of each assignment in this grader and instead
    only show the total score of this grader in the breakdown.

    hide_average is to suppress the display of the total score in this grader and instead
    only show each assignment in this grader in the breakdown.

    If there is only a single assignment in this grader, then it returns only one entry for the
    grader.  Since the assignment and the total are the same, the total is returned but is not
    labeled as an average.

    category should be presentable to the user, but may not appear. When the grade breakdown is
    displayed, scores from the same category will be similar (for example, by color).

    section_type is a string that is the type of a singular section. For example, for Labs it
    would be "Lab". This defaults to be the same as category.

    short_label is similar to section_type, but shorter. For example, for Homework it would be
    "HW".

    starting_index is the first number that will appear. For example, starting_index=3 and
    min_count = 2 would produce the labels "Assignment 3", "Assignment 4"

    """
    def __init__(  # lint-amnesty, pylint: disable=super-init-not-called
            self,
            type,  # pylint: disable=redefined-builtin
            min_count,
            drop_count,
            category=None,
            section_type=None,
            short_label=None,
            show_only_average=False,
            hide_average=False,
            starting_index=1
    ):
        self.type = type
        self.min_count = min_count
        self.drop_count = drop_count
        self.category = category or self.type
        self.section_type = section_type or self.type
        self.short_label = short_label or self.type
        self.show_only_average = show_only_average
        self.starting_index = starting_index
        self.hide_average = hide_average

    def total_with_drops(self, breakdown):
        """
        Calculates total score for a section while dropping lowest scores
        """
        # Create an array of tuples with (index, mark), sorted by mark['percent'] descending
        sorted_breakdown = sorted(enumerate(breakdown), key=lambda x: -x[1]['percent'])

        # A list of the indices of the dropped scores
        dropped_indices = []
        if self.drop_count > 0:
            dropped_indices = [x[0] for x in sorted_breakdown[-self.drop_count:]]
        aggregate_score = 0
        for index, mark in enumerate(breakdown):
            if index not in dropped_indices:
                aggregate_score += mark['percent']

        if len(breakdown) - self.drop_count > 0:
            aggregate_score /= len(breakdown) - self.drop_count

        return aggregate_score, dropped_indices

    def grade(self, grade_sheet, generate_random_scores=False):
        scores = list(grade_sheet.get(self.type, {}).values())
        breakdown = []
        labeler = get_short_labeler(self.short_label)
        for i in range(max(int(float(self.min_count)), len(scores))):
            if i < len(scores) or generate_random_scores:
                if generate_random_scores:  	# for debugging!
                    earned = random.randint(2, 15)
                    possible = random.randint(earned, 15)
                    section_name = _("Generated")
                    sequential_id = None
                else:
                    earned = scores[i].graded_total.earned
                    possible = scores[i].graded_total.possible
                    section_name = scores[i].display_name
                    sequential_id = str(scores[i].location)

                percentage = scores[i].percent_graded
                summary_format = "{section_type} {index} - {name} - {percent:.2%} ({earned:.3n}/{possible:.3n})"
                summary = summary_format.format(
                    index=i + self.starting_index,
                    section_type=self.section_type,
                    name=section_name,
                    percent=percentage,
                    earned=float(earned),
                    possible=float(possible)
                )
            else:
                percentage = 0.0
                # Translators: "Homework 1 - Unreleased - 0% (?/?)" The section has not been released for viewing.
                summary = _("{section_type} {index} Unreleased - 0% (?/?)").format(
                    index=i + self.starting_index,
                    section_type=self.section_type
                )
                sequential_id = None
            short_label = labeler(i + self.starting_index)

            breakdown.append({'percent': percentage, 'label': short_label,
                              'detail': summary, 'category': self.category, 'sequential_id': sequential_id})

        total_percent, dropped_indices = self.total_with_drops(breakdown)

        for dropped_index in dropped_indices:
            breakdown[dropped_index]['mark'] = {
                'detail': _("The lowest {drop_count} {section_type} scores are dropped.").format(
                    drop_count=self.drop_count,
                    section_type=self.section_type
                )
            }

        if len(breakdown) == 1:
            # if there is only one entry in a section, suppress the existing individual entry and the average,
            # and just display a single entry for the section.
            total_detail = "{section_type} = {percent:.2%}".format(
                percent=total_percent,
                section_type=self.section_type,
            )
            total_label = f"{self.short_label}"
            breakdown = [{'percent': total_percent, 'label': total_label,
                          'detail': total_detail, 'category': self.category, 'prominent': True,
                          'sequential_id': str(scores[0].location) if scores else None}, ]
        else:
            # Translators: "Homework Average = 0%"
            total_detail = _("{section_type} Average = {percent:.2%}").format(
                percent=total_percent,
                section_type=self.section_type
            )
            # Translators: Avg is short for Average
            total_label = _("{short_label} Avg").format(short_label=self.short_label)

            if self.show_only_average:
                breakdown = []

            if not self.hide_average:
                breakdown.append({'percent': total_percent, 'label': total_label,
                                  'detail': total_detail, 'category': self.category, 'prominent': True})

        return {
            'percent': total_percent,
            'section_breakdown': breakdown,
            # No grade_breakdown here
        }


def _iter_graded(scores):
    """
    Yield the scores that belong to explicitly graded blocks
    """
    return (score for score in scores if score.graded)


def _min_or_none(itr):
    """
    Return the lowest value in itr, or None if itr is empty.

    In python 3, this is just min(itr, default=None)
    """
    try:
        return min(itr)
    except ValueError:
        return None
