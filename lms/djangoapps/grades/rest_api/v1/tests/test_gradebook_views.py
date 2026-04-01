"""  # lint-amnesty, pylint: disable=cyclic-import
Tests for the course grading API view
"""


import json
from collections import OrderedDict, namedtuple
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch
from common.djangoapps.student.models.course_enrollment import CourseEnrollment

import ddt
import pytest
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time
from opaque_keys.edx.locator import BlockUsageLocator
from pytz import UTC
from rest_framework import status
from rest_framework.test import APITestCase
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

import openedx.core.djangoapps.content.block_structure.api as bs_api
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseCcxCoachRole,
    CourseDataResearcherRole,
    CourseInstructorRole,
    CourseStaffRole
)
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import StaffFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.api import (
    get_certificate_for_user_id,
    create_or_update_eligible_certificate_for_user
)
from lms.djangoapps.grades.config.waffle import BULK_MANAGEMENT, WRITABLE_GRADEBOOK
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.course_data import CourseData
from lms.djangoapps.grades.course_grade import CourseGrade
from lms.djangoapps.grades.models import (
    BlockRecord,
    BlockRecordList,
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride
)
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v1.views import CourseEnrollmentPagination
from lms.djangoapps.grades.subsection_grade import ReadSubsectionGrade
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory


# pylint: disable=unused-variable
class CourseGradingViewTest(SharedModuleStoreTestCase, APITestCase):
    """
    Test course grading view via a RESTful API
    """
    view_name = 'grades_api:v1:course_gradebook_grading_info'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.course = CourseFactory.create(display_name='test course', run="Testing_course")
        cls.course_key = cls.course.id

        cls.password = 'test'
        cls.student = UserFactory(username='dummy', password=cls.password)
        cls.staff = StaffFactory(course_key=cls.course.id, password=cls.password)

        cls.initialize_course(cls.course)

    @classmethod
    def initialize_course(cls, course):
        """
        Sets up the structure of the test course.
        """
        course.grade_cutoffs = {
            "Pass": 0.5,
        }
        cls.section = BlockFactory.create(
            parent_location=course.location,
            category="chapter",
        )
        cls.subsection1 = BlockFactory.create(
            parent_location=cls.section.location,
            category="sequential",
        )
        unit1 = BlockFactory.create(
            parent_location=cls.subsection1.location,
            category="vertical",
        )
        BlockFactory.create(
            parent_location=unit1.location,
            category="video",
        )
        BlockFactory.create(
            parent_location=unit1.location,
            category="problem",
        )

        cls.subsection2 = BlockFactory.create(
            parent_location=cls.section.location,
            category="sequential",
        )
        unit2 = BlockFactory.create(
            parent_location=cls.subsection2.location,
            category="vertical",
        )
        unit3 = BlockFactory.create(
            parent_location=cls.subsection2.location,
            category="vertical",
        )
        BlockFactory.create(
            parent_location=unit3.location,
            category="video",
        )
        BlockFactory.create(
            parent_location=unit3.location,
            category="video",
        )
        cls.homework = BlockFactory.create(
            parent_location=cls.section.location,
            category="sequential",
            graded=True,
            format='Homework',
        )
        cls.midterm = BlockFactory.create(
            parent_location=cls.section.location,
            category="sequential",
            graded=True,
            format='Midterm Exam',
        )

    def get_url(self, course_id):
        """
        Helper function to create the url
        """
        return reverse(
            self.view_name,
            kwargs={
                'course_id': course_id,
            }
        )

    def _get_expected_data(self):
        return {
            "grade_cutoffs": {
                "Pass": 0.5,
            },
            'assignment_types': {
                'Final Exam': {
                    'drop_count': 0,
                    'min_count': 1,
                    'short_label': 'Final',
                    'type': 'Final Exam',
                    'weight': 0.4
                },
                'Homework': {
                    'drop_count': 2,
                    'min_count': 12,
                    'short_label': 'HW',
                    'type': 'Homework',
                    'weight': 0.15
                },
                'Lab': {
                    'drop_count': 2,
                    'min_count': 12,
                    'short_label': 'Lab',
                    'type': 'Lab',
                    'weight': 0.15
                },
                'Midterm Exam': {
                    'drop_count': 0,
                    'min_count': 1,
                    'short_label': 'Midterm',
                    'type': 'Midterm Exam',
                    'weight': 0.3
                }
            },
            'subsections': [
                {
                    'assignment_type': None,
                    'display_name': self.subsection1.display_name,
                    'graded': False,
                    'module_id': str(self.subsection1.location),
                    'short_label': None
                },
                {
                    'assignment_type': None,
                    'display_name': self.subsection2.display_name,
                    'graded': False,
                    'module_id': str(self.subsection2.location),
                    'short_label': None
                },
                {
                    'assignment_type': 'Homework',
                    'display_name': self.homework.display_name,
                    'graded': True,
                    'module_id': str(self.homework.location),
                    'short_label': 'HW 01',
                },
                {
                    'assignment_type': 'Midterm Exam',
                    'display_name': self.midterm.display_name,
                    'graded': True,
                    'module_id': str(self.midterm.location),
                    'short_label': 'Midterm 01',
                },
            ],
            'grades_frozen': False,
            'can_see_bulk_management': False,
        }

    def test_student_fails(self):
        self.client.login(username=self.student.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_succeeds(self):
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))
        assert resp.status_code == status.HTTP_200_OK
        expected_data = self._get_expected_data()
        assert expected_data == resp.data

    def test_staff_succeeds_graded_only(self):
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key), {'graded_only': True})
        assert resp.status_code == status.HTTP_200_OK
        expected_data = self._get_expected_data()
        expected_data['subsections'] = [sub for sub in expected_data['subsections'] if sub['graded']]
        assert expected_data == resp.data

    def test_course_grade_frozen(self):
        with patch('lms.djangoapps.grades.rest_api.v1.gradebook_views.are_grades_frozen') as mock_frozen_grades:
            mock_frozen_grades.return_value = True
            self.client.login(username=self.staff.username, password=self.password)
            resp = self.client.get(self.get_url(self.course_key))
            assert resp.status_code == status.HTTP_200_OK
            expected_data = self._get_expected_data()
            expected_data['grades_frozen'] = True
            assert expected_data == resp.data

    @patch('lms.djangoapps.grades.rest_api.v1.gradebook_views.get_course_enrollment_details')
    def test_can_see_bulk_management_non_masters(self, mock_course_enrollment_details):
        # Given a course without a master's track
        mock_course_enrollment_details.return_value = {'course_modes': [{'slug': 'not-masters'}]}

        # When getting course grading view
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))

        # Course staff should not be shown bulk management controls
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['can_see_bulk_management'] is False

    @patch('lms.djangoapps.grades.rest_api.v1.gradebook_views.get_course_enrollment_details')
    def test_can_see_bulk_management_masters(self, mock_course_enrollment_details):
        # Given a course with a master's track
        mock_course_enrollment_details.return_value = {'course_modes': [{'slug': 'not-masters'}, {'slug': 'masters'}]}

        # When getting course grading view
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))

        # Course staff should be shown bulk management controls (default on for master's track courses)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['can_see_bulk_management'] is True

    @override_waffle_flag(BULK_MANAGEMENT, active=True)
    def test_can_see_bulk_management_force_enabled(self):
        # Given a course without (or with) a master's track where bulk management is enabled with the config flag
        # When getting course grading view
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))

        # # Course staff should be able to see bulk management
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['can_see_bulk_management'] is True


class GradebookViewTestBase(GradeViewTestMixin, APITestCase):
    """
    Base class for the gradebook GET and POST view tests.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.namespaced_url = 'grades_api:v1:course_gradebook'
        cls.waffle_flag = WRITABLE_GRADEBOOK

        cls.course = CourseFactory.create(display_name='test-course', run='run-1')
        cls.course_key = cls.course.id
        cls.course_overview = CourseOverviewFactory.create(id=cls.course.id)

        cls.chapter_1 = BlockFactory.create(
            category='chapter',
            parent_location=cls.course.location,
            display_name="Chapter 1",
        )
        cls.chapter_2 = BlockFactory.create(
            category='chapter',
            parent_location=cls.course.location,
            display_name="Chapter 2",
        )
        cls.subsections = {
            cls.chapter_1.location: [
                BlockFactory.create(
                    category='sequential',
                    parent_location=cls.chapter_1.location,
                    due=datetime(2017, 12, 18, 11, 30, 00),
                    display_name='HW 1',
                    format='Homework',
                    graded=True,
                ),
                BlockFactory.create(
                    category='sequential',
                    parent_location=cls.chapter_1.location,
                    due=datetime(2017, 12, 18, 11, 30, 00),
                    display_name='Lab 1',
                    format='Lab',
                    graded=True,
                ),
            ],
            cls.chapter_2.location: [
                BlockFactory.create(
                    category='sequential',
                    parent_location=cls.chapter_2.location,
                    due=datetime(2017, 12, 18, 11, 30, 00),
                    display_name='HW 2',
                    format='Homework',
                    graded=True,
                ),
                BlockFactory.create(
                    category='sequential',
                    parent_location=cls.chapter_2.location,
                    due=datetime(2017, 12, 18, 11, 30, 00),
                    display_name='Lab 2',
                    format='Lab',
                    graded=True,
                ),
            ],
        }

        # Data about graded subsections visible to staff only
        # should not be exposed via the gradebook API
        cls.hidden_subsection = BlockFactory.create(
            parent_location=cls.chapter_1.location,
            category='sequential',
            graded=True,
            visible_to_staff_only=True,
            display_name='Hidden Section',
        )

        cls.course_data = CourseData(None, course=cls.course)
        # we have to force the collection of course data from the block_structure API
        # so that CourseGrade.course_data objects can later have a non-null effective_structure
        _ = cls.course_data.collected_structure

    def get_url(self, course_key=None):
        """
        Helper function to create the course gradebook API url.
        """
        return reverse(
            self.namespaced_url,
            kwargs={
                'course_id': course_key or self.course_key,
            }
        )

    def login_staff(self):
        """
        Helper function to login the global staff user, who has permissions to read from the
        Gradebook API.
        """
        self.client.login(username=self.global_staff.username, password=self.password)
        return self.global_staff

    def login_course_staff(self):
        """
        Helper function to login the course staff user, who has permissions to read from the
        Gradebook API.
        """
        course_staff = StaffFactory.create(course_key=self.course_key)
        self._create_user_enrollments(course_staff)
        self.client.login(username=course_staff.username, password=self.password)
        return course_staff

    def login_course_admin(self):
        """
        Helper function to login the course admin user, who has permissions to read from the
        Gradebook API.
        """
        course_admin = InstructorFactory.create(course_key=self.course_key)
        self._create_user_enrollments(course_admin)
        self.client.login(username=course_admin.username, password=self.password)
        return course_admin


@ddt.ddt
class GradebookViewTest(GradebookViewTestBase):
    """
    Tests for the gradebook view.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock_subsection_grades = {
            cls.subsections[cls.chapter_1.location][0].location: cls.mock_subsection_grade(
                cls.subsections[cls.chapter_1.location][0],
                earned_all=1.0,
                possible_all=2.0,
                earned_graded=1.0,
                possible_graded=2.0,
            ),
            cls.subsections[cls.chapter_1.location][1].location: cls.mock_subsection_grade(
                cls.subsections[cls.chapter_1.location][1],
                earned_all=1.0,
                possible_all=2.0,
                earned_graded=1.0,
                possible_graded=2.0,
            ),
            cls.subsections[cls.chapter_2.location][0].location: cls.mock_subsection_grade(
                cls.subsections[cls.chapter_2.location][0],
                earned_all=1.0,
                possible_all=2.0,
                earned_graded=1.0,
                possible_graded=2.0,
            ),
            cls.subsections[cls.chapter_2.location][1].location: cls.mock_subsection_grade(
                cls.subsections[cls.chapter_2.location][1],
                earned_all=1.0,
                possible_all=2.0,
                earned_graded=1.0,
                possible_graded=2.0,
            ),
        }

    def get_url(self, course_key=None, username=None, user_contains=None):  # pylint: disable=arguments-differ
        """
        Helper function to create the course gradebook API read url.
        """
        base_url = super().get_url(course_key)
        if username:
            return f"{base_url}?username={username}"
        if user_contains:
            return f"{base_url}?user_contains={user_contains}"
        return base_url

    @staticmethod
    def mock_subsection_grade(subsection, **kwargs):
        """
        Helper function to mock a subsection grade.
        """
        model = MagicMock(**kwargs)
        if 'override' not in kwargs:
            del model.override
        factory = MagicMock()
        return ReadSubsectionGrade(subsection, model, factory)

    def mock_course_grade(self, user, **kwargs):
        """
        Helper function to return a mock CourseGrade object.
        """
        course_grade = CourseGrade(user=user, course_data=self.course_data, **kwargs)
        course_grade.subsection_grade = lambda key: self.mock_subsection_grades[key]
        return course_grade

    def expected_subsection_grades(self):
        """
        Helper function to generate expected subsection detail results.
        """
        return [
            OrderedDict([
                ('attempted', True),
                ('category', 'Homework'),
                ('label', 'HW 01'),
                ('module_id', str(self.subsections[self.chapter_1.location][0].location)),
                ('percent', 0.5),
                ('score_earned', 1.0),
                ('score_possible', 2.0),
                ('subsection_name', 'HW 1')
            ]),
            OrderedDict([
                ('attempted', True),
                ('category', 'Lab'),
                ('label', 'Lab 01'),
                ('module_id', str(self.subsections[self.chapter_1.location][1].location)),
                ('percent', 0.5),
                ('score_earned', 1.0),
                ('score_possible', 2.0),
                ('subsection_name', 'Lab 1')
            ]),
            OrderedDict([
                ('attempted', True),
                ('category', 'Homework'),
                ('label', 'HW 02'),
                ('module_id', str(self.subsections[self.chapter_2.location][0].location)),
                ('percent', 0.5),
                ('score_earned', 1.0),
                ('score_possible', 2.0),
                ('subsection_name', 'HW 2')
            ]),
            OrderedDict([
                ('attempted', True),
                ('category', 'Lab'),
                ('label', 'Lab 02'),
                ('module_id', str(self.subsections[self.chapter_2.location][1].location)),
                ('percent', 0.5),
                ('score_earned', 1.0),
                ('score_possible', 2.0),
                ('subsection_name', 'Lab 2')
            ]),
        ]

    def _assert_data_all_users(self, response):
        """
        Helper method to assert that self.student, self.other_student, and
        self.program_student have the expected gradebook data.
        """
        expected_results = [
            OrderedDict([
                ('user_id', self.student.id),
                ('username', self.student.username),
                ('email', ''),
                ('percent', 0.85),
                ('section_breakdown', self.expected_subsection_grades()),
            ]),
            OrderedDict([
                ('user_id', self.other_student.id),
                ('username', self.other_student.username),
                ('email', ''),
                ('percent', 0.45),
                ('section_breakdown', self.expected_subsection_grades()),
            ]),
            OrderedDict([
                ('user_id', self.program_student.id),
                ('username', self.program_student.username),
                ('email', ''),
                ('external_user_key', 'program_user_key_0'),
                ('percent', 0.75),
                ('section_breakdown', self.expected_subsection_grades()),
            ])
        ]

        assert status.HTTP_200_OK == response.status_code
        actual_data = dict(response.data)
        assert actual_data['next'] is None
        assert actual_data['previous'] is None
        assert expected_results == actual_data['results']
        # assert that the hidden subsection data is not represented in the response
        for actual_user_data in actual_data['results']:
            actual_subsection_display_names = [
                item['subsection_name'] for item in actual_user_data['section_breakdown']
            ]
            assert self.hidden_subsection.display_name not in actual_subsection_display_names

    def _assert_empty_response(self, response):
        """
        Helper method for assertions about OK, empty responses.
        """
        assert status.HTTP_200_OK == response.status_code
        actual_data = dict(response.data)
        assert actual_data['next'] is None
        assert actual_data['previous'] is None
        assert [] == actual_data['results']

    def test_feature_not_enabled(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        with override_waffle_flag(self.waffle_flag, active=False):
            resp = self.client.get(
                self.get_url(course_key=self.empty_course.id)
            )
            assert status.HTTP_403_FORBIDDEN == resp.status_code

    def test_anonymous(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            resp = self.client.get(self.get_url())
            assert status.HTTP_401_UNAUTHORIZED == resp.status_code

    def test_student(self):
        self.client.login(username=self.student.username, password=self.password)
        with override_waffle_flag(self.waffle_flag, active=True):
            resp = self.client.get(self.get_url())
            assert status.HTTP_403_FORBIDDEN == resp.status_code

    def test_course_does_not_exist(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            self.login_staff()
            resp = self.client.get(
                self.get_url(course_key='course-v1:MITx+8.MechCX+2014_T1')
            )
            assert status.HTTP_404_NOT_FOUND == resp.status_code

    def test_user_does_not_exist(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            self.login_staff()
            resp = self.client.get(
                self.get_url(course_key=self.course.id, username='not-a-real-user')
            )
            assert status.HTTP_404_NOT_FOUND == resp.status_code

    def test_user_not_enrolled(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            self.login_staff()
            resp = self.client.get(
                self.get_url(course_key=self.empty_course.id, username=self.student.username)
            )
            assert status.HTTP_404_NOT_FOUND == resp.status_code

    def test_course_no_enrollments(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            self.login_staff()
            resp = self.client.get(
                self.get_url(course_key=self.empty_course.id)
            )
            self._assert_empty_response(resp)

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_gradebook_data_for_course(self, login_method):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.85),
                self.mock_course_grade(self.other_student, passed=False, percent=0.45),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75)
            ]

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id)
                )
                self._assert_data_all_users(resp)

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_gradebook_data_for_single_learner(self, login_method):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(self.student, passed=True, percent=0.85)

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, username=self.student.username)
                )
                expected_results = OrderedDict([
                    ('user_id', self.student.id),
                    ('username', self.student.username),
                    ('email', ''),
                    ('percent', 0.85),
                    ('section_breakdown', self.expected_subsection_grades()),
                ])

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data
                # assert that the hidden subsection data is not represented in the response
                actual_subsection_display_names = [
                    item['subsection_name'] for item in actual_data['section_breakdown']
                ]
                assert self.hidden_subsection.display_name not in actual_subsection_display_names

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_gradebook_data_for_single_learner_override(self, login_method):
        """
        Tests that, when a subsection grade that was created from an override, and thus
        does not have a truthy `first_attempted` attribute, is the only grade for a
        user's subsection, we still get data including a non-zero possible score.
        """
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            course_grade = self.mock_course_grade(self.student, passed=True, percent=0.85)

            mock_override = MagicMock(
                earned_all_override=1.0,
                possible_all_override=2.0,
                earned_graded_override=1.0,
                possible_graded_override=2.0,
            )
            mock_subsection_grades = {
                self.subsections[self.chapter_1.location][0].location: self.mock_subsection_grade(
                    self.subsections[self.chapter_1.location][0],
                    earned_all=1.0,
                    possible_all=2.0,
                    earned_graded=2.0,
                    possible_graded=2.0,
                    first_attempted=None,
                    override=mock_override,
                ),
                self.subsections[self.chapter_1.location][1].location: self.mock_subsection_grade(
                    self.subsections[self.chapter_1.location][1],
                    earned_all=1.0,
                    possible_all=2.0,
                    earned_graded=2.0,
                    possible_graded=2.0,
                    first_attempted=None,
                    override=mock_override,
                ),
                self.subsections[self.chapter_2.location][0].location: self.mock_subsection_grade(
                    self.subsections[self.chapter_2.location][0],
                    earned_all=1.0,
                    possible_all=2.0,
                    earned_graded=2.0,
                    possible_graded=2.0,
                    first_attempted=None,
                    override=mock_override,
                ),
                self.subsections[self.chapter_2.location][1].location: self.mock_subsection_grade(
                    self.subsections[self.chapter_2.location][1],
                    earned_all=1.0,
                    possible_all=2.0,
                    earned_graded=2.0,
                    possible_graded=2.0,
                    first_attempted=None,
                    override=mock_override,
                ),
            }
            course_grade.subsection_grade = lambda key: mock_subsection_grades[key]
            mock_grade.return_value = course_grade

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, username=self.student.username)
                )
                expected_results = OrderedDict([
                    ('user_id', self.student.id),
                    ('username', self.student.username),
                    ('email', ''),
                    ('percent', 0.85),
                    ('section_breakdown', self.expected_subsection_grades()),
                ])

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_gradebook_data_filter_username_contains(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(
                self.program_student, passed=True, percent=0.75
            )

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()

                # check username contains "program"
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, user_contains='program')
                )
                expected_results = [
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_masters_student.id),
                        ('username', self.program_masters_student.username),
                        ('full_name', self.program_masters_student.profile.name),
                        ('email', self.program_masters_student.email),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert actual_data['next'] is None
                assert actual_data['previous'] is None
                assert expected_results == actual_data['results']

                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 2

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_gradebook_data_filter_masters_track_username_contains(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(
                self.program_masters_student, passed=True, percent=0.75
            )

            # need to create a masters track enrollment, which should return email
            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()

                # check username contains "program"
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, user_contains='program')
                )
                expected_results = [
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_masters_student.id),
                        ('username', self.program_masters_student.username),
                        ('full_name', self.program_masters_student.profile.name),
                        ('email', self.program_masters_student.email),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert actual_data['next'] is None
                assert actual_data['previous'] is None
                assert expected_results == actual_data['results']

                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 2

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_gradebook_data_filter_email_contains(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(
                self.other_student, passed=True, percent=0.85
            )

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()

                # check email contains "like"
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, user_contains='like')
                )
                expected_results = [
                    OrderedDict([
                        ('user_id', self.other_student.id),
                        ('username', self.other_student.username),
                        ('email', ''),
                        ('percent', 0.85),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert actual_data['next'] is None
                assert actual_data['previous'] is None
                assert expected_results == actual_data['results']

                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 1

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_gradebook_data_filter_external_user_key_contains(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(
                self.program_student, passed=True, percent=0.75
            )

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()

                # check external user key contains "key"
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, user_contains='key')
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_masters_student.id),
                        ('username', self.program_masters_student.username),
                        ('full_name', self.program_masters_student.profile.name),
                        ('email', self.program_masters_student.email),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert actual_data['next'] is None
                assert actual_data['previous'] is None
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 2

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_gradebook_data_filter_user_contains_no_match(self, login_method):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(
                self.other_student, passed=True, percent=0.85
            )

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id, user_contains='fooooooooooooooooo')
                )
                self._assert_empty_response(resp)

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_filter_cohort_id_and_enrollment_mode(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(self.student, passed=True, percent=0.85)

            cohort = CohortFactory(course_id=self.course.id, name="TestCohort", users=[self.student])
            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                # both of our test users are in the audit track, so this is functionally equivalent
                # to just `?cohort_id=cohort.id`.
                query = f'?cohort_id={cohort.id}&enrollment_mode={CourseMode.AUDIT}'
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + query
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.student.id),
                        ('username', self.student.username),
                        ('email', ''),
                        ('percent', 0.85),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert actual_data['next'] is None
                assert actual_data['previous'] is None
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 1

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_filter_cohort_id_does_not_exist(self, login_method):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.return_value = self.mock_course_grade(self.student, passed=True, percent=0.85)

            empty_cohort = CohortFactory(course_id=self.course.id, name="TestCohort", users=[])
            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + f'?cohort_id={empty_cohort.id}'
                )
                self._assert_empty_response(resp)

    @ddt.data(
        ['login_staff', 5, 3],
        ['login_course_admin', 6, 4],
        ['login_course_staff', 6, 4],
    )
    @ddt.unpack
    def test_filter_enrollment_mode(self, login_method, num_enrollments, num_filtered_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.85),
                self.mock_course_grade(self.other_student, passed=False, percent=0.45),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            # Enroll a verified student, for whom data should not be returned.
            verified_student = UserFactory()
            _ = CourseEnrollmentFactory(
                course_id=self.course.id,
                user=verified_student,
                created=datetime(2013, 1, 1, tzinfo=UTC),
                mode=CourseMode.VERIFIED,
            )
            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + f'?enrollment_mode={CourseMode.AUDIT}'
                )

                self._assert_data_all_users(resp)
                actual_data = dict(resp.data)

                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == num_filtered_enrollments

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_filter_enrollment_mode_no_students(self, login_method):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.85),
                self.mock_course_grade(self.other_student, passed=False, percent=0.45),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + f'?enrollment_mode={CourseMode.VERIFIED}'
                )
                self._assert_empty_response(resp)

    @ddt.data(None, 2, 3, 10, 60, 80)
    def test_page_size_parameter(self, page_size):
        user_size = 60
        with patch(
            'lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read'
        ) as mock_grade:
            users = UserFactory.create_batch(user_size)
            mocked_course_grades = []
            for user in users:
                self._create_user_enrollments(user)
                mocked_course_grades.append(self.mock_course_grade(user, passed=True, percent=0.85))

            mock_grade.side_effect = mocked_course_grades

            with override_waffle_flag(self.waffle_flag, active=True):
                self.login_staff()
                query = ''
                if page_size:
                    query = f'?page_size={page_size}'
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + query
                )
                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                expected_page_size = page_size or CourseEnrollmentPagination.page_size
                if expected_page_size > user_size:  # lint-amnesty, pylint: disable=consider-using-min-builtin
                    expected_page_size = user_size
                assert len(actual_data['results']) == expected_page_size

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_filter_course_grade_min(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            # even though we're creating actual PersistentCourseGrades below, we still need
            # mocked subsection grades
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.85),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            PersistentCourseGrade(
                user_id=self.student.id,
                course_id=self.course_key,
                percent_grade=0.85
            ).save()
            PersistentCourseGrade(
                user_id=self.other_student.id,
                course_id=self.course_key,
                percent_grade=0.45
            ).save()
            PersistentCourseGrade(
                user_id=self.program_student.id,
                course_id=self.course_key,
                percent_grade=0.75
            ).save()

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + '?course_grade_min=50'
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.student.id),
                        ('username', self.student.username),
                        ('email', ''),
                        ('percent', 0.85),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ])
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 2

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_filter_course_grade_min_and_max(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            # even though we're creating actual PersistentCourseGrades below, we still need
            # mocked subsection grades
            mock_grade.side_effect = [
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            PersistentCourseGrade(
                user_id=self.student.id,
                course_id=self.course_key,
                percent_grade=0.85
            ).save()
            PersistentCourseGrade(
                user_id=self.other_student.id,
                course_id=self.course_key,
                percent_grade=0.45
            ).save()
            PersistentCourseGrade(
                user_id=self.program_student.id,
                course_id=self.course_key,
                percent_grade=0.75
            ).save()

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + '?course_grade_min=50&course_grade_max=80'
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == 1

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_filter_course_grade_absent_with_min(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            # even though we're creating actual PersistentCourseGrades below, we still need
            # mocked subsection grades
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.0),
                self.mock_course_grade(self.other_student, passed=False, percent=0.45),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            PersistentCourseGrade(
                user_id=self.other_student.id,
                course_id=self.course_key,
                percent_grade=0.45
            ).save()
            PersistentCourseGrade(
                user_id=self.program_student.id,
                course_id=self.course_key,
                percent_grade=0.75
            ).save()

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + '?course_grade_min=0'
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.student.id),
                        ('username', self.student.username),
                        ('email', ''),
                        ('percent', 0.0),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.other_student.id),
                        ('username', self.other_student.username),
                        ('email', ''),
                        ('percent', 0.45),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ])
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == num_enrollments

    @ddt.data(
        ['login_staff', 4],
        ['login_course_admin', 5],
        ['login_course_staff', 5]
    )
    @ddt.unpack
    def test_filter_course_grade_absent_without_min(self, login_method, num_enrollments):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
            # even though we're creating actual PersistentCourseGrades below, we still need
            # mocked subsection grades
            mock_grade.side_effect = [
                self.mock_course_grade(self.student, passed=True, percent=0.0),
                self.mock_course_grade(self.other_student, passed=False, percent=0.45),
                self.mock_course_grade(self.program_student, passed=True, percent=0.75),
            ]

            PersistentCourseGrade(
                user_id=self.other_student.id,
                course_id=self.course_key,
                percent_grade=0.45
            ).save()
            PersistentCourseGrade(
                user_id=self.program_student.id,
                course_id=self.course_key,
                percent_grade=0.75
            ).save()

            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                resp = self.client.get(
                    self.get_url(course_key=self.course.id) + '?course_grade_max=80'
                )

                expected_results = [
                    OrderedDict([
                        ('user_id', self.student.id),
                        ('username', self.student.username),
                        ('email', ''),
                        ('percent', 0.0),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.other_student.id),
                        ('username', self.other_student.username),
                        ('email', ''),
                        ('percent', 0.45),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ]),
                    OrderedDict([
                        ('user_id', self.program_student.id),
                        ('username', self.program_student.username),
                        ('email', ''),
                        ('external_user_key', 'program_user_key_0'),
                        ('percent', 0.75),
                        ('section_breakdown', self.expected_subsection_grades()),
                    ])
                ]

                assert status.HTTP_200_OK == resp.status_code
                actual_data = dict(resp.data)
                assert expected_results == actual_data['results']
                assert actual_data['total_users_count'] == num_enrollments
                assert actual_data['filtered_users_count'] == num_enrollments

    @contextmanager
    def _mock_all_course_grade_reads(self, percent=0.9):
        """
        A context manager for mocking CourseGradeFactory.read and returning the same grade for all learners.
        """
        # pylint: disable=unused-argument
        def fake_course_grade_read(*args, **kwargs):
            return self.mock_course_grade(kwargs['user'], passed=True, percent=percent)

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_read:
            mock_read.side_effect = fake_course_grade_read
            yield

    def _assert_usernames(self, response, expected_usernames):
        """ Helper method to assert that the expected users were returned from the endpoint """
        assert status.HTTP_200_OK == response.status_code
        response_data = dict(response.data)
        actual_usernames = [row['username'] for row in response_data['results']]
        assert set(actual_usernames) == set(expected_usernames)

    def test_users_with_course_roles(self):
        """ Test that a staff member erolled in the course will be included in grade results. """
        # This function creates and enrolls a course staff (not global staff) user
        staff_user = self.login_course_staff()
        with override_waffle_flag(self.waffle_flag, active=True):
            with self._mock_all_course_grade_reads():
                response = self.client.get(self.get_url(course_key=self.course.id))
        course_students = [
            self.student.username,
            self.other_student.username,
            self.program_student.username,
            self.program_masters_student.username
        ]
        self._assert_usernames(
            response,
            course_students + [staff_user.username]
        )

    @ddt.data(
        None,
        [],
        ['all'],
        [CourseInstructorRole.ROLE, CourseBetaTesterRole.ROLE, CourseCcxCoachRole.ROLE],
        [CourseInstructorRole.ROLE, 'all'],
        [CourseBetaTesterRole.ROLE, 'nonexistant-role'],
        [
            CourseInstructorRole.ROLE,
            CourseStaffRole.ROLE,
            CourseBetaTesterRole.ROLE,
            CourseCcxCoachRole.ROLE,
            CourseDataResearcherRole.ROLE
        ],
    )
    def test_filter_course_roles(self, excluded_course_roles):
        """ Test that excluded_course_roles=all filters out any user with a course role """
        # Create test users, enroll them in the course, and give them roles.
        role_user_usernames = {}
        course_roles_to_create = [
            CourseInstructorRole,
            CourseStaffRole,
            CourseBetaTesterRole,
            CourseCcxCoachRole,
            CourseDataResearcherRole,
        ]
        for role in course_roles_to_create:
            user = UserFactory.create(username="test_filter_course_roles__" + role.ROLE)
            role(self.course.id).add_users(user)
            self._create_user_enrollments(user)
            role_user_usernames[role.ROLE] = user.username

        # This will create global staff and not enroll them in the course
        self.login_staff()
        with self._mock_all_course_grade_reads():
            with override_waffle_flag(self.waffle_flag, active=True):
                response = self.client.get(
                    self.get_url(course_key=self.course.id),
                    {'excluded_course_roles': excluded_course_roles} if excluded_course_roles is not None else {}
                )

        expected_usernames = [
            self.student.username,
            self.other_student.username,
            self.program_student.username,
            self.program_masters_student.username,
        ]
        if not excluded_course_roles:
            # Don't filter out any course roles
            expected_usernames += list(role_user_usernames.values())
        elif 'all' in excluded_course_roles:
            # Filter out every course role
            pass
        else:
            # Filter out some number of course roles
            for role, username in role_user_usernames.items():
                if role not in excluded_course_roles:
                    expected_usernames.append(username)

        self._assert_usernames(response, expected_usernames)

    @ddt.data(False, True)
    def test_exclude_course_roles_for_another_course(self, other_student_role_in_self_course):
        """
        Test for filtering errors when users have roles in other courses.
        """
        # Conditionally make other_student a beta tester (arbitrary role) in self.course
        if other_student_role_in_self_course:
            CourseBetaTesterRole(self.course.id).add_users(self.other_student)

        # Create another course, enroll other_student, and make other_student course staff in other course
        another_course = CourseFactory.create(display_name='another-course', run='run-1')
        CourseEnrollmentFactory(
            course_id=another_course.id,
            user=self.other_student,
        )
        CourseStaffRole(another_course.id).add_users(self.other_student)

        # Query the gradebook view for self.course, excluding staff.
        # other_student is staff in another-course, not self.course, so
        # they should still be included.
        self.login_staff()
        with self._mock_all_course_grade_reads():
            with override_waffle_flag(self.waffle_flag, active=True):
                response = self.client.get(
                    self.get_url(course_key=self.course.id),
                    {'excluded_course_roles': CourseStaffRole.ROLE}
                )
        self._assert_usernames(
            response,
            [
                self.student.username,
                self.other_student.username,
                self.program_student.username,
                self.program_masters_student.username,
            ]
        )

    def test_full_name__full_course(self):
        """ Test that masters students have full_name and that no one else in the course does. """
        self.login_course_staff()
        with override_waffle_flag(self.waffle_flag, active=True):
            with self._mock_all_course_grade_reads():
                response = self.client.get(self.get_url(course_key=self.course.id))

        assert status.HTTP_200_OK == response.status_code
        response_data = dict(response.data)
        assert response_data['next'] is None

        usernames = set()
        masters_students = set()
        for row in response_data['results']:
            username = row['username']
            usernames.add(username)
            enrollment = CourseEnrollment.objects.get(user__username=username, course_id=self.course.id)
            if enrollment.mode == CourseMode.MASTERS:
                masters_students.add(username)
                assert 'full_name' in row
                assert row['full_name'] == enrollment.user.profile.name
            else:
                assert 'full_name' not in row
        assert self.program_masters_student.username in masters_students
        assert self.program_masters_student.username in usernames

    def test_full_name__single_student(self):
        """ Test that a request for a masters student includes a full name """
        self.login_course_staff()
        with override_waffle_flag(self.waffle_flag, active=True):
            with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade:
                mock_grade.return_value = self.mock_course_grade(self.student, passed=True, percent=0.85)
                response = self.client.get(self.get_url(
                    course_key=self.course.id,
                    username=self.program_masters_student.username
                ))

        assert status.HTTP_200_OK == response.status_code
        response_data = dict(response.data)
        assert response_data['full_name'] == self.program_masters_student.profile.name


@ddt.ddt
class GradebookBulkUpdateViewTest(GradebookViewTestBase):
    """
    Tests for the gradebook bulk-update view.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.namespaced_url = 'grades_api:v1:course_gradebook_bulk_update'

    def test_feature_not_enabled(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        with override_waffle_flag(self.waffle_flag, active=False):
            resp = self.client.post(
                self.get_url(course_key=self.empty_course.id)
            )
            assert status.HTTP_403_FORBIDDEN == resp.status_code

    def test_anonymous(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            resp = self.client.post(self.get_url())
            assert status.HTTP_401_UNAUTHORIZED == resp.status_code

    def test_student(self):
        self.client.login(username=self.student.username, password=self.password)
        with override_waffle_flag(self.waffle_flag, active=True):
            resp = self.client.post(self.get_url())
            assert status.HTTP_403_FORBIDDEN == resp.status_code

    def test_course_does_not_exist(self):
        with override_waffle_flag(self.waffle_flag, active=True):
            self.login_staff()
            resp = self.client.post(
                self.get_url(course_key='course-v1:MITx+8.MechCX+2014_T1')
            )
            assert status.HTTP_404_NOT_FOUND == resp.status_code

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_grades_frozen(self, login_method):
        """
        Should receive a 403 when grades have been frozen for a course.
        """
        with patch('lms.djangoapps.grades.rest_api.v1.gradebook_views.are_grades_frozen', return_value=True):
            with override_waffle_flag(self.waffle_flag, active=True):
                getattr(self, login_method)()
                post_data = [
                    {
                        'user_id': self.student.id,
                        'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                        'grade': {},  # doesn't matter what we put here.
                    }
                ]

                resp = self.client.post(
                    self.get_url(),
                    data=json.dumps(post_data),
                    content_type='application/json',
                )
                assert status.HTTP_403_FORBIDDEN == resp.status_code

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_user_not_enrolled(self, login_method):
        with override_waffle_flag(self.waffle_flag, active=True):
            getattr(self, login_method)()
            unenrolled_student = UserFactory()
            post_data = [
                {
                    'user_id': unenrolled_student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'grade': {},  # doesn't matter what we put here.
                }
            ]

            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )

            expected_data = [
                {
                    'user_id': unenrolled_student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'success': False,
                    'reason': 'CourseEnrollment matching query does not exist.',
                },
            ]
            assert status.HTTP_422_UNPROCESSABLE_ENTITY == resp.status_code
            assert expected_data == resp.data

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_user_does_not_exist(self, login_method):
        with override_waffle_flag(self.waffle_flag, active=True):
            getattr(self, login_method)()
            post_data = [
                {
                    'user_id': -123,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'grade': {},  # doesn't matter what we put here.
                }
            ]

            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )

            expected_data = [
                {
                    'user_id': -123,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'success': False,
                    'reason': 'User matching query does not exist.',
                },
            ]
            assert status.HTTP_422_UNPROCESSABLE_ENTITY == resp.status_code
            assert expected_data == resp.data

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_invalid_usage_key(self, login_method):
        with override_waffle_flag(self.waffle_flag, active=True):
            getattr(self, login_method)()
            post_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': 'not-a-valid-usage-key',
                    'grade': {},  # doesn't matter what we put here.
                }
            ]

            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )

            expected_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': 'not-a-valid-usage-key',
                    'success': False,
                    'reason': "<class 'opaque_keys.edx.locator.BlockUsageLocator'>: not-a-valid-usage-key",
                },
            ]
            assert status.HTTP_422_UNPROCESSABLE_ENTITY == resp.status_code
            assert expected_data == resp.data

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_subsection_does_not_exist(self, login_method):
        """
        When trying to override a grade for a valid usage key that does not exist in the requested course,
        we should get an error reason specifying that the key does not exist in the course.
        """
        with override_waffle_flag(self.waffle_flag, active=True):
            getattr(self, login_method)()
            usage_id = 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@workflow'
            post_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': usage_id,
                    'grade': {},  # doesn't matter what we put here.
                }
            ]

            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )

            expected_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': usage_id,
                    'success': False,
                    'reason': f'usage_key {usage_id} does not exist in this course.',
                },
            ]
            assert status.HTTP_422_UNPROCESSABLE_ENTITY == resp.status_code
            assert expected_data == resp.data

    @ddt.data('login_staff', 'login_course_staff', 'login_course_admin')
    def test_override_is_created(self, login_method):
        """
        Test that when we make multiple requests to update grades for the same user/subsection,
        the score from the most recent request is recorded.
        """
        with override_waffle_flag(self.waffle_flag, active=True):
            request_user = getattr(self, login_method)()
            post_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'grade': {
                        'earned_all_override': 3,
                        'possible_all_override': 3,
                        'earned_graded_override': 2,
                        'possible_graded_override': 2,
                    },
                },
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][1].location),
                    'grade': {
                        'earned_all_override': 1,
                        'possible_all_override': 4,
                        'earned_graded_override': 1,
                        'possible_graded_override': 4,
                    },
                }
            ]

            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )

            expected_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'success': True,
                    'reason': None,
                },
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][1].location),
                    'success': True,
                    'reason': None,
                },
            ]
            assert status.HTTP_202_ACCEPTED == resp.status_code
            assert expected_data == resp.data

            second_post_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][1].location),
                    'grade': {
                        'earned_all_override': 3,
                        'possible_all_override': 4,
                        'earned_graded_override': 3,
                        'possible_graded_override': 4,
                    },
                },
            ]

            self.client.post(
                self.get_url(),
                data=json.dumps(second_post_data),
                content_type='application/json',
            )

            GradeFields = namedtuple('GradeFields', ['earned_all', 'possible_all', 'earned_graded', 'possible_graded'])

            # We should now have PersistentSubsectionGradeOverride records corresponding to
            # our bulk-update request, and PersistentSubsectionGrade records with grade values
            # equal to the aggregate of their problem scores (in this case, zeros, since we
            # didn't mock out CourseGradeFactory.read() to return a non-zero score for anything).
            for usage_key, expected_grades, expected_grade_overrides in (
                (
                    self.subsections[self.chapter_1.location][0].location,
                    GradeFields(0, 0, 0, 0),
                    GradeFields(3, 3, 2, 2)
                ),
                (
                    self.subsections[self.chapter_1.location][1].location,
                    GradeFields(0, 0, 0, 0),
                    GradeFields(3, 4, 3, 4)
                ),
            ):
                # this selects related PersistentSubsectionGradeOverride objects.
                grade = PersistentSubsectionGrade.read_grade(
                    user_id=self.student.id,
                    usage_key=usage_key,
                )
                for field_name in expected_grade_overrides._fields:
                    expected_value = getattr(expected_grade_overrides, field_name)
                    assert expected_value == getattr(grade.override, (field_name + '_override'))
                for field_name in expected_grades._fields:
                    expected_value = getattr(expected_grades, field_name)
                    assert expected_value == getattr(grade, field_name)

    def test_update_failing_grade(self):
        """
        Test that when we update a user's grade to failing, their certificate is marked notpassing
        """
        with override_waffle_flag(self.waffle_flag, active=True):
            cert_args = {
                "user": self.student,
                "course_id": self.course.id,
                "status": CertificateStatuses.downloadable,
            }
            create_or_update_eligible_certificate_for_user(**cert_args)
            self.login_staff()
            post_data = [
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][0].location),
                    'grade': {
                        'earned_all_override': 0,
                        'possible_all_override': 3,
                        'earned_graded_override': 0,
                        'possible_graded_override': 2,
                    },
                },
                {
                    'user_id': self.student.id,
                    'usage_id': str(self.subsections[self.chapter_1.location][1].location),
                    'grade': {
                        'earned_all_override': 0,
                        'possible_all_override': 4,
                        'earned_graded_override': 0,
                        'possible_graded_override': 4,
                    },
                }
            ]
            resp = self.client.post(
                self.get_url(),
                data=json.dumps(post_data),
                content_type='application/json',
            )
            assert status.HTTP_202_ACCEPTED == resp.status_code
            cert = get_certificate_for_user_id(self.student, self.course.id)
            assert cert.status == CertificateStatuses.notpassing


@ddt.ddt
class SubsectionGradeViewTest(GradebookViewTestBase):
    """ Test for the audit api call """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.namespaced_url = 'grades_api:v1:course_grade_overrides'
        cls.locator_a = BlockUsageLocator(
            course_key=cls.course_key,
            block_type='problem',
            block_id='block_id_a'
        )
        cls.locator_b = BlockUsageLocator(
            course_key=cls.course_key,
            block_type='problem',
            block_id='block_id_b'
        )
        cls.record_a = BlockRecord(locator=cls.locator_a, weight=1, raw_possible=10, graded=False)
        cls.record_b = BlockRecord(locator=cls.locator_b, weight=1, raw_possible=10, graded=True)
        cls.block_records = BlockRecordList([cls.record_a, cls.record_b], cls.course_key)
        cls.usage_key = cls.subsections[cls.chapter_1.location][0].location
        cls.user = UserFactory.create()
        cls.user_id = cls.user.id
        cls.params = {
            "user_id": cls.user_id,
            "usage_key": cls.usage_key,
            "course_version": "deadbeef",
            "subtree_edited_timestamp": "2016-08-01 18:53:24.354741Z",
            "earned_all": 6.0,
            "possible_all": 12.0,
            "earned_graded": 6.0,
            "possible_graded": 8.0,
            "visible_blocks": cls.block_records,
            "first_attempted": datetime(2000, 1, 1, 12, 30, 45, tzinfo=UTC),
        }
        cls.grade = PersistentSubsectionGrade.update_or_create_grade(**cls.params)

    def get_url(self, subsection_id=None, user_id=None, history_record_limit=5):  # pylint: disable=arguments-differ
        """
        Helper function to create the course gradebook API url.
        """
        base_url = reverse(
            self.namespaced_url,
            kwargs={
                'subsection_id': subsection_id or self.subsection_id,
            }
        )
        return f"{base_url}?user_id={user_id or self.user_id}&history_record_limit={history_record_limit}"

    @patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.create')
    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_no_grade(self, login_method, mocked_factory):
        getattr(self, login_method)()
        user_no_grade = UserFactory.create()
        all_total_mock = MagicMock(
            earned=1,
            possible=2,
        )
        graded_total_mock = MagicMock(
            earned=3,
            possible=4,
        )
        mock_return_value = MagicMock(
            all_total=all_total_mock,
            graded_total=graded_total_mock
        )
        mocked_factory.return_value = mock_return_value
        with pytest.raises(PersistentSubsectionGrade.DoesNotExist):
            PersistentSubsectionGrade.objects.get(
                user_id=user_no_grade.id,
                course_id=self.usage_key.course_key,
                usage_key=self.usage_key
            )

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key, user_id=user_no_grade.id)
        )

        expected_data = {
            'success': True,
            'original_grade': OrderedDict([
                ('earned_all', 1.0),
                ('possible_all', 2.0),
                ('earned_graded', 3.0),
                ('possible_graded', 4.0)
            ]),
            'user_id': user_no_grade.id,
            'override': None,
            'course_id': str(self.usage_key.course_key),
            'subsection_id': str(self.usage_key),
            'history': []
        }
        assert expected_data == resp.data

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_no_override(self, login_method):
        getattr(self, login_method)()

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key)
        )

        expected_data = {
            'success': True,
            'original_grade': OrderedDict([
                ('earned_all', 6.0),
                ('possible_all', 12.0),
                ('earned_graded', 6.0),
                ('possible_graded', 8.0)
            ]),
            'user_id': self.user_id,
            'override': None,
            'course_id': str(self.course_key),
            'subsection_id': str(self.usage_key),
            'history': []
        }

        assert expected_data == resp.data

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    @freeze_time('2019-01-01')
    def test_with_override_no_modification(self, login_method):
        getattr(self, login_method)()

        override = PersistentSubsectionGradeOverride.objects.create(
            grade=self.grade,
            earned_all_override=0.0,
            possible_all_override=12.0,
            earned_graded_override=0.0,
            possible_graded_override=8.0
        )

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key)
        )

        expected_data = {
            'success': True,
            'original_grade': OrderedDict([
                ('earned_all', 6.0),
                ('possible_all', 12.0),
                ('earned_graded', 6.0),
                ('possible_graded', 8.0)
            ]),
            'user_id': self.user_id,
            'override': OrderedDict([
                ('earned_all_override', 0.0),
                ('possible_all_override', 12.0),
                ('earned_graded_override', 0.0),
                ('possible_graded_override', 8.0)
            ]),
            'course_id': str(self.course_key),
            'subsection_id': str(self.usage_key),
            'history': [OrderedDict([
                ('created', '2019-01-01T00:00:00Z'),
                ('grade_id', 1),
                ('history_id', 1),
                ('earned_all_override', 0.0),
                ('earned_graded_override', 0.0),
                ('override_reason', None),
                ('system', None),
                ('history_date', '2019-01-01T00:00:00Z'),
                ('history_type', '+'),
                ('history_user', None),
                ('history_user_id', None),
                ('id', 1),
                ('possible_all_override', 12.0),
                ('possible_graded_override', 8.0),
            ])],
        }

        assert expected_data == resp.data

    @freeze_time('2019-01-01')
    def test_with_override_with_long_history(self):
        """
        Test that history is truncated to 5 most recent entries
        """
        self.login_staff()

        for i in range(6):
            override = PersistentSubsectionGradeOverride.update_or_create_override(
                requesting_user=self.global_staff,
                subsection_grade_model=self.grade,
                earned_all_override=i,
                earned_graded_override=i,
                feature=GradeOverrideFeatureEnum.gradebook,
            )

        resp = self.client.get(
            self.get_url(
                subsection_id=self.usage_key,
                history_record_limit=5,
            )
        )

        assert len(resp.data['history']) == 5
        assert resp.data['history'][0]['earned_all_override'] != 0.0

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    @freeze_time('2019-01-01')
    def test_with_override_with_history(self, login_method):
        getattr(self, login_method)()

        override = PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.global_staff,
            subsection_grade_model=self.grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            feature=GradeOverrideFeatureEnum.gradebook,
        )

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key)
        )

        expected_data = {
            'success': True,
            'original_grade': OrderedDict([
                ('earned_all', 6.0),
                ('possible_all', 12.0),
                ('earned_graded', 6.0),
                ('possible_graded', 8.0)
            ]),
            'user_id': self.user_id,
            'override': OrderedDict([
                ('earned_all_override', 0.0),
                ('possible_all_override', 12.0),
                ('earned_graded_override', 0.0),
                ('possible_graded_override', 8.0)
            ]),
            'course_id': str(self.course_key),
            'subsection_id': str(self.usage_key),
            'history': [OrderedDict([
                ('created', '2019-01-01T00:00:00Z'),
                ('grade_id', 1),
                ('history_id', 1),
                ('earned_all_override', 0.0),
                ('earned_graded_override', 0.0),
                ('override_reason', None),
                ('system', None),
                ('history_date', '2019-01-01T00:00:00Z'),
                ('history_type', '+'),
                ('history_user', self.global_staff.username),
                ('history_user_id', self.global_staff.id),
                ('id', 1),
                ('possible_all_override', 12.0),
                ('possible_graded_override', 8.0),
            ])],
        }

        assert expected_data == resp.data

    def test_comment_appears(self):
        """
        Test that comments passed (e.g. from proctoring) appear in the history rows
        """
        proctoring_failure_fake_comment = "Failed Test Proctoring"
        self.login_course_staff()
        override = PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.global_staff,
            subsection_grade_model=self.grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            feature=GradeOverrideFeatureEnum.proctoring,
            comment=proctoring_failure_fake_comment
        )

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key)
        )

        assert resp.data['history'][0]['override_reason'] == proctoring_failure_fake_comment

    @ddt.data(
        'login_staff',
    )
    def test_with_invalid_format_subsection_id(self, login_method):
        getattr(self, login_method)()

        resp = self.client.get(
            self.get_url(subsection_id='notAValidSubectionId')
        )

        assert status.HTTP_404_NOT_FOUND == resp.status_code

    @ddt.data(
        'login_staff',
    )
    def test_with_invalid_format_user_id(self, login_method):
        getattr(self, login_method)()

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key, user_id='notAnIntegerUserId')
        )

        assert status.HTTP_404_NOT_FOUND == resp.status_code

    @ddt.data(
        'login_staff',
        'login_course_admin',
        'login_course_staff',
    )
    def test_with_valid_subsection_id_and_valid_user_id_but_no_record(self, login_method):
        getattr(self, login_method)()

        override = PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.global_staff,
            subsection_grade_model=self.grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            feature=GradeOverrideFeatureEnum.gradebook,
        )

        other_user = UserFactory.create()
        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key, user_id=other_user.id)
        )
        expected_data = {
            'success': True,
            'original_grade': OrderedDict([
                ('earned_all', 0.0),
                ('possible_all', 0.0),
                ('earned_graded', 0.0),
                ('possible_graded', 0.0)
            ]),
            'user_id': other_user.id,
            'override': None,
            'course_id': str(self.usage_key.course_key),
            'subsection_id': str(self.usage_key),
            'history': []
        }

        assert expected_data == resp.data

    def test_with_unauthorized_user(self):
        student = UserFactory(username='dummy', password='test')
        self.client.login(username=student.username, password='test')

        resp = self.client.get(
            self.get_url(subsection_id=self.usage_key)
        )

        assert status.HTTP_403_FORBIDDEN == resp.status_code

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_get_override_for_unreleased_block(self):
        self.login_course_staff()
        unreleased_subsection = BlockFactory.create(
            parent_location=self.chapter_1.location,
            category='sequential',
            graded=True,
            start=datetime(2999, 1, 1, tzinfo=UTC),  # arbitrary future date
            display_name='Unreleased Section',
        )

        # We need to update the course in the cache after we create the new block.
        bs_api.update_course_in_cache(self.course_data.course_key)

        resp = self.client.get(
            self.get_url(subsection_id=unreleased_subsection.location)
        )

        expected_data = {
            'success': False,
            'error_message': "Cannot override subsection grade: subsection is not available for target learner.",
            'original_grade': OrderedDict([
                ('earned_all', 0.0),
                ('possible_all', 0.0),
                ('earned_graded', 0.0),
                ('possible_graded', 0.0)
            ]),
            'user_id': self.user_id,
            'override': None,
            'course_id': str(self.usage_key.course_key),
            'subsection_id': str(unreleased_subsection.location),
            'history': []
        }
        assert expected_data == resp.data
