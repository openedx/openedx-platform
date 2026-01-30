"""
Tests use cases related to LMS Entrance Exam behavior, such as gated content access (TOC)
"""


from unittest.mock import patch
from crum import set_current_request
from django.test import override_settings
from django.urls import reverse
from milestones.tests.utils import MilestonesTestCaseMixin
from lms.djangoapps.courseware.entrance_exams import (
    course_has_entrance_exam,
    get_entrance_exam_content,
    user_can_skip_entrance_exam,
    user_has_passed_entrance_exam
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.block_render import get_block, handle_xblock_callback, toc_for_course
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from openedx.core.djangolib.testing.utils import get_mock_request
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AnonymousUserFactory
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import RequestFactoryNoCsrf
from common.djangoapps.student.tests.factories import StaffFactory
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.milestones_helpers import (
    add_course_content_milestone,
    add_course_milestone,
    add_milestone,
    generate_milestone_namespace,
    get_milestone_relationship_types,
    get_namespace_choices
)


@override_settings(ENTRANCE_EXAMS=True)
class EntranceExamTestCases(LoginEnrollmentTestCase, ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Check that content is properly gated.

    Creates a test course from scratch. The tests below are designed to execute
    workflows regardless of the feature flag settings.
    """
    @override_settings(ENTRANCE_EXAMS=True)
    def setUp(self):
        """
        Test case scaffolding
        """
        super().setUp()
        self.course = CourseFactory.create(
            metadata={
                'entrance_exam_enabled': True,
            }
        )
        self.chapter = BlockFactory.create(
            parent=self.course,
            display_name='Overview'
        )
        self.welcome = BlockFactory.create(
            parent=self.chapter,
            display_name='Welcome'
        )
        BlockFactory.create(
            parent=self.course,
            category='chapter',
            display_name="Week 1"
        )
        self.chapter_subsection = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Lesson 1"
        )
        chapter_vertical = BlockFactory.create(
            parent=self.chapter_subsection,
            category='vertical',
            display_name='Lesson 1 Vertical - Unit 1'
        )
        BlockFactory.create(
            parent=chapter_vertical,
            category="problem",
            display_name="Problem - Unit 1 Problem 1"
        )
        BlockFactory.create(
            parent=chapter_vertical,
            category="problem",
            display_name="Problem - Unit 1 Problem 2"
        )

        self.entrance_exam = BlockFactory.create(
            parent=self.course,
            category="chapter",
            display_name="Entrance Exam Section - Chapter 1",
            is_entrance_exam=True,
            in_entrance_exam=True
        )
        self.exam_1 = BlockFactory.create(
            parent=self.entrance_exam,
            category='sequential',
            display_name="Exam Sequential - Subsection 1",
            graded=True,
            in_entrance_exam=True
        )
        subsection = BlockFactory.create(
            parent=self.exam_1,
            category='vertical',
            display_name='Exam Vertical - Unit 1'
        )
        problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 3',
            choices=[False, False, True, False],
            choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
        )
        self.problem_1 = BlockFactory.create(
            parent=subsection,
            category="problem",
            display_name="Exam Problem - Problem 1",
            data=problem_xml
        )
        self.problem_2 = BlockFactory.create(
            parent=subsection,
            category="problem",
            display_name="Exam Problem - Problem 2"
        )

        add_entrance_exam_milestone(self.course, self.entrance_exam)

        self.course.entrance_exam_enabled = True
        self.course.entrance_exam_minimum_score_pct = 0.50
        self.course.entrance_exam_id = str(self.entrance_exam.scope_ids.usage_id)

        self.anonymous_user = AnonymousUserFactory()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory())
        self.course = self.update_course(self.course, self.request.user.id)

        self.client.login(username=self.request.user.username, password=self.TEST_PASSWORD)
        CourseEnrollment.enroll(self.request.user, self.course.id)

        self.expected_locked_toc = (
            [
                {
                    'active': True,
                    'sections': [
                        {
                            'url_name': 'Exam_Sequential_-_Subsection_1',
                            'display_name': 'Exam Sequential - Subsection 1',
                            'graded': True,
                            'format': '',
                            'due': None,
                            'active': True
                        }
                    ],
                    'url_name': 'Entrance_Exam_Section_-_Chapter_1',
                    'display_name': 'Entrance Exam Section - Chapter 1',
                    'display_id': 'entrance-exam-section-chapter-1',
                }
            ]
        )
        self.expected_unlocked_toc = (
            [
                {
                    'active': False,
                    'sections': [
                        {
                            'url_name': 'Welcome',
                            'display_name': 'Welcome',
                            'graded': False,
                            'format': '',
                            'due': None,
                            'active': False
                        },
                        {
                            'url_name': 'Lesson_1',
                            'display_name': 'Lesson 1',
                            'graded': False,
                            'format': '',
                            'due': None,
                            'active': False
                        }
                    ],
                    'url_name': 'Overview',
                    'display_name': 'Overview',
                    'display_id': 'overview'
                },
                {
                    'active': False,
                    'sections': [],
                    'url_name': 'Week_1',
                    'display_name': 'Week 1',
                    'display_id': 'week-1'
                },
                {
                    'active': True,
                    'sections': [
                        {
                            'url_name': 'Exam_Sequential_-_Subsection_1',
                            'display_name': 'Exam Sequential - Subsection 1',
                            'graded': True,
                            'format': '',
                            'due': None,
                            'active': True
                        }
                    ],
                    'url_name': 'Entrance_Exam_Section_-_Chapter_1',
                    'display_name': 'Entrance Exam Section - Chapter 1',
                    'display_id': 'entrance-exam-section-chapter-1'
                }
            ]
        )

    def test_get_entrance_exam_content(self):
        """
        test get entrance exam content method
        """
        exam_chapter = get_entrance_exam_content(self.request.user, self.course)
        assert exam_chapter.url_name == self.entrance_exam.url_name
        assert not user_has_passed_entrance_exam(self.request.user, self.course)

        answer_entrance_exam_problem(self.course, self.request, self.problem_1)
        answer_entrance_exam_problem(self.course, self.request, self.problem_2)

        exam_chapter = get_entrance_exam_content(self.request.user, self.course)
        assert exam_chapter is None
        assert user_has_passed_entrance_exam(self.request.user, self.course)

    def test_entrance_exam_gating(self):
        """
        Unit Test: test_entrance_exam_gating
        """
        # This user helps to cover a discovered bug in the milestone fulfillment logic
        chaos_user = UserFactory()
        locked_toc = self._return_table_of_contents()
        for toc_section in self.expected_locked_toc:
            assert toc_section in locked_toc

        # Set up the chaos user
        answer_entrance_exam_problem(self.course, self.request, self.problem_1, chaos_user)
        answer_entrance_exam_problem(self.course, self.request, self.problem_1)
        answer_entrance_exam_problem(self.course, self.request, self.problem_2)

        unlocked_toc = self._return_table_of_contents()

        for toc_section in self.expected_unlocked_toc:
            assert toc_section in unlocked_toc

    def test_skip_entrance_exam_gating(self):
        """
        Tests gating is disabled if skip entrance exam is set for a user.
        """
        # make sure toc is locked before allowing user to skip entrance exam
        locked_toc = self._return_table_of_contents()
        for toc_section in self.expected_locked_toc:
            assert toc_section in locked_toc

        # hit skip entrance exam api in instructor app
        instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)
        url = reverse('mark_student_can_skip_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.request.user.email,
        })
        assert response.status_code == 200

        unlocked_toc = self._return_table_of_contents()
        for toc_section in self.expected_unlocked_toc:
            assert toc_section in unlocked_toc

    def test_entrance_exam_gating_for_staff(self):
        """
        Tests gating is disabled if user is member of staff.
        """

        # Login as member of staff
        self.client.logout()
        staff_user = StaffFactory(course_key=self.course.id)
        staff_user.is_staff = True
        self.client.login(username=staff_user.username, password=self.TEST_PASSWORD)

        # assert staff has access to all toc
        self.request.user = staff_user
        unlocked_toc = self._return_table_of_contents()
        for toc_section in self.expected_unlocked_toc:
            assert toc_section in unlocked_toc

    def test_can_skip_entrance_exam_with_anonymous_user(self):
        """
        Test can_skip_entrance_exam method with anonymous user
        """
        assert not user_can_skip_entrance_exam(self.anonymous_user, self.course)

    def test_has_passed_entrance_exam_with_anonymous_user(self):
        """
        Test has_passed_entrance_exam method with anonymous user
        """
        self.request.user = self.anonymous_user
        assert not user_has_passed_entrance_exam(self.request.user, self.course)

    def test_course_has_entrance_exam_missing_exam_id(self):
        course = CourseFactory.create(
            metadata={
                'entrance_exam_enabled': True,
            }
        )
        assert not course_has_entrance_exam(course)

    def test_user_has_passed_entrance_exam_short_circuit_missing_exam(self):
        course = CourseFactory.create(
        )
        assert user_has_passed_entrance_exam(self.request.user, course)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_MASQUERADE': False})
    def test_entrance_exam_xblock_response(self):
        """
        Tests entrance exam xblock has `entrance_exam_passed` key in json response.
        """
        request_factory = RequestFactoryNoCsrf()
        data = {f'input_{str(self.problem_1.location.html_id())}_2_1': 'choice_2'}
        request = request_factory.post(
            'problem_check',
            data=data
        )
        request.user = self.user
        response = handle_xblock_callback(
            request,
            str(self.course.id),
            str(self.problem_1.location),
            'xmodule_handler',
            'problem_check',
        )
        assert response.status_code == 200
        self.assertContains(response, 'entrance_exam_passed')

    def _return_table_of_contents(self):
        """
        Returns table of content for the entrance exam specific to this test

        Returns the table of contents for course self.course, for chapter
        self.entrance_exam, and for section self.exam1
        """
        self.field_data_cache = FieldDataCache.cache_for_block_descendents(  # pylint: disable=attribute-defined-outside-init
            self.course.id,
            self.request.user,
            self.entrance_exam
        )
        toc = toc_for_course(
            self.request.user,
            self.request,
            self.course,
            self.entrance_exam.url_name,
            self.exam_1.url_name,
            self.field_data_cache
        )
        return toc['chapters']


def answer_entrance_exam_problem(course, request, problem, user=None, value=1, max_value=1):
    """
    Takes a required milestone `problem` in a `course` and fulfills it.

    Args:
        course (Course): Course object, the course the required problem is in
        request (Request): request Object
        problem (xblock): xblock object, the problem to be fulfilled
        user (User): User object in case it is different from request.user
        value (int): raw_earned value of the problem
        max_value (int): raw_possible value of the problem
    """
    if not user:
        user = request.user

    grade_dict = {'value': value, 'max_value': max_value, 'user_id': user.id}
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        course.id,
        user,
        course,
        depth=2
    )
    block = get_block(
        user,
        request,
        problem.scope_ids.usage_id,
        field_data_cache,
    )
    block.runtime.publish(problem, 'grade', grade_dict)


def add_entrance_exam_milestone(course, entrance_exam):
    """
    Adds the milestone for given `entrance_exam` in `course`

    Args:
        course (Course): Course object in which the extrance_exam is located
        entrance_exam (xblock): the entrance exam to be added as a milestone
    """
    namespace_choices = get_namespace_choices()
    milestone_relationship_types = get_milestone_relationship_types()

    milestone_namespace = generate_milestone_namespace(
        namespace_choices.get('ENTRANCE_EXAM'),
        course.id
    )
    milestone = add_milestone(
        {
            'name': 'Test Milestone',
            'namespace': milestone_namespace,
            'description': 'Testing Courseware Entrance Exam Chapter',
        }
    )
    add_course_milestone(
        str(course.id),
        milestone_relationship_types['REQUIRES'],
        milestone
    )
    add_course_content_milestone(
        str(course.id),
        str(entrance_exam.location),
        milestone_relationship_types['FULFILLS'],
        milestone
    )
