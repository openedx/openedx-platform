"""
Unit tests for gating.signals module
"""


from unittest.mock import Mock, patch

from ddt import data, ddt, unpack
from milestones import api as milestones_api
from milestones.tests.utils import MilestonesTestCaseMixin

from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from lms.djangoapps.gating.api import evaluate_prerequisite
from openedx.core.lib.gating import api as gating_api
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class GatingTestCase(LoginEnrollmentTestCase, ModuleStoreTestCase):
    """
    Base TestCase class for setting up a basic course structure
    and testing the gating feature
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
        self.update_course(self.course, 0)

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
            display_name='gating sequential'
        )
        self.seq2 = BlockFactory.create(
            parent_location=self.chapter1.location,
            category='sequential',
            display_name='gated sequential'
        )


@ddt
class TestEvaluatePrerequisite(GatingTestCase, MilestonesTestCaseMixin):
    """
    Tests for the evaluate_prerequisite function
    """

    def setUp(self):
        super().setUp()
        self.user_dict = {'id': self.user.id}
        self.prereq_milestone = None
        self.subsection_grade = Mock(location=self.seq1.location, percent_graded=0.5)

    def _setup_gating_milestone(self, min_score, min_completion):
        """
        Setup a gating milestone for testing
        """
        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(
            self.course.id, self.seq2.location, self.seq1.location, min_score, min_completion
        )
        self.prereq_milestone = gating_api.get_gating_milestone(self.course.id, self.seq1.location, 'fulfills')

    @patch('openedx.core.lib.gating.api.get_subsection_completion_percentage')
    @data(
        (50, 0, 50, 0, True),
        (50, 0, 10, 0, False),
        (0, 50, 0, 50, True),
        (0, 50, 0, 10, False),
        (50, 50, 50, 10, False),
        (50, 50, 10, 50, False),
        (50, 50, 50, 50, True),
    )
    @unpack
    def test_min_score_achieved(
            self, min_score, min_completion, module_score, module_completion, result, mock_completion
    ):
        self._setup_gating_milestone(min_score, min_completion)
        mock_completion.return_value = module_completion
        self.subsection_grade.percent_graded = module_score / 100.0

        evaluate_prerequisite(self.course, self.subsection_grade, self.user)
        assert milestones_api.user_has_milestone(self.user_dict, self.prereq_milestone) == result

    @patch('openedx.core.lib.gating.api.get_subsection_completion_percentage')
    @patch('openedx.core.lib.gating.api._get_minimum_required_percentage')
    @data((50, 50, False), (100, 50, False), (50, 100, False), (100, 100, True))
    @unpack
    def test_invalid_min_score(self, module_score, module_completion, result, mock_min_score, mock_completion):
        self._setup_gating_milestone(None, None)
        mock_completion.return_value = module_completion
        self.subsection_grade.percent_graded = module_score / 100.0
        mock_min_score.return_value = 100, 100

        evaluate_prerequisite(self.course, self.subsection_grade, self.user)
        assert milestones_api.user_has_milestone(self.user_dict, self.prereq_milestone) == result

    @patch('openedx.core.lib.gating.api.get_subsection_grade_percentage')
    def test_no_prerequisites(self, mock_score):
        evaluate_prerequisite(self.course, self.subsection_grade, self.user)
        assert not mock_score.called

    @patch('openedx.core.lib.gating.api.get_subsection_grade_percentage')
    def test_no_gated_content(self, mock_score):
        gating_api.add_prerequisite(self.course.id, self.seq1.location)

        evaluate_prerequisite(self.course, self.subsection_grade, self.user)
        assert not mock_score.called
