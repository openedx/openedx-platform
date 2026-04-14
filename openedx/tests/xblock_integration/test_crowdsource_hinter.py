"""
Test scenarios for the crowdsource hinter xblock.
"""


import unittest

import simplejson as json
from django.conf import settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import GlobalStaffFactory
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from openedx.core.lib.url_utils import quote_slashes
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestCrowdsourceHinter(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Create the test environment with the crowdsourcehinter xblock.
    """
    STUDENTS = [
        {'email': 'view@test.com', 'password': SharedModuleStoreTestCase.TEST_PASSWORD},
        {'email': 'view2@test.com', 'password': SharedModuleStoreTestCase.TEST_PASSWORD}
    ]
    XBLOCK_NAMES = ['crowdsourcehinter']

    @classmethod
    def setUpClass(cls):
        # Nose runs setUpClass methods even if a class decorator says to skip
        # the class: https://github.com/nose-devs/nose/issues/946
        # So, skip the test class here if we are not in the LMS.
        if settings.ROOT_URLCONF != 'lms.urls':
            raise unittest.SkipTest('Test only valid in lms')

        super().setUpClass()
        cls.course = CourseFactory.create(
            display_name='CrowdsourceHinter_Test_Course'
        )
        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.chapter = BlockFactory.create(
                parent=cls.course, display_name='Overview'
            )
            cls.section = BlockFactory.create(
                parent=cls.chapter, display_name='Welcome'
            )
            cls.unit = BlockFactory.create(
                parent=cls.section, display_name='New Unit'
            )
            cls.xblock = BlockFactory.create(
                parent=cls.unit,
                category='crowdsourcehinter',
                display_name='crowdsourcehinter'
            )

        cls.course_url = reverse(
            'courseware_subsection',
            kwargs={
                'course_id': str(cls.course.id),
                'section': 'Overview',
                'subsection': 'Welcome',
            }
        )

    def setUp(self):
        super().setUp()
        for idx, student in enumerate(self.STUDENTS):
            username = f"u{idx}"
            self.create_account(username, student['email'], student['password'])
            self.activate_user(student['email'])

        self.staff_user = GlobalStaffFactory()

    def get_handler_url(self, handler, xblock_name=None):
        """
        Get url for the specified xblock handler
        """
        if xblock_name is None:
            xblock_name = TestCrowdsourceHinter.XBLOCK_NAMES[0]
        return reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': quote_slashes(str(self.course.id.make_usage_key('crowdsourcehinter', xblock_name))),
            'handler': handler,
            'suffix': ''
        })

    def enroll_student(self, email, password):
        """
        Student login and enroll for the course
        """
        self.login(email, password)
        self.enroll(self.course, verify=True)

    def enroll_staff(self, staff):
        """
        Staff login and enroll for the course
        """
        email = staff.email
        password = 'test'
        self.login(email, password)
        self.enroll(self.course, verify=True)

    def initialize_database_by_id(self, handler, resource_id, times, xblock_name=None):
        """
        Call a ajax event (vote, delete, endorse) on a resource by its id
        several times
        """
        if xblock_name is None:
            xblock_name = TestCrowdsourceHinter.XBLOCK_NAMES[0]
        url = self.get_handler_url(handler, xblock_name)
        for _ in range(times):
            self.client.post(url, json.dumps({'id': resource_id}), '')

    def call_event(self, handler, resource, xblock_name=None):
        """
        Call a ajax event (add, edit, flag, etc.) by specifying the resource
        it takes
        """
        if xblock_name is None:
            xblock_name = TestCrowdsourceHinter.XBLOCK_NAMES[0]
        url = self.get_handler_url(handler, xblock_name)
        return self.client.post(url, json.dumps(resource), '')

    def check_event_response_by_element(self, handler, resource, resp_key, resp_val, xblock_name=None):
        """
        Call the event specified by the handler with the resource, and check
        whether the element (resp_key) in response is as expected (resp_val)
        """
        if xblock_name is None:
            xblock_name = TestCrowdsourceHinter.XBLOCK_NAMES[0]
        resp = self.call_event(handler, resource, xblock_name)
        assert resp[resp_key] == resp_val
        self.assert_request_status_code(200, self.course_url)


class TestHinterFunctions(TestCrowdsourceHinter):
    """
    Check that the essential functions of the hinter work as expected.
    Tests cover the basic process of receiving a hint, adding a new hint,
    and rating/reporting hints.
    """

    def test_get_hint_with_no_hints(self):
        """
        Check that a generic statement is returned when no default/specific hints exist
        """
        result = self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'}, 'crowdsourcehinter')
        expected = {'BestHint': 'Sorry, there are no hints for this answer.', 'StudentAnswer': 'incorrect answer 1',
                    'HintCategory': False}
        assert json.loads(result.content) == expected

    def test_add_new_hint(self):
        """
        Test the ability to add a new specific hint
        """
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        data = {'new_hint_submission': 'new hint for answer 1', 'answer': 'incorrect answer 1'}
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        result = self.call_event('add_new_hint', data)
        expected = {'success': True,
                    'result': 'Hint added'}
        assert json.loads(result.content) == expected

    def test_get_hint(self):
        """
        Check that specific hints are returned
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        result = self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        expected = {'BestHint': 'new hint for answer 1', 'StudentAnswer': 'incorrect answer 1',
                    'HintCategory': 'ErrorResponse'}
        assert json.loads(result.content) == expected

    def test_rate_hint_upvote(self):
        """
        Test hint upvoting
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        data = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1',
            'student_rating': 'upvote'
        }
        expected = {'success': True}
        result = self.call_event('rate_hint', data)
        assert json.loads(result.content) == expected

    def test_rate_hint_downvote(self):
        """
        Test hint downvoting
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        data = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1',
            'student_rating': 'downvote'
        }
        expected = {'success': True}
        result = self.call_event('rate_hint', data)
        assert json.loads(result.content) == expected

    def test_report_hint(self):
        """
        Test hint reporting
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        data = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1',
            'student_rating': 'report'
        }
        expected = {'rating': 'reported', 'hint': 'new hint for answer 1'}
        result = self.call_event('rate_hint', data)
        assert json.loads(result.content) == expected

    def test_dont_show_reported_hint(self):
        """
        Check that reported hints are returned
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        data = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1',
            'student_rating': 'report'
        }
        self.call_event('rate_hint', data)
        result = self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        expected = {'BestHint': 'Sorry, there are no hints for this answer.', 'StudentAnswer': 'incorrect answer 1',
                    'HintCategory': False}
        assert json.loads(result.content) == expected

    def test_get_used_hint_answer_data(self):
        """
        Check that hint/answer information from previous submissions are returned upon correctly
        answering the problem
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        self.call_event('get_used_hint_answer_data', "")
        submission = {'new_hint_submission': 'new hint for answer 1',
                      'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission)
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        result = self.call_event('get_used_hint_answer_data', "")
        expected = {'new hint for answer 1': 'incorrect answer 1'}
        assert json.loads(result.content) == expected

    def test_show_best_hint(self):
        """
        Check that the most upvoted hint is shown
        """
        self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        submission1 = {'new_hint_submission': 'new hint for answer 1',
                       'answer': 'incorrect answer 1'}
        submission2 = {'new_hint_submission': 'new hint for answer 1 to report',
                       'answer': 'incorrect answer 1'}
        self.call_event('add_new_hint', submission1)
        self.call_event('add_new_hint', submission2)
        data_upvote = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1 to report',
            'student_rating': 'upvote'
        }
        self.call_event('rate_hint', data_upvote)
        data_downvote = {
            'student_answer': 'incorrect answer 1',
            'hint': 'new hint for answer 1 to report',
            'student_rating': 'report'
        }
        self.call_event('rate_hint', data_downvote)
        result = self.call_event('get_hint', {'submittedanswer': 'ans=incorrect+answer+1'})
        expected = {'BestHint': 'new hint for answer 1', 'StudentAnswer': 'incorrect answer 1',
                    'HintCategory': 'ErrorResponse'}
        assert json.loads(result.content) == expected
