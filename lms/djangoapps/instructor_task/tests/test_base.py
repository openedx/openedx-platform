"""
Base test classes for LMS instructor-initiated background tasks

"""


import json

# pylint: disable=attribute-defined-outside-init
import os
import shutil
from tempfile import mkdtemp
from unittest.mock import Mock, patch
from uuid import uuid4

import unicodecsv
from celery.states import FAILURE, SUCCESS
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locations import Location
from xblocks_contrib.problem.capa.tests.response_xml_factory import OptionResponseXMLFactory

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.model_data import StudentModule
from lms.djangoapps.courseware.tests.tests import LoginEnrollmentTestCase
from lms.djangoapps.instructor_task.api_helper import encode_problem_and_student_input
from lms.djangoapps.instructor_task.models import PROGRESS, QUEUING, ReportStore
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from lms.djangoapps.instructor_task.views import instructor_task_status
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.core.lib.url_utils import quote_slashes
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

TEST_COURSE_ORG = 'edx'
TEST_COURSE_NAME = 'test_course'
TEST_COURSE_NUMBER = '1.23x'
TEST_COURSE_KEY = CourseKey.from_string(f'course-v1:{TEST_COURSE_ORG}+{TEST_COURSE_NUMBER}+{TEST_COURSE_NAME}')
TEST_CHAPTER_NAME = "Section"
TEST_SECTION_NAME = "Subsection"

TEST_FAILURE_MESSAGE = 'task failed horribly'
TEST_FAILURE_EXCEPTION = 'RandomCauseError'

OPTION_1 = 'Option 1'
OPTION_2 = 'Option 2'


class InstructorTaskTestCase(CacheIsolationTestCase):
    """
    Tests API and view methods that involve the reporting of status for background tasks.
    """
    def setUp(self):
        super().setUp()

        self.student = UserFactory.create(username="student", email="student@edx.org")
        self.instructor = UserFactory.create(username="instructor", email="instructor@edx.org")
        self.problem_url = InstructorTaskTestCase.problem_location("test_urlname")

    @staticmethod
    def problem_location(problem_url_name):
        """
        Create an internal location for a test problem.
        """
        return TEST_COURSE_KEY.make_usage_key('problem', problem_url_name)

    def _create_entry(self, task_state=QUEUING, task_output=None, student=None):
        """Creates a InstructorTask entry for testing."""
        task_id = str(uuid4())
        progress_json = json.dumps(task_output) if task_output is not None else None
        task_input, task_key = encode_problem_and_student_input(self.problem_url, student)

        instructor_task = InstructorTaskFactory.create(course_id=TEST_COURSE_KEY,
                                                       requester=self.instructor,
                                                       task_input=json.dumps(task_input),
                                                       task_key=task_key,
                                                       task_id=task_id,
                                                       task_state=task_state,
                                                       task_output=progress_json)
        return instructor_task

    def _create_failure_entry(self):
        """Creates a InstructorTask entry representing a failed task."""
        # view task entry for task failure
        progress = {'message': TEST_FAILURE_MESSAGE,
                    'exception': TEST_FAILURE_EXCEPTION,
                    }
        return self._create_entry(task_state=FAILURE, task_output=progress)

    def _create_success_entry(self, student=None):
        """Creates a InstructorTask entry representing a successful task."""
        return self._create_progress_entry(student, task_state=SUCCESS)

    def _create_progress_entry(self, student=None, task_state=PROGRESS):
        """Creates a InstructorTask entry representing a task in progress."""
        progress = {'attempted': 3,
                    'succeeded': 2,
                    'total': 5,
                    'action_name': 'rescored',
                    }
        return self._create_entry(task_state=task_state, task_output=progress, student=student)


class InstructorTaskCourseTestCase(LoginEnrollmentTestCase, ModuleStoreTestCase):
    """
    Base test class for InstructorTask-related tests that require
    the setup of a course.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    course = None
    current_user = None

    def initialize_course(self, course_factory_kwargs=None):
        """
        Create a course in the store, with a chapter and section.

        Arguments:
            course_factory_kwargs (dict): kwargs dict to pass to
            CourseFactory.create()
        """
        self.module_store = modulestore()

        # Create the course
        course_args = {
            "org": TEST_COURSE_ORG,
            "number": TEST_COURSE_NUMBER,
            "display_name": TEST_COURSE_NAME
        }
        if course_factory_kwargs is not None:
            course_args.update(course_factory_kwargs)
        self.course = CourseFactory.create(**course_args)
        self.add_course_content()

    def add_course_content(self):
        """
        Add a chapter and a sequential to the current course.
        """
        # Add a chapter to the course
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            display_name=TEST_CHAPTER_NAME,
        )

        # add a sequence to the course to which the problems can be added
        self.problem_section = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'},
            display_name=TEST_SECTION_NAME,
        )

    @staticmethod
    def get_user_email(username):
        """Generate email address based on username"""
        return f'{username}@test.com'

    def login_username(self, username):
        """Login the user, given the `username`."""
        if self.current_user != username:
            self.logout()
            user_email = User.objects.get(username=username).email
            self.login(user_email, self.TEST_PASSWORD)
            self.current_user = username

    def _create_user(self, username, email=None, is_staff=False, mode='honor', enrollment_active=True):
        """Creates a user and enrolls them in the test course."""
        if email is None:
            email = InstructorTaskCourseTestCase.get_user_email(username)
        thisuser = UserFactory.create(username=username, email=email, is_staff=is_staff)
        CourseEnrollmentFactory.create(user=thisuser, course_id=self.course.id, mode=mode, is_active=enrollment_active)
        return thisuser

    def create_instructor(self, username, email=None):
        """Creates an instructor for the test course."""
        return self._create_user(username, email, is_staff=True)

    def create_student(self, username, email=None, mode='honor', enrollment_active=True):
        """Creates a student for the test course."""
        return self._create_user(username, email, is_staff=False, mode=mode, enrollment_active=enrollment_active)

    @staticmethod
    def get_task_status(task_id):
        """Use api method to fetch task status, using mock request."""
        mock_request = Mock()
        mock_request.GET = mock_request.POST = {'task_id': task_id}
        response = instructor_task_status(mock_request)
        status = json.loads(response.content.decode('utf-8'))
        return status

    def create_task_request(self, requester_username):
        """Generate request that can be used for submitting tasks"""
        request = Mock()
        request.user = User.objects.get(username=requester_username)
        request.get_host = Mock(return_value="testhost")
        request.META = {'REMOTE_ADDR': '0:0:0:0', 'SERVER_NAME': 'testhost'}
        request.is_secure = Mock(return_value=False)
        return request


class InstructorTaskModuleTestCase(InstructorTaskCourseTestCase):
    """
    Base test class for InstructorTask-related tests that require
    the setup of a course and problem in order to access StudentModule state.
    """
    @staticmethod
    def problem_location(problem_url_name, course_key=None):
        """
        Create an internal location for a test problem.
        """
        if "i4x:" in problem_url_name:
            return Location.from_string(problem_url_name)
        elif course_key:
            return course_key.make_usage_key('problem', problem_url_name)
        else:
            return TEST_COURSE_KEY.make_usage_key('problem', problem_url_name)

    def _option_problem_factory_args(self, correct_answer=OPTION_1, num_inputs=1, num_responses=2):
        """
        Returns the factory args for the option problem type.
        """
        return {
            'question_text': f'The correct answer is {correct_answer}',
            'options': [OPTION_1, OPTION_2],
            'correct_option': correct_answer,
            'num_responses': num_responses,
            'num_inputs': num_inputs,
        }

    def define_option_problem(self, problem_url_name, parent=None, **kwargs):
        """Create the problem definition so the answer is Option 1"""
        if parent is None:
            parent = self.problem_section
        factory = OptionResponseXMLFactory()
        factory_args = self._option_problem_factory_args()
        problem_xml = factory.build_xml(**factory_args)
        return BlockFactory.create(parent_location=parent.location,
                                   parent=parent,
                                   category="problem",
                                   display_name=problem_url_name,
                                   data=problem_xml,
                                   **kwargs)

    def redefine_option_problem(self, problem_url_name, correct_answer=OPTION_1, num_inputs=1, num_responses=2):
        """Change the problem definition so the answer is Option 2"""
        factory = OptionResponseXMLFactory()
        factory_args = self._option_problem_factory_args(correct_answer, num_inputs, num_responses)
        problem_xml = factory.build_xml(**factory_args)
        location = InstructorTaskTestCase.problem_location(problem_url_name)
        item = self.module_store.get_item(location)
        with self.module_store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, location.course_key):
            item.data = problem_xml
            self.module_store.update_item(item, self.user.id)
            self.module_store.publish(location, self.user.id)

    def get_student_module(self, username, block):
        """Get StudentModule object for test course, given the `username` and the problem's `block`."""
        return StudentModule.objects.get(course_id=self.course.id,
                                         student=User.objects.get(username=username),
                                         module_type=block.location.block_type,
                                         module_state_key=block.location,
                                         )

    def submit_student_answer(self, username, problem_url_name, responses):
        """
        Use ajax interface to submit a student answer.

        Assumes the input list of responses has two values.
        """
        def get_input_id(response_id):
            """Creates input id using information about the test course and the current problem."""
            # Note that this is a capa-specific convention.  The form is a version of the problem's
            # URL, modified so that it can be easily stored in html, prepended with "input-" and
            # appended with a sequence identifier for the particular response the input goes to.
            return 'input_{}_{}'.format(  # noqa: UP032
                problem_url_name,
                response_id
            )

        # make sure that the requested user is logged in, so that the ajax call works
        # on the right problem:
        self.login_username(username)
        # make ajax call:
        modx_url = reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': quote_slashes(
                str(InstructorTaskModuleTestCase.problem_location(problem_url_name, self.course.id))
            ),
            'handler': 'xmodule_handler',
            'suffix': 'problem_check',
        })

        # assign correct identifier to each response.
        resp = self.client.post(modx_url, {
            get_input_id('{}_1').format(index): response for index, response in enumerate(responses, 2)
        })
        return resp


class TestReportMixin:
    """
    Cleans up after tests that place files in the reports directory.
    """

    def setUp(self):

        def clean_up_tmpdir():
            """Remove temporary directory created for instructor task models."""
            if os.path.exists(self.tmp_dir):
                shutil.rmtree(self.tmp_dir)

        super().setUp()

        # Ensure that working with the temp directories in tests is thread safe
        # by creating a unique temporary directory for each testcase.
        self.tmp_dir = mkdtemp()

        mock_grades_download = {'STORAGE_TYPE': 'localfs', 'BUCKET': 'test-grades', 'ROOT_PATH': self.tmp_dir}
        self.grades_patch = patch.dict('django.conf.settings.GRADES_DOWNLOAD', mock_grades_download)
        self.grades_patch.start()
        self.addCleanup(self.grades_patch.stop)

        mock_fin_report = {'STORAGE_TYPE': 'localfs', 'BUCKET': 'test-financial-reports', 'ROOT_PATH': self.tmp_dir}
        self.reports_patch = patch.dict('django.conf.settings.FINANCIAL_REPORTS', mock_fin_report)
        self.reports_patch.start()
        self.addCleanup(self.reports_patch.stop)

        self.addCleanup(clean_up_tmpdir)

    def verify_rows_in_csv(self, expected_rows, file_index=0, verify_order=True, ignore_other_columns=False):
        """
        Verify that the last ReportStore CSV contains the expected content.

        Arguments:
            expected_rows (iterable): An iterable of dictionaries,
                where each dict represents a row of data in the last
                ReportStore CSV.  Each dict maps keys from the CSV
                header to values in that row's corresponding cell.
            file_index (int): Describes which report store file to
                open.  Files are ordered by last modified date, and 0
                corresponds to the most recently modified file.
            verify_order (boolean): When True (default), we verify that
                both the content and order of `expected_rows` matches
                the actual csv rows.  When False, we only verify that
                the content matches.
            ignore_other_columns (boolean): When True, we verify that `expected_rows`
                contain data which is the subset of actual csv rows.
        """
        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        report_csv_filename = report_store.links_for(self.course.id)[file_index][0]
        report_path = report_store.path_to(self.course.id, report_csv_filename)
        with report_store.storage.open(report_path) as csv_file:
            # Expand the dict reader generator so we don't lose it's content
            csv_rows = list(unicodecsv.DictReader(csv_file, encoding='utf-8-sig'))

            if ignore_other_columns:
                csv_rows = [
                    {key: row.get(key) for key in expected_rows[index].keys()} for index, row in enumerate(csv_rows)
                ]

            numeric_csv_rows = [self._extract_and_round_numeric_items(row) for row in csv_rows]
            numeric_expected_rows = [self._extract_and_round_numeric_items(row) for row in expected_rows]

            if verify_order:
                assert csv_rows == expected_rows
                assert numeric_csv_rows == numeric_expected_rows
            else:
                self.assertCountEqual(csv_rows, expected_rows)  # noqa: PT009
                self.assertCountEqual(numeric_csv_rows, numeric_expected_rows)  # noqa: PT009

    @staticmethod
    def _extract_and_round_numeric_items(dictionary):
        """
        csv data may contain numeric values that are converted to strings, and fractional
        numbers can be imprecise (e.g. 1 / 6 is sometimes '0.16666666666666666' and other times
        '0.166666666667'). This function returns a new dictionary that contains only the
        numerically-valued items from it, rounded to four decimal places.
        """
        extracted = {}
        for key in list(dictionary):
            try:
                extracted[key] = round(float(dictionary[key]), 4)
            except ValueError:
                pass
        return extracted

    def get_csv_row_with_headers(self):
        """
        Helper function to return list with the column names from the CSV file (the first row)
        """
        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        report_csv_filename = report_store.links_for(self.course.id)[0][0]
        report_path = report_store.path_to(self.course.id, report_csv_filename)
        with report_store.storage.open(report_path) as csv_file:
            rows = unicodecsv.reader(csv_file, encoding='utf-8-sig')
            return next(rows)
