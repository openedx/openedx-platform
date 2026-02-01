"""
Unit tests for instructor.api methods.
"""
import datetime
import functools
import io
import json
import random
import shutil
import tempfile
from unittest.mock import Mock, NonCallableMock, patch

import dateutil
import ddt
import pytest
import pytz
from botocore.exceptions import ClientError
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, TestCase
from django.test.client import MULTIPART_CONTENT
from django.test.utils import override_settings
from django.urls import reverse as django_reverse
from django.utils.translation import gettext as _
from edx_toggles.toggles.testutils import override_waffle_flag
from edx_when.api import get_dates_for_course, get_overrides_for_user, set_date_for_block
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import UsageKey
from pytz import UTC
from testfixtures import LogCapture
from xblock.fields import Date

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    CourseAccessRole,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    ManualEnrollmentAudit,
    NonExistentCourseError,
    get_retired_email_by_email,
    get_retired_username_by_username
)
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseDataResearcherRole,
    CourseFinanceAdminRole,
    CourseInstructorRole
)
from common.djangoapps.student.tests.factories import (
    BetaTesterFactory,
    CourseAccessRoleFactory,
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory
)

from lms.djangoapps.bulk_email.models import BulkEmailFlag, CourseEmail, CourseEmailTemplate
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from lms.djangoapps.instructor.tests.utils import FakeContentTask, FakeEmail, FakeEmailInfo
from lms.djangoapps.instructor.views.api import (
    _get_certificate_for_user,
    _get_student_from_request_data,
    _split_input_list,
    common_exceptions_400,
    generate_unique_password,
    require_finance_admin
)
from lms.djangoapps.instructor_task.api_helper import (
    AlreadyRunningError,
    QueueConnectionError,
    generate_already_running_error_message
)
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import InstructorTask, InstructorTaskSchedule
from lms.djangoapps.program_enrollments.tests.factories import ProgramEnrollmentFactory
from openedx.core.djangoapps.course_date_signals.handlers import extract_dates
from openedx.core.djangoapps.course_groups.cohorts import set_course_cohorted
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_COMMUNITY_TA, Role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.oauth_dispatch import jwt as jwt_api
from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.preferences.api import delete_user_preference
from openedx.core.lib.teams_config import TeamsConfig
from openedx.core.lib.xblock_utils import grade_histogram
from openedx.features.course_experience import RELATIVE_DATES_FLAG
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase
)
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .test_tools import msk_from_problem_urlname

LOG_PATH = "lms.djangoapps.instructor.views.api"
DATE_FIELD = Date()
EXPECTED_CSV_HEADER = (
    '"code","redeem_code_url","course_id","company_name","created_by","redeemed_by","invoice_id","purchaser",'
    '"customer_reference_number","internal_reference"'
)

# ddt data for test cases involving reports
REPORTS_DATA = (
    {
        'report_type': 'grade',
        'instructor_api_endpoint': 'calculate_grades_csv',
        'task_api_endpoint': 'lms.djangoapps.instructor_task.api.submit_calculate_grades_csv',
        'extra_instructor_api_kwargs': {}
    },
    {
        'report_type': 'enrolled learner profile',
        'instructor_api_endpoint': 'get_students_features',
        'task_api_endpoint': 'lms.djangoapps.instructor_task.api.submit_calculate_students_features_csv',
        'extra_instructor_api_kwargs': {'csv': '/csv'}
    },
    {
        'report_type': 'enrollment',
        'instructor_api_endpoint': 'get_students_who_may_enroll',
        'task_api_endpoint': 'lms.djangoapps.instructor_task.api.submit_calculate_may_enroll_csv',
        'extra_instructor_api_kwargs': {},
    },
    {
        'report_type': 'proctored exam results',
        'instructor_api_endpoint': 'get_proctored_exam_results',
        'task_api_endpoint': 'lms.djangoapps.instructor_task.api.submit_proctored_exam_results_report',
        'extra_instructor_api_kwargs': {},
    },
    {
        'report_type': 'problem responses',
        'instructor_api_endpoint': 'get_problem_responses',
        'task_api_endpoint': 'lms.djangoapps.instructor_task.api.submit_calculate_problem_responses_csv',
        'extra_instructor_api_kwargs': {},
    }
)


INSTRUCTOR_GET_ENDPOINTS = {
    'get_anon_ids',
    'get_issued_certificates',
    'instructor_api_v1:list_instructor_tasks',
    'instructor_api_v1:list_report_downloads',
}
INSTRUCTOR_POST_ENDPOINTS = {
    'add_users_to_cohorts',
    'bulk_beta_modify_access',
    'calculate_grades_csv',
    'change_due_date',
    'instructor_api_v2:change_due_date',
    'export_ora2_data',
    'export_ora2_submission_files',
    'export_ora2_summary',
    'get_grading_config',
    'get_problem_responses',
    'get_proctored_exam_results',
    'get_student_enrollment_status',
    'get_student_progress_url',
    'get_students_features',
    'get_students_who_may_enroll',
    'list_background_email_tasks',
    'list_course_role_members',
    'list_email_content',
    'list_entrance_exam_instructor_tasks',
    'list_forum_members',
    'list_instructor_tasks',
    'list_report_downloads',
    'mark_student_can_skip_entrance_exam',
    'modify_access',
    'register_and_enroll_students',
    'rescore_entrance_exam',
    'rescore_problem',
    'reset_due_date',
    'reset_student_attempts',
    'reset_student_attempts_for_entrance_exam',
    'show_student_extensions',
    'show_unit_extensions',
    'send_email',
    'students_update_enrollment',
    'update_forum_role_membership',
    'override_problem_score',
    'instructor_api_v1:generate_problem_responses'
}


def reverse(endpoint, args=None, kwargs=None, is_dashboard_endpoint=True):
    """
    Simple wrapper of Django's reverse that first ensures that we have declared
    each endpoint under test.

    Arguments:
        args: The args to be passed through to reverse.
        endpoint: The endpoint to be passed through to reverse.
        kwargs: The kwargs to be passed through to reverse.
        is_dashboard_endpoint: True if this is an instructor dashboard endpoint
            that must be declared in the INSTRUCTOR_GET_ENDPOINTS or
            INSTRUCTOR_GET_ENDPOINTS sets, or false otherwise.

    Returns:
        The return of Django's reverse function

    """
    is_endpoint_declared = endpoint in INSTRUCTOR_GET_ENDPOINTS or endpoint in INSTRUCTOR_POST_ENDPOINTS
    if is_dashboard_endpoint and is_endpoint_declared is False:
        # Verify that all endpoints are declared so we can ensure they are
        # properly validated elsewhere.
        raise ValueError(f"The endpoint {endpoint} must be declared in ENDPOINTS before use.")
    return django_reverse(endpoint, args=args, kwargs=kwargs)


@common_exceptions_400
def view_success(request):
    "A dummy view for testing that returns a simple HTTP response"
    return HttpResponse('success')


@common_exceptions_400
def view_user_doesnotexist(request):
    "A dummy view that raises a User.DoesNotExist exception"
    raise User.DoesNotExist()


@common_exceptions_400
def view_alreadyrunningerror(request):
    "A dummy view that raises an AlreadyRunningError exception"
    raise AlreadyRunningError()


@common_exceptions_400
def view_alreadyrunningerror_unicode(request):
    """
    A dummy view that raises an AlreadyRunningError exception with unicode message
    """
    raise AlreadyRunningError('Text with unicode chárácters')


@common_exceptions_400
def view_queue_connection_error(request):
    """
    A dummy view that raises a QueueConnectionError exception.
    """
    raise QueueConnectionError()


@ddt.ddt
class TestCommonExceptions400(TestCase):
    """
    Testing the common_exceptions_400 decorator.
    """

    def setUp(self):
        super().setUp()
        self.request = Mock(spec=HttpRequest)
        self.request.META = {}

    def test_happy_path(self):
        resp = view_success(self.request)
        assert resp.status_code == 200

    def test_user_doesnotexist(self):
        self.request.accepts("application/json").return_value = False
        resp = view_user_doesnotexist(self.request)
        self.assertContains(resp, "User does not exist", status_code=400)

    def test_user_doesnotexist_ajax(self):
        self.request.accepts("application/json").return_value = True
        resp = view_user_doesnotexist(self.request)
        self.assertContains(resp, "User does not exist", status_code=400)

    @ddt.data(True, False)
    def test_alreadyrunningerror(self, is_ajax):
        self.request.accepts("application/json").return_value = is_ajax
        resp = view_alreadyrunningerror(self.request)
        self.assertContains(resp, "Requested task is already running", status_code=400)

    @ddt.data(True, False)
    def test_alreadyrunningerror_with_unicode(self, is_ajax):
        self.request.accepts("application/json").return_value = is_ajax
        resp = view_alreadyrunningerror_unicode(self.request)
        self.assertContains(
            resp,
            'Text with unicode chárácters',
            status_code=400,
        )

    @ddt.data(True, False)
    def test_queue_connection_error(self, is_ajax):
        """
        Tests that QueueConnectionError exception is handled in common_exception_400.
        """
        self.request.accepts("application/json").return_value = is_ajax
        resp = view_queue_connection_error(self.request)
        self.assertContains(
            resp,
            'Error occured. Please try again later',
            status_code=400,
        )


@ddt.ddt
class TestEndpointHttpMethods(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Ensure that users can make GET requests against endpoints that allow GET,
    and not against those that don't allow GET.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up test course.
        """
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        """
        Set up global staff role so authorization will not fail.
        """
        super().setUp()
        global_user = GlobalStaffFactory()
        self.client.login(username=global_user.username, password=self.TEST_PASSWORD)

    @ddt.data(*INSTRUCTOR_POST_ENDPOINTS)
    def test_endpoints_reject_get(self, data):
        """
        Tests that POST endpoints are rejected with 405 when using GET.
        """
        url = reverse(data, kwargs={'course_id': str(self.course.id)})
        response = self.client.get(url)

        assert response.status_code == 405, \
            f'Endpoint {data} returned status code {response.status_code} instead of a 405. It should not allow GET.'

    @ddt.data(*INSTRUCTOR_GET_ENDPOINTS)
    def test_endpoints_accept_get(self, data):
        """
        Tests that GET endpoints are not rejected with 405 when using GET.
        """
        url = reverse(data, kwargs={'course_id': str(self.course.id)})
        response = self.client.get(url)

        assert response.status_code != 405, \
            f"Endpoint {data} returned status code 405 where it shouldn't, since it should allow GET."


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class TestInstructorAPIDenyLevels(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Ensure that users cannot access endpoints they shouldn't be able to.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.chapter = BlockFactory.create(
            parent=cls.course,
            category='chapter',
            display_name="Chapter",
            publish_item=True,
            start=datetime.datetime(2018, 3, 10, tzinfo=UTC),
        )
        cls.sequential = BlockFactory.create(
            parent=cls.chapter,
            category='sequential',
            display_name="Lesson",
            publish_item=True,
            start=datetime.datetime(2018, 3, 10, tzinfo=UTC),
            metadata={'graded': True, 'format': 'Homework'},
        )
        cls.vertical = BlockFactory.create(
            parent=cls.sequential,
            category='vertical',
            display_name='Subsection',
            publish_item=True,
            start=datetime.datetime(2018, 3, 10, tzinfo=UTC),
        )
        cls.problem = BlockFactory.create(
            category="problem",
            parent=cls.vertical,
            display_name="A Problem Block",
            weight=1,
            publish_item=True,
        )

        cls.problem_urlname = str(cls.problem.location)
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        BulkEmailFlag.objects.all().delete()

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        CourseEnrollment.enroll(self.user, self.course.id)

        _module = StudentModule.objects.create(
            student=self.user,
            course_id=self.course.id,
            module_state_key=self.problem.location,
            state=json.dumps({'attempts': 10}),
        )

        # Endpoints that only Staff or Instructors can access
        self.staff_level_endpoints = [
            ('students_update_enrollment',
             {'identifiers': 'foo@example.org', 'action': 'enroll'}),
            ('get_grading_config', {}),
            ('get_students_features', {}),
            ('get_student_progress_url', {'unique_student_identifier': self.user.username}),
            ('update_forum_role_membership',
             {'unique_student_identifier': self.user.email, 'rolename': 'Moderator', 'action': 'allow'}),
            ('list_forum_members', {'rolename': FORUM_ROLE_COMMUNITY_TA}),
            ('send_email', {'send_to': '["staff"]', 'subject': 'test', 'message': 'asdf'}),
            ('list_instructor_tasks', {}),
            ('instructor_api_v1:list_instructor_tasks', {}),
            ('list_background_email_tasks', {}),
            ('instructor_api_v1:list_report_downloads', {}),
            ('calculate_grades_csv', {}),
            ('get_students_features', {}),
            ('get_students_who_may_enroll', {}),
            ('get_proctored_exam_results', {}),
            ('get_problem_responses', {}),
            ('instructor_api_v1:generate_problem_responses', {"problem_locations": [str(self.problem.location)]}),
            ('export_ora2_data', {}),
            ('export_ora2_submission_files', {}),
            ('export_ora2_summary', {}),
            ('rescore_problem',
             {'problem_to_reset': self.problem_urlname, 'unique_student_identifier': self.user.email}),
            ('override_problem_score',
             {'problem_to_reset': self.problem_urlname, 'unique_student_identifier': self.user.email, 'score': 0}),
            ('reset_student_attempts',
             {'problem_to_reset': self.problem_urlname, 'unique_student_identifier': self.user.email}),
            (
                'reset_student_attempts',
                {
                    'problem_to_reset': self.problem_urlname,
                    'unique_student_identifier': self.user.email,
                    'delete_module': True
                }
            ),
        ]
        # Endpoints that only Instructors can access
        self.instructor_level_endpoints = [
            ('bulk_beta_modify_access', {'identifiers': 'foo@example.org', 'action': 'add'}),
            ('modify_access', {'unique_student_identifier': self.user.email, 'rolename': 'beta', 'action': 'allow'}),
            ('list_course_role_members', {'rolename': 'beta'}),
            ('rescore_problem', {'problem_to_reset': self.problem_urlname, 'all_students': True}),
            ('reset_student_attempts', {'problem_to_reset': self.problem_urlname, 'all_students': True}),
        ]

    def _access_endpoint(self, endpoint, args, status_code, msg, content_type=MULTIPART_CONTENT):
        """
        Asserts that accessing the given `endpoint` gets a response of `status_code`.

        endpoint: string, endpoint for instructor dash API
        args: dict, kwargs for `reverse` call
        status_code: expected HTTP status code response
        msg: message to display if assertion fails.
        """
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        if endpoint in INSTRUCTOR_GET_ENDPOINTS:
            response = self.client.get(url, args)
        else:
            response = self.client.post(url, args, content_type=content_type)
        assert response.status_code == status_code, msg

    def test_student_level(self):
        """
        Ensure that an enrolled student can't access staff or instructor endpoints.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        for endpoint, args in self.staff_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Student should not be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Student should not be allowed to access endpoint " + endpoint
            )

    def _access_problem_responses_endpoint(self, endpoint, msg):
        """
        Access endpoint for problem responses report, ensuring that
        UsageKey.from_string returns a problem key that the endpoint
        can work with.

        msg: message to display if assertion fails.
        """
        mock_problem_key = NonCallableMock(return_value='')
        mock_problem_key.course_key = self.course.id
        with patch.object(UsageKey, 'from_string') as patched_method:
            patched_method.return_value = mock_problem_key
            self._access_endpoint(endpoint, {"problem_locations": ["test"]}, 200, msg, content_type="application/json")

    def test_staff_level(self):
        """
        Ensure that a staff member can't access instructor endpoints.
        """
        staff_member = StaffFactory(course_key=self.course.id)
        CourseEnrollment.enroll(staff_member, self.course.id)
        CourseFinanceAdminRole(self.course.id).add_users(staff_member)
        CourseDataResearcherRole(self.course.id).add_users(staff_member)
        self.client.login(username=staff_member.username, password=self.TEST_PASSWORD)
        # Try to promote to forums admin - not working
        # update_forum_role(self.course.id, staff_member, FORUM_ROLE_ADMINISTRATOR, 'allow')

        for endpoint, args in self.staff_level_endpoints:
            expected_status = 200

            # TODO: make these work
            if endpoint in ['update_forum_role_membership', 'list_forum_members']:
                continue
            elif endpoint in ('get_problem_responses', 'instructor_api_v1:generate_problem_responses'):
                self._access_problem_responses_endpoint(
                    endpoint,
                    "Staff member should be allowed to access endpoint " + endpoint
                )
                continue
            self._access_endpoint(
                endpoint,
                args,
                expected_status,
                "Staff member should be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            self._access_endpoint(
                endpoint,
                args,
                403,
                "Staff member should not be allowed to access endpoint " + endpoint
            )

    def test_instructor_level(self):
        """
        Ensure that an instructor member can access all endpoints.
        """
        inst = InstructorFactory(course_key=self.course.id)
        CourseEnrollment.enroll(inst, self.course.id)

        CourseFinanceAdminRole(self.course.id).add_users(inst)
        CourseDataResearcherRole(self.course.id).add_users(inst)
        self.client.login(username=inst.username, password=self.TEST_PASSWORD)

        for endpoint, args in self.staff_level_endpoints:
            expected_status = 200

            # TODO: make these work
            if endpoint in ['update_forum_role_membership']:
                continue
            elif endpoint in ('get_problem_responses', 'instructor_api_v1:generate_problem_responses'):
                self._access_problem_responses_endpoint(
                    endpoint,
                    "Instructor should be allowed to access endpoint " + endpoint
                )
                continue
            self._access_endpoint(
                endpoint,
                args,
                expected_status,
                "Instructor should be allowed to access endpoint " + endpoint
            )

        for endpoint, args in self.instructor_level_endpoints:
            expected_status = 200
            self._access_endpoint(
                endpoint,
                args,
                expected_status,
                "Instructor should be allowed to access endpoint " + endpoint
            )


@patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': True})
@ddt.ddt
class TestInstructorAPIBulkAccountCreationAndEnrollment(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test Bulk account creation and enrollment from csv file
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

        # Create a course with mode 'audit'
        cls.audit_course = CourseFactory.create()
        CourseModeFactory.create(course_id=cls.audit_course.id, mode_slug=CourseMode.AUDIT)

        cls.url = reverse(
            'register_and_enroll_students', kwargs={'course_id': str(cls.course.id)}
        )
        cls.audit_course_url = reverse(
            'register_and_enroll_students', kwargs={'course_id': str(cls.audit_course.id)}
        )

    def setUp(self):
        super().setUp()

        # Create a course with mode 'honor' and with price
        self.white_label_course = CourseFactory.create()
        self.white_label_course_mode = CourseModeFactory.create(
            course_id=self.white_label_course.id,
            mode_slug=CourseMode.HONOR,
            min_price=10,
            suggested_prices='10',
        )

        self.white_label_course_url = reverse(
            'register_and_enroll_students', kwargs={'course_id': str(self.white_label_course.id)}
        )

        self.request = RequestFactory().request()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.audit_course_instructor = InstructorFactory(course_key=self.audit_course.id)
        self.white_label_course_instructor = InstructorFactory(course_key=self.white_label_course.id)

        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.not_enrolled_student = UserFactory(
            username='NotEnrolledStudent',
            email='nonenrolled@test.com',
            first_name='NotEnrolled',
            last_name='Student'
        )

    def test_api_without_login(self):
        """
        verify in case of no authentication it returns 401.
        """
        self.client.logout()
        uploaded_file = SimpleUploadedFile("temp.jpg", io.BytesIO(b"some initial binary data: \x00\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 401

    def test_api_without_permission(self):
        """
        verify in case of no authentication it returns 403.
        """
        # removed role from course for instructor
        CourseInstructorRole(self.course.id).remove_users(self.instructor)
        uploaded_file = SimpleUploadedFile("temp.jpg", io.BytesIO(b"some initial binary data: \x00\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 403

    @patch('lms.djangoapps.instructor.views.api.log.info')
    @ddt.data(
        b"test_student@example.com,test_student_1,tester1,USA",  # Typical use case.
        b"\ntest_student@example.com,test_student_1,tester1,USA\n\n",  # Blank lines.
        b"\xef\xbb\xbftest_student@example.com,test_student_1,tester1,USA",  # Unicode signature (BOM).
    )
    def test_account_creation_and_enrollment_with_csv(self, csv_content, info_log):
        """
        Happy path test to create a single new user
        """
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file, 'email-students': True})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) == 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 0

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED

        # test the log for email that's send to new created user.
        info_log.assert_called_with('email sent to new created user at %s', 'test_student@example.com')

    @patch('lms.djangoapps.instructor.views.api.log.info')
    def test_email_and_username_already_exist(self, info_log):
        """
        If the email address and username already exists
        and the user is enrolled in the course, do nothing (including no email gets sent out)
        """
        csv_content = b"test_student@example.com,test_student_1,tester1,USA\n" \
                      b"test_student@example.com,test_student_1,tester2,US"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) == 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 0

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED

        # test the log for email that's send to new created user.
        info_log.assert_called_with(
            "user already exists with username '%s' and email '%s'",
            'test_student_1',
            'test_student@example.com'
        )

    def test_file_upload_type_not_csv(self):
        """
        Try uploading some non-CSV file and verify that it is rejected
        """
        uploaded_file = SimpleUploadedFile("temp.jpg", io.BytesIO(b"some initial binary data: \x00\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) != 0
        assert data['general_errors'][0]['response'] ==\
               'Make sure that the file you upload is in CSV format with no extraneous characters or rows.'

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    def test_bad_file_upload_type(self):
        """
        Try uploading some non-CSV file and verify that it is rejected
        """
        uploaded_file = SimpleUploadedFile("temp.csv", io.BytesIO(b"some initial binary data: \xC3\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) != 0
        assert data['general_errors'][0]['response'] == 'Could not read uploaded file.'

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    def test_insufficient_data(self):
        """
        Try uploading a CSV file which does not have the exact four columns of data
        """
        csv_content = b"test_student@example.com,test_student_1\n"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) == 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 1
        assert data['general_errors'][0]['response'] == (
            'Data in row #1 must have exactly four columns: email, username, '
            'full name, and country.'
        )
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    def test_invalid_email_in_csv(self):
        """
        Test failure case of a poorly formatted email field
        """
        csv_content = b"test_student.example.com,test_student_1,tester1,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        data = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert len(data['row_errors']) != 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 0
        assert data['row_errors'][0]['response'] == 'Invalid email {}.'.format('test_student.example.com')

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    @patch('lms.djangoapps.instructor.views.api.log.info')
    def test_csv_user_exist_and_not_enrolled(self, info_log):
        """
        If the email address and username already exists
        and the user is not enrolled in the course, enrolled him/her and iterate to next one.
        """
        csv_content = b"nonenrolled@test.com,NotEnrolledStudent,tester1,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        info_log.assert_called_with(
            'user %s enrolled in the course %s',
            'NotEnrolledStudent',
            self.course.id
        )
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition, UNENROLLED_TO_ENROLLED

    def test_user_with_already_existing_email_in_csv(self):
        """
        If the email address already exists, but the username is different,
        assume it is the correct user and just register the user in the course.
        """
        csv_content = b"test_student@example.com,test_student_1,tester1,USA\n" \
                      b"test_student@example.com,test_student_2,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        warning_message = 'An account with email {email} exists but the provided username {username} ' \
                          'is different. Enrolling anyway with {email}.'.format(email='test_student@example.com', username='test_student_2')  # lint-amnesty, pylint: disable=line-too-long
        assert len(data['warnings']) != 0
        assert data['warnings'][0]['response'] == warning_message
        user = User.objects.get(email='test_student@example.com')
        assert CourseEnrollment.is_enrolled(user, self.course.id)

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition, UNENROLLED_TO_ENROLLED

    def test_user_with_retired_email_in_csv(self):
        """
        If the CSV contains email addresses which correspond with users which
        have already been retired, confirm that the attempt returns invalid
        email errors.
        """

        # This email address is re-used to create a retired account and another account.
        conflicting_email = 'test_student@example.com'

        # prep a retired user
        user = UserFactory.create(username='old_test_student', email=conflicting_email)
        user.email = get_retired_email_by_email(user.email)
        user.username = get_retired_username_by_username(user.username)
        user.is_active = False
        user.save()

        csv_content = "{email},{username},tester,USA".format(email=conflicting_email, username='new_test_student')
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content.encode())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) != 0
        assert data['row_errors'][0]['response'] == f'Invalid email {conflicting_email}.'
        assert not User.objects.filter(email=conflicting_email).exists()

    def test_user_with_already_existing_username_in_csv(self):
        """
        If the username already exists (but not the email),
        assume it is a different user and fail to create the new account.
        """
        csv_content = b"test_student1@example.com,test_student_1,tester1,USA\n" \
                      b"test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)

        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) != 0
        assert data['row_errors'][0]['response'] == 'Username {user} already exists.'.format(user='test_student_1')
        # lint-amnesty, pylint: disable=line-too-long

    def test_csv_file_not_attached(self):
        """
        Test when the user does not attach a file
        """
        csv_content = b"test_student1@example.com,test_student_1,tester1,USA\n" \
                      b"test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)

        response = self.client.post(self.url, {'file_not_found': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) != 0
        assert data['general_errors'][0]['response'] == 'File is not attached.'

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    def test_raising_exception_in_auto_registration_and_enrollment_case(self):
        """
        Test that exceptions are handled well
        """
        csv_content = b"test_student1@example.com,test_student_1,tester1,USA\n" \
                      b"test_student2@example.com,test_student_1,tester2,US"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        with patch('lms.djangoapps.instructor.views.api.create_manual_course_enrollment') as mock:
            mock.side_effect = NonExistentCourseError()
            response = self.client.post(self.url, {'students_list': uploaded_file})

        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) != 0
        assert data['row_errors'][0]['response'] == 'NonExistentCourseError'

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    def test_generate_unique_password(self):
        """
        generate_unique_password should generate a unique password string that excludes certain characters.
        """
        password = generate_unique_password([], 12)
        assert len(password) == 12
        for letter in password:
            assert letter not in 'aAeEiIoOuU1l'

    def test_users_created_and_enrolled_successfully_if_others_fail(self):

        # prep a retired user
        user = UserFactory.create(username='old_test_student_4', email='test_student4@example.com')
        user.email = get_retired_email_by_email(user.email)
        user.username = get_retired_username_by_username(user.username)
        user.is_active = False
        user.save()

        csv_content = b"test_student1@example.com,test_student_1,tester1,USA\n" \
                      b"test_student3@example.com,test_student_1,tester3,CA\n" \
                      b"test_student4@example.com,test_student_4,tester4,USA\n" \
                      b"test_student2@example.com,test_student_2,tester2,USA"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) != 0
        assert data['row_errors'][0]['response'] == 'Username {user} already exists.'.format(user='test_student_1')
        assert data['row_errors'][1]['response'] == 'Invalid email {email}.'.format(email='test_student4@example.com')
        assert User.objects.filter(username='test_student_1', email='test_student1@example.com').exists()
        assert User.objects.filter(username='test_student_2', email='test_student2@example.com').exists()
        assert not User.objects.filter(email='test_student3@example.com').exists()
        assert not User.objects.filter(email='test_student4@example.com').exists()

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 2

    def test_generate_unique_password_no_reuse(self):
        """
        generate_unique_password should generate a unique password string that hasn't been generated before.
        """
        with patch('lms.djangoapps.instructor.views.api.generate_random_string') as mock:
            mock.side_effect = ['first', 'first', 'second']

            generated_password = ['first']
            password = generate_unique_password(generated_password, 12)
            assert password != 'first'

    @patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': False})
    def test_allow_automated_signups_flag_not_set(self):
        csv_content = b"test_student1@example.com,test_student_1,tester1,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 403

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 0

    @patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': True})
    def test_audit_enrollment_mode(self):
        """
        Test that enrollment mode for audit courses (paid courses) is 'audit'.
        """
        # Login Audit Course instructor
        self.client.login(username=self.audit_course_instructor.username, password=self.TEST_PASSWORD)

        csv_content = b"test_student_wl@example.com,test_student_wl,Test Student,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.audit_course_url, {'students_list': uploaded_file})

        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) == 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 0

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED

        # Verify enrollment modes to be 'audit'
        for enrollment in manual_enrollments:
            assert enrollment.enrollment.mode == CourseMode.AUDIT

    @patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': True})
    def test_honor_enrollment_mode(self):
        """
        Test that enrollment mode for unpaid honor courses is 'honor'.
        """
        # Remove white label course price
        self.white_label_course_mode.min_price = 0
        self.white_label_course_mode.suggested_prices = ''
        self.white_label_course_mode.save()

        # Login Audit Course instructor
        self.client.login(username=self.white_label_course_instructor.username, password=self.TEST_PASSWORD)

        csv_content = b"test_student_wl@example.com,test_student_wl,Test Student,USA"
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.white_label_course_url, {'students_list': uploaded_file})

        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['row_errors']) == 0
        assert len(data['warnings']) == 0
        assert len(data['general_errors']) == 0

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED

        # Verify enrollment modes to be 'honor'
        for enrollment in manual_enrollments:
            assert enrollment.enrollment.mode == CourseMode.HONOR


@ddt.ddt
class TestInstructorAPIEnrollment(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test enrollment modification endpoint.

    This test does NOT exhaustively test state changes, that is the
    job of test_enrollment. This tests the response and action switch.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

        # Email URL values
        cls.site_name = configuration_helpers.get_value(
            'SITE_NAME',
            settings.SITE_NAME
        )
        cls.about_path = f'/courses/{cls.course.id}/about'
        cls.course_path = f'/courses/{cls.course.id}/'

    def setUp(self):
        super().setUp()

        self.request = RequestFactory().request()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.enrolled_student = UserFactory(username='EnrolledStudent', first_name='Enrolled', last_name='Student')
        CourseEnrollment.enroll(
            self.enrolled_student,
            self.course.id
        )
        self.notenrolled_student = UserFactory(username='NotEnrolledStudent', first_name='NotEnrolled',
                                               last_name='Student')

        # Create invited, but not registered, user
        cea = CourseEnrollmentAllowed(email='robot-allowed@robot.org', course_id=self.course.id)
        cea.save()
        self.allowed_email = 'robot-allowed@robot.org'

        self.notregistered_email = 'robot-not-an-email-yet@robot.org'
        assert User.objects.filter(email=self.notregistered_email).count() == 0

        # uncomment to enable enable printing of large diffs
        # from failed assertions in the event of a test failure.
        # (comment because pylint C0103(invalid-name))
        # self.maxDiff = None

    def test_missing_params(self):
        """ Test missing all query parameters. """
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_bad_action(self):
        """ Test with an invalid action. """
        action = 'robot-not-an-action'
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': action})
        assert response.status_code == 400

    def test_invalid_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': 'percivaloctavius@', 'action': 'enroll', 'email_students': False})  # lint-amnesty, pylint: disable=line-too-long
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": 'percivaloctavius@',
                    "invalidIdentifier": True,
                    "success": False,
                    "error_type": "invalid_identifier",
                    "error_message": "Invalid email address",
                }
            ]
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_invalid_username(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url,
                                    {'identifiers': 'percivaloctavius', 'action': 'enroll', 'email_students': False})
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": 'percivaloctavius',
                    "invalidIdentifier": True,
                    "success": False,
                    "error_type": "invalid_identifier",
                    "error_message": "Invalid email address",
                }
            ]
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_enroll_with_username(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'enroll',
                                          'email_students': False})
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "results": [
                {
                    "identifier": self.notenrolled_student.username,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": UNENROLLED_TO_ENROLLED,
                }
            ]
        }
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_enroll_without_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'enroll',
                                          'email_students': False})
        print(f"type(self.notenrolled_student.email): {type(self.notenrolled_student.email)}")
        assert response.status_code == 200

        # test that the user is now enrolled
        user = User.objects.get(email=self.notenrolled_student.email)
        assert CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": UNENROLLED_TO_ENROLLED,
                }
            ]
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    @ddt.data('http', 'https')
    def test_enroll_with_email(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)

        print(f"type(self.notenrolled_student.email): {type(self.notenrolled_student.email)}")
        assert response.status_code == 200

        # test that the user is now enrolled
        user = User.objects.get(email=self.notenrolled_student.email)
        assert CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": UNENROLLED_TO_ENROLLED,
                }
            ]
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been enrolled in {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]

        assert text_body.startswith('Dear NotEnrolled Student\n\n')

        for body in [text_body, html_body]:
            assert f'You have been enrolled in {self.course.display_name} at edx.org by a member of the course staff.'\
                   in body

            assert 'This course will now appear on your edx.org dashboard.' in body
            assert f'{protocol}://{self.site_name}{self.course_path}' in body

        assert 'To start accessing course materials, please visit' in text_body
        assert 'This email was automatically sent from edx.org to NotEnrolled Student\n\n' in text_body

    @ddt.data('http', 'https')
    def test_enroll_with_email_not_registered(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ALLOWEDTOENROLL
        assert response.status_code == 200

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to register for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        register_url = f'{protocol}://{self.site_name}/register'

        assert text_body.startswith('Dear student,')
        assert 'To finish your registration, please visit {register_url}'.format(
            register_url=register_url,
        ) in text_body
        assert 'Please finish your registration and fill out' in html_body
        assert register_url in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to join {course} at edx.org by a member of the course staff.'.format(
                course=self.course.display_name
            ) in body

            assert ('fill out the registration form making sure to use '
                    'robot-not-an-email-yet@robot.org in the Email field') in body

            assert 'Once you have registered and activated your account,' in body

            assert '{proto}://{site}{about_path}'.format(
                proto=protocol,
                site=self.site_name,
                about_path=self.about_path
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    @ddt.data('http', 'https')
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_enroll_email_not_registered_mktgsite(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ALLOWEDTOENROLL
        assert response.status_code == 200

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]

        assert text_body.startswith('Dear student,')
        assert 'To finish your registration, please visit' in text_body
        assert 'Please finish your registration and fill' in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to join {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name
            ) in body

            assert '{proto}://{site}/register'.format(
                proto=protocol,
                site=self.site_name
            ) in body

            assert ('fill out the registration form making sure to use '
                    'robot-not-an-email-yet@robot.org in the Email field') in body

            assert 'You can then enroll in {display_name}.'.format(
                display_name=self.course.display_name
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    @ddt.data('http', 'https')
    def test_enroll_with_email_not_registered_autoenroll(self, protocol):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        print(f"type(self.notregistered_email): {type(self.notregistered_email)}")
        assert response.status_code == 200

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to register for {self.course.display_name}'
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ALLOWEDTOENROLL

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        register_url = '{proto}://{site}/register'.format(
            proto=protocol,
            site=self.site_name,
        )

        assert text_body.startswith('Dear student,')
        assert 'To finish your registration, please visit {register_url}'.format(
            register_url=register_url,
        ) in text_body
        assert 'Please finish your registration and fill out the registration' in html_body
        assert 'Finish Your Registration' in html_body
        assert register_url in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to join {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name
            ) in body

            assert (' and fill '
                    'out the registration form making sure to use robot-not-an-email-yet@robot.org '
                    'in the Email field') in body

            assert ('Once you have registered and activated your account, '
                    'you will see {display_name} listed on your dashboard.').format(
                display_name=self.course.display_name
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    def test_unenroll_without_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': 'unenroll',
                                          'email_students': False})
        print(f"type(self.enrolled_student.email): {type(self.enrolled_student.email)}")
        assert response.status_code == 200

        # test that the user is now unenrolled
        user = User.objects.get(email=self.enrolled_student.email)
        assert not CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.enrolled_student.email,
                    "before": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": ENROLLED_TO_UNENROLLED,
                }
            ]
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == ENROLLED_TO_UNENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    def test_unenroll_with_email(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.enrolled_student.email, 'action': 'unenroll',
                                          'email_students': True})
        print(f"type(self.enrolled_student.email): {type(self.enrolled_student.email)}")
        assert response.status_code == 200

        # test that the user is now unenrolled
        user = User.objects.get(email=self.enrolled_student.email)
        assert not CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.enrolled_student.email,
                    "before": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": ENROLLED_TO_UNENROLLED,
                }
            ]
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == ENROLLED_TO_UNENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been unenrolled from {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]

        assert text_body.startswith('Dear Enrolled Student')

        for body in [text_body, html_body]:
            assert 'You have been unenrolled from {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'This course will no longer appear on your edx.org dashboard.' in body
            assert 'Your other courses have not been affected.' in body
            assert 'This email was automatically sent from edx.org to Enrolled Student' in body

    def test_unenroll_with_email_allowed_student(self):
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url,
                                    {'identifiers': self.allowed_email, 'action': 'unenroll', 'email_students': True})
        print(f"type(self.allowed_email): {type(self.allowed_email)}")
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.allowed_email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": True,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": ALLOWEDTOENROLL_TO_ENROLLED,
                }
            ]
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == ALLOWEDTOENROLL_TO_UNENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been unenrolled from {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        assert text_body.startswith('Dear Student,')

        for body in [text_body, html_body]:
            assert 'You have been unenrolled from the course {display_name} by a member of the course staff.'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'Please disregard the invitation previously sent.' in body
            assert 'This email was automatically sent from edx.org to robot-allowed@robot.org' in body

    @ddt.data('http', 'https')
    @patch('lms.djangoapps.instructor.enrollment.uses_shib')
    def test_enroll_with_email_not_registered_with_shib(self, protocol, mock_uses_shib):
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        assert response.status_code == 200

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to register for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        course_url = '{proto}://{site}{about_path}'.format(
            proto=protocol,
            site=self.site_name,
            about_path=self.about_path,
        )
        assert text_body.startswith('Dear student,')
        assert 'To access this course visit {course_url} and register for this course.'.format(
            course_url=course_url,
        ) in text_body
        assert 'To access this course visit it and register:' in html_body
        assert course_url in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to join {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    @patch('lms.djangoapps.instructor.enrollment.uses_shib')
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_enroll_email_not_registered_shib_mktgsite(self, mock_uses_shib):
        # Try with marketing site enabled and shib on
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        # Try with marketing site enabled
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
            response = self.client.post(url, {'identifiers': self.notregistered_email, 'action': 'enroll',
                                              'email_students': True})

        assert response.status_code == 200

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        assert text_body.startswith('Dear student,')

        for body in [text_body, html_body]:
            assert 'You have been invited to join {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    @ddt.data('http', 'https')
    @patch('lms.djangoapps.instructor.enrollment.uses_shib')
    def test_enroll_with_email_not_registered_with_shib_autoenroll(self, protocol, mock_uses_shib):
        mock_uses_shib.return_value = True

        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        print(f"type(self.notregistered_email): {type(self.notregistered_email)}")
        assert response.status_code == 200

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to register for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        course_url = '{proto}://{site}{course_path}'.format(
            proto=protocol, site=self.site_name, course_path=self.course_path,
        )

        assert text_body.startswith('Dear student,')
        assert course_url in html_body
        assert f'To access this course visit {course_url} and login.' in text_body
        assert 'To access this course click on the button below and login:' in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to join {display_name} at edx.org by a member of the course staff.'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'This email was automatically sent from edx.org to robot-not-an-email-yet@robot.org' in body

    def test_enroll_already_enrolled_student(self):
        """
        Ensure that already enrolled "verified" students cannot be downgraded
        to "honor"
        """
        course_enrollment = CourseEnrollment.objects.get(
            user=self.enrolled_student, course_id=self.course.id
        )
        # make this enrollment "verified"
        course_enrollment.mode = 'verified'
        course_enrollment.save()
        assert course_enrollment.mode == 'verified'

        # now re-enroll the student through the instructor dash
        self._change_student_enrollment(self.enrolled_student, self.course, 'enroll')

        # affirm that the student is still in "verified" mode
        course_enrollment = CourseEnrollment.objects.get(
            user=self.enrolled_student, course_id=self.course.id
        )
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == ENROLLED_TO_ENROLLED
        assert course_enrollment.mode == 'verified'

    def create_paid_course(self):
        """
        create paid course mode.
        """
        paid_course = CourseFactory.create()
        CourseModeFactory.create(course_id=paid_course.id, min_price=50, mode_slug=CourseMode.HONOR)
        CourseInstructorRole(paid_course.id).add_users(self.instructor)
        return paid_course

    def test_unenrolled_allowed_to_enroll_user(self):
        """
        test to unenroll allow to enroll user.
        """
        paid_course = self.create_paid_course()
        url = reverse('students_update_enrollment', kwargs={'course_id': str(paid_course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': False,
                  'auto_enroll': False, 'reason': 'testing..', 'role': 'Learner'}
        response = self.client.post(url, params)
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ALLOWEDTOENROLL
        assert response.status_code == 200

        # now registered the user
        UserFactory(email=self.notregistered_email)
        url = reverse('students_update_enrollment', kwargs={'course_id': str(paid_course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': False,
                  'auto_enroll': False, 'reason': 'testing', 'role': 'Learner'}
        response = self.client.post(url, params)
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 2
        assert manual_enrollments[1].state_transition == ALLOWEDTOENROLL_TO_ENROLLED
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notregistered_email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": True,
                    },
                    "after": {
                        "enrollment": True,
                        "auto_enroll": False,
                        "user": True,
                        "allowed": True,
                    },
                    "success": True,
                    "state_transition": ALLOWEDTOENROLL_TO_ENROLLED,
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_unenrolled_already_not_enrolled_user(self):
        """
        test unenrolled user already not enrolled in a course.
        """
        paid_course = self.create_paid_course()
        course_enrollment = CourseEnrollment.objects.filter(
            user__email=self.notregistered_email, course_id=paid_course.id
        )
        assert course_enrollment.count() == 0

        url = reverse('students_update_enrollment', kwargs={'course_id': str(paid_course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'unenroll', 'email_students': False,
                  'auto_enroll': False, 'reason': 'testing', 'role': 'Learner'}

        response = self.client.post(url, params)
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "results": [
                {
                    "identifier": self.notregistered_email,
                    "before": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": False,
                    },
                    "after": {
                        "enrollment": False,
                        "auto_enroll": False,
                        "user": False,
                        "allowed": False,
                    },
                    "success": True,
                    "state_transition": UNENROLLED_TO_UNENROLLED,
                }
            ]
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_UNENROLLED

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_unenroll_and_enroll_verified(self):
        """
        Test that unenrolling and enrolling a student from a verified track
        results in that student being in the default track
        """
        course_enrollment = CourseEnrollment.objects.get(
            user=self.enrolled_student, course_id=self.course.id
        )
        # upgrade enrollment
        course_enrollment.mode = 'verified'
        course_enrollment.save()
        assert course_enrollment.mode == 'verified'

        self._change_student_enrollment(self.enrolled_student, self.course, 'unenroll')

        self._change_student_enrollment(self.enrolled_student, self.course, 'enroll')

        course_enrollment = CourseEnrollment.objects.get(
            user=self.enrolled_student, course_id=self.course.id
        )
        assert course_enrollment.mode == CourseMode.DEFAULT_MODE_SLUG

    def test_reason_is_persisted(self):
        """
        test that reason field is persisted in the database
        """
        paid_course = self.create_paid_course()
        url = reverse('students_update_enrollment', kwargs={'course_id': str(paid_course.id)})
        params = {'identifiers': self.notregistered_email, 'action': 'enroll', 'email_students': False,
                  'auto_enroll': False, 'reason': 'testing'}
        response = self.client.post(url, params)

        manual_enrollment = ManualEnrollmentAudit.objects.first()
        assert manual_enrollment.reason == 'testing'
        assert response.status_code == 200

    def _change_student_enrollment(self, user, course, action):
        """
        Helper function that posts to 'students_update_enrollment' to change
        a student's enrollment
        """
        url = reverse(
            'students_update_enrollment',
            kwargs={'course_id': str(course.id)},
        )
        params = {
            'identifiers': user.email,
            'action': action,
            'email_students': True,
            'reason': 'change user enrollment',
            'role': 'Learner'
        }
        response = self.client.post(url, params)
        assert response.status_code == 200
        return response

    def test_get_enrollment_status(self):
        """Check that enrollment states are reported correctly."""

        # enrolled, active
        url = reverse(
            'get_student_enrollment_status',
            kwargs={'course_id': str(self.course.id)},
        )
        params = {
            'unique_student_identifier': 'EnrolledStudent'
        }
        response = self.client.post(url, params)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['enrollment_status'] == 'Enrollment status for EnrolledStudent: active'

        # unenrolled, inactive
        CourseEnrollment.unenroll(
            self.enrolled_student,
            self.course.id
        )

        response = self.client.post(url, params)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['enrollment_status'] == 'Enrollment status for EnrolledStudent: inactive'

        # invited, not yet registered
        params = {
            'unique_student_identifier': 'robot-allowed@robot.org'
        }

        response = self.client.post(url, params)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['enrollment_status'] == 'Enrollment status for robot-allowed@robot.org: pending'

        # never enrolled or invited
        params = {
            'unique_student_identifier': 'nonotever@example.com'
        }

        response = self.client.post(url, params)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['enrollment_status'] == 'Enrollment status for nonotever@example.com: never enrolled'

    @patch("lms.djangoapps.instructor_task.api.submit_student_enrollment_batch")
    def test_enroll_async_processing_success(self, mock_submit_task):
        """Test async enrollment with async_processing=True"""
        mock_task = Mock()
        mock_task.task_id = "test-task-id-123"
        mock_task.task_state = "QUEUED"
        mock_submit_task.return_value = mock_task
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": self.notenrolled_student.email,
                "action": "enroll",
                "email_students": False,
                "async_processing": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))

        # Verify async response structure
        self.assertEqual(res_json, {
            "action": "enroll",
            "auto_enroll": False,
            "async_processing": True,
            "task_id": "test-task-id-123",
            "task_state": "QUEUED",
            "message": "Async enroll task submitted for 1 students",
            "total_students": 1
        })

        # Verify the task was called with correct parameters
        self.assertTrue(mock_submit_task.called)
        call_args = mock_submit_task.call_args
        self.assertEqual(call_args[1]["course_key"], self.course.id)
        self.assertEqual(call_args[1]["action"], "enroll")
        self.assertEqual(call_args[1]["identifiers"], [self.notenrolled_student.email])

    @patch("lms.djangoapps.instructor_task.api.submit_student_enrollment_batch")
    def test_unenroll_async_processing_success(self, mock_submit_task):
        """Test async unenrollment with async_processing=True"""
        mock_task = Mock()
        mock_task.task_id = "test-unenroll-task-456"
        mock_task.task_state = "QUEUED"
        mock_submit_task.return_value = mock_task
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": self.enrolled_student.email,
                "action": "unenroll",
                "email_students": True,
                "async_processing": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))

        self.assertEqual(res_json, {
            "action": "unenroll",
            "auto_enroll": False,
            "async_processing": True,
            "task_id": "test-unenroll-task-456",
            "task_state": "QUEUED",
            "message": "Async unenroll task submitted for 1 students",
            "total_students": 1
        })

    @patch("lms.djangoapps.instructor_task.api.submit_student_enrollment_batch")
    def test_async_enrollment_already_running_error(self, mock_submit_task):
        """Test handling of AlreadyRunningError in async mode"""
        mock_submit_task.side_effect = AlreadyRunningError("Task already running")
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url, {"identifiers": self.notenrolled_student.email, "action": "enroll", "async_processing": True}
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))
        self.assertTrue(res_json["async_processing"])
        self.assertIn("error", res_json)
        self.assertIn("already running", res_json["error"].lower())
        self.assertEqual(res_json["action"], "enroll")

    @patch("lms.djangoapps.instructor_task.api.submit_student_enrollment_batch")
    def test_async_enrollment_multiple_identifiers(self, mock_submit_task):
        """Test async enrollment with multiple student identifiers"""
        student2 = UserFactory(username="Student2", email="student2@example.com")
        student3 = UserFactory(username="Student3", email="student3@example.com")
        mock_task = Mock()
        mock_task.task_id = "test-bulk-task-789"
        mock_task.task_state = "QUEUED"
        mock_submit_task.return_value = mock_task
        identifiers = f"{self.notenrolled_student.email},{student2.email},{student3.email}"
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": identifiers,
                "action": "enroll",
                "email_students": False,
                "async_processing": True,
                "auto_enroll": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))
        self.assertEqual(res_json["total_students"], 3)
        self.assertTrue(res_json["auto_enroll"])

        # Verify task was called with all identifiers
        call_args = mock_submit_task.call_args
        identifiers_list = call_args[1]["identifiers"]
        self.assertEqual(len(identifiers_list), 3)
        self.assertIn(self.notenrolled_student.email, identifiers_list)
        self.assertIn(student2.email, identifiers_list)
        self.assertIn(student3.email, identifiers_list)

    def test_async_processing_default_false(self):
        """Test that async_processing defaults to False for backward compatibility"""
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": self.notenrolled_student.email,
                "action": "enroll",
                "email_students": False,
                # async_processing not provided
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))

        # Should have sync response structure (with 'results')
        self.assertIn("results", res_json)
        self.assertNotIn("async_processing", res_json)
        self.assertEqual(res_json["action"], "enroll")

    @patch("lms.djangoapps.instructor_task.api.submit_student_enrollment_batch")
    def test_async_enrollment_with_reason(self, mock_submit_task):
        """Test async enrollment with reason field"""
        mock_task = Mock()
        mock_task.task_id = "test-task-with-reason"
        mock_task.task_state = "QUEUED"
        mock_submit_task.return_value = mock_task
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": self.notenrolled_student.email,
                "action": "enroll",
                "email_students": False,
                "async_processing": True,
                "reason": "Testing async enrollment",
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))
        self.assertTrue(res_json["async_processing"])

        # Verify reason was passed to task
        call_args = mock_submit_task.call_args
        self.assertEqual(call_args[1]["reason"], "Testing async enrollment")

    def test_sync_enrollment_still_works(self):
        """Test that synchronous enrollment still works (async_processing=False)"""
        url = reverse("students_update_enrollment", kwargs={"course_id": str(self.course.id)})

        response = self.client.post(
            url,
            {
                "identifiers": self.notenrolled_student.email,
                "action": "enroll",
                "email_students": False,
                "async_processing": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        res_json = json.loads(response.content.decode("utf-8"))

        # Should have sync response structure
        self.assertIn("results", res_json)
        self.assertEqual(len(res_json["results"]), 1)
        self.assertTrue(res_json["results"][0]["success"])

        # Verify actual enrollment happened
        self.assertTrue(CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id))


@ddt.ddt
class TestInstructorAPIBulkBetaEnrollment(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test bulk beta modify access endpoint.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        # Email URL values
        cls.site_name = configuration_helpers.get_value(
            'SITE_NAME',
            settings.SITE_NAME
        )
        cls.about_path = f'/courses/{cls.course.id}/about'
        cls.course_path = f'/courses/{cls.course.id}/'

    def setUp(self):
        super().setUp()

        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.beta_tester = BetaTesterFactory(course_key=self.course.id)
        CourseEnrollment.enroll(
            self.beta_tester,
            self.course.id
        )
        assert CourseBetaTesterRole(self.course.id).has_user(self.beta_tester)

        self.notenrolled_student = UserFactory(username='NotEnrolledStudent')

        self.notregistered_email = 'robot-not-an-email-yet@robot.org'
        assert User.objects.filter(email=self.notregistered_email).count() == 0

        self.request = RequestFactory().request()

        # uncomment to enable enable printing of large diffs
        # from failed assertions in the event of a test failure.
        # (comment because pylint C0103(invalid-name))
        # self.maxDiff = None

    def test_missing_params(self):
        """ Test missing all query parameters. """
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_bad_action(self):
        """ Test with an invalid action. """
        action = 'robot-not-an-action'
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.beta_tester.email, 'action': action})
        assert response.status_code == 400

    def add_notenrolled(self, response, identifier):
        """
        Test Helper Method (not a test, called by other tests)

        Takes a client response from a call to bulk_beta_modify_access with 'email_students': False,
        and the student identifier (email or username) given as 'identifiers' in the request.

        Asserts the reponse returns cleanly, that the student was added as a beta tester, and the
        response properly contains their identifier, 'error': False, and 'userDoesNotExist': False.
        Additionally asserts no email was sent.
        """
        assert response.status_code == 200
        assert CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student)
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": identifier,
                    "error": False,
                    "userDoesNotExist": False,
                    "is_active": True
                }
            ]
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    def test_add_notenrolled_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': False})  # lint-amnesty, pylint: disable=line-too-long
        self.add_notenrolled(response, self.notenrolled_student.email)
        assert not CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id)

    def test_add_notenrolled_email_autoenroll(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': False, 'auto_enroll': True})  # lint-amnesty, pylint: disable=line-too-long
        self.add_notenrolled(response, self.notenrolled_student.email)
        assert CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id)

    def test_add_notenrolled_username(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'add', 'email_students': False})  # lint-amnesty, pylint: disable=line-too-long
        self.add_notenrolled(response, self.notenrolled_student.username)
        assert not CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id)

    def test_add_notenrolled_username_autoenroll(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.username, 'action': 'add', 'email_students': False, 'auto_enroll': True})  # lint-amnesty, pylint: disable=line-too-long
        self.add_notenrolled(response, self.notenrolled_student.username)
        assert CourseEnrollment.is_enrolled(self.notenrolled_student, self.course.id)

    def test_add_notenrolled_username_autoenroll_with_multiple_users(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        identifiers = (f"Lorem@ipsum.dolor, "
                       f"sit@amet.consectetur\nadipiscing@elit.Aenean\r convallis@at.lacus\r, ut@lacinia.Sed, "
                       f"{self.notenrolled_student.username}"
                       )
        response = self.client.post(url, {'identifiers': identifiers, 'action': 'add', 'email_students': False, 'auto_enroll': True})  # lint-amnesty, pylint: disable=line-too-long
        assert 6, len(json.loads(response.content.decode())['results'])

    @ddt.data('http', 'https')
    def test_add_notenrolled_with_email(self, protocol):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        assert response.status_code == 200

        assert CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student)
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "error": False,
                    "userDoesNotExist": False,
                    "is_active": True
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to a beta test for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        student_name = self.notenrolled_student.profile.name
        assert text_body.startswith(f'Dear {student_name}')
        assert f'Visit {self.course.display_name}' in html_body

        for body in [text_body, html_body]:
            assert 'You have been invited to be a beta tester for {display_name} at edx.org'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'by a member of the course staff.' in body
            assert 'enroll in this course and begin the beta test' in body

            assert '{proto}://{site}{about_path}'.format(
                proto=protocol,
                site=self.site_name,
                about_path=self.about_path,
            ) in body

            assert 'This email was automatically sent from edx.org to {student_email}'.format(
                student_email=self.notenrolled_student.email,
            ) in body

    @ddt.data('http', 'https')
    def test_add_notenrolled_with_email_autoenroll(self, protocol):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        params = {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True,
                  'auto_enroll': True}
        environ = {'wsgi.url_scheme': protocol}
        response = self.client.post(url, params, **environ)
        assert response.status_code == 200

        assert CourseBetaTesterRole(self.course.id).has_user(self.notenrolled_student)
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notenrolled_student.email,
                    "error": False,
                    "userDoesNotExist": False,
                    "is_active": True
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been invited to a beta test for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        student_name = self.notenrolled_student.profile.name
        assert text_body.startswith(f'Dear {student_name}')

        for body in [text_body, html_body]:
            assert 'You have been invited to be a beta tester for {display_name} at edx.org'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'by a member of the course staff' in body

            assert 'To start accessing course materials, please visit' in body
            assert '{proto}://{site}{course_path}'.format(
                proto=protocol,
                site=self.site_name,
                course_path=self.course_path
            )

            assert 'This email was automatically sent from edx.org to {student_email}'.format(
                student_email=self.notenrolled_student.email,
            ) in body

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_add_notenrolled_email_mktgsite(self):
        # Try with marketing site enabled
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'identifiers': self.notenrolled_student.email, 'action': 'add', 'email_students': True})  # lint-amnesty, pylint: disable=line-too-long

        assert response.status_code == 200

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        student_name = self.notenrolled_student.profile.name
        assert text_body.startswith(f'Dear {student_name}')

        for body in [text_body, html_body]:
            assert 'You have been invited to be a beta tester for {display_name} at edx.org'.format(
                display_name=self.course.display_name,
            ) in body

            assert 'by a member of the course staff.' in body
            assert 'Visit edx.org' in body
            assert 'enroll in this course and begin the beta test' in body
            assert 'This email was automatically sent from edx.org to {student_email}'.format(
                student_email=self.notenrolled_student.email,
            ) in body

    def test_enroll_with_email_not_registered(self):
        # User doesn't exist
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url,
                                    {'identifiers': self.notregistered_email, 'action': 'add', 'email_students': True,
                                     'reason': 'testing'})
        assert response.status_code == 200
        # test the response data
        expected = {
            "action": "add",
            "results": [
                {
                    "identifier": self.notregistered_email,
                    "error": True,
                    "userDoesNotExist": True,
                    "is_active": None
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    def test_remove_without_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url,
                                    {'identifiers': self.beta_tester.email, 'action': 'remove', 'email_students': False,
                                     'reason': 'testing'})
        assert response.status_code == 200

        # Works around a caching bug which supposedly can't happen in prod. The instance here is not ==
        # the instance fetched from the email above which had its cache cleared
        if hasattr(self.beta_tester, '_roles'):
            del self.beta_tester._roles
        assert not CourseBetaTesterRole(self.course.id).has_user(self.beta_tester)

        # test the response data
        expected = {
            "action": "remove",
            "results": [
                {
                    "identifier": self.beta_tester.email,
                    "error": False,
                    "userDoesNotExist": False,
                    "is_active": True
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    def test_remove_with_email(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url,
                                    {'identifiers': self.beta_tester.email, 'action': 'remove', 'email_students': True,
                                     'reason': 'testing'})
        assert response.status_code == 200

        # Works around a caching bug which supposedly can't happen in prod. The instance here is not ==
        # the instance fetched from the email above which had its cache cleared
        if hasattr(self.beta_tester, '_roles'):
            del self.beta_tester._roles
        assert not CourseBetaTesterRole(self.course.id).has_user(self.beta_tester)

        # test the response data
        expected = {
            "action": "remove",
            "results": [
                {
                    "identifier": self.beta_tester.email,
                    "error": False,
                    "userDoesNotExist": False,
                    "is_active": True
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected
        # Check the outbox
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f'You have been removed from a beta test for {self.course.display_name}'

        text_body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0][0]
        assert text_body.startswith(f'Dear {self.beta_tester.profile.name}')

        for body in [text_body, html_body]:
            assert 'You have been removed as a beta tester for {display_name} at edx.org'.format(
                display_name=self.course.display_name,
            ) in body

            assert ('This course will remain on your dashboard, but you will no longer be '
                    'part of the beta testing group.') in body

            assert 'Your other courses have not been affected.' in body

            assert 'This email was automatically sent from edx.org to {email_address}'.format(
                email_address=self.beta_tester.email,
            ) in body


class TestInstructorAPILevelsAccess(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints whereby instructors can change permissions
    of other users.

    This test does NOT test whether the actions had an effect on the
    database, that is the job of test_access.
    This tests the response and action switch.
    Actually, modify_access does not have a very meaningful
    response yet, so only the status code is tested.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.other_instructor = InstructorFactory(course_key=self.course.id)
        self.other_staff = StaffFactory(course_key=self.course.id)
        self.other_user = UserFactory()
        self.list_forum_members_url = reverse('list_forum_members', kwargs={'course_id': str(self.course.id)})

    def test_modify_access_noparams(self):
        """ Test missing all query parameters. """
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_modify_access_bad_action(self):
        """ Test with an invalid action parameter. """
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'staff',
            'action': 'robot-not-an-action',
        })
        assert response.status_code == 400

    def test_modify_access_bad_role(self):
        """ Test with an invalid action parameter. """
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'robot-not-a-roll',
            'action': 'revoke',
        })
        assert response.status_code == 400

    def test_modify_access_api(self):
        for rolename in ["staff", "limited_staff", "instructor", "data_researcher"]:
            assert CourseEnrollment.is_enrolled(self.other_user, self.course.id) is False
            url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
            response = self.client.post(url, {
                'unique_student_identifier': self.other_user.email,
                'rolename': rolename,
                'action': 'allow',
            })
            assert response.status_code == 200
            # User should be auto enrolled in the course
            assert CourseEnrollment.is_enrolled(self.other_user, self.course.id)
            # Test role revoke action
            response = self.client.post(url, {
                'unique_student_identifier': self.other_user.email,
                'rolename': rolename,
                'action': 'revoke',
            })
            assert response.status_code == 200
            CourseEnrollment.unenroll(self.other_user, self.course.id)

    def test_modify_access_allow_with_uname(self):
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_instructor.username,
            'rolename': 'staff',
            'action': 'allow',
        })
        assert response.status_code == 200

    def test_modify_access_revoke_with_username(self):
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_staff.username,
            'rolename': 'staff',
            'action': 'revoke',
        })
        assert response.status_code == 200

    def test_modify_access_with_fake_user(self):
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': 'GandalfTheGrey',
            'rolename': 'staff',
            'action': 'revoke',
        })
        assert response.status_code == 200
        expected = {
            'unique_student_identifier': 'GandalfTheGrey',
            'userDoesNotExist': True,
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_modify_access_with_inactive_user(self):
        self.other_user.is_active = False
        self.other_user.save()
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_user.username,
            'rolename': 'beta',
            'action': 'allow',
        })
        assert response.status_code == 200
        expected = {
            'unique_student_identifier': self.other_user.username,
            'inactiveUser': True,
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_modify_access_revoke_not_allowed(self):
        """ Test revoking access that a user does not have. """
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.other_staff.email,
            'rolename': 'instructor',
            'action': 'revoke',
        })
        assert response.status_code == 200

    def test_modify_access_revoke_self(self):
        """
        Test that an instructor cannot remove instructor privelages from themself.
        """
        url = reverse('modify_access', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.instructor.email,
            'rolename': 'instructor',
            'action': 'revoke',
        })
        assert response.status_code == 200
        # check response content
        expected = {
            'unique_student_identifier': self.instructor.username,
            'rolename': 'instructor',
            'action': 'revoke',
            'removingSelfAsInstructor': True,
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_list_course_role_members_noparams(self):
        """ Test missing all query parameters. """
        url = reverse('list_course_role_members', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_list_course_role_members_bad_rolename(self):
        """ Test with an invalid rolename parameter. """
        url = reverse('list_course_role_members', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'rolename': 'robot-not-a-rolename',
        })
        assert response.status_code == 400

    def test_list_course_role_members_staff(self):
        url = reverse('list_course_role_members', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'rolename': 'staff',
        })
        assert response.status_code == 200

        # check response content
        expected = {
            'course_id': str(self.course.id),
            'staff': [
                {
                    'username': self.other_staff.username,
                    'email': self.other_staff.email,
                    'first_name': self.other_staff.first_name,
                    'last_name': self.other_staff.last_name,
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_list_course_role_members_beta(self):
        url = reverse('list_course_role_members', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'rolename': 'beta',
        })
        assert response.status_code == 200

        # check response content
        expected = {
            'course_id': str(self.course.id),
            'beta': []
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_update_forum_role_membership(self):
        """
        Test update forum role membership with user's email and username.
        """

        # Seed forum roles for course.
        seed_permissions_roles(self.course.id)

        for user in [self.instructor, self.other_user]:
            for identifier_attr in [user.email, user.username]:
                for rolename in ["Administrator", "Moderator", "Community TA"]:
                    for action in ["allow", "revoke"]:
                        self.assert_update_forum_role_membership(user, identifier_attr, rolename, action)

    def assert_update_forum_role_membership(self, current_user, identifier, rolename, action):
        """
        default roles require either (staff & forum admin) or (instructor)
        User should be forum-admin and staff to access this endpoint.

        But if request rolename is  FORUM_ROLE_ADMINISTRATOR, then user must also have
        instructor-level access to proceed.
        """
        url = reverse('update_forum_role_membership', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(
            url,
            {
                'unique_student_identifier': identifier,
                'rolename': rolename,
                'action': action,
            }
        )

        # Status code should be 200.
        assert response.status_code == 200

        user_roles = current_user.roles.filter(course_id=self.course.id).values_list("name", flat=True)
        if action == 'allow':
            assert rolename in user_roles
        elif action == 'revoke':
            assert rolename not in user_roles

    def test_autoenroll_on_forum_role_add(self):
        """
        Test forum role modification auto enrolls user.
        """
        seed_permissions_roles(self.course.id)
        user = UserFactory()
        for rolename in ["Administrator", "Moderator", "Community TA"]:
            assert CourseEnrollment.is_enrolled(user, self.course.id) is False
            self.assert_update_forum_role_membership(user, user.email, rolename, "allow")
            assert CourseEnrollment.is_enrolled(user, self.course.id)
            self.assert_update_forum_role_membership(user, user.email, rolename, "revoke")
            CourseEnrollment.unenroll(user, self.course.id)

    def create_forum_roles(self, role_name, user):
        """
        DRY Utility method for adding roles.
        """
        role, __ = Role.objects.get_or_create(
            course_id=self.course.id,
            name=role_name
        )
        role.users.add(user)

    def access_list_forum(self, user):
        """
        Utility method for adding forums rules and hitting the url.
        """
        for role_name in ["Group Moderator", "Moderator", "Community TA", "Administrator"]:
            self.create_forum_roles(role_name, user)
            return self.client.post(self.list_forum_members_url, {'rolename': role_name})

    def test_staff_without_forum_admin_access(self):
        """
        Test to ensure that an error is raised if the given rolename lacks the appropriate permissions.
        Allowed Role is either (staff & forum admin) or (instructor).

        In this test case user has staff permissions but his forum admin role is missing.
        """
        self.client.logout()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        role_name = "staff"
        CourseAccessRoleFactory(
            course_id=self.course.id,
            user=self.other_user,
            role=role_name,
            org=self.course.id.org
        )

        response = self.access_list_forum(self.other_user)
        assert response.status_code == 403

        if role_name in ["Administrator"]:
            # if the rolename is `Administrator` then user must need `Administrator` access.
            assert (response.__dict__['data'].get('detail') ==
                    "Operation requires instructor access.")
        else:
            assert (response.__dict__['data'].get('detail') ==
                    "Operation requires staff & forum admin or instructor access")

    def test_staff_with_forum_admin_access(self):
        """
        Test to ensure that an error is raised if the given rolename lacks the appropriate permissions.
        Allowed Role is either (staff & forum admin) or (instructor)

        In this test case user has staff permissions and forum admin role also.
        """
        self.client.logout()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)

        CourseAccessRoleFactory(
            course_id=self.course.id,
            user=self.other_user,
            role="staff",
            org=self.course.id.org
        )

        # make user staff and administrator
        self.create_forum_roles('Administrator', self.other_user)
        response = self.access_list_forum(self.other_user)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert data['course_id'] == str(self.course.id)

    def test_staff_with_forum_admin_access_with_oauth(self):
        """
        Verify the endpoint using JWT authentication.
        """
        self.client.logout()
        dot_application = ApplicationFactory(user=self.other_user, authorization_grant_type='password')
        access_token = AccessTokenFactory(user=self.other_user, application=dot_application)
        oauth_adapter = DOTAdapter()
        token_dict = {
            'access_token': access_token,
            'scope': 'email profile',
        }
        jwt_token = jwt_api.create_jwt_from_token(token_dict, oauth_adapter, use_asymmetric_key=True)
        headers = {
            'HTTP_AUTHORIZATION': 'JWT ' + jwt_token
        }
        response = self.client.post(
            self.list_forum_members_url,
            data={'rolename': 'Moderator'},
            **headers
        )
        # JWT authentication works but it has no permissions.
        assert response.status_code == 403

        # add user as course staff.
        CourseAccessRoleFactory(
            course_id=self.course.id,
            user=self.other_user,
            role="staff",
            org=self.course.id.org
        )

        # add user as forum admin.
        self.create_forum_roles('Administrator', self.other_user)
        response = self.client.post(self.list_forum_members_url, data={'rolename': 'Moderator'}, **headers)
        assert response.status_code == 200


@ddt.ddt
class TestInstructorAPILevelsDataDump(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints that show data without side effects.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.course_mode = CourseMode(course_id=self.course.id,
                                      mode_slug="honor",
                                      mode_display_name="honor cert",
                                      min_price=40)
        self.course_mode.save()
        self.instructor = InstructorFactory(course_key=self.course.id)
        CourseDataResearcherRole(self.course.id).add_users(self.instructor)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.students = [UserFactory() for _ in range(6)]
        for student in self.students:
            CourseEnrollment.enroll(student, self.course.id)

        self.students_who_may_enroll = self.students + [UserFactory() for _ in range(5)]
        for student in self.students_who_may_enroll:
            CourseEnrollmentAllowed.objects.create(
                email=student.email, course_id=self.course.id
            )

    @ddt.data(
        ('get_problem_responses', {'problem_location': ""}),
        ('instructor_api_v1:generate_problem_responses', {"problem_locations": ["abc"]}),
    )
    @ddt.unpack
    def test_get_problem_responses_invalid_location(self, endpoint, post_data):
        """
        Test whether get_problem_responses returns an appropriate status
        message when users submit an invalid problem location.
        """
        url = reverse(
            endpoint,
            kwargs={'course_id': str(self.course.id)}
        )

        response = self.client.post(url, post_data, content_type="application/json")
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == 'Could not find problem with this location.'

    def valid_problem_location(test):  # pylint: disable=no-self-argument
        """
        Decorator for tests that target get_problem_responses endpoint and
        need to pretend user submitted a valid problem location.
        """
        @functools.wraps(test)
        def wrapper(self, *args, **kwargs):
            """
            Run `test` method, ensuring that UsageKey.from_string returns a
            problem key that the get_problem_responses endpoint can
            work with.
            """
            mock_problem_key = NonCallableMock(return_value='')
            mock_problem_key.course_key = self.course.id
            with patch.object(UsageKey, 'from_string') as patched_method:
                patched_method.return_value = mock_problem_key
                test(self, *args, **kwargs)
        return wrapper

    @valid_problem_location
    @ddt.data(
        ('get_problem_responses', {'problem_location': "test"}),
        ('instructor_api_v1:generate_problem_responses', {'problem_locations': ["test"]}),
    )
    @ddt.unpack
    def test_get_problem_responses_successful(self, endpoint, post_data):
        """
        Test whether get_problem_responses returns an appropriate status
        message if CSV generation was started successfully.
        """
        url = reverse(
            endpoint,
            kwargs={'course_id': str(self.course.id)}
        )

        response = self.client.post(url, post_data, content_type="application/json")
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'status' in res_json
        status = res_json['status']
        assert 'is being created' in status
        assert 'already in progress' not in status
        assert 'task_id' in res_json

    @valid_problem_location
    @ddt.data('get_problem_responses', 'instructor_api_v1:generate_problem_responses')
    def test_get_problem_responses_already_running(self, endpoint):
        """
        Test whether get_problem_responses returns an appropriate status
        message if CSV generation is already in progress.
        """
        url = reverse(
            endpoint,
            kwargs={'course_id': str(self.course.id)}
        )
        task_type = 'problem_responses_csv'
        already_running_status = generate_already_running_error_message(task_type)
        with patch('lms.djangoapps.instructor_task.api.submit_calculate_problem_responses_csv') as submit_task_function:
            error = AlreadyRunningError(already_running_status)
            submit_task_function.side_effect = error
            response = self.client.post(url, {"problem_locations": ["test"]}, content_type="application/json")

        self.assertContains(response, already_running_status, status_code=400)

    def test_get_students_features(self):
        """
        Test that some minimum of information is formatted
        correctly in the response to get_students_features.
        """
        for student in self.students:
            student.profile.city = f"Mos Eisley {student.id}"
            student.profile.save()
        url = reverse('get_students_features', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {})
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'students' in res_json
        for student in self.students:
            student_json = [
                x for x in res_json['students']
                if x['username'] == student.username
            ][0]
            assert student_json['username'] == student.username
            assert student_json['email'] == student.email
            assert student_json['city'] == student.profile.city
            assert student_json['country'] == ''

    def test_get_students_features_private_fields(self):
        """
        Test that the get_students_features does not return the private fields
        if they are by default in the `PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS` setting.
        """
        url = reverse("get_students_features", kwargs={"course_id": str(self.course.id)})
        response = self.client.post(url, {})
        res_json = json.loads(response.content.decode("utf-8"))

        assert "students" in res_json
        for student in res_json["students"]:
            for field in settings.PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS:
                assert field not in student

    def test_get_students_features_private_fields_with_custom_config(self):
        """
        Test that the get_students_features does not return the private custom
        fields set in the `PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS` setting.
        """
        private_fields = ["email", "location", "gender"]

        with override_settings(PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS=private_fields):
            url = reverse("get_students_features", kwargs={"course_id": str(self.course.id)})
            response = self.client.post(url, {})
            res_json = json.loads(response.content.decode("utf-8"))

            assert "students" in res_json
            for student in res_json["students"]:
                for field in private_fields:
                    assert field not in student

    @override_settings(PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS=[])
    def test_get_students_features_private_fields_empty(self):
        """
        Test that the get_students_features returns all the fields if the
        `PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS` setting is empty.
        """
        custom_config = {
            "student_profile_download_fields": [
                "id",
                "username",
                "email",
                "language",
                "year_of_birth",
            ]
        }

        with with_site_configuration_context(configuration=custom_config):
            url = reverse("get_students_features", kwargs={"course_id": str(self.course.id)})
            response = self.client.post(url, {})
            res_json = json.loads(response.content.decode("utf-8"))

            assert "students" in res_json
            for student in res_json["students"]:
                for field in custom_config["student_profile_download_fields"]:
                    assert field in student

    @ddt.data(True, False)
    def test_get_students_features_cohorted(self, is_cohorted):
        """
        Test that get_students_features includes cohort info when the course is
        cohorted, and does not when the course is not cohorted.
        """
        url = reverse('get_students_features', kwargs={'course_id': str(self.course.id)})
        set_course_cohorted(self.course.id, is_cohorted)

        response = self.client.post(url, {})
        res_json = json.loads(response.content.decode('utf-8'))

        assert ('cohort' in res_json['feature_names']) == is_cohorted

    @ddt.data(True, False)
    def test_get_students_features_teams(self, has_teams):
        """
        Test that get_students_features includes team info when the course is
        has teams enabled, and does not when the course does not have teams enabled
        """
        if has_teams:
            self.course = CourseFactory.create(teams_configuration=TeamsConfig({
                'max_size': 2, 'topics': [{'id': 'topic', 'name': 'Topic', 'description': 'A Topic'}]
            }))
            course_instructor = InstructorFactory(course_key=self.course.id)
            CourseDataResearcherRole(self.course.id).add_users(course_instructor)
            self.client.login(username=course_instructor.username, password=self.TEST_PASSWORD)

        url = reverse('get_students_features', kwargs={'course_id': str(self.course.id)})

        response = self.client.post(url, {})
        res_json = json.loads(response.content.decode('utf-8'))

        assert ('team' in res_json['feature_names']) == has_teams

    @ddt.data(True, False)
    def test_get_students_features_external_user_key(self, has_program_enrollments):
        external_key_dict = {}
        if has_program_enrollments:
            for i in range(len(self.students)):
                student = self.students[i]
                external_key = f"{student.username}_{i}"
                ProgramEnrollmentFactory.create(user=student, external_user_key=external_key)
                external_key_dict[student.username] = external_key

        url = reverse('get_students_features', kwargs={'course_id': str(self.course.id)})

        response = self.client.post(url, {})
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'external_user_key' in res_json['feature_names']
        for student in self.students:
            student_json = [
                x for x in res_json['students']
                if x['username'] == student.username
            ][0]
            assert student_json['username'] == student.username
            if has_program_enrollments:
                assert student_json['external_user_key'] == external_key_dict[student.username]
            else:
                assert student_json['external_user_key'] == ''

    def test_get_students_features_without_permissions(self):
        """ Test that get_students_features returns 403 without credentials. """

        # removed both roles from courses for instructor
        CourseDataResearcherRole(self.course.id).remove_users(self.instructor)
        CourseInstructorRole(self.course.id).remove_users(self.instructor)
        url = reverse('get_students_features', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {})
        assert response.status_code == 403

    def test_get_students_who_may_enroll(self):
        """
        Test whether get_students_who_may_enroll returns an appropriate
        status message when users request a CSV file of students who
        may enroll in a course.
        """
        url = reverse(
            'get_students_who_may_enroll',
            kwargs={'course_id': str(self.course.id)}
        )
        # Successful case:
        response = self.client.post(url, {})
        assert response.status_code == 200
        # CSV generation already in progress:
        task_type = InstructorTaskTypes.MAY_ENROLL_INFO_CSV
        already_running_status = generate_already_running_error_message(task_type)
        with patch('lms.djangoapps.instructor_task.api.submit_calculate_may_enroll_csv') as submit_task_function:
            error = AlreadyRunningError(already_running_status)
            submit_task_function.side_effect = error
            response = self.client.post(url, {})
        self.assertContains(response, already_running_status, status_code=400)

    def test_get_student_exam_results(self):
        """
        Test whether get_proctored_exam_results returns an appropriate
        status message when users request a CSV file.
        """
        url = reverse(
            'get_proctored_exam_results',
            kwargs={'course_id': str(self.course.id)}
        )

        # Successful case:
        response = self.client.post(url, {})
        assert response.status_code == 200
        # CSV generation already in progress:
        task_type = 'proctored_exam_results_report'
        already_running_status = generate_already_running_error_message(task_type)
        with patch('lms.djangoapps.instructor_task.api.submit_proctored_exam_results_report') as submit_task_function:
            error = AlreadyRunningError(already_running_status)
            submit_task_function.side_effect = error
            response = self.client.post(url, {})
            self.assertContains(response, already_running_status, status_code=400)

    def test_access_course_finance_admin_with_invalid_course_key(self):
        """
        Test assert require_course fiance_admin before generating
        a detailed enrollment report
        """
        func = Mock()
        decorated_func = require_finance_admin(func)
        request = self.mock_request()
        response = decorated_func(request, 'invalid_course_key')
        assert response.status_code == 404
        assert not func.called

    def mock_request(self):
        """
        mock request
        """
        request = Mock()
        request.user = self.instructor
        return request

    def test_access_course_finance_admin_with_valid_course_key(self):
        """
        Test to check the course_finance_admin role with valid key
        but doesn't have access to the function
        """
        func = Mock()
        decorated_func = require_finance_admin(func)
        request = self.mock_request()
        response = decorated_func(request, 'valid/course/key')
        assert response.status_code == 403
        assert not func.called

    def test_add_user_to_fiance_admin_role_with_valid_course(self):
        """
        test to check that a function is called using a fiance_admin
        rights.
        """
        func = Mock()
        decorated_func = require_finance_admin(func)
        request = self.mock_request()
        CourseFinanceAdminRole(self.course.id).add_users(self.instructor)
        decorated_func(request, str(self.course.id))
        assert func.called

    @patch('lms.djangoapps.instructor_task.models.logger.error')
    @patch.dict(settings.GRADES_DOWNLOAD, {'STORAGE_TYPE': 's3', 'ROOT_PATH': 'tmp/edx-s3/grades'})
    @ddt.data('list_report_downloads', 'instructor_api_v1:list_report_downloads')
    def test_list_report_downloads_error_boto(self, endpoint, mock_error):
        """
        Tests the Rate-Limit exceeded is handled and does not raise 500 error.
        """
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        error_response = {'Error': {'Code': 503, 'Message': 'error found'}}
        operation_name = 'test'

        with patch(
            'storages.backends.s3boto3.S3Boto3Storage.listdir',
            side_effect=ClientError(error_response, operation_name)
        ):
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url)
            else:
                response = self.client.post(url, {})
        mock_error.assert_called_with(
            'Fetching files failed for course: %s, status: %s, reason: %s',
            self.course.id,
            error_response.get('Error'),
            error_response.get('Error').get('Message')
        )

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == {'downloads': []}

    @patch('lms.djangoapps.instructor_task.models.logger.error')
    @patch(
        'lms.djangoapps.instructor_task.models.DJANGO_STORE_STORAGE_CLASS',
        'storages.backends.s3boto3.S3Boto3Storage'
    )
    @patch.dict(settings.GRADES_DOWNLOAD, {'STORAGE_TYPE': 's3', 'ROOT_PATH': 'tmp/edx-s3/grades'})
    @ddt.data('list_report_downloads', 'instructor_api_v1:list_report_downloads')
    def test_list_report_downloads_error_boto3(self, endpoint, mock_error):
        """
        Tests the Rate-Limit exceeded is handled and does not raise 500 error.
        """
        error_response = {'Error': {'Code': 503, 'Message': 'error found'}}
        operation_name = 'test'
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        with patch(
            'storages.backends.s3boto3.S3Boto3Storage.listdir',
            side_effect=ClientError(error_response, operation_name)
        ):
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url)
            else:
                response = self.client.post(url, {})

        mock_error.assert_called_with(
            'Fetching files failed for course: %s, status: %s, reason: %s',
            self.course.id,
            error_response.get('Error'),
            error_response.get('Error').get('Message')
        )

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == {'downloads': []}

    @ddt.data('list_report_downloads', 'instructor_api_v1:list_report_downloads')
    def test_list_report_downloads(self, endpoint):
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        with patch('lms.djangoapps.instructor_task.models.DjangoStorageReportStore.links_for') as mock_links_for:
            mock_links_for.return_value = [
                ('mock_file_name_1', 'https://1.mock.url'),
                ('mock_file_name_2', 'https://2.mock.url'),
            ]
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url)
            else:
                response = self.client.post(url, {})

        expected_response = {
            "downloads": [
                {
                    "url": "https://1.mock.url",
                    "link": "<a href=\"https://1.mock.url\">mock_file_name_1</a>",
                    "name": "mock_file_name_1"
                },
                {
                    "url": "https://2.mock.url",
                    "link": "<a href=\"https://2.mock.url\">mock_file_name_2</a>",
                    "name": "mock_file_name_2"
                }
            ]
        }
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected_response

    @ddt.data(*REPORTS_DATA)
    @ddt.unpack
    @valid_problem_location
    def test_calculate_report_csv_success(
        self, report_type, instructor_api_endpoint, task_api_endpoint, extra_instructor_api_kwargs
    ):
        kwargs = {'course_id': str(self.course.id)}
        kwargs.update(extra_instructor_api_kwargs)
        url = reverse(instructor_api_endpoint, kwargs=kwargs)
        success_status = f"The {report_type} report is being created."
        with patch(task_api_endpoint) as mock_task_api_endpoint:
            if report_type == 'problem responses':
                mock_task_api_endpoint.return_value = Mock(task_id='task-id-1138')
                response = self.client.post(url, {'problem_location': ''})
                self.assertContains(response, success_status)
            else:
                CourseFinanceAdminRole(self.course.id).add_users(self.instructor)
                response = self.client.post(url, {})
                self.assertContains(response, success_status)

    @valid_problem_location
    def test_idv_retirement_student_features_report(self):
        kwargs = {'course_id': str(self.course.id)}
        kwargs.update({'csv': '/csv'})
        url = reverse('get_students_features', kwargs=kwargs)
        success_status = 'The enrolled learner profile report is being created.'
        with patch('lms.djangoapps.instructor_task.api.submit_calculate_students_features_csv') as mock_task_endpoint:
            CourseFinanceAdminRole(self.course.id).add_users(self.instructor)
            response = self.client.post(url, {})
            self.assertContains(response, success_status)

            # assert that if the integrity signature is enabled, the verification
            # status is not included as a query feature
            args = mock_task_endpoint.call_args.args
            self.assertEqual(len(args), 3)
            query_features = args[2]

            self.assertNotIn('verification_status', query_features)

    def test_get_ora2_responses_success(self):
        url = reverse('export_ora2_data', kwargs={'course_id': str(self.course.id)})

        with patch('lms.djangoapps.instructor_task.api.submit_export_ora2_data') as mock_submit_ora2_task:
            mock_submit_ora2_task.return_value = True
            response = self.client.post(url, {})
        success_status = "The ORA data report is being created."
        self.assertContains(response, success_status)

    def test_get_ora2_responses_already_running(self):
        url = reverse('export_ora2_data', kwargs={'course_id': str(self.course.id)})
        task_type = InstructorTaskTypes.EXPORT_ORA2_DATA
        already_running_status = generate_already_running_error_message(task_type)

        with patch('lms.djangoapps.instructor_task.api.submit_export_ora2_data') as mock_submit_ora2_task:
            mock_submit_ora2_task.side_effect = AlreadyRunningError(already_running_status)
            response = self.client.post(url, {})

        self.assertContains(response, already_running_status, status_code=400)

    def test_get_ora2_submission_files_success(self):
        url = reverse('export_ora2_submission_files', kwargs={'course_id': str(self.course.id)})

        with patch(
            'lms.djangoapps.instructor_task.api.submit_export_ora2_submission_files'
        ) as mock_submit_ora2_task:
            mock_submit_ora2_task.return_value = True
            response = self.client.post(url, {})

        success_status = 'Attachments archive is being created.'

        self.assertContains(response, success_status)

    def test_get_ora2_submission_files_already_running(self):
        url = reverse('export_ora2_submission_files', kwargs={'course_id': str(self.course.id)})
        task_type = InstructorTaskTypes.EXPORT_ORA2_SUBMISSION_FILES
        already_running_status = generate_already_running_error_message(task_type)

        with patch(
            'lms.djangoapps.instructor_task.api.submit_export_ora2_submission_files'
        ) as mock_submit_ora2_task:
            mock_submit_ora2_task.side_effect = AlreadyRunningError(already_running_status)
            response = self.client.post(url, {})

        self.assertContains(response, already_running_status, status_code=400)

    def test_get_ora2_summary_responses_success(self):
        url = reverse('export_ora2_summary', kwargs={'course_id': str(self.course.id)})

        with patch('lms.djangoapps.instructor_task.api.submit_export_ora2_summary') as mock_submit_ora2_task:
            mock_submit_ora2_task.return_value = True
            response = self.client.post(url, {})
        success_status = "The ORA summary report is being created."
        self.assertContains(response, success_status)

    def test_get_ora2_summary_responses_already_running(self):
        url = reverse('export_ora2_summary', kwargs={'course_id': str(self.course.id)})
        task_type = InstructorTaskTypes.EXPORT_ORA2_SUMMARY
        already_running_status = generate_already_running_error_message(task_type)

        with patch('lms.djangoapps.instructor_task.api.submit_export_ora2_summary') as mock_submit_ora2_task:
            mock_submit_ora2_task.side_effect = AlreadyRunningError(already_running_status)
            response = self.client.post(url, {})

        self.assertContains(response, already_running_status, status_code=400)

    def test_get_student_progress_url(self):
        """ Test that progress_url is in the successful response. """
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        data = {'unique_student_identifier': self.students[0].email}
        response = self.client.post(url, data)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        expected_data = {
            'course_id': str(self.course.id),
            'progress_url': f'/courses/{self.course.id}/progress/{self.students[0].id}/'
        }

        for key, value in expected_data.items():
            self.assertIn(key, res_json)
            self.assertEqual(res_json[key], value)

    def test_get_student_progress_url_response_headers(self):
        """
        Test that the progress_url endpoint returns the correct headers.
        """
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        data = {'unique_student_identifier': self.students[0].email}
        response = self.client.post(url, data)
        assert response.status_code == 200

        expected_headers = {
            'Allow': 'POST, OPTIONS',  # drf view brings this key.
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Content-Language': 'en',
            'Content-Length': str(len(response.content.decode('utf-8'))),
            'Content-Type': 'application/json',
            'Vary': 'Cookie, Accept-Language, origin',
            'X-Frame-Options': 'DENY'
        }

        for key, value in expected_headers.items():
            self.assertIn(key, response.headers)
            self.assertEqual(response.headers[key], value)

    def test_get_student_progress_url_from_uname(self):
        """ Test that progress_url is in the successful response. """
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        data = {'unique_student_identifier': self.students[0].username}
        response = self.client.post(url, data)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'progress_url' in res_json
        expected_data = {
            'course_id': str(self.course.id),
            'progress_url': f'/courses/{self.course.id}/progress/{self.students[0].id}/'
        }

        for key, value in expected_data.items():
            self.assertIn(key, res_json)
            self.assertEqual(res_json[key], value)

    def test_get_student_progress_url_noparams(self):
        """ Test that the endpoint 404's without the required query params. """
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_get_student_progress_url_nostudent(self):
        """ Test that the endpoint 400's when requesting an unknown email. """
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)
        assert response.status_code == 400

    def test_get_student_progress_url_without_permissions(self):
        """ Test that progress_url returns 403 without credentials. """

        # removed both roles from courses for instructor
        CourseDataResearcherRole(self.course.id).remove_users(self.instructor)
        CourseInstructorRole(self.course.id).remove_users(self.instructor)
        url = reverse('get_student_progress_url', kwargs={'course_id': str(self.course.id)})
        data = {'unique_student_identifier': self.students[0].email}
        response = self.client.post(url, data)
        assert response.status_code == 403


class TestInstructorAPIRegradeTask(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints whereby instructors can change student grades.
    This includes resetting attempts and starting rescore tasks.

    This test does NOT test whether the actions had an effect on the
    database, that is the job of task tests and test_enrollment.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.problem_location = msk_from_problem_urlname(
            cls.course.id,
            'robot-some-problem-urlname'
        )
        cls.problem_urlname = str(cls.problem_location)

    def setUp(self):
        super().setUp()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.module_to_reset = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )

    def test_reset_student_attempts_deletall(self):
        """ Make sure no one can delete all students state on a problem. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
            'delete_module': True,
        })
        assert response.status_code == 400

    def test_reset_student_attempts_single(self):
        """ Test reset single student attempts. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200
        # make sure problem attempts have been reset.
        changed_module = StudentModule.objects.get(pk=self.module_to_reset.pk)
        assert json.loads(changed_module.state)['attempts'] == 0

    # mock out the function which should be called to execute the action.
    @patch('lms.djangoapps.instructor_task.api.submit_reset_problem_attempts_for_all_students')
    def test_reset_student_attempts_all(self, act):
        """ Test reset all student attempts. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
        })
        assert response.status_code == 200
        assert act.called

    def test_reset_student_attempts_missingmodule(self):
        """ Test reset for non-existant problem. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': 'robot-not-a-real-module',
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 400

    @patch('lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send')
    def test_reset_student_attempts_delete(self, _mock_signal):
        """ Test delete single student state. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
            'delete_module': True,
        })
        assert response.status_code == 200
        # make sure the module has been deleted
        assert StudentModule.objects\
            .filter(student=self.module_to_reset.student, course_id=self.module_to_reset.course_id).count() == 0

    def test_reset_student_attempts_nonsense(self):
        """ Test failure with both unique_student_identifier and all_students. """
        url = reverse('reset_student_attempts', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
            'all_students': True,
        })
        assert response.status_code == 400

    @patch('lms.djangoapps.instructor_task.api.submit_rescore_problem_for_student')
    def test_rescore_problem_single(self, act):
        """ Test rescoring of a single student. """
        url = reverse('rescore_problem', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200
        assert act.called

    @patch('lms.djangoapps.instructor_task.api.submit_rescore_problem_for_student')
    def test_rescore_problem_single_from_uname(self, act):
        """ Test rescoring of a single student. """
        url = reverse('rescore_problem', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'unique_student_identifier': self.student.username,
        })
        assert response.status_code == 200
        assert act.called

    @patch('lms.djangoapps.instructor_task.api.submit_rescore_problem_for_all_students')
    def test_rescore_problem_all(self, act):
        """ Test rescoring for all students. """
        url = reverse('rescore_problem', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'problem_to_reset': self.problem_urlname,
            'all_students': True,
        })
        assert response.status_code == 200
        assert act.called

    @override_settings(ENTRANCE_EXAMS=True)
    def test_course_has_entrance_exam_in_student_attempts_reset(self):
        """ Test course has entrance exam id set while resetting attempts"""
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'all_students': True,
            'delete_module': False,
        })
        assert response.status_code == 400

    @override_settings(ENTRANCE_EXAMS=True)
    def test_rescore_entrance_exam_with_invalid_exam(self):
        """ Test course has entrance exam id set while re-scoring. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 400


@override_settings(ENTRANCE_EXAMS=True)
@ddt.ddt
class TestEntranceExamInstructorAPIRegradeTask(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints whereby instructors can rescore student grades,
    reset student attempts and delete state for entrance exam.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='test_org',
            course='test_course',
            run='test_run',
            entrance_exam_id='i4x://{}/{}/chapter/Entrance_exam'.format('test_org', 'test_course')
        )
        cls.course_with_invalid_ee = CourseFactory.create(entrance_exam_id='invalid_exam')

        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.entrance_exam = BlockFactory.create(
                parent=cls.course,
                category='chapter',
                display_name='Entrance exam'
            )
            subsection = BlockFactory.create(
                parent=cls.entrance_exam,
                category='sequential',
                display_name='Subsection 1'
            )
            vertical = BlockFactory.create(
                parent=subsection,
                category='vertical',
                display_name='Vertical 1'
            )
            cls.ee_problem_1 = BlockFactory.create(
                parent=vertical,
                category="problem",
                display_name="Exam Problem - Problem 1"
            )
            cls.ee_problem_2 = BlockFactory.create(
                parent=vertical,
                category="problem",
                display_name="Exam Problem - Problem 2"
            )

    def setUp(self):
        super().setUp()

        self.instructor = InstructorFactory(course_key=self.course.id)
        # Add instructor to invalid ee course
        CourseInstructorRole(self.course_with_invalid_ee.id).add_users(self.instructor)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        ee_module_to_reset1 = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.ee_problem_1.location,
            state=json.dumps({'attempts': 10, 'done': True}),
        )
        ee_module_to_reset2 = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.ee_problem_2.location,
            state=json.dumps({'attempts': 10, 'done': True}),
        )
        self.ee_modules = [ee_module_to_reset1.module_state_key, ee_module_to_reset2.module_state_key]

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_grade_histogram(self, store):
        """
        Verify that a histogram has been created.
        """
        course = CourseFactory.create(default_store=store)

        usage_key = course.id.make_usage_key('problem', 'first_problem')
        StudentModule.objects.create(
            student_id=1,
            grade=100,
            module_state_key=usage_key
        )
        StudentModule.objects.create(
            student_id=2,
            grade=50,
            module_state_key=usage_key
        )

        grades = grade_histogram(usage_key)
        assert grades[0] == (50.0, 1)
        assert grades[1] == (100.0, 1)

    def test_reset_entrance_exam_student_attempts_delete_all(self):
        """ Make sure no one can delete all students state on entrance exam. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'all_students': True,
            'delete_module': True,
        })
        assert response.status_code == 400

    def test_reset_entrance_exam_student_attempts_single(self):
        """ Test reset single student attempts for entrance exam. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200
        # make sure problem attempts have been reset.
        changed_modules = StudentModule.objects.filter(module_state_key__in=self.ee_modules)
        for changed_module in changed_modules:
            assert json.loads(changed_module.state)['attempts'] == 0

    # mock out the function which should be called to execute the action.
    @patch('lms.djangoapps.instructor_task.api.submit_reset_problem_attempts_in_entrance_exam')
    def test_reset_entrance_exam_all_student_attempts(self, act):
        """ Test reset all student attempts for entrance exam. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'all_students': True,
        })
        assert response.status_code == 200
        assert act.called

    def test_reset_student_attempts_invalid_entrance_exam(self):
        """ Test reset for invalid entrance exam. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course_with_invalid_ee.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 400

    def test_entrance_exam_student_delete_state(self):
        """ Test delete single student entrance exam state. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
            'delete_module': True,
        })
        assert response.status_code == 200
        # make sure the module has been deleted
        changed_modules = StudentModule.objects.filter(module_state_key__in=self.ee_modules)
        assert changed_modules.count() == 0

    def test_entrance_exam_delete_state_with_staff(self):
        """ Test entrance exam delete state failure with staff access. """
        self.client.logout()
        staff_user = StaffFactory(course_key=self.course.id)
        self.client.login(username=staff_user.username, password=self.TEST_PASSWORD)
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
            'delete_module': True,
        })
        assert response.status_code == 403

    def test_entrance_exam_reset_student_attempts_nonsense(self):
        """ Test failure with both unique_student_identifier and all_students. """
        url = reverse('reset_student_attempts_for_entrance_exam',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
            'all_students': True,
        })
        assert response.status_code == 400

    @patch('lms.djangoapps.instructor_task.api.submit_rescore_entrance_exam_for_student')
    def test_rescore_entrance_exam_single_student(self, act):
        """ Test re-scoring of entrance exam for single student. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200
        assert act.called

    def test_rescore_entrance_exam_all_student(self):
        """ Test rescoring for all students. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'all_students': True,
        })
        assert response.status_code == 200

    def test_rescore_entrance_exam_if_higher_all_student(self):
        """ Test rescoring for all students only if higher. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'all_students': True,
            'only_if_higher': True,
        })
        assert response.status_code == 200

    def test_rescore_entrance_exam_all_student_and_single(self):
        """ Test re-scoring with both all students and single student parameters. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
            'all_students': True,
        })
        assert response.status_code == 400

    def test_rescore_entrance_exam_with_invalid_exam(self):
        """ Test re-scoring of entrance exam with invalid exam. """
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course_with_invalid_ee.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 400

    def test_list_entrance_exam_instructor_tasks_student(self):
        """ Test list task history for entrance exam AND student. """
        # create a re-score entrance exam task
        url = reverse('rescore_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200

        url = reverse('list_entrance_exam_instructor_tasks', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200

        # check response
        tasks = json.loads(response.content.decode('utf-8'))['tasks']
        assert len(tasks) == 1
        assert tasks[0]['status'] == _('Complete')

    def test_list_entrance_exam_instructor_tasks_all_student(self):
        """ Test list task history for entrance exam AND all student. """
        url = reverse('list_entrance_exam_instructor_tasks', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {})
        assert response.status_code == 200

        # check response
        tasks = json.loads(response.content.decode('utf-8'))['tasks']
        assert len(tasks) == 0

    def test_list_entrance_exam_instructor_with_invalid_exam_key(self):
        """ Test list task history for entrance exam failure if course has invalid exam. """
        url = reverse('list_entrance_exam_instructor_tasks',
                      kwargs={'course_id': str(self.course_with_invalid_ee.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 400

    def test_skip_entrance_exam_student_with_invalid_student(self):
        """ Test skip entrance exam api for non existing user. """
        # create a re-score entrance exam task
        url = reverse('mark_student_can_skip_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': 'test',
        })
        assert response.status_code == 400

    def test_skip_entrance_exam_student(self):
        """ Test skip entrance exam api for student. """
        # create a re-score entrance exam task
        url = reverse('mark_student_can_skip_entrance_exam', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })
        assert response.status_code == 200
        # check response
        message = _('This student (%s) will skip the entrance exam.') % self.student.email
        self.assertContains(response, message)

        # post again with same student
        response = self.client.post(url, {
            'unique_student_identifier': self.student.email,
        })

        # This time response message should be different
        message = _('This student (%s) is already allowed to skip the entrance exam.') % self.student.email
        self.assertContains(response, message)


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class TestInstructorSendEmail(SiteMixin, SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Checks that only instructors have access to email endpoints, and that
    these endpoints are only accessible with courses that actually exist,
    only with valid email messages.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        BulkEmailFlag.objects.all().delete()

    def setUp(self):
        super().setUp()
        test_subject = '\u1234 test subject'
        test_message = '\u6824 test message'
        self.full_test_message = {
            'send_to': '["myself", "staff"]',
            'subject': test_subject,
            'message': test_message,
        }

        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

    def tearDown(self):
        super().tearDown()
        delete_user_preference(self.instructor, 'time_zone', username=self.instructor.username)

    def test_send_email_as_logged_in_instructor(self):
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 200

    def test_send_email_but_not_logged_in(self):
        self.client.logout()
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 401

    def test_send_email_logged_in_but_no_perms(self):
        self.client.logout()
        user = UserFactory()
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 403

    def test_send_email_but_not_staff(self):
        self.client.logout()
        student = UserFactory()
        self.client.login(username=student.username, password=self.TEST_PASSWORD)
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 403

    def test_send_email_but_course_not_exist(self):
        url = reverse('send_email', kwargs={'course_id': 'GarbageCourse/DNE/NoTerm'})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code != 200

    def test_send_email_no_sendto(self):
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'subject': 'test subject',
            'message': 'test message',
        })
        assert response.status_code == 400

    def test_send_email_invalid_sendto(self):
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'send_to': '["invalid_target", "staff"]',
            'subject': 'test subject',
            'message': 'test message',
        })
        assert response.status_code == 400

    def test_send_email_no_subject(self):
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'send_to': '["staff"]',
            'message': 'test message',
        })
        assert response.status_code == 400

    def test_send_email_no_message(self):
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'send_to': '["staff"]',
            'subject': 'test subject',
        })
        assert response.status_code == 400

    def test_send_email_with_site_template_and_from_addr(self):
        site_email = self.site_configuration.site_values.get('course_email_from_addr')
        site_template = self.site_configuration.site_values.get('course_email_template_name')
        CourseEmailTemplate.objects.create(name=site_template)
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 200
        assert 1 == CourseEmail.objects.filter(course_id=self.course.id, sender=self.instructor,
                                               subject=self.full_test_message['subject'],
                                               html_message=self.full_test_message['message'],
                                               template_name=site_template, from_addr=site_email).count()

    def test_send_email_with_org_template_and_from_addr(self):
        org_email = 'fake_org@example.com'
        org_template = 'fake_org_email_template'
        CourseEmailTemplate.objects.create(name=org_template)
        self.site_configuration.site_values.update({
            'course_email_from_addr': {self.course.id.org: org_email},
            'course_email_template_name': {self.course.id.org: org_template}
        })
        self.site_configuration.save()
        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)
        assert response.status_code == 200
        assert 1 == CourseEmail.objects.filter(course_id=self.course.id, sender=self.instructor,
                                               subject=self.full_test_message['subject'],
                                               html_message=self.full_test_message['message'],
                                               template_name=org_template, from_addr=org_email).count()

    @patch("lms.djangoapps.instructor.views.api.task_api.submit_bulk_course_email")
    def test_send_email_with_schedule(self, mock_task_api):
        """
        Test for the new scheduling logic added to the `send_email` function.
        """
        schedule = "2099-05-02T14:00:00.000Z"
        self.full_test_message['schedule'] = schedule
        expected_schedule = dateutil.parser.parse(schedule).replace(tzinfo=pytz.utc)

        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, self.full_test_message)

        assert response.status_code == 200
        _, _, _, arg_schedule = mock_task_api.call_args.args
        assert arg_schedule == expected_schedule

    @patch("lms.djangoapps.instructor.views.api.task_api.submit_bulk_course_email")
    def test_send_email_with_malformed_schedule_expect_error(self, mock_task_api):
        schedule = "Blub Glub"
        self.full_test_message['schedule'] = "Blub Glub"
        expected_message = (
            f"Error occurred creating a scheduled bulk email task. Schedule provided: '{schedule}'. Error: Unknown "
            "string format: Blub Glub"
        )

        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        with LogCapture() as log:
            response = self.client.post(url, self.full_test_message)

        assert response.status_code == 400
        log.check_present((LOG_PATH, "ERROR", expected_message),)
        mock_task_api.assert_not_called()

    def test_send_email_with_lapsed_date_expect_error(self):
        schedule = "2020-01-01T00:00:00.000Z"
        self.full_test_message['schedule'] = schedule
        expected_message = (
            f"Error occurred creating a scheduled bulk email task. Schedule provided: '{schedule}'. Error: the "
            "requested schedule is in the past"
        )

        url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        with LogCapture() as log:

            response = self.client.post(url, self.full_test_message)

        assert response.status_code == 400
        log.check_present((LOG_PATH, "ERROR", expected_message),)


class MockCompletionInfo:
    """Mock for get_task_completion_info"""
    times_called = 0

    def mock_get_task_completion_info(self, *args):  # pylint: disable=unused-argument
        """Mock for get_task_completion_info"""
        self.times_called += 1
        if self.times_called % 2 == 0:
            return True, 'Task Completed'
        return False, 'Task Errored In Some Way'


@ddt.ddt
class TestInstructorAPITaskLists(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test instructor task list endpoint.
    """

    class FakeTask:
        """ Fake task object """
        FEATURES = [
            'task_type',
            'task_input',
            'task_id',
            'requester',
            'task_state',
            'created',
            'status',
            'task_message',
            'duration_sec'
        ]

        def __init__(self, completion):
            for feature in self.FEATURES:
                setattr(self, feature, 'expected')
            # created needs to be a datetime
            self.created = datetime.datetime(2013, 10, 25, 11, 42, 35)
            # set 'status' and 'task_message' attrs
            success, task_message = completion()
            if success:
                self.status = "Complete"
            else:
                self.status = "Incomplete"
            self.task_message = task_message
            # Set 'task_output' attr, which will be parsed to the 'duration_sec' attr.
            self.task_output = '{"duration_ms": 1035000}'
            self.duration_sec = 1035000 / 1000.0

        def make_invalid_output(self):
            """Munge task_output to be invalid json"""
            self.task_output = 'HI MY NAME IS INVALID JSON'
            # This should be given the value of 'unknown' if the task output
            # can't be properly parsed
            self.duration_sec = 'unknown'

        def to_dict(self):
            """ Convert fake task to dictionary representation. """
            attr_dict = {key: getattr(self, key) for key in self.FEATURES}
            attr_dict['created'] = attr_dict['created'].isoformat()
            return attr_dict

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            entrance_exam_id='i4x://{}/{}/chapter/Entrance_exam'.format('test_org', 'test_course')
        )
        cls.problem_location = msk_from_problem_urlname(
            cls.course.id,
            'robot-some-problem-urlname'
        )
        cls.problem_urlname = str(cls.problem_location)

    def setUp(self):
        super().setUp()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.module = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem_location,
            state=json.dumps({'attempts': 10}),
        )
        mock_factory = MockCompletionInfo()
        self.tasks = [self.FakeTask(mock_factory.mock_get_task_completion_info) for _ in range(7)]
        self.tasks[-1].make_invalid_output()

    @patch('lms.djangoapps.instructor_task.api.get_running_instructor_tasks')
    @ddt.data('instructor_api_v1:list_instructor_tasks', 'list_instructor_tasks')
    def test_list_instructor_tasks_running(self, endpoint, act):
        """ Test list of all running tasks. """
        act.return_value = self.tasks
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        mock_factory = MockCompletionInfo()
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.get_task_completion_info'
        ) as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url)
            else:
                response = self.client.post(url, {})
        assert response.status_code == 200

        # check response
        assert act.called
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content.decode('utf-8'))['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        assert actual_tasks == expected_tasks

    @patch('lms.djangoapps.instructor_task.api.get_instructor_task_history')
    def test_list_background_email_tasks(self, act):
        """Test list of background email tasks."""
        act.return_value = self.tasks
        url = reverse('list_background_email_tasks', kwargs={'course_id': str(self.course.id)})
        mock_factory = MockCompletionInfo()
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.get_task_completion_info'
        ) as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            response = self.client.post(url, {})
        assert response.status_code == 200

        # check response
        assert act.called
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content.decode('utf-8'))['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        assert actual_tasks == expected_tasks

    @patch('lms.djangoapps.instructor_task.api.get_instructor_task_history')
    @ddt.data('instructor_api_v1:list_instructor_tasks', 'list_instructor_tasks')
    def test_list_instructor_tasks_problem(self, endpoint, act):
        """ Test list task history for problem. """
        act.return_value = self.tasks
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        mock_factory = MockCompletionInfo()
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.get_task_completion_info'
        ) as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url, {
                    'problem_location_str': self.problem_urlname,
                })
            else:
                response = self.client.post(url, {
                    'problem_location_str': self.problem_urlname,
                })
        assert response.status_code == 200

        # check response
        assert act.called
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content.decode('utf-8'))['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)
        assert actual_tasks == expected_tasks

    @patch('lms.djangoapps.instructor_task.api.get_instructor_task_history')
    @ddt.data('list_instructor_tasks', 'instructor_api_v1:list_instructor_tasks')
    def test_list_instructor_tasks_problem_student(self, endpoint, act):
        """ Test list task history for problem AND student. """
        act.return_value = self.tasks
        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        mock_factory = MockCompletionInfo()
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.get_task_completion_info'
        ) as mock_completion_info:
            mock_completion_info.side_effect = mock_factory.mock_get_task_completion_info
            if endpoint in INSTRUCTOR_GET_ENDPOINTS:
                response = self.client.get(url, {
                    'problem_location_str': self.problem_urlname,
                    'unique_student_identifier': self.student.email,
                })
            else:
                response = self.client.post(url, {
                    'problem_location_str': self.problem_urlname,
                    'unique_student_identifier': self.student.email,
                })
        assert response.status_code == 200

        # check response
        assert act.called
        expected_tasks = [ftask.to_dict() for ftask in self.tasks]
        actual_tasks = json.loads(response.content.decode('utf-8'))['tasks']
        for exp_task, act_task in zip(expected_tasks, actual_tasks):
            self.assertDictEqual(exp_task, act_task)

        assert actual_tasks == expected_tasks


@patch('lms.djangoapps.instructor_task.api.get_instructor_task_history', autospec=True)
class TestInstructorEmailContentList(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the instructor email content history endpoint.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.tasks = {}
        self.emails = {}
        self.emails_info = {}

    def setup_fake_email_info(self, num_emails, with_failures=False, with_schedules=False):
        """ Initialize the specified number of fake emails """
        for email_id in range(num_emails):
            num_sent = random.randint(1, 15401)
            if with_failures:
                failed = random.randint(1, 15401)
            else:
                failed = 0

            self.tasks[email_id] = FakeContentTask(email_id, num_sent, failed, 'expected')
            self.emails[email_id] = FakeEmail(email_id)
            self.emails_info[email_id] = FakeEmailInfo(self.emails[email_id], num_sent, failed)
            if with_schedules and email_id % 2 == 0:
                instructor_task = InstructorTask.create(self.course.id, self.tasks[email_id].task_type,
                                                        self.tasks[email_id].task_key, self.tasks[email_id].task_input,
                                                        self.instructor)
                schedule = "2099-05-02T14:00:00.000Z"
                InstructorTaskSchedule.objects.create(
                    task=instructor_task,
                    task_args=json.dumps(self.tasks[email_id].task_output),
                    task_due=schedule,
                )

    def get_matching_mock_email(self, **kwargs):
        """ Returns the matching mock emails for the given id """
        email_id = kwargs.get('id', 0)
        return self.emails[email_id]

    def get_email_content_response(self, num_emails, task_history_request, with_failures=False, with_schedules=False):
        """ Calls the list_email_content endpoint and returns the respsonse """
        self.setup_fake_email_info(num_emails, with_failures, with_schedules)
        task_history_request.return_value = list(self.tasks.values())
        url = reverse('list_email_content', kwargs={'course_id': str(self.course.id)})
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.CourseEmail.objects.get'
        ) as mock_email_info:
            mock_email_info.side_effect = self.get_matching_mock_email
            response = self.client.post(url, {})
        assert response.status_code == 200
        return response

    def check_emails_sent(self, num_emails, task_history_request, with_failures=False, with_schedules=False):
        """ Tests sending emails with or without failures """
        response = self.get_email_content_response(num_emails, task_history_request, with_failures, with_schedules)
        assert task_history_request.called
        expected_email_info = [email_info.to_dict() for email_info in self.emails_info.values()]
        actual_email_info = json.loads(response.content.decode('utf-8'))['emails']

        assert len(actual_email_info) == num_emails
        for exp_email, act_email in zip(expected_email_info, actual_email_info):
            self.assertDictEqual(exp_email, act_email)

        assert expected_email_info == actual_email_info

    def test_content_list_one_email(self, task_history_request):
        """ Test listing of bulk emails when email list has one email """
        response = self.get_email_content_response(1, task_history_request)
        assert task_history_request.called
        email_info = json.loads(response.content.decode('utf-8'))['emails']

        # Emails list should have one email
        assert len(email_info) == 1

        # Email content should be what's expected
        expected_message = self.emails[0].html_message
        returned_email_info = email_info[0]
        received_message = returned_email_info['email']['html_message']
        assert expected_message == received_message

    def test_content_list_no_emails(self, task_history_request):
        """ Test listing of bulk emails when email list empty """
        response = self.get_email_content_response(0, task_history_request)
        assert task_history_request.called
        email_info = json.loads(response.content.decode('utf-8'))['emails']

        # Emails list should be empty
        assert len(email_info) == 0

    def test_content_list_email_content_many(self, task_history_request):
        """ Test listing of bulk emails sent large amount of emails """
        self.check_emails_sent(50, task_history_request)
        self.check_emails_sent(50, task_history_request, False, True)

    def test_list_email_content_error(self, task_history_request):
        """ Test handling of error retrieving email """
        invalid_task = FakeContentTask(0, 0, 0, 'test')
        invalid_task.make_invalid_input()
        task_history_request.return_value = [invalid_task]
        url = reverse('list_email_content', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {})
        assert response.status_code == 200

        assert task_history_request.called
        returned_email_info = json.loads(response.content.decode('utf-8'))['emails']
        assert len(returned_email_info) == 1
        returned_info = returned_email_info[0]
        for info in ['created', 'sent_to', 'email', 'number_sent', 'requester']:
            assert returned_info[info] is None

    def test_list_email_with_failure(self, task_history_request):
        """ Test the handling of email task that had failures """
        self.check_emails_sent(1, task_history_request, True)
        self.check_emails_sent(1, task_history_request, True, True)

    def test_list_many_emails_with_failures(self, task_history_request):
        """ Test the handling of many emails with failures """
        self.check_emails_sent(50, task_history_request, True)
        self.check_emails_sent(50, task_history_request, True, True)

    def test_list_email_with_no_successes(self, task_history_request):
        task_info = FakeContentTask(0, 0, 10, 'expected')
        email = FakeEmail(0)
        email_info = FakeEmailInfo(email, 0, 10)
        task_history_request.return_value = [task_info]
        url = reverse('list_email_content', kwargs={'course_id': str(self.course.id)})
        with patch(
            'lms.djangoapps.instructor.views.instructor_task_helpers.CourseEmail.objects.get'
        ) as mock_email_info:
            mock_email_info.return_value = email
            response = self.client.post(url, {})
        assert response.status_code == 200

        assert task_history_request.called
        returned_info_list = json.loads(response.content.decode('utf-8'))['emails']

        assert len(returned_info_list) == 1
        returned_info = returned_info_list[0]
        expected_info = email_info.to_dict()
        self.assertDictEqual(expected_info, returned_info)


class TestInstructorAPIHelpers(TestCase):
    """ Test helpers for instructor.api """

    def test_split_input_list(self):
        strings = []
        lists = []
        strings.append(
            "Lorem@ipsum.dolor, sit@amet.consectetur\nadipiscing@elit.Aenean\r convallis@at.lacus\r, ut@lacinia.Sed")
        lists.append(['Lorem@ipsum.dolor', 'sit@amet.consectetur', 'adipiscing@elit.Aenean', 'convallis@at.lacus',
                      'ut@lacinia.Sed'])

        for (stng, lst) in zip(strings, lists):
            assert _split_input_list(stng) == lst

    def test_split_input_list_unicode(self):
        assert _split_input_list('robot@robot.edu, robot2@robot.edu') == ['robot@robot.edu', 'robot2@robot.edu']
        assert _split_input_list('robot@robot.edu, robot2@robot.edu') == ['robot@robot.edu', 'robot2@robot.edu']
        assert _split_input_list('robot@robot.edu, robot2@robot.edu') == ['robot@robot.edu', 'robot2@robot.edu']
        scary_unistuff = chr(40960) + 'abcd' + chr(1972)
        assert _split_input_list(scary_unistuff) == [scary_unistuff]

    def test_msk_from_problem_urlname(self):
        course_id = CourseKey.from_string('MITx/6.002x/2013_Spring')
        name = 'L2Node1'
        output = 'i4x://MITx/6.002x/problem/L2Node1'
        assert str(msk_from_problem_urlname(course_id, name)) == output

    def test_msk_from_problem_urlname_error(self):
        args = ('notagoodcourse', 'L2Node1')
        with pytest.raises(ValueError):
            msk_from_problem_urlname(*args)


def get_extended_due(course, unit, user):
    """
    Gets the overridden due date for the given user on the given unit.  Returns
    `None` if there is no override set.
    """
    location = str(unit.location)
    dates = get_overrides_for_user(course.id, user)
    for override in dates:
        if str(override['location']) == location:
            return override['actual_date']
    return None


def get_date_for_block(course, unit, user, use_cached=False):
    """
    Gets the due date for the given user on the given unit (overridden or original).
    Returns `None` if there is no date set.
    Differs from edx-when's get_date_for_block only in that we skip the cache when `use_cached` is `False` (default).
    """
    return get_dates_for_course(course.id, user=user, use_cached=use_cached).get((unit.location, 'due'), None)


class TestDueDateExtensions(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test data dumps for reporting.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.due = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=UTC)

        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.week1 = BlockFactory.create(due=cls.due)
            cls.week2 = BlockFactory.create(due=cls.due)
            cls.week3 = BlockFactory.create()  # No due date
            cls.course.children = [
                str(cls.week1.location),
                str(cls.week2.location),
                str(cls.week3.location)
            ]
            cls.homework = BlockFactory.create(
                parent_location=cls.week1.location,
                due=cls.due
            )
            cls.week1.children = [str(cls.homework.location)]

    def setUp(self):
        """
        Fixtures.
        """
        super().setUp()

        user1 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week2.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week3.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        user2 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        user3 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        self.user1 = user1
        self.user2 = user2
        CourseEnrollmentFactory.create(user=self.user1, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=self.course.id)
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        extract_dates(None, self.course.id)

    def test_change_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        due_date = datetime.datetime(2013, 12, 30, tzinfo=UTC)
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'due_datetime': '12/30/2013 00:00'
        })
        assert response.status_code == 200, response.content
        assert get_extended_due(self.course, self.week1, self.user1) == due_date
        # This operation regenerates the cache, so we can use cached results from edx-when.
        assert get_date_for_block(self.course, self.week1, self.user1, use_cached=True) == due_date

    def test_change_due_date_with_reason(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        due_date = datetime.datetime(2013, 12, 30, tzinfo=UTC)
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'due_datetime': '12/30/2013 00:00',
            'reason': 'Testing reason.'  # this is optional field.
        })
        assert response.status_code == 200, response.content

        assert get_extended_due(self.course, self.week1, self.user1) == due_date
        # This operation regenerates the cache, so we can use cached results from edx-when.
        assert get_date_for_block(self.course, self.week1, self.user1, use_cached=True) == due_date

    def test_reset_due_date_with_reason(self):
        url = reverse('reset_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'reason': 'Testing reason.'  # this is optional field.
        })
        assert response.status_code == 200
        assert 'Successfully reset due date for student' in response.content.decode('utf-8')

    def test_change_to_invalid_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'due_datetime': '01/01/2009 00:00'
        })
        assert response.status_code == 400, response.content
        assert get_extended_due(self.course, self.week1, self.user1) is None

    def test_change_nonexistent_due_date(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week3.location),
            'due_datetime': '12/30/2013 00:00'
        })
        assert response.status_code == 400, response.content
        assert get_extended_due(self.course, self.week3, self.user1) is None

    def test_change_to_invalid_username(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': 'invalid_username',
            'url': str(self.week1.location),
            'due_datetime': '12/30/2026 02:00'
        })
        assert response.status_code == 404, response.content
        assert get_extended_due(self.course, self.week1, self.user1) is None

    def test_change_to_invalid_due_date_format(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'due_datetime': '12/30/2kkk 00:00:00'
        })
        assert response.status_code == 400, response.content
        assert get_extended_due(self.course, self.week1, self.user1) is None

    def test_change_with_blank_fields(self):
        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': '',
            'url': '',
            'due_datetime': ''
        })
        assert response.status_code == 400, response.content
        assert get_extended_due(self.course, self.week1, self.user1) is None

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_reset_date(self):
        self.test_change_due_date()
        url = reverse('reset_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
        })
        assert response.status_code == 200, response.content
        assert self.due == get_extended_due(self.course, self.week1, self.user1)

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_reset_date_only_in_edx_when(self):
        # Start with a unit that only has a date in edx-when
        assert get_date_for_block(self.course, self.week3, self.user1) is None
        original_due = datetime.datetime(2010, 4, 1, tzinfo=UTC)
        set_date_for_block(self.course.id, self.week3.location, 'due', original_due)
        assert get_date_for_block(self.course, self.week3, self.user1) == original_due

        # set override, confirm it took
        override = datetime.datetime(2010, 7, 1, tzinfo=UTC)
        set_date_for_block(self.course.id, self.week3.location, 'due', override, user=self.user1)
        assert get_date_for_block(self.course, self.week3, self.user1) == override

        # Now test that we noticed the edx-when date
        url = reverse('reset_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week3.location),
        })
        self.assertContains(response, 'Successfully reset due date for student')
        # This operation regenerates the cache, so we can use cached results from edx-when.
        assert get_date_for_block(self.course, self.week3, self.user1, use_cached=True) == original_due

    def test_show_unit_extensions(self):
        self.test_change_due_date()
        url = reverse('show_unit_extensions',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'url': str(self.week1.location)})
        assert response.status_code == 200, response.content
        assert json.loads(response.content.decode('utf-8')) ==\
               {'data': [{'Extended Due Date': '2013-12-30 00:00',
                          'Full Name': self.user1.profile.name,
                          'Username': self.user1.username}],
                'header': ['Username', 'Full Name', 'Extended Due Date'],
                'title': ('Users with due date extensions for %s' % self.week1.display_name)}

    def test_show_student_extensions(self):
        self.test_change_due_date()
        url = reverse('show_student_extensions',
                      kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {'student': self.user1.username})
        assert response.status_code == 200, response.content
        assert json.loads(response.content.decode('utf-8')) ==\
               {'data': [{'Extended Due Date': '2013-12-30 00:00', 'Unit': self.week1.display_name}],
                'header': ['Unit', 'Extended Due Date'],
                'title': (f'Due date extensions for {self.user1.profile.name} ({self.user1.username})')}


class TestChangeDueDateV2(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests for the V2 due date extension API endpoint.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.due = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=UTC)

        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.week1 = BlockFactory.create(due=cls.due)
            cls.week2 = BlockFactory.create(due=cls.due)
            cls.week3 = BlockFactory.create()  # No due date
            cls.course.children = [
                str(cls.week1.location),
                str(cls.week2.location),
                str(cls.week3.location)
            ]
            cls.homework = BlockFactory.create(
                parent_location=cls.week1.location,
                due=cls.due
            )
            cls.week1.children = [str(cls.homework.location)]

    def setUp(self):
        """
        Fixtures.
        """
        super().setUp()

        self.user1 = UserFactory.create()
        self.user2 = UserFactory.create()

        # Create StudentModule objects for tracking student progress
        StudentModule(
            state='{}',
            student_id=self.user1.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=self.user1.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()
        StudentModule(
            state='{}',
            student_id=self.user2.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=self.user2.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        CourseEnrollmentFactory.create(user=self.user1, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=self.course.id)
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        extract_dates(None, self.course.id)

    def test_change_due_date_v2_success(self):
        """Test successful due date change using V2 API endpoint"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        due_date = datetime.datetime(2013, 12, 30, tzinfo=UTC)
        response = self.client.post(url, json.dumps({
            'email_or_username': self.user1.username,
            'block_id': str(self.homework.location),
            'due_datetime': '12/30/2013 00:00',
            'reason': 'Testing V2 API.'
        }), content_type='application/json')

        assert response.status_code == 200, response.content
        response_data = json.loads(response.content.decode('utf-8'))
        assert 'Successfully changed due date for learner' in response_data['message']

        assert get_extended_due(self.course, self.homework, self.user1) == due_date

    def test_change_due_date_v2_with_email(self):
        """Test due date change using email instead of username"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        due_date = datetime.datetime(2013, 12, 30, tzinfo=UTC)
        response = self.client.post(url, json.dumps({
            'email_or_username': self.user1.email,
            'block_id': str(self.homework.location),
            'due_datetime': '12/30/2013 00:00',
            'reason': 'Testing V2 API with email.'
        }), content_type='application/json')

        assert response.status_code == 200, response.content
        response_data = json.loads(response.content.decode('utf-8'))
        assert 'Successfully changed due date for learner' in response_data['message']

        assert get_extended_due(self.course, self.homework, self.user1) == due_date

    def test_change_due_date_v2_invalid_user(self):
        """Test error handling for invalid user"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, json.dumps({
            'email_or_username': 'nonexistent@example.com',
            'block_id': str(self.homework.location),
            'due_datetime': '12/30/2013 00:00'
        }), content_type='application/json')

        assert response.status_code == 400, response.content
        response_data = json.loads(response.content.decode('utf-8'))
        assert 'Invalid learner identifier' in response_data['error']['email_or_username'][0]

    def test_change_due_date_v2_invalid_block(self):
        """Test error handling for invalid block location"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        # Invalid block location should cause an exception (500 error)
        with self.assertRaises(Exception):
            self.client.post(url, json.dumps({
                'email_or_username': self.user1.username,
                'block_id': 'i4x://invalid/block/location',
                'due_datetime': '12/30/2013 00:00'
            }), content_type='application/json')

    def test_change_due_date_v2_invalid_date_format(self):
        """Test error handling for invalid date format"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        # V2 API accepts form data as well
        response = self.client.post(url, {
            'email_or_username': self.user1.username,
            'block_id': self.homework.location,
            'due_datetime': 'invalid-date-format'
        })

        self.assertEqual(response.status_code, 400)

    def test_change_due_date_v2_missing_fields(self):
        """Test error handling for missing required fields"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        # V2 API accepts form data
        response = self.client.post(url, {
            'email_or_username': self.user1.username,
            # Missing 'block_id' and 'due_datetime'
        })

        self.assertEqual(response.status_code, 400)

    def test_change_due_date_v2_unenrolled_user(self):
        """Test error handling for user not enrolled in course"""
        unenrolled_user = UserFactory.create()
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        # V2 API accepts form data
        response = self.client.post(url, {
            'email_or_username': unenrolled_user.username,
            'block_id': self.homework.location,
            'due_datetime': '12/30/2013 00:00'
        })

        self.assertEqual(response.status_code, 400)

    def test_change_due_date_v2_json_content_type(self):
        """Test that V2 API works with both JSON and form data"""
        url = reverse('instructor_api_v2:change_due_date', kwargs={'course_id': str(self.course.id)})
        # Send as form data instead of JSON
        response = self.client.post(url, {
            'email_or_username': self.user1.username,
            'block_id': self.homework.location,
            'due_datetime': '12/30/2013 00:00'
        })

        # The V2 endpoint works with form data and should succeed
        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content.decode('utf-8'))
        self.assertIn('Successfully changed due date for learner', response_data['message'])


class TestDueDateExtensionsDeletedDate(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests for deleting due date extensions
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Fixtures.
        """
        super().setUp()

        self.course = CourseFactory.create()
        self.due = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=UTC)

        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.week1 = BlockFactory.create(due=self.due)
            self.week2 = BlockFactory.create(due=self.due)
            self.week3 = BlockFactory.create()  # No due date
            self.course.children = [
                self.week1.location,
                self.week2.location,
                self.week3.location
            ]
            self.homework = BlockFactory.create(
                parent_location=self.week1.location,
                due=self.due
            )
            self.week1.children = [self.homework.location]

        user1 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week2.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.week3.location).save()
        StudentModule(
            state='{}',
            student_id=user1.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        user2 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user2.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        user3 = UserFactory.create()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=self.course.id,
            module_state_key=self.week1.location).save()
        StudentModule(
            state='{}',
            student_id=user3.id,
            course_id=self.course.id,
            module_state_key=self.homework.location).save()

        self.user1 = user1
        self.user2 = user2
        CourseEnrollmentFactory.create(user=self.user1, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=self.course.id)
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        extract_dates(None, self.course.id)

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_reset_extension_to_deleted_date(self):
        """
        Test that we can delete a due date extension after deleting the normal
        due date, without causing an error.
        """

        url = reverse('change_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
            'due_datetime': '12/30/2013 00:00'
        })
        assert response.status_code == 200, response.content
        assert datetime.datetime(2013, 12, 30, 0, 0, tzinfo=UTC) ==\
               get_extended_due(self.course, self.week1, self.user1)

        self.week1.due = None
        self.week1 = self.store.update_item(self.week1, self.user1.id)
        extract_dates(None, self.course.id)
        # Now, week1's normal due date is deleted but the extension still exists.
        url = reverse('reset_due_date', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, {
            'student': self.user1.username,
            'url': str(self.week1.location),
        })
        assert response.status_code == 200, response.content
        assert self.due == get_extended_due(self.course, self.week1, self.user1)


class TestCourseIssuedCertificatesData(SharedModuleStoreTestCase):
    """
    Test data dumps for issued certificates.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

    def generate_certificate(self, course_id, mode, status):
        """
        Generate test certificate
        """
        test_user = UserFactory()
        GeneratedCertificateFactory.create(
            user=test_user,
            course_id=course_id,
            mode=mode,
            status=status
        )

    def test_certificates_features_against_status(self):
        """
        Test certificates with status 'downloadable' should be in the response.
        """
        url = reverse('get_issued_certificates', kwargs={'course_id': str(self.course.id)})
        # firstly generating downloadable certificates with 'honor' mode
        certificate_count = 3
        for __ in range(certificate_count):
            self.generate_certificate(course_id=self.course.id, mode='honor', status=CertificateStatuses.generating)

        response = self.client.post(url)
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'certificates' in res_json
        assert len(res_json['certificates']) == 0

        # Certificates with status 'downloadable' should be in response.
        self.generate_certificate(course_id=self.course.id, mode='honor', status=CertificateStatuses.downloadable)
        response = self.client.post(url)
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'certificates' in res_json
        assert len(res_json['certificates']) == 1

    def test_certificates_features_group_by_mode(self):
        """
        Test for certificate csv features against mode. Certificates should be group by 'mode' in reponse.
        """
        url = reverse('get_issued_certificates', kwargs={'course_id': str(self.course.id)})
        # firstly generating downloadable certificates with 'honor' mode
        certificate_count = 3
        for __ in range(certificate_count):
            self.generate_certificate(course_id=self.course.id, mode='honor', status=CertificateStatuses.downloadable)

        response = self.client.post(url)
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'certificates' in res_json
        assert len(res_json['certificates']) == 1

        # retrieve the first certificate from the list, there should be 3 certificates for 'honor' mode.
        certificate = res_json['certificates'][0]
        assert certificate.get('total_issued_certificate') == 3
        assert certificate.get('mode') == 'honor'
        assert certificate.get('course_id') == str(self.course.id)

        # Now generating downloadable certificates with 'verified' mode
        for __ in range(certificate_count):
            self.generate_certificate(
                course_id=self.course.id,
                mode='verified',
                status=CertificateStatuses.downloadable
            )

        response = self.client.post(url)
        res_json = json.loads(response.content.decode('utf-8'))
        assert 'certificates' in res_json

        # total certificate count should be 2 for 'verified' mode.
        assert len(res_json['certificates']) == 2

        # retrieve the second certificate from the list
        certificate = res_json['certificates'][1]
        assert certificate.get('total_issued_certificate') == 3
        assert certificate.get('mode') == 'verified'

    def test_certificates_features_csv(self):
        """
        Test for certificate csv features.
        """
        url = reverse('get_issued_certificates', kwargs={'course_id': str(self.course.id)})
        # firstly generating downloadable certificates with 'honor' mode
        certificate_count = 3
        for __ in range(certificate_count):
            self.generate_certificate(course_id=self.course.id, mode='honor', status=CertificateStatuses.downloadable)

        current_date = datetime.date.today().strftime("%B %d, %Y")
        response = self.client.get(url, {'csv': 'true'})
        assert response['Content-Type'] == 'text/csv'
        assert response['Content-Disposition'] == 'attachment; filename={}'.format('issued_certificates.csv')
        assert response.content.strip().decode('utf-8') == \
               (((('"CourseID","Certificate Type","Total Certificates Issued","Date Report Run"\r\n"' +
                   str(self.course.id)) + '","honor","3","') + current_date) + '"')


class TestBulkCohorting(SharedModuleStoreTestCase):
    """
    Test adding users to cohorts in bulk via CSV upload.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.staff_user = StaffFactory(course_key=self.course.id)
        self.non_staff_user = UserFactory.create()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

    def call_add_users_to_cohorts(self, csv_data, suffix='.csv'):
        """
        Call `add_users_to_cohorts` with a file generated from `csv_data`.
        """
        # this temporary file will be removed in `self.tearDown()`
        __, file_name = tempfile.mkstemp(suffix=suffix, dir=self.tempdir)
        with open(file_name, 'w', encoding='utf-8-sig') as file_pointer:
            file_pointer.write(csv_data)
        with open(file_name) as file_pointer:
            url = reverse('add_users_to_cohorts', kwargs={'course_id': str(self.course.id)})
            return self.client.post(url, {'uploaded-file': file_pointer})

    def expect_error_on_file_content(self, file_content, error, file_suffix='.csv'):
        """
        Verify that we get the error we expect for a given file input.
        """
        self.client.login(username=self.staff_user.username, password=self.TEST_PASSWORD)
        response = self.call_add_users_to_cohorts(file_content, suffix=file_suffix)
        assert response.status_code == 400
        result = json.loads(response.content.decode('utf-8'))
        assert result['error'] == error

    def verify_success_on_file_content(self, file_content, mock_store_upload, mock_cohort_task):
        """
        Verify that `addd_users_to_cohorts` successfully validates the
        file content, uploads the input file, and triggers the
        background task.
        """
        mock_store_upload.return_value = (None, 'fake_file_name.csv')
        self.client.login(username=self.staff_user.username, password=self.TEST_PASSWORD)
        response = self.call_add_users_to_cohorts(file_content)
        assert response.status_code == 204
        assert mock_store_upload.called
        assert mock_cohort_task.called

    def test_no_cohort_field(self):
        """
        Verify that we get a descriptive verification error when we haven't
        included a cohort field in the uploaded CSV.
        """
        self.expect_error_on_file_content(
            'username,email\n', "The file must contain a 'cohort' column containing cohort names."
        )

    def test_no_username_or_email_field(self):
        """
        Verify that we get a descriptive verification error when we haven't
        included a username or email field in the uploaded CSV.
        """
        self.expect_error_on_file_content(
            'cohort\n', "The file must contain a 'username' column, an 'email' column, or both."
        )

    def test_empty_csv(self):
        """
        Verify that we get a descriptive verification error when we haven't
        included any data in the uploaded CSV.
        """
        self.expect_error_on_file_content(
            '', "The file must contain a 'cohort' column containing cohort names."
        )

    def test_wrong_extension(self):
        """
        Verify that we get a descriptive verification error when we haven't
        uploaded a file with a '.csv' extension.
        """
        self.expect_error_on_file_content(
            '', "The file must end with the extension '.csv'.", file_suffix='.notcsv'
        )

    def test_non_staff_no_access(self):
        """
        Verify that we can't access the view when we aren't a staff user.
        """
        self.client.login(username=self.non_staff_user.username, password=self.TEST_PASSWORD)
        response = self.call_add_users_to_cohorts('')
        assert response.status_code == 403

    @patch('lms.djangoapps.instructor_task.api.submit_cohort_students')
    @patch('lms.djangoapps.instructor.views.api.store_uploaded_file')
    def test_success_username(self, mock_store_upload, mock_cohort_task):
        """
        Verify that we store the input CSV and call a background task when
        the CSV has username and cohort columns.
        """
        self.verify_success_on_file_content(
            'username,cohort\nfoo_username,bar_cohort', mock_store_upload, mock_cohort_task
        )

    @patch('lms.djangoapps.instructor_task.api.submit_cohort_students')
    @patch('lms.djangoapps.instructor.views.api.store_uploaded_file')
    def test_success_email(self, mock_store_upload, mock_cohort_task):
        """
        Verify that we store the input CSV and call the cohorting background
        task when the CSV has email and cohort columns.
        """
        self.verify_success_on_file_content(
            'email,cohort\nfoo_email,bar_cohort', mock_store_upload, mock_cohort_task
        )

    @patch('lms.djangoapps.instructor_task.api.submit_cohort_students')
    @patch('lms.djangoapps.instructor.views.api.store_uploaded_file')
    def test_success_username_and_email(self, mock_store_upload, mock_cohort_task):
        """
        Verify that we store the input CSV and call the cohorting background
        task when the CSV has username, email and cohort columns.
        """
        self.verify_success_on_file_content(
            'username,email,cohort\nfoo_username,bar_email,baz_cohort', mock_store_upload, mock_cohort_task
        )

    @patch('lms.djangoapps.instructor_task.api.submit_cohort_students')
    @patch('lms.djangoapps.instructor.views.api.store_uploaded_file')
    def test_success_carriage_return(self, mock_store_upload, mock_cohort_task):
        """
        Verify that we store the input CSV and call the cohorting background
        task when lines in the CSV are delimited by carriage returns.
        """
        self.verify_success_on_file_content(
            'username,email,cohort\rfoo_username,bar_email,baz_cohort', mock_store_upload, mock_cohort_task
        )

    @patch('lms.djangoapps.instructor_task.api.submit_cohort_students')
    @patch('lms.djangoapps.instructor.views.api.store_uploaded_file')
    def test_success_carriage_return_line_feed(self, mock_store_upload, mock_cohort_task):
        """
        Verify that we store the input CSV and call the cohorting background
        task when lines in the CSV are delimited by carriage returns and line
        feeds.
        """
        self.verify_success_on_file_content(
            'username,email,cohort\r\nfoo_username,bar_email,baz_cohort', mock_store_upload, mock_cohort_task
        )


class TestInstructorCertificateExceptions(SharedModuleStoreTestCase):
    """
    Tests for utility functions utilized in the Instructor Dashboard Certificates app.
    """

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.course = CourseFactory.create()
        self.user = UserFactory()
        CourseEnrollment.enroll(self.user, self.course.id)

    def test_get_student_from_request_data(self):
        """
        Test ability to retrieve a learner record using their username and course id
        """
        student = _get_student_from_request_data({"user": self.user.username})

        assert student.username == self.user.username

    def test_get_student_from_request_data_empty_username(self):
        """
        Test that we receive an expected error when no learner's username or email is entered
        """
        with pytest.raises(ValueError) as error:
            _get_student_from_request_data({"user": ""})

        assert str(error.value) == (
            'Student username/email field is required and can not be empty. Kindly fill in username/email and then '
            'press "Invalidate Certificate" button.'
        )

    def test_get_student_from_request_data_user_dne(self):
        """
        Test to verify an expected error message is returned when attempting to retrieve a learner that does not exist
        in the LMS.
        """
        with pytest.raises(ValueError) as error:
            _get_student_from_request_data({"user": "Neo"})

        assert str(error.value) == "Neo does not exist in the LMS. Please check your spelling and retry."

    def test_get_certificate_for_user(self):
        """
        Test that attempts to retrieve a Certificate for a learner in a course-run.
        """
        generated_certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            mode='verified',
            status=CertificateStatuses.downloadable,
        )

        retrieved_certificate = _get_certificate_for_user(self.course.id, self.user)
        assert retrieved_certificate.id == generated_certificate.id

    def test_get_certificate_for_user_no_certificate(self):
        """
        Test to verify an expected error message is returned when attempting to retrieve a certificate for a learner
        that does not exist yet.
        """
        with pytest.raises(ValueError) as error:
            _get_certificate_for_user(self.course.id, self.user)

        assert str(error.value) == (
            f"The student {self.user} does not have certificate for the course {self.course.id.course}. Kindly "
            "verify student username/email and the selected course are correct and try again."
        )


@patch.dict(settings.FEATURES, {'ALLOW_AUTOMATED_SIGNUPS': True})
class TestOauthInstructorAPILevelsAccess(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test endpoints using Oauth2 authentication.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='test_org',
            course='test_course',
            run='test_run',
            entrance_exam_id='i4x://{}/{}/chapter/Entrance_exam'.format('test_org', 'test_course')
        )
        self.problem_location = msk_from_problem_urlname(
            self.course.id,
            'robot-some-problem-urlname'
        )
        self.problem_urlname = str(self.problem_location)

        self.other_user = UserFactory()
        dot_application = ApplicationFactory(user=self.other_user, authorization_grant_type='password')
        access_token = AccessTokenFactory(user=self.other_user, application=dot_application)
        oauth_adapter = DOTAdapter()
        token_dict = {
            'access_token': access_token,
            'scope': 'email profile',
        }
        jwt_token = jwt_api.create_jwt_from_token(token_dict, oauth_adapter, use_asymmetric_key=True)

        self.headers = {
            'HTTP_AUTHORIZATION': 'JWT ' + jwt_token
        }

        # endpoints contains all urls with body and role.
        self.endpoints = [
            ('list_course_role_members', {'rolename': 'staff'}, 'instructor'),
            ('register_and_enroll_students', {}, 'staff'),
            ('get_student_progress_url', {'course_id': str(self.course.id),
                                          'unique_student_identifier': self.other_user.email
                                          }, 'staff'
             ),
            ('list_entrance_exam_instructor_tasks', {'unique_student_identifier': self.other_user.email}, 'staff'),
            ('list_email_content', {}, 'staff'),
            ('show_student_extensions', {'student': self.other_user.email}, 'staff'),
            ('list_email_content', {}, 'staff'),
            ('list_report_downloads', {
                "send-to": ["myself"],
                "subject": "This is subject",
                "message": "message"
            }, 'data_researcher'),
            ('list_instructor_tasks',
             {
                 'problem_location_str': self.problem_urlname,
                 'unique_student_identifier': self.other_user.email
             },
             'data_researcher'),
            ('list_instructor_tasks', {}, 'data_researcher')
        ]

        self.fake_jwt = ('wyJUxMiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJjaGFuZ2UtbWUiLCJleHAiOjE3MjU4OTA2NzIsImdyY'
                         'W50X3R5cGUiOiJwYXNzd29yZCIsImlhdCI6MTcyNTg4NzA3MiwiaXNzIjoiaHR0cDovLzEyNy4wLjAuMTo4MDAwL29h'
                         'XNlcl9pZCI6MX0'
                         '.ec8neWp1YAuF40ye4oeK40obaapUvjfNPUQCycrsajwvcu58KcuLc96sf0JKmMMMn7DH9N98hg8W38iwbhKif1kLsCKr'
                         'tStl1u2XGvFkyMov8TtespbHit5LYRZpJwrhC1h50ru2buYj3isWrAElGPIDyAj0FAvSJnvJhWSMDtIwB2gxZI1DqOm'
                         'M6mzT7JbOU4QH2PNZrb2EZ11F6k9I-HrHnLQymr4s0vyjMlcBWllW3y19futNCgsFFRMXI4Z9zIbspsy5bq_Skub'
                         'dBpnl0P9x8vUJCAbFnJABAVPtF7F7nNsROQMKsZtQxaUUwdcYZi5qKL2GcgGfO0eTL4IbJA')

    def assert_all_end_points(self, endpoint, body, role, add_role, use_jwt=True):
        """
        Util method for verifying different end-points.
        """
        if add_role:
            role, _ = CourseAccessRole.objects.get_or_create(
                course_id=self.course.id,
                user=self.other_user,
                role=role,
                org=self.course.id.org
            )

        if use_jwt:
            headers = self.headers
        else:
            headers = {
                'HTTP_AUTHORIZATION': 'JWT ' + self.fake_jwt  # this is fake jwt.
            }

        url = reverse(endpoint, kwargs={'course_id': str(self.course.id)})
        response = self.client.post(
            url,
            data=body,
            **headers
        )
        return response

    def run_endpoint_tests(self, expected_status, add_role, use_jwt):
        """
        Util method for running different end-points.
        """
        for endpoint, body, role in self.endpoints:
            with self.subTest(endpoint=endpoint, role=role, body=body):
                response = self.assert_all_end_points(endpoint, body, role, add_role, use_jwt)
                # JWT authentication works but it has no permissions.
                assert response.status_code == expected_status, f"Failed for endpoint: {endpoint}"

    def test_end_points_with_oauth_without_jwt(self):
        """
        Verify the endpoint using invalid JWT returns 401.
        """
        self.run_endpoint_tests(expected_status=401, add_role=False, use_jwt=False)

    def test_end_points_with_oauth_without_permissions(self):
        """
        Verify the endpoint using JWT authentication. But has no permissions.
        """
        self.run_endpoint_tests(expected_status=403, add_role=False, use_jwt=True)

    def test_end_points_with_oauth_with_permissions(self):
        """
        Verify the endpoint using JWT authentication with permissions.
        """
        self.run_endpoint_tests(expected_status=200, add_role=True, use_jwt=True)
