"""
Python tests for the Survey models
"""


from collections import OrderedDict

from django.test.client import Client

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.survey.models import SurveyForm
from lms.djangoapps.survey.utils import check_survey_required_and_unanswered, is_survey_required_for_course
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class SurveyModelsTests(ModuleStoreTestCase):
    """
    All tests for the utils.py file
    """

    def setUp(self):
        """
        Set up the test data used in the specific tests
        """
        super().setUp()

        self.client = Client()

        # Create two accounts
        self.password = self.TEST_PASSWORD
        self.student = UserFactory.create(
            username='student', email='student@test.com', password=self.password,
        )
        self.student2 = UserFactory.create(
            username='student2', email='student2@test.com', password=self.password,
        )

        self.staff = UserFactory.create(
            username='staff', email='staff@test.com', password=self.password,
        )
        self.staff.is_staff = True
        self.staff.save()

        self.test_survey_name = 'TestSurvey'
        self.test_form = '<input name="foo"></input>'

        self.student_answers = OrderedDict({
            'field1': 'value1',
            'field2': 'value2',
        })

        self.student2_answers = OrderedDict({
            'field1': 'value3'
        })

        self.course = CourseFactory.create(
            course_survey_required=True,
            course_survey_name=self.test_survey_name
        )

        self.survey = SurveyForm.create(self.test_survey_name, self.test_form)

    def test_is_survey_required_for_course(self):
        """
        Assert the a requried course survey is when both the flags is set and a survey name
        is set on the course block
        """
        assert is_survey_required_for_course(self.course)

    def test_is_survey_not_required_for_course(self):
        """
        Assert that if various data is not available or if the survey is not found
        then the survey is not considered required
        """
        course = CourseFactory.create()
        assert not is_survey_required_for_course(course)

        course = CourseFactory.create(
            course_survey_required=False
        )
        assert not is_survey_required_for_course(course)

        course = CourseFactory.create(
            course_survey_required=True,
            course_survey_name="NonExisting"
        )
        assert not is_survey_required_for_course(course)

        course = CourseFactory.create(
            course_survey_required=False,
            course_survey_name=self.test_survey_name
        )
        assert not is_survey_required_for_course(course)

    def test_user_not_yet_answered_required_survey(self):
        """
        Assert that a new course which has a required survey but user has not answered it yet
        """
        assert not check_survey_required_and_unanswered(self.student, self.course)

        temp_course = CourseFactory.create(
            course_survey_required=False
        )
        assert check_survey_required_and_unanswered(self.student, temp_course)

        temp_course = CourseFactory.create(
            course_survey_required=True,
            course_survey_name="NonExisting"
        )
        assert check_survey_required_and_unanswered(self.student, temp_course)

    def test_user_has_answered_required_survey(self):
        """
        Assert that a new course which has a required survey and user has answers for it
        """
        self.survey.save_user_answers(self.student, self.student_answers, None)
        assert check_survey_required_and_unanswered(self.student, self.course)

    def test_staff_must_answer_survey(self):
        """
        Assert that someone with staff level permissions does not have to answer the survey
        """
        assert check_survey_required_and_unanswered(self.staff, self.course)
