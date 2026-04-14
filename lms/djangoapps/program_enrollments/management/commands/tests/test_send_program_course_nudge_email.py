"""
Tests for the send_program_course_nudge_email management command.
"""
from datetime import timedelta
from unittest.mock import patch

import ddt
from django.core.management import call_command
from django.test.utils import override_settings
from django.utils import timezone
from testfixtures import LogCapture

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory as CatalogCourseFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory, ProgramFactory
from openedx.features.enterprise_support.tests.factories import EnterpriseCustomerUserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

LOG_PATH = 'lms.djangoapps.program_enrollments.management.commands.send_program_course_nudge_email'


@ddt.ddt
class TestSendProgramCourseNudgeEmailCommand(SharedModuleStoreTestCase):
    """
    Test send_program_course_nudge_email command.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = 'send_program_course_nudge_email'

    def setUp(self):
        super().setUp()
        self.user_1 = UserFactory()
        self.user_2 = UserFactory()

        self.enterprise_customer_user = EnterpriseCustomerUserFactory.create(
            user_id=self.user_1.id, enterprise_customer__enable_learner_portal=True
        )

        completed_course = CourseFactory.create()
        self.completed_course_run = CourseRunFactory(key=str(completed_course.id))
        user_1_in_progress_course = CourseFactory.create()
        self.user_1_in_progress_course_run = CourseRunFactory(type='audit', key=str(user_1_in_progress_course.id))
        not_started_course_1 = CourseFactory.create()
        self.not_started_course_run_1 = CourseRunFactory(key=str(not_started_course_1.id))
        not_started_course_2 = CourseFactory.create()
        self.not_started_course_run_2 = CourseRunFactory(key=str(not_started_course_2.id))

        self.catalog_completed_course = CatalogCourseFactory(course_runs=[self.completed_course_run])
        self.catalog_user_1_in_progress_course = CatalogCourseFactory(course_runs=[self.user_1_in_progress_course_run])
        self.catalog_not_started_course_1 = CatalogCourseFactory(course_runs=[self.not_started_course_run_1])
        self.catalog_not_started_course_2 = CatalogCourseFactory(course_runs=[self.not_started_course_run_2])

        self.partially_completed_program_1 = ProgramFactory(
            courses=[self.catalog_completed_course, self.catalog_not_started_course_1],
            type='MicroBachelors'
        )
        self.partially_completed_program_2 = ProgramFactory(
            courses=[
                self.catalog_completed_course, self.catalog_user_1_in_progress_course, self.catalog_not_started_course_2
            ],
            type='MicroMasters'
        )
        self.completed_program = ProgramFactory(
            courses=[self.catalog_completed_course],
            type='MicroMasters'
        )
        self.not_linked_program = ProgramFactory()

        self.enroll_user(user=self.user_1, course=user_1_in_progress_course)
        self.enroll_user(user=self.user_1, course=completed_course, create_grade=True)
        self.enroll_user(user=self.user_2, course=completed_course, create_grade=True)

    def enroll_user(self, user, course, create_grade=False):
        """
        Create PersistentCourseGrade records for given user and course
        """
        CourseEnrollmentFactory(user=user, course_id=course.id, mode=CourseMode.AUDIT)
        if create_grade:
            params = {
                "user_id": user.id,
                "course_id": course.id,
                "course_version": "JoeMcEwing",
                "percent_grade": 77.7,
                "letter_grade": "Great job",
                "passed_timestamp": timezone.now() - timedelta(days=1),
            }
            PersistentCourseGrade.objects.create(**params)
            GeneratedCertificateFactory(
                user=user,
                course_id=course.id,
                status=CertificateStatuses.downloadable,
                mode='verified',
                download_url='www.google.com',
            )

    @ddt.data(
        False,
        True,
    )
    @patch('common.djangoapps.student.models.course_enrollment.segment.track')
    @patch('lms.djangoapps.program_enrollments.management.commands.send_program_course_nudge_email.get_programs')
    @patch('lms.djangoapps.certificates.api.certificates_viewable_for_course', return_value=True)
    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    def test_email_send(self, add_no_commit, __, get_programs_mock, mock_track):  # noqa: PT019
        """
        Test Segment fired as expected.
        """
        # partially_completed_program_2 program is a Micro Program so should be processed first irrespective of the
        # order return from get_programs_mock. Program are sorted on based on their types
        get_programs_mock.return_value = [self.partially_completed_program_1, self.partially_completed_program_2]
        with LogCapture() as logger:
            if add_no_commit:
                call_command(self.command, '--no-commit')
            else:
                call_command(self.command)
            # As user_1_in_progress_course_run is only in-progress for user_1 and not_started for user_2 so user 2
            # should be suggested with user_1_in_progress_course_run and user_1 should be suggested with
            # not_started_course_run_1
            logger.check_present(
                (
                    LOG_PATH,
                    'INFO',
                    f"[Program Course Nudge Email] 2 Emails sent. Records: ["
                    f"'User: {self.user_1.username}, Completed Course: {self.completed_course_run['key']},"
                    f" Suggested Course: {self.not_started_course_run_1['key']}', "
                    f"'User: {self.user_2.username}, Completed Course: {self.completed_course_run['key']},"
                    f" Suggested Course: {self.user_1_in_progress_course_run['key']}']"
                )
            )
            if add_no_commit:
                assert mock_track.call_count == 0
            else:
                assert mock_track.call_count == 2

    @ddt.data(
        False, True
    )
    @patch('common.djangoapps.student.models.course_enrollment.segment.track')
    @patch('lms.djangoapps.program_enrollments.management.commands.send_program_course_nudge_email.get_programs')
    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    def test_email_no_course_recommendation(self, add_no_commit, get_programs_mock, mock_track):
        """
        Test Segment fired as expected.
        """
        get_programs_mock.return_value = [self.completed_program]
        with LogCapture() as logger:
            if add_no_commit:
                call_command(self.command, '--no-commit')
            else:
                call_command(self.command)
            logger.check_present(
                (
                    LOG_PATH,
                    'INFO',
                    '[Program Course Nudge Email] 0 Emails sent. Records: []'
                )
            )
            assert mock_track.call_count == 0
