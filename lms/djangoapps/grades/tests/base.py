"""
Base file for Grades tests
"""


from crum import set_current_request
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

from xblocks_contrib.problem.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from openedx.core.djangolib.testing.utils import get_mock_request

from ..course_data import CourseData
from ..subsection_grade_factory import SubsectionGradeFactory


class GradeTestBase(SharedModuleStoreTestCase):
    """
    Base class for some Grades tests.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        with cls.store.bulk_operations(cls.course.id):
            cls.chapter = BlockFactory.create(
                parent=cls.course,
                category="chapter",
                display_name="Test Chapter"
            )
            cls.sequence = BlockFactory.create(
                parent=cls.chapter,
                category='sequential',
                display_name="Test Sequential X with an & Ampersand",
                graded=True,
                format="Homework"
            )
            cls.vertical = BlockFactory.create(
                parent=cls.sequence,
                category='vertical',
                display_name='Test Vertical 1'
            )
            problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
                question_text='The correct answer is Choice 3',
                choices=[False, False, True, False],
                choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
            )
            cls.problem = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                display_name="Test Problem",
                data=problem_xml
            )
            cls.sequence2 = BlockFactory.create(
                parent=cls.chapter,
                category='sequential',
                display_name="Test Sequential A",
                graded=True,
                format="Homework"
            )
            cls.problem2 = BlockFactory.create(
                parent=cls.sequence2,
                category="problem",
                display_name="Test Problem 2",
                data=problem_xml
            )
            # AED 2017-06-19: make cls.sequence belong to multiple parents,
            # so we can test that DAGs with this shape are handled correctly.
            cls.chapter_2 = BlockFactory.create(
                parent=cls.course,
                category='chapter',
                display_name='Test Chapter 2'
            )
            cls.chapter_2.children.append(cls.sequence.location)
            cls.store.update_item(cls.chapter_2, UserFactory().id)

    def setUp(self):
        super().setUp()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory())
        self.client.login(username=self.request.user.username, password="test")
        self._set_grading_policy()
        self.course_structure = get_course_blocks(self.request.user, self.course.location)
        self.course_data = CourseData(self.request.user, structure=self.course_structure)
        self.subsection_grade_factory = SubsectionGradeFactory(self.request.user, self.course, self.course_structure)
        CourseEnrollment.enroll(self.request.user, self.course.id)

    def _set_grading_policy(self, passing=0.5):
        """
        Updates the course's grading policy.
        """
        self.grading_policy = {
            "GRADER": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "HW",
                    "weight": 1.0,
                },
                {
                    "type": "NoCredit",
                    "min_count": 0,
                    "drop_count": 0,
                    "short_label": "NC",
                    "weight": 0.0,
                },
            ],
            "GRADE_CUTOFFS": {
                "Pass": passing,
            },
        }
        self.course.set_grading_policy(self.grading_policy)
        self.store.update_item(self.course, 0)
