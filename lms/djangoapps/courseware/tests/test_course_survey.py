"""
Python tests for the Survey workflows
"""


from collections import OrderedDict
from copy import deepcopy
from urllib.parse import quote

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.urls import reverse

from common.test.utils import XssTestMixin
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from lms.djangoapps.survey.models import SurveyAnswer, SurveyForm
from openedx.features.course_experience import course_home_url
from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class SurveyViewsTests(LoginEnrollmentTestCase, SharedModuleStoreTestCase, XssTestMixin):
    """
    All tests for the views.py file
    """
    STUDENT_INFO = [('view@test.com', 'Password1234')]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_survey_name = 'TestSurvey'
        cls.course = CourseFactory.create(
            display_name='<script>alert("XSS")</script>',
            course_survey_required=True,
            course_survey_name=cls.test_survey_name
        )

        cls.course_with_bogus_survey = CourseFactory.create(
            course_survey_required=True,
            course_survey_name="DoesNotExist"
        )

        cls.course_without_survey = CourseFactory.create()

    def setUp(self):
        """
        Set up the test data used in the specific tests
        """
        super().setUp()

        self.test_form = '<input name="field1"></input>'
        self.survey = SurveyForm.create(self.test_survey_name, self.test_form)

        self.student_answers = OrderedDict({
            'field1': 'value1',
            'field2': 'value2',
        })

        # Create student accounts and activate them.
        for i in range(len(self.STUDENT_INFO)):
            email, password = self.STUDENT_INFO[i]
            username = f'u{i}'
            self.create_account(username, email, password)
            self.activate_user(email)

        email, password = self.STUDENT_INFO[0]
        self.login(email, password)
        self.enroll(self.course, True)
        self.enroll(self.course_without_survey, True)
        self.enroll(self.course_with_bogus_survey, True)

        self.user = User.objects.get(email=email)

        self.view_url = reverse('view_survey', args=[self.test_survey_name])
        self.postback_url = reverse('submit_answers', args=[self.test_survey_name])

    def _assert_survey_redirect(self, course):
        """
        Helper method to assert that all known redirect points do redirect as expected
        """
        for view_name in ['courseware', 'progress']:
            resp = self.client.get(
                reverse(
                    view_name,
                    kwargs={'course_id': str(course.id)}
                )
            )
            self.assertRedirects(
                resp,
                reverse('course_survey', kwargs={'course_id': str(course.id)})
            )

    def _assert_no_survey_redirect(self, course):
        """
        Helper method to assert that all known conditionally redirecting endpoints do
        not redirect to the survey as expected
        """

        # Make sure we get to the progress page.
        resp = self.client.get(
            reverse(
                'progress',
                kwargs={'course_id': str(course.id)}
            )
        )
        assert resp.status_code == 200

        # Make sure we are redirected to the MFE for courseware
        resp = self.client.get(
            reverse(
                'courseware',
                kwargs={'course_id': str(course.id)}
            )
        )
        assert resp.status_code == 302
        expected_redirect_url = make_learning_mfe_courseware_url(
            course.id,
            None,
            None,
            params=None,
            preview=False
        )
        assert resp.url == expected_redirect_url

    def test_visiting_course_without_survey(self):
        """
        Verifies that going to the courseware which does not have a survey does
        not redirect to a survey
        """
        self._assert_no_survey_redirect(self.course_without_survey)

    def test_visiting_course_with_survey_redirects(self):
        """
        Verifies that going to the courseware with an unanswered survey, redirects to the survey
        """
        self._assert_survey_redirect(self.course)

    def test_anonymous_user_visiting_course_with_survey(self):
        """
        Verifies that anonymous user going to the course with an unanswered survey is not
        redirected to survey.
        """
        self.logout()
        resp = self.client.get(
            reverse(
                'courseware',
                kwargs={'course_id': str(self.course.id)}
            )
        )
        self.assertRedirects(
            resp,
            f'/login?next=/courses/{quote(str(self.course.id))}/courseware'
        )

    def test_visiting_course_with_existing_answers(self):
        """
        Verifies that going to the courseware with an answered survey, there is no redirect
        """
        resp = self.client.post(
            self.postback_url,
            self.student_answers
        )
        assert resp.status_code == 200

        self._assert_no_survey_redirect(self.course)

    def test_course_id_field(self):
        """
        Assert that the course_id will be in the form fields, if available
        """

        resp = self.client.get(
            reverse(
                'course_survey',
                kwargs={'course_id': str(self.course.id)}
            )
        )

        assert resp.status_code == 200
        expected = '<input type="hidden" name="course_id" value="{course_id}" />'.format(  # noqa: UP032
            course_id=str(self.course.id)
        )

        self.assertContains(resp, expected)

    def test_course_id_persists(self):
        """
        Assert that a posted back course_id is stored in the database
        """

        answers = deepcopy(self.student_answers)
        answers.update({
            'course_id': str(self.course.id)
        })

        resp = self.client.post(
            self.postback_url,
            answers
        )
        assert resp.status_code == 200

        self._assert_no_survey_redirect(self.course)

        # however we want to make sure we persist the course_id
        answer_objs = SurveyAnswer.objects.filter(
            user=self.user,
            form=self.survey
        )

        for answer_obj in answer_objs:
            assert answer_obj.course_key == self.course.id

    def test_visiting_course_with_bogus_survey(self):
        """
        Verifies that going to the courseware with a required, but non-existing survey, does not redirect
        """
        self._assert_no_survey_redirect(self.course_with_bogus_survey)

    def test_visiting_survey_with_bogus_survey_name(self):
        """
        Verifies that going to the courseware with a required, but non-existing survey, does not redirect
        """

        resp = self.client.get(
            reverse(
                'course_survey',
                kwargs={'course_id': str(self.course_with_bogus_survey.id)}
            )
        )
        self.assertRedirects(
            resp,
            course_home_url(self.course_with_bogus_survey.id),
            fetch_redirect_response=False,
        )

    def test_visiting_survey_with_no_course_survey(self):
        """
        Verifies that going to the courseware with a required, but non-existing survey, does not redirect
        """

        resp = self.client.get(
            reverse(
                'course_survey',
                kwargs={'course_id': str(self.course_without_survey.id)}
            )
        )
        self.assertRedirects(
            resp,
            course_home_url(self.course_without_survey.id),
            fetch_redirect_response=False,
        )

    def test_survey_xss(self):
        """Test that course display names are correctly HTML-escaped."""
        response = self.client.get(
            reverse(
                'course_survey',
                kwargs={'course_id': str(self.course.id)}
            )
        )
        self.assert_no_xss(response, '<script>alert("XSS")</script>')
