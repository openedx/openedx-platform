"""
Python tests for the Survey views
"""


import json
from collections import OrderedDict

from django.test.client import Client
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.survey.models import SurveyAnswer, SurveyForm
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class SurveyViewsTests(ModuleStoreTestCase):
    """
    All tests for the views.py file
    """

    def setUp(self):
        """
        Set up the test data used in the specific tests
        """
        super().setUp()

        self.client = Client()

        # Create two accounts
        self.password = self.TEST_PASSWORD
        self.student = UserFactory.create(username='student', email='student@test.com', password=self.password)

        self.test_survey_name = 'TestSurvey'
        self.test_form = '''
            <input name="field1" /><input name="field2" /><select name="ddl"><option>1</option></select>
            <textarea name="textarea" />
        '''

        self.student_answers = OrderedDict({
            'field1': 'value1',
            'field2': 'value2',
            'ddl': '1',
            'textarea': 'textarea'
        })

        self.course = CourseFactory.create(
            display_name='Test Course',
            course_survey_required=True,
            course_survey_name=self.test_survey_name
        )

        self.survey = SurveyForm.create(self.test_survey_name, self.test_form)

        self.view_url = reverse('view_survey', args=[self.test_survey_name])
        self.postback_url = reverse('submit_answers', args=[self.test_survey_name])

        self.client.login(username=self.student.username, password=self.password)

    def test_unauthenticated_survey_view(self):
        """
        Asserts that an unauthenticated user cannot access a survey
        """
        anon_user = Client()

        resp = anon_user.get(self.view_url)
        assert resp.status_code == 302

    def test_survey_not_found(self):
        """
        Asserts that if we ask for a Survey that does not exist, then we get a 302 redirect
        """
        resp = self.client.get(reverse('view_survey', args=['NonExisting']))
        assert resp.status_code == 302

    def test_authenticated_survey_view(self):
        """
        Asserts that an authenticated user can see the survey
        """
        resp = self.client.get(self.view_url)

        # is the SurveyForm html present in the HTML response?
        self.assertContains(resp, self.test_form)

    def test_unauthenticated_survey_postback(self):
        """
        Asserts that an anonymous user cannot answer a survey
        """
        anon_user = Client()
        resp = anon_user.post(
            self.postback_url,
            self.student_answers
        )
        assert resp.status_code == 302

    def test_survey_postback_to_nonexisting_survey(self):
        """
        Asserts that any attempts to post back to a non existing survey returns a 404
        """
        resp = self.client.post(
            reverse('submit_answers', args=['NonExisting']),
            self.student_answers
        )
        assert resp.status_code == 404

    def test_survey_postback(self):
        """
        Asserts that a well formed postback of survey answers is properly stored in the
        database
        """
        resp = self.client.post(
            self.postback_url,
            self.student_answers
        )
        assert resp.status_code == 200
        data = json.loads(resp.content.decode('utf-8'))
        assert 'redirect_url' in data

        answers = self.survey.get_answers(self.student)
        assert answers[self.student.id] == self.student_answers

    def test_strip_extra_fields(self):
        """
        Verify that any not expected field name in the post-back is not stored
        in the database
        """
        data = dict.copy(self.student_answers)

        data['csrfmiddlewaretoken'] = 'foo'
        data['_redirect_url'] = 'bar'
        data['course_id'] = str(self.course.id)

        resp = self.client.post(
            self.postback_url,
            data
        )
        assert resp.status_code == 200
        answers = self.survey.get_answers(self.student)
        assert 'csrfmiddlewaretoken' not in answers[self.student.id]
        assert '_redirect_url' not in answers[self.student.id]
        assert 'course_id' not in answers[self.student.id]

        # however we want to make sure we persist the course_id
        answer_objs = SurveyAnswer.objects.filter(
            user=self.student,
            form=self.survey
        )

        for answer_obj in answer_objs:
            assert str(answer_obj.course_key) == data['course_id']

    def test_encoding_answers(self):
        """
        Verify that if some potentially harmful input data is sent, that is is properly HTML encoded
        """
        data = dict.copy(self.student_answers)

        data['field1'] = '<script type="javascript">alert("Deleting filesystem...")</script>'

        resp = self.client.post(
            self.postback_url,
            data
        )
        assert resp.status_code == 200
        answers = self.survey.get_answers(self.student)
        assert '&lt;script type=&quot;javascript&quot;&gt;alert(&quot;Deleting filesystem...&quot;)&lt;/script&gt;' ==\
               answers[self.student.id]['field1']
