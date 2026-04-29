"""
Test grading with access changes.
"""


from crum import set_current_request
from xblocks_contrib.problem.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.courseware.tests.test_submitting_problems import ProblemSubmissionTestMixin
from openedx.core.djangolib.testing.utils import get_mock_request
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from ...subsection_grade_factory import SubsectionGradeFactory


class GradesAccessIntegrationTest(ProblemSubmissionTestMixin, SharedModuleStoreTestCase):
    """
    Tests integration between grading and block access.
    """
    ENABLED_SIGNALS = ['course_published']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = modulestore()
        cls.course = CourseFactory.create()
        cls.chapter = BlockFactory.create(
            parent=cls.course,
            category="chapter",
            display_name="Test Chapter"
        )
        cls.sequence = BlockFactory.create(
            parent=cls.chapter,
            category='sequential',
            display_name="Test Sequential 1",
            graded=True,
            format="Homework"
        )
        cls.vertical = BlockFactory.create(
            parent=cls.sequence,
            category='vertical',
            display_name='Test Vertical 1'
        )
        problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 2',
            choices=[False, False, True, False],
            choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
        )
        cls.problem = BlockFactory.create(
            parent=cls.vertical,
            category="problem",
            display_name="p1",
            data=problem_xml,
            metadata={'weight': 2}
        )

        cls.problem_2 = BlockFactory.create(
            parent=cls.vertical,
            category="problem",
            display_name="p2",
            data=problem_xml,
            metadata={'weight': 2}
        )

    def setUp(self):
        super().setUp()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory())
        self.student = self.request.user
        self.client.login(username=self.student.username, password=self.TEST_PASSWORD)
        CourseEnrollment.enroll(self.student, self.course.id)
        self.instructor = UserFactory.create(is_staff=True, username='test_instructor', password=self.TEST_PASSWORD)
        self.refresh_course()

    def test_subsection_access_changed(self):
        """
        Tests retrieving a subsection grade before and after losing access
        to a block in the subsection.
        """
        # submit answers
        self.submit_question_answer('p1', {'2_1': 'choice_choice_2'})
        self.submit_question_answer('p2', {'2_1': 'choice_choice_2'})

        # check initial subsection grade
        course_structure = get_course_blocks(self.request.user, self.course.location)
        subsection_grade_factory = SubsectionGradeFactory(self.request.user, self.course, course_structure)
        grade = subsection_grade_factory.create(self.sequence, read_only=True)
        assert grade.graded_total.earned == 4.0
        assert grade.graded_total.possible == 4.0

        # set a block in the subsection to be visible to staff only
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            problem_2 = self.store.get_item(self.problem_2.location)
            problem_2.visible_to_staff_only = True
            self.store.update_item(problem_2, self.instructor.id)
            self.store.publish(self.course.location, self.instructor.id)
        course_structure = get_course_blocks(self.student, self.course.location)

        # ensure that problem_2 is not accessible for the student
        assert problem_2.location not in course_structure

        # make sure we can still get the subsection grade
        subsection_grade_factory = SubsectionGradeFactory(self.student, self.course, course_structure)
        grade = subsection_grade_factory.create(self.sequence, read_only=True)
        assert grade.graded_total.earned == 4.0
        assert grade.graded_total.possible == 4.0
