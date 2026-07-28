# pylint: disable=missing-module-docstring
import datetime
import itertools

import ddt
import pytz
from crum import set_current_request
from django.test.utils import override_settings
from xblocks_contrib.problem.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.courseware.tests.test_submitting_problems import ProblemSubmissionTestMixin
from openedx.core.djangolib.testing.utils import get_mock_request
from xmodule.capa_block import reset_class
from xmodule.graders import ProblemScore
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.modulestore.tests.utils import TEST_DATA_DIR
from xmodule.modulestore.xml_importer import import_course_from_xml

from ...subsection_grade_factory import SubsectionGradeFactory
from ..utils import answer_problem, mock_get_submissions_score


@ddt.ddt
class _TestMultipleProblemTypesSubsectionScoresBase(SharedModuleStoreTestCase):
    """
    Test grading of different problem types.
    """
    __test__ = False
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    SCORED_BLOCK_COUNT = 7
    ACTUAL_TOTAL_POSSIBLE = 17.0

    @classmethod
    def setUpClass(cls):
        reset_class()
        super().setUpClass()
        cls.load_scoreable_course()
        chapter1 = cls.course.get_children()[0]
        cls.seq1 = chapter1.get_children()[0]

    def setUp(self):
        reset_class()
        super().setUp()
        self.student = UserFactory.create(is_staff=False, username='test_student', password=self.TEST_PASSWORD)
        self.client.login(username=self.student.username, password=self.TEST_PASSWORD)
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(self.student)
        self.course_structure = get_course_blocks(self.student, self.course.location)

    @classmethod
    def load_scoreable_course(cls):
        """
        This test course lives at `common/test/data/scoreable`.

        For details on the contents and structure of the file, see
        `common/test/data/scoreable/README`.
        """
        user = UserFactory.create(is_staff=False, username='test_student', password=cls.TEST_PASSWORD)

        course_items = import_course_from_xml(
            cls.store,
            user.id,
            TEST_DATA_DIR,
            source_dirs=['scoreable'],
            static_content_store=None,
            target_id=cls.store.make_course_key('edX', 'scoreable', '3000'),
            raise_on_failure=True,
            create_if_not_present=True,
        )

        cls.course = course_items[0]

    def test_score_submission_for_all_problems(self):
        subsection_factory = SubsectionGradeFactory(
            self.student,
            course_structure=self.course_structure,
            course=self.course,
        )
        score = subsection_factory.create(self.seq1)

        assert score.all_total.earned == 0.0
        assert score.all_total.possible == self.ACTUAL_TOTAL_POSSIBLE

        # Choose arbitrary, non-default values for earned and possible.
        earned_per_block = 3.0
        possible_per_block = 7.0
        with mock_get_submissions_score(earned_per_block, possible_per_block) as mock_score:
            # Configure one block to return no possible score, the rest to return 3.0 earned / 7.0 possible
            block_count = self.SCORED_BLOCK_COUNT - 1
            mock_score.side_effect = itertools.chain(
                [(earned_per_block, None, earned_per_block, None, datetime.datetime(2000, 1, 1))],
                itertools.repeat(mock_score.return_value)
            )
            score = subsection_factory.update(self.seq1)
        assert score.all_total.earned == (earned_per_block * block_count)
        assert score.all_total.possible == (possible_per_block * block_count)


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=True)
class ExtractedTestMultipleProblemTypesSubsectionScores(_TestMultipleProblemTypesSubsectionScoresBase):
    __test__ = True


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=False)
class BuiltInTestMultipleProblemTypesSubsectionScores(_TestMultipleProblemTypesSubsectionScoresBase):
    __test__ = True


@ddt.ddt
class _TestVariedMetadataBase(ProblemSubmissionTestMixin, ModuleStoreTestCase):
    """
    Test that changing the metadata on a block has the desired effect on the
    persisted score.
    """
    __test__ = False
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    default_problem_metadata = {
        'graded': True,
        'weight': 2.5,
        'due': datetime.datetime(2099, 3, 15, 12, 30, 0, tzinfo=pytz.utc),
    }

    def setUp(self):
        reset_class()
        super().setUp()
        self.course = CourseFactory.create()
        with self.store.bulk_operations(self.course.id):
            self.chapter = BlockFactory.create(
                parent=self.course,
                category="chapter",
                display_name="Test Chapter"
            )
            self.sequence = BlockFactory.create(
                parent=self.chapter,
                category='sequential',
                display_name="Test Sequential 1",
                graded=True
            )
            self.vertical = BlockFactory.create(
                parent=self.sequence,
                category='vertical',
                display_name='Test Vertical 1'
            )
        self.problem_xml = '''
            <problem url_name="capa-optionresponse">
              <optionresponse>
                <optioninput options="('Correct', 'Incorrect')" correct="Correct"></optioninput>
                <optioninput options="('Correct', 'Incorrect')" correct="Correct"></optioninput>
              </optionresponse>
            </problem>
        '''
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory())
        self.client.login(username=self.request.user.username, password=self.TEST_PASSWORD)
        CourseEnrollment.enroll(self.request.user, self.course.id)

    def _get_altered_metadata(self, alterations):
        """
        Returns a copy of the default_problem_metadata dict updated with the
        specified alterations.
        """
        metadata = self.default_problem_metadata.copy()
        metadata.update(alterations)
        return metadata

    def _add_problem_with_alterations(self, alterations):
        """
        Add a problem to the course with the specified metadata alterations.
        """

        metadata = self._get_altered_metadata(alterations)
        BlockFactory.create(
            parent=self.vertical,
            category="problem",
            display_name="problem",
            data=self.problem_xml,
            metadata=metadata,
        )

    def _get_score(self):
        """
        Return the score of the test problem when one correct problem (out of
        two) is submitted.
        """

        self.submit_question_answer('problem', {'2_1': 'Correct'})
        course_structure = get_course_blocks(self.request.user, self.course.location)
        subsection_factory = SubsectionGradeFactory(
            self.request.user,
            course_structure=course_structure,
            course=self.course,
        )
        return subsection_factory.create(self.sequence)

    @ddt.data(
        ({}, 1.25, 2.5),
        ({'weight': 27}, 13.5, 27),
        ({'weight': 1.0}, 0.5, 1.0),
        ({'weight': 0.0}, 0.0, 0.0),
        ({'weight': None}, 1.0, 2.0),
    )
    @ddt.unpack
    def test_weight_metadata_alterations(self, alterations, expected_earned, expected_possible):
        self._add_problem_with_alterations(alterations)
        score = self._get_score()
        assert score.all_total.earned == expected_earned
        assert score.all_total.possible == expected_possible

    @ddt.data(
        ({'graded': True}, 1.25, 2.5),
        ({'graded': False}, 0.0, 0.0),
    )
    @ddt.unpack
    def test_graded_metadata_alterations(self, alterations, expected_earned, expected_possible):
        self._add_problem_with_alterations(alterations)
        score = self._get_score()
        assert score.graded_total.earned == expected_earned
        assert score.graded_total.possible == expected_possible


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=True)
class ExtractedTestVariedMetadata(_TestVariedMetadataBase):
    __test__ = True


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=False)
class BuiltInTestVariedMetadata(_TestVariedMetadataBase):
    __test__ = True


@ddt.ddt
class _TestWeightedProblemsBase(SharedModuleStoreTestCase):
    """
    Test scores and grades with various problem weight values.
    """
    __test__ = False
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        reset_class()
        super().setUpClass()
        cls.course = CourseFactory.create()
        with cls.store.bulk_operations(cls.course.id):
            cls.chapter = BlockFactory.create(parent=cls.course, category="chapter", display_name="chapter")
            cls.sequential = BlockFactory.create(parent=cls.chapter, category="sequential", display_name="sequential")
            cls.vertical = BlockFactory.create(parent=cls.sequential, category="vertical", display_name="vertical1")
            problem_xml = cls._create_problem_xml()
            cls.problems = []
            for i in range(2):
                cls.problems.append(
                    BlockFactory.create(
                        parent=cls.vertical,
                        category="problem",
                        display_name=f"problem_{i}",
                        data=problem_xml,
                    )
                )

    def setUp(self):
        reset_class()
        super().setUp()
        self.user = UserFactory()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(self.user)

    @classmethod
    def _create_problem_xml(cls):
        """
        Creates and returns XML for a multiple choice response problem
        """
        return MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 3',
            choices=[False, False, True, False],
            choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
        )

    def _verify_grades(self, raw_earned, raw_possible, weight, expected_score):
        """
        Verifies the computed grades are as expected.
        """
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            for problem in self.problems:
                problem.weight = weight
                self.store.update_item(problem, self.user.id)
            self.store.publish(self.course.location, self.user.id)

        course_structure = get_course_blocks(self.request.user, self.course.location)

        # answer all problems
        for problem in self.problems:
            answer_problem(self.course, self.request, problem, score=raw_earned, max_value=raw_possible)

        # get grade
        subsection_grade = SubsectionGradeFactory(
            self.request.user, self.course, course_structure
        ).update(self.sequential)

        # verify all problem grades
        for problem in self.problems:
            problem_score = subsection_grade.problem_scores[problem.location]
            assert isinstance(expected_score.first_attempted, type(problem_score.first_attempted))
            expected_score.first_attempted = problem_score.first_attempted
            assert problem_score == expected_score

        # verify subsection grades
        assert subsection_grade.all_total.earned == (expected_score.earned * len(self.problems))
        assert subsection_grade.all_total.possible == (expected_score.possible * len(self.problems))

    @ddt.data(
        *itertools.product(
            (0.0, 0.5, 1.0, 2.0),  # raw_earned
            (-2.0, -1.0, 0.0, 0.5, 1.0, 2.0),  # raw_possible
            (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 50.0, None),  # weight
        )
    )
    @ddt.unpack
    def test_problem_weight(self, raw_earned, raw_possible, weight):

        use_weight = weight is not None and raw_possible != 0
        if use_weight:
            expected_w_earned = raw_earned / raw_possible * weight
            expected_w_possible = weight
        else:
            expected_w_earned = raw_earned
            expected_w_possible = raw_possible

        expected_graded = expected_w_possible > 0

        expected_score = ProblemScore(
            raw_earned=raw_earned,
            raw_possible=raw_possible,
            weighted_earned=expected_w_earned,
            weighted_possible=expected_w_possible,
            weight=weight,
            graded=expected_graded,
            first_attempted=datetime.datetime(2010, 1, 1),
        )
        self._verify_grades(raw_earned, raw_possible, weight, expected_score)


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=True)
class ExtractedTestWeightedProblems(_TestWeightedProblemsBase):
    __test__ = True


@override_settings(USE_EXTRACTED_PROBLEM_BLOCK=False)
class BuiltInTestWeightedProblems(_TestWeightedProblemsBase):
    __test__ = True
