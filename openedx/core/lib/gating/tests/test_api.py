"""
Tests for the gating API
"""

from unittest.mock import Mock, patch

import pytest
from completion.models import BlockCompletion
from ddt import data, ddt, unpack
from milestones import api as milestones_api
from milestones.tests.utils import MilestonesTestCaseMixin

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.gating import api as lms_gating_api
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.models import PersistentSubsectionGrade, PersistentSubsectionGradeOverride
from lms.djangoapps.grades.tests.base import GradeTestBase
from lms.djangoapps.grades.tests.utils import mock_get_score
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.gating import api as gating_api
from openedx.core.lib.gating.exceptions import GatingValidationError
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt
class TestGatingApi(ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Tests for the gating API
    """
    def setUp(self):
        """
        Initial data setup
        """
        super().setUp()

        # create course
        self.course = CourseFactory.create(
            org='edX',
            number='EDX101',
            run='EDX101_RUN1',
            display_name='edX 101'
        )
        self.course.enable_subsection_gating = True
        self.course.save()

        # create chapter
        self.chapter1 = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name='untitled chapter 1'
        )

        # create sequentials
        self.seq1 = BlockFactory.create(
            parent_location=self.chapter1.location,
            category='sequential',
            display_name='untitled sequential 1'
        )
        self.seq2 = BlockFactory.create(
            parent_location=self.chapter1.location,
            category='sequential',
            display_name='untitled sequential 2'
        )

        # create vertical
        self.vertical = BlockFactory.create(
            parent_location=self.seq1.location,
            category='vertical',
            display_name='untitled vertical 1'
        )

        self.generic_milestone = {
            'name': 'Test generic milestone',
            'namespace': str(self.seq1.location),
        }

    @patch('openedx.core.lib.gating.api.log.warning')
    def test_get_prerequisite_milestone_returns_none(self, mock_log):
        """ Test test_get_prerequisite_milestone_returns_none """

        prereq = gating_api._get_prerequisite_milestone(self.seq1.location)  # pylint: disable=protected-access
        assert prereq is None
        assert mock_log.called

    def test_get_prerequisite_milestone_returns_milestone(self):
        """ Test test_get_prerequisite_milestone_returns_milestone """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        prereq = gating_api._get_prerequisite_milestone(self.seq1.location)  # pylint: disable=protected-access
        assert prereq is not None

    @data('', '0', '50', '100')
    def test_validate_min_score_is_valid(self, min_score):
        """ Test test_validate_min_score_is_valid """

        assert gating_api._validate_min_score(min_score) is None  # pylint: disable=protected-access

    @data('abc', '-10', '110')
    def test_validate_min_score_raises(self, min_score):
        """ Test test_validate_min_score_non_integer """

        with pytest.raises(GatingValidationError):
            gating_api._validate_min_score(min_score)  # pylint: disable=protected-access

    def test_find_gating_milestones(self):
        """ Test test_find_gating_milestones """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100)
        milestone = milestones_api.add_milestone(self.generic_milestone)
        milestones_api.add_course_content_milestone(self.course.id, self.seq1.location, 'fulfills', milestone)

        assert len(gating_api.find_gating_milestones(self.course.id, self.seq1.location, 'fulfills')) == 1
        assert len(gating_api.find_gating_milestones(self.course.id, self.seq1.location, 'requires')) == 0
        assert len(gating_api.find_gating_milestones(self.course.id, self.seq2.location, 'fulfills')) == 0
        assert len(gating_api.find_gating_milestones(self.course.id, self.seq2.location, 'requires')) == 1

    def test_get_gating_milestone_not_none(self):
        """ Test test_get_gating_milestone_not_none """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100)

        assert gating_api.get_gating_milestone(self.course.id, self.seq1.location, 'fulfills') is not None
        assert gating_api.get_gating_milestone(self.course.id, self.seq2.location, 'requires') is not None

    def test_get_gating_milestone_is_none(self):
        """ Test test_get_gating_milestone_is_none """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100)

        assert gating_api.get_gating_milestone(self.course.id, self.seq1.location, 'requires') is None
        assert gating_api.get_gating_milestone(self.course.id, self.seq2.location, 'fulfills') is None

    def test_prerequisites(self):
        """ Test test_prerequisites """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)

        prereqs = gating_api.get_prerequisites(self.course.id)
        assert len(prereqs) == 1
        assert prereqs[0]['block_display_name'] == self.seq1.display_name
        assert prereqs[0]['block_usage_key'] == str(self.seq1.location)
        assert gating_api.is_prerequisite(self.course.id, self.seq1.location)

        gating_api.remove_prerequisite(self.seq1.location)

        assert len(gating_api.get_prerequisites(self.course.id)) == 0
        assert not gating_api.is_prerequisite(self.course.id, self.seq1.location)

    def test_required_content(self):
        """ Test test_required_content """

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100, 100)

        prereq_content_key, min_score, min_completion = gating_api.get_required_content(
            self.course.id, self.seq2.location
        )
        assert prereq_content_key == str(self.seq1.location)
        assert min_score == 100
        assert min_completion == 100

        gating_api.set_required_content(self.course.id, self.seq2.location, None, None, None)

        prereq_content_key, min_score, min_completion = gating_api.get_required_content(
            self.course.id, self.seq2.location
        )
        assert prereq_content_key is None
        assert min_score is None
        assert min_completion is None

    def test_get_gated_content(self):
        """
        Verify staff bypasses gated content and student gets list of unfulfilled prerequisites.
        """

        staff = UserFactory(is_staff=True)
        student = UserFactory(is_staff=False)

        assert gating_api.get_gated_content(self.course, staff) == []
        assert gating_api.get_gated_content(self.course, student) == []

        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100)
        milestone = milestones_api.get_course_content_milestones(self.course.id, self.seq2.location, 'requires')[0]

        assert gating_api.get_gated_content(self.course, staff) == []
        assert gating_api.get_gated_content(self.course, student) == [str(self.seq2.location)]

        milestones_api.add_user_milestone({'id': student.id}, milestone)

        assert gating_api.get_gated_content(self.course, student) == []

    @data(
        (100, 0, 50, 0, False),
        (100, 0, 100, 0, True),
        (0, 100, 0, 50, False),
        (0, 100, 0, 100, True),
        (100, 100, 50, 100, False),
        (100, 100, 100, 50, False),
        (100, 100, 100, 100, True),
    )
    @unpack
    def test_is_gate_fulfilled(self, min_score, min_completion, learner_score, learner_completion, is_gate_fulfilled):
        """
        Test if prereq section has any unfulfilled milestones
        """
        student = UserFactory(is_staff=False)
        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(
            self.course.id, self.seq2.location, self.seq1.location, min_score, min_completion
        )
        milestone = milestones_api.add_milestone(self.generic_milestone)
        milestones_api.add_course_content_milestone(self.course.id, self.seq1.location, 'fulfills', milestone)

        assert not gating_api.is_gate_fulfilled(self.course.id, self.seq1.location, student.id)

        # complete the prerequisite to unlock the gated content
        # this call triggers reevaluation of prerequisites fulfilled by the gating block.
        with patch.object(gating_api, 'get_subsection_completion_percentage') as mock_grade:
            mock_grade.return_value = learner_completion
            lms_gating_api.evaluate_prerequisite(
                self.course,
                Mock(location=self.seq1.location, percent_graded=learner_score / 100.0),
                student,
            )
            assert gating_api.is_gate_fulfilled(self.course.id, self.seq1.location, student.id) == is_gate_fulfilled

    @data(
        (1, 1, 100),
        (0, 0, 0),
        (1, 0, 100),
        (0, 1, 0),
    )
    @unpack
    @skip_unless_lms
    def test_get_subsection_completion_percentage(self, user_problem_completion, user_html_completion,
                                                  expected_completion_percentage):
        """
        Test if gating_api.get_subsection_completion_percentage returns expected completion percentage

        Note:
            html blocks are ignored in computation of completion_percentage,so it should not affect result.

        """
        student = UserFactory(is_staff=False)
        problem_block = BlockFactory.create(
            parent_location=self.vertical.location,
            category='problem',
            display_name='some problem'
        )
        html_block = BlockFactory.create(
            parent_location=self.vertical.location,
            category='html',
            display_name='some html block'
        )
        with patch.object(BlockCompletion, 'get_learning_context_completions') as course_block_completions_mock:
            course_block_completions_mock.return_value = {
                problem_block.location: user_problem_completion,
                html_block.location: user_html_completion,
            }
            completion_percentage = gating_api.get_subsection_completion_percentage(self.seq1.location, student)
            assert completion_percentage == expected_completion_percentage

    @data(
        ('discussion', None, 100),
        ('html', None, 100),
        ('html', 1, 100),
        ('problem', 1, 100),
        ('problem', 0, 0),
        ('openassessment', 1, 100),
        ('openassessment', 0, 0),
    )
    @unpack
    @skip_unless_lms
    def test_get_subsection_completion_percentage_single_component(
        self,
        component_type,
        completed,
        expected_completion_percentage
    ):
        """
        Test if gating_api.get_subsection_completion_percentage returns expected completion percentage
        when only a single component in a vertical/unit

        Note:
            html blocks and discussion blocks are ignored in calculations so should always return
            100% complete
        """
        student = UserFactory(is_staff=False)

        component = BlockFactory.create(
            parent_location=self.vertical.location,
            category=component_type,
            display_name=f'{component_type} block'
        )

        with patch.object(BlockCompletion, 'get_learning_context_completions') as course_block_completions_mock:
            course_block_completions_mock.return_value = {
                component.location: completed,
            }
            completion_percentage = gating_api.get_subsection_completion_percentage(self.seq1.location, student)
            assert completion_percentage == expected_completion_percentage

    @skip_unless_lms
    def test_compute_is_prereq_met(self):
        """
        Test if prereq has been met and force recompute
        """
        student = UserFactory(is_staff=False)
        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100, 0)

        # complete the prerequisite to unlock the gated content
        # this call triggers reevaluation of prerequisites fulfilled by the gating block.
        with patch.object(gating_api, 'get_subsection_grade_percentage') as mock_grade:
            mock_grade.return_value = 75
            # don't force recompute
            prereq_met, prereq_meta_info = gating_api.compute_is_prereq_met(self.seq2.location, student.id, False)
            assert not prereq_met
            assert prereq_meta_info['url'] is None
            assert prereq_meta_info['display_name'] is None

            # force recompute
            prereq_met, prereq_meta_info = gating_api.compute_is_prereq_met(self.seq2.location, student.id, True)
            assert not prereq_met
            assert prereq_meta_info['url'] is not None
            assert prereq_meta_info['display_name'] is not None

            # change to passing grade
            mock_grade.return_value = 100

            # don't force recompute
            prereq_met, prereq_meta_info = gating_api.compute_is_prereq_met(self.seq2.location, student.id, False)
            assert not prereq_met
            assert prereq_meta_info['url'] is None
            assert prereq_meta_info['display_name'] is None

            # force recompute
            prereq_met, prereq_meta_info = gating_api.compute_is_prereq_met(self.seq2.location, student.id, True)
            assert prereq_met
            assert prereq_meta_info['url'] is not None
            assert prereq_meta_info['display_name'] is not None


class TestGatingGradesIntegration(GradeTestBase):
    """
    Tests the integration between the gating API and our Persistent Grades framework.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @skip_unless_lms
    def test_get_subsection_grade_percentage(self):
        user = self.request.user
        subsection_key = self.sequence.location

        with mock_get_score(3, 3):
            # this update() call creates a persistent grade
            self.subsection_grade_factory.update(self.sequence)

            # it's important that we stay in the mock_get_score() context here,
            # since get_subsection_grade_percentage() creates its own SubsectionGradeFactory,
            # which will in turn make calls to get_score().
            grade_percentage = gating_api.get_subsection_grade_percentage(subsection_key, user)
            assert 100.0 == grade_percentage

    @skip_unless_lms
    def test_get_subsection_grade_percentage_with_override(self):
        user = self.request.user
        subsection_key = self.sequence.location

        with mock_get_score(3, 3):
            # this update() call creates a persistent grade
            self.subsection_grade_factory.update(self.sequence)

            # there should only be one persistent grade
            persistent_grade = PersistentSubsectionGrade.objects.first()

            PersistentSubsectionGradeOverride.update_or_create_override(
                UserFactory(),  # it doesn't matter to us who created the override
                persistent_grade,
                earned_graded_override=0,
                earned_all_override=0,
                possible_graded_override=3,
                feature=GradeOverrideFeatureEnum.gradebook,
            )

            # it's important that we stay in the mock_get_score() context here,
            # since get_subsection_grade_percentage() creates its own SubsectionGradeFactory,
            # which will in turn make calls to get_score().
            grade_percentage = gating_api.get_subsection_grade_percentage(subsection_key, user)
            assert 0 == grade_percentage
