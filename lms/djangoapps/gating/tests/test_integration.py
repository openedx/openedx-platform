"""
Integration tests for gated content.
"""


import ddt
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from crum import set_current_request
from edx_django_utils.cache import RequestCache
from edx_toggles.toggles.testutils import override_waffle_switch
from milestones import api as milestones_api
from milestones.tests.utils import MilestonesTestCaseMixin

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.grades.tests.utils import answer_problem
from openedx.core.djangolib.testing.utils import get_mock_request
from openedx.core.lib.gating import api as gating_api
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class TestGatedContent(MilestonesTestCaseMixin, SharedModuleStoreTestCase):
    """
    Base TestCase class for setting up a basic course structure
    and testing the gating feature
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.set_up_course()

    def setUp(self):
        super().setUp()
        self.setup_gating_milestone(50, 100)
        self.non_staff_user = UserFactory()
        self.staff_user = UserFactory(is_staff=True, is_superuser=True)
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(self.non_staff_user)

    @classmethod
    def set_up_course(cls):
        """
        Set up a course for testing gated content.
        """
        course = CourseFactory.create(
            org='edX',
            number='EDX101',
            run='EDX101_RUN1',
            display_name='edX 101',
            enable_subsection_gating=True,
            grading_policy={
                "GRADER": [{
                    "type": "Homework",
                    "min_count": 3,
                    "drop_count": 0,
                    "short_label": "HW",
                    "weight": 1.0
                }]
            },
        )
        with modulestore().bulk_operations(course.id):
            # create chapter
            cls.chapter1 = BlockFactory.create(
                parent=course,
                category='chapter',
                display_name='chapter 1'
            )

            # create sequentials
            cls.seq1 = BlockFactory.create(
                parent=cls.chapter1,
                category='sequential',
                display_name='gating sequential 1',
                graded=True,
                format='Homework',
            )
            cls.seq2 = BlockFactory.create(
                parent=cls.chapter1,
                category='sequential',
                display_name='gated sequential 2',
                graded=True,
                format='Homework',
            )
            cls.seq3 = BlockFactory.create(
                parent=cls.chapter1,
                category='sequential',
                display_name='sequential 3',
                graded=True,
                format='Homework',
            )

            # create problem
            cls.gating_prob1 = BlockFactory.create(
                parent=cls.seq1,
                category='problem',
                display_name='gating problem 1',
            )
            # add a discussion block to the prerequisite subsection
            # this should give us ability to test gating with blocks
            # which needs to be excluded from completion tracking
            BlockFactory.create(
                parent=cls.seq1,
                category="discussion",
                discussion_id="discussion 1",
                discussion_category="discussion category",
                discussion_target="discussion target",
            )

            cls.gated_prob2 = BlockFactory.create(
                parent=cls.seq2,
                category='problem',
                display_name='gated problem 2',
            )
            cls.prob3 = BlockFactory.create(
                parent=cls.seq3,
                category='problem',
                display_name='problem 3',
            )
        # get updated course
        cls.course = cls.store.get_item(course.location)

    def setup_gating_milestone(self, min_score, min_completion):
        """
        Setup a gating milestone for testing.
        Gating content: seq1 (must be fulfilled before access to seq2)
        Gated content: seq2 (requires completion of seq1 before access)
        """
        gating_api.add_prerequisite(self.course.id, str(self.seq1.location))
        gating_api.set_required_content(
            self.course.id, str(self.seq2.location), str(self.seq1.location), min_score, min_completion
        )
        self.prereq_milestone = gating_api.get_gating_milestone(self.course.id, self.seq1.location, 'fulfills')

    def assert_access_to_gated_content(self, user):
        """
        Verifies access to gated content for the given user is as expected.
        """
        # clear the request cache to flush any cached access results
        RequestCache.clear_all_namespaces()

        # access to gating content (seq1) remains constant
        assert bool(has_access(user, 'load', self.seq1, self.course.id))

        # access to gated content (seq2) remains constant, access is prevented in SeqBlock loading
        assert bool(has_access(user, 'load', self.seq2, self.course.id))

    def assert_user_has_prereq_milestone(self, user, expected_has_milestone):
        """
        Verifies whether or not the user has the prereq milestone
        """
        assert milestones_api.user_has_milestone({'id': user.id}, self.prereq_milestone) == expected_has_milestone

    def assert_course_grade(self, user, expected_percent):
        """
        Verifies the given user's course grade is the expected percentage.
        Also verifies the user's grade information contains values for
        all problems in the course, whether or not they are currently
        gated.
        """
        course_grade = CourseGradeFactory().read(user, self.course)
        for prob in [self.gating_prob1, self.gated_prob2, self.prob3]:
            assert prob.location in course_grade.problem_scores

        assert course_grade.percent == expected_percent

    def test_gated_for_nonstaff(self):
        self.assert_user_has_prereq_milestone(self.non_staff_user, expected_has_milestone=False)
        self.assert_access_to_gated_content(self.non_staff_user)

    def test_not_gated_for_staff(self):
        self.assert_user_has_prereq_milestone(self.staff_user, expected_has_milestone=False)
        self.assert_access_to_gated_content(self.staff_user)

    def test_gated_content_always_in_grades(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            # start with a grade from a non-gated subsection
            answer_problem(self.course, self.request, self.prob3, 10, 10)

            # verify gated status and overall course grade percentage
            self.assert_user_has_prereq_milestone(self.non_staff_user, expected_has_milestone=False)
            self.assert_access_to_gated_content(self.non_staff_user)
            self.assert_course_grade(self.non_staff_user, .33)

            # fulfill the gated requirements
            answer_problem(self.course, self.request, self.gating_prob1, 10, 10)

            # verify gated status and overall course grade percentage
            self.assert_user_has_prereq_milestone(self.non_staff_user, expected_has_milestone=True)
            self.assert_access_to_gated_content(self.non_staff_user)
            self.assert_course_grade(self.non_staff_user, .67)

    @ddt.data((1, 1, True), (1, 2, True), (1, 3, False), (0, 1, False))
    @ddt.unpack
    def test_ungating_when_fulfilled(self, earned, max_possible, result):
        self.assert_user_has_prereq_milestone(self.non_staff_user, expected_has_milestone=False)
        self.assert_access_to_gated_content(self.non_staff_user)
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            answer_problem(self.course, self.request, self.gating_prob1, earned, max_possible)

            self.assert_user_has_prereq_milestone(self.non_staff_user, expected_has_milestone=result)
            self.assert_access_to_gated_content(self.non_staff_user)
