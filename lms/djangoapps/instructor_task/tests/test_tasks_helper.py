"""
Unit tests for LMS instructor-initiated background tasks helper functions.

- Tests that CSV grade report generation works with unicode emails.
- Tests all of the existing reports.

"""


import os
import shutil
import tempfile
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, Mock, patch

import ddt
import pytest
import unicodecsv
from django.conf import settings
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache
from freezegun import freeze_time
from pytz import UTC

import openedx.core.djangoapps.user_api.course_tag.api as course_tag_api
import openedx.core.djangoapps.content.block_structure.api as bs_api
from xmodule.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory  # lint-amnesty, pylint: disable=wrong-import-order
from lms.djangoapps.course_blocks.transformers import library_content
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import CertificateAllowlistFactory, GeneratedCertificateFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.course_data import CourseData
from lms.djangoapps.grades.models import PersistentCourseGrade, PersistentSubsectionGradeOverride
from lms.djangoapps.grades.subsection_grade import CreateSubsectionGrade
from lms.djangoapps.grades.transformer import GradesTransformer
from lms.djangoapps.instructor_analytics.basic import UNAVAILABLE, list_problem_responses
from lms.djangoapps.instructor_task.tasks_helper.certs import generate_students_certificates
from lms.djangoapps.instructor_task.tasks_helper.enrollments import upload_may_enroll_csv, upload_students_csv
from lms.djangoapps.instructor_task.tasks_helper.grades import (
    ENROLLED_IN_COURSE,
    NOT_ENROLLED_IN_COURSE,
    CourseGradeReport,
    ProblemGradeReport,
    ProblemResponses,
)
from lms.djangoapps.instructor_task.tasks_helper.misc import (
    cohort_students_and_upload,
    upload_course_survey_report,
    upload_ora2_data,
    upload_ora2_submission_files,
    upload_ora2_summary
)
from lms.djangoapps.instructor_task.tests.test_base import (
    InstructorTaskCourseTestCase,
    InstructorTaskModuleTestCase,
    TestReportMixin
)
from lms.djangoapps.survey.models import SurveyAnswer, SurveyForm
from lms.djangoapps.teams.tests.factories import CourseTeamFactory, CourseTeamMembershipFactory
from lms.djangoapps.verify_student.tests.factories import SoftwareSecurePhotoVerificationFactory
from openedx.core.djangoapps.course_groups.models import CohortMembership, CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.credit.tests.factories import CreditCourseFactory
from openedx.core.djangoapps.user_api.partition_schemes import RandomUserPartitionScheme
from openedx.core.djangoapps.util.testing import ContentGroupTestCase, TestConditionalContent
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, check_mongo_calls  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order
# noinspection PyUnresolvedReferences
from xmodule.tests.helpers import override_descriptor_system  # pylint: disable=unused-import

from ..models import ReportStore
from ..tasks_helper.utils import UPDATE_STATUS_FAILED, UPDATE_STATUS_SUCCEEDED
from common.test.utils import assert_dict_contains_subset

_TEAMS_CONFIG = TeamsConfig({
    'max_size': 2,
    'topics': [{'id': 'topic', 'name': 'Topic', 'description': 'A Topic'}],
})
USE_ON_DISK_GRADE_REPORT = 'lms.djangoapps.instructor_task.tasks_helper.grades.use_on_disk_grade_reporting'


class InstructorGradeReportTestCase(TestReportMixin, InstructorTaskCourseTestCase):
    """ Base class for grade report tests. """

    def _verify_cell_data_for_user(
        self, username, course_id, column_header, expected_cell_content, num_rows=2, use_tempfile=False
    ):
        """
        Verify cell data in the grades CSV for a particular user.
        """
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
                result = CourseGradeReport.generate(None, None, course_id, {}, 'graded')
            assert_dict_contains_subset(self, {'attempted': num_rows, 'succeeded': num_rows, 'failed': 0}, result)
            report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
            report_csv_filename = report_store.links_for(course_id)[0][0]
            report_path = report_store.path_to(course_id, report_csv_filename)
            found_user = False
            with report_store.storage.open(report_path) as csv_file:
                for row in unicodecsv.DictReader(csv_file):
                    if row.get('Username') == username:
                        assert row[column_header] == expected_cell_content
                        found_user = True
            assert found_user


@ddt.ddt
class TestInstructorGradeReport(InstructorGradeReportTestCase):
    """
    Tests that CSV grade report generation works.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @ddt.data(True, False)
    def test_unicode_emails(self, use_tempfile):
        """
        Test that students with unicode characters in emails is handled.
        """
        emails = ['student@example.com', 'ni\xf1o@example.com']
        for i, email in enumerate(['student@example.com', 'ni\xf1o@example.com']):
            self.create_student(f'student{i}', email)

        self.current_task = Mock()  # pylint: disable=attribute-defined-outside-init
        self.current_task.update_state = Mock()
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = self.current_task
            with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
                result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
        num_students = len(emails)
        assert_dict_contains_subset(self, {'attempted': num_students, 'succeeded': num_students, 'failed': 0}, result)

    @ddt.data(True, False)
    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.iter')
    def test_grading_failure(self, use_tempfile, mock_grades_iter, _mock_current_task):
        """
        Test that any grading errors are properly reported in the
        progress dict and uploaded to the report store.
        """
        mock_grades_iter.return_value = [
            (self.create_student('username', 'student@example.com'), None, TypeError('Cannot grade student'))
        ]
        with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert_dict_contains_subset(self, {'attempted': 1, 'succeeded': 0, 'failed': 1}, result)

        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        assert any(('grade_report_err' in item[0]) for item in report_store.links_for(self.course.id))

    def test_cohort_data_in_grading(self):
        """
        Test that cohort data is included in grades csv if cohort configuration is enabled for course.
        """
        cohort_groups = ['cohort 1', 'cohort 2']
        course = CourseFactory.create(cohort_config={'cohorted': True, 'auto_cohort': True,
                                                     'auto_cohort_groups': cohort_groups})

        user_1 = 'user_1'
        user_2 = 'user_2'
        CourseEnrollment.enroll(UserFactory.create(username=user_1), course.id)
        CourseEnrollment.enroll(UserFactory.create(username=user_2), course.id)

        # In auto cohorting a group will be assigned to a user only when user visits a problem
        # In grading calculation we only add a group in csv if group is already assigned to
        # user rather than creating a group automatically at runtime
        self._verify_cell_data_for_user(user_1, course.id, 'Cohort Name', '')
        self._verify_cell_data_for_user(user_2, course.id, 'Cohort Name', '')

    def test_unicode_cohort_data_in_grading(self):
        """
        Test that cohorts can contain unicode characters.
        """
        course = CourseFactory.create(cohort_config={'cohorted': True})

        # Create users and manually assign cohorts
        user1 = UserFactory.create(username='user1')
        user2 = UserFactory.create(username='user2')
        CourseEnrollment.enroll(user1, course.id)
        CourseEnrollment.enroll(user2, course.id)
        professor_x = 'ÞrÖfessÖr X'
        magneto = 'MàgnëtÖ'
        cohort1 = CohortFactory(course_id=course.id, name=professor_x)
        cohort2 = CohortFactory(course_id=course.id, name=magneto)
        membership1 = CohortMembership(course_user_group=cohort1, user=user1)
        membership1.save()
        membership2 = CohortMembership(course_user_group=cohort2, user=user2)
        membership2.save()

        self._verify_cell_data_for_user(user1.username, course.id, 'Cohort Name', professor_x)
        self._verify_cell_data_for_user(user2.username, course.id, 'Cohort Name', magneto)

    def test_unicode_user_partitions(self):
        """
        Test that user partition groups can contain unicode characters.
        """
        user_groups = ['ÞrÖfessÖr X', 'MàgnëtÖ']
        user_partition = UserPartition(
            0,
            'x_man',
            'X Man',
            [
                Group(0, user_groups[0]),
                Group(1, user_groups[1])
            ]
        )

        # Create course with group configurations
        self.initialize_course(
            course_factory_kwargs={
                'user_partitions': [user_partition]
            }
        )

        _groups = [group.name for group in self.course.user_partitions[0].groups]
        assert _groups == user_groups

    def test_cohort_scheme_partition(self):
        """
        Test that cohort-schemed user partitions are ignored in the
        grades export.
        """
        # Set up a course with 'cohort' and 'random' user partitions.
        cohort_scheme_partition = UserPartition(
            0,
            'Cohort-schemed Group Configuration',
            'Group Configuration based on Cohorts',
            [Group(0, 'Group A'), Group(1, 'Group B')],
            scheme_id='cohort'
        )
        experiment_group_a = Group(2, 'Expériment Group A')
        experiment_group_b = Group(3, 'Expériment Group B')
        experiment_partition = UserPartition(
            1,
            'Content Expériment Configuration',
            'Group Configuration for Content Expériments',
            [experiment_group_a, experiment_group_b],
            scheme_id='random'
        )
        course = CourseFactory.create(
            cohort_config={'cohorted': True},
            user_partitions=[cohort_scheme_partition, experiment_partition]
        )

        # Create user_a and user_b which are enrolled in the course
        # and assigned to experiment_group_a and experiment_group_b,
        # respectively.
        user_a = UserFactory.create(username='user_a')
        user_b = UserFactory.create(username='user_b')
        CourseEnrollment.enroll(user_a, course.id)
        CourseEnrollment.enroll(user_b, course.id)
        course_tag_api.set_course_tag(
            user_a,
            course.id,
            RandomUserPartitionScheme.key_for_partition(experiment_partition),
            experiment_group_a.id
        )
        course_tag_api.set_course_tag(
            user_b,
            course.id,
            RandomUserPartitionScheme.key_for_partition(experiment_partition),
            experiment_group_b.id
        )

        # Assign user_a to a group in the 'cohort'-schemed user
        # partition (by way of a cohort) to verify that the user
        # partition group does not show up in the "Experiment Group"
        # cell.
        cohort_a = CohortFactory.create(course_id=course.id, name='Cohørt A', users=[user_a])
        CourseUserGroupPartitionGroup(
            course_user_group=cohort_a,
            partition_id=cohort_scheme_partition.id,
            group_id=cohort_scheme_partition.groups[0].id
        ).save()

        # Verify that we see user_a and user_b in their respective
        # content experiment groups, and that we do not see any
        # content groups.
        experiment_group_message = 'Experiment Group ({content_experiment})'
        self._verify_cell_data_for_user(
            user_a.username,
            course.id,
            experiment_group_message.format(
                content_experiment=experiment_partition.name
            ),
            experiment_group_a.name
        )
        self._verify_cell_data_for_user(
            user_b.username,
            course.id,
            experiment_group_message.format(
                content_experiment=experiment_partition.name
            ),
            experiment_group_b.name
        )

        # Make sure cohort info is correct.
        cohort_name_header = 'Cohort Name'
        self._verify_cell_data_for_user(
            user_a.username,
            course.id,
            cohort_name_header,
            cohort_a.name
        )
        self._verify_cell_data_for_user(
            user_b.username,
            course.id,
            cohort_name_header,
            '',
        )

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.iter')
    def test_unicode_in_csv_header(self, mock_grades_iter, _mock_current_task):
        """
        Tests that CSV grade report works if unicode in headers.
        """
        mock_course_grade = MagicMock()
        mock_course_grade.summary = {'section_breakdown': [{'label': '\u8282\u540e\u9898 01'}]}
        mock_course_grade.letter_grade = None
        mock_course_grade.percent = 0
        mock_grades_iter.return_value = [
            (
                self.create_student('username', 'student@example.com'),
                mock_course_grade,
                None,
            )
        ]
        result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert_dict_contains_subset(self, {'attempted': 1, 'succeeded': 1, 'failed': 0}, result)

    def test_certificate_eligibility(self):
        """
        Verifies that whether a learner has a failing grade in the database or the grade is
        calculated on the fly, a failing grade will result in a Certificate Eligibility of
        "N" in the report.

        Also confirms that a persisted passing grade will result in a Certificate Eligibility
        of "Y" for verified learners and "N" for audit learners.
        """
        course = CourseFactory.create()
        audit_user = CourseEnrollment.enroll(UserFactory.create(), course.id)
        self._verify_cell_data_for_user(audit_user.username, course.id, 'Certificate Eligible', 'N', num_rows=1)
        grading_policy_hash = GradesTransformer.grading_policy_hash(course)
        PersistentCourseGrade.update_or_create(
            user_id=audit_user.user_id,
            course_id=course.id,
            passed=False,
            percent_grade=0.0,
            grading_policy_hash=grading_policy_hash,
        )
        self._verify_cell_data_for_user(audit_user.username, course.id, 'Certificate Eligible', 'N', num_rows=1)
        PersistentCourseGrade.update_or_create(
            user_id=audit_user.user_id,
            course_id=course.id,
            passed=True,
            percent_grade=0.8,
            letter_grade="pass",
            grading_policy_hash=grading_policy_hash,
        )
        # verifies that audit passing learner is not eligible for certificate
        self._verify_cell_data_for_user(audit_user.username, course.id, 'Certificate Eligible', 'N', num_rows=1)

        verified_user = CourseEnrollment.enroll(UserFactory.create(), course.id, 'verified')
        PersistentCourseGrade.update_or_create(
            user_id=verified_user.user_id,
            course_id=course.id,
            passed=True,
            percent_grade=0.8,
            letter_grade="pass",
            grading_policy_hash=grading_policy_hash,
        )
        # verifies that verified passing learner is eligible for certificate
        self._verify_cell_data_for_user(verified_user.username, course.id, 'Certificate Eligible', 'Y', num_rows=2)

    def test_query_counts(self):
        experiment_group_a = Group(2, 'Expériment Group A')
        experiment_group_b = Group(3, 'Expériment Group B')
        experiment_partition = UserPartition(
            1,
            'Content Expériment Configuration',
            'Group Configuration for Content Expériments',
            [experiment_group_a, experiment_group_b],
            scheme_id='random'
        )
        course = CourseFactory.create(
            cohort_config={'cohorted': True, 'auto_cohort': True, 'auto_cohort_groups': ['cohort 1', 'cohort 2']},
            user_partitions=[experiment_partition],
            teams_configuration=_TEAMS_CONFIG,
        )
        _ = CreditCourseFactory(course_key=course.id)

        bs_api.update_course_in_cache(course.id)

        num_users = 5
        for _ in range(num_users):
            user = UserFactory.create()
            CourseEnrollment.enroll(user, course.id, mode='verified')
            SoftwareSecurePhotoVerificationFactory.create(user=user, status='approved')

        RequestCache.clear_all_namespaces()

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with check_mongo_calls(2):
                with self.assertNumQueries(46):
                    CourseGradeReport.generate(None, None, course.id, {}, 'graded')

    def test_inactive_enrollments(self):
        """
        Test that students with inactive enrollments are included in report.
        """
        self.create_student('active-student', 'active@example.com')
        self.create_student('inactive-student', 'inactive@example.com', enrollment_active=False)

        self.current_task = Mock()  # pylint: disable=attribute-defined-outside-init
        self.current_task.update_state = Mock()

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = self.current_task
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')

        self._verify_cell_data_for_user('active-student', self.course.id, 'Enrollment Status', ENROLLED_IN_COURSE)
        self._verify_cell_data_for_user('inactive-student', self.course.id, 'Enrollment Status', NOT_ENROLLED_IN_COURSE)

        expected_students = 2
        assert_dict_contains_subset(
            self,
            {'attempted': expected_students, 'succeeded': expected_students, 'failed': 0},
            result,
        )


@ddt.ddt
class TestTeamGradeReport(InstructorGradeReportTestCase):
    """ Test that teams appear correctly in the grade report when it is enabled for the course. """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(teams_configuration=_TEAMS_CONFIG)
        self.student1 = UserFactory.create()
        CourseEnrollment.enroll(self.student1, self.course.id)
        self.student2 = UserFactory.create()
        CourseEnrollment.enroll(self.student2, self.course.id)

    @ddt.data(True, False)
    def test_team_in_grade_report(self, use_tempfile):
        self._verify_cell_data_for_user(
            self.student1.username, self.course.id, 'Team Name', '', use_tempfile=use_tempfile
        )

    def test_correct_team_name_in_grade_report(self):
        team1 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team1, user=self.student1)
        team2 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team2, user=self.student2)
        self._verify_cell_data_for_user(self.student1.username, self.course.id, 'Team Name', team1.name)
        self._verify_cell_data_for_user(self.student2.username, self.course.id, 'Team Name', team2.name)

    def test_team_deleted(self):
        team1 = CourseTeamFactory.create(course_id=self.course.id)
        membership1 = CourseTeamMembershipFactory.create(team=team1, user=self.student1)
        team2 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team2, user=self.student2)

        team1.delete()
        membership1.delete()

        self._verify_cell_data_for_user(self.student1.username, self.course.id, 'Team Name', '')
        self._verify_cell_data_for_user(self.student2.username, self.course.id, 'Team Name', team2.name)


# pylint: disable=protected-access
@ddt.ddt
class TestProblemResponsesReport(TestReportMixin, InstructorTaskModuleTestCase):
    """
    Tests that generation of CSV files listing student answers to a
    given problem works.
    """

    def setUp(self):
        super().setUp()
        self.initialize_course()
        self.instructor = self.create_instructor('instructor')
        self.student = self.create_student('student')

    @contextmanager
    def _remove_capa_report_generator(self):
        """
        Temporarily removes the generate_report_data method so we can test
        report generation when it's absent.
        """
        from xmodule.capa_block import ProblemBlock
        generate_report_data = ProblemBlock.generate_report_data
        del ProblemBlock.generate_report_data
        try:
            yield
        finally:
            ProblemBlock.generate_report_data = generate_report_data

    @patch.dict('django.conf.settings.FEATURES', {'MAX_PROBLEM_RESPONSES_COUNT': 4})
    def test_build_student_data_limit(self):
        """
        Ensure that the _build_student_data method respects the global setting for
        maximum responses to return in a report.
        """
        self.define_option_problem('Problem1')
        for ctr in range(5):
            student = self.create_student(f'student{ctr}')
            self.submit_student_answer(student.username, 'Problem1', ['Option 1'])

        student_data, _ = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(self.course.location)],
        )

        assert len(student_data) == 4

    @patch('lms.djangoapps.instructor_task.tasks_helper.grades.list_problem_responses', return_value=[])
    def test_problem_responses_excludes_library_content_transformers(self, _mock_list_problem_responses):
        """Ensure ProblemResponses bypasses per-user library_content transformers.

        The default course block access transformers include library_content transformers
        that filter children based on the requesting user's selections. Reports must exclude
        those transformers so output is not dependent on the instructor running the report.
        """
        problem = self.define_option_problem('Problem1')

        captured = {}

        class _FakeCourseBlocks:
            """Minimal fake CourseBlocks object for testing."""
            def get_xblock_field(self, _usage_key, field_name):
                if field_name == 'display_name':
                    return 'Problem1'
                return None

            def get_children(self, _usage_key):
                return []

        def _fake_get_course_blocks(_user, _usage_key, transformers=None, **_kwargs):
            captured['transformers'] = transformers
            return _FakeCourseBlocks()

        with patch(
            'lms.djangoapps.instructor_task.tasks_helper.grades.get_course_blocks',
            side_effect=_fake_get_course_blocks,
        ):
            ProblemResponses._build_student_data(
                user_id=self.instructor.id,
                course_key=self.course.id,
                usage_key_str_list=[str(problem.location)],
            )

        transformers = captured.get('transformers')
        assert transformers is not None
        all_transformers = transformers._transformers['supports_filter'] + transformers._transformers['no_filter']
        assert not any(isinstance(t, library_content.ContentLibraryTransformer) for t in all_transformers)
        assert not any(isinstance(t, library_content.ContentLibraryOrderTransformer) for t in all_transformers)

    @patch(
        'lms.djangoapps.instructor_task.tasks_helper.grades.list_problem_responses',
        wraps=list_problem_responses
    )
    def test_build_student_data_for_block_without_generate_report_data(self, mock_list_problem_responses):
        """
        Ensure that building student data for a block the doesn't have the
        ``generate_report_data`` method works as expected.
        """
        problem = self.define_option_problem('Problem1')
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        with self._remove_capa_report_generator():
            student_data, student_data_keys_list = ProblemResponses._build_student_data(
                user_id=self.instructor.id,
                course_key=self.course.id,
                usage_key_str_list=[str(problem.location)],
            )
        assert len(student_data) == 1
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
            },
            student_data[0],
        )
        assert 'state' in student_data[0]
        assert student_data_keys_list == ['username', 'title', 'location', 'block_key', 'state']
        mock_list_problem_responses.assert_called_with(self.course.id, ANY, ANY)

    @patch('xmodule.capa_block.ProblemBlock.generate_report_data', create=True)
    def test_build_student_data_for_block_with_mock_generate_report_data(self, mock_generate_report_data):
        """
        Ensure that building student data for a block that supports the
        ``generate_report_data`` method works as expected.
        """
        self.define_option_problem('Problem1')
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        state1 = {'some': 'state1', 'more': 'state1!'}
        state2 = {'some': 'state2', 'more': 'state2!'}
        mock_generate_report_data.return_value = iter([
            ('student', state1),
            ('student', state2),
        ])
        student_data, student_data_keys_list = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(self.course.location)],
        )
        assert len(student_data) == 2
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
                'some': 'state1',
                'more': 'state1!',
            },
            student_data[0],
        )
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
                'some': 'state2',
                'more': 'state2!',
            },
            student_data[1],
        )
        assert student_data[0]['state'] == student_data[1]['state']
        assert student_data_keys_list == ['username', 'title', 'location', 'more', 'some', 'block_key', 'state']

    @patch('xmodule.capa_block.ProblemBlock.generate_report_data', create=True)
    def test_build_student_data_for_block_with_ordered_generate_report_data(self, mock_generate_report_data):
        """
        Ensure that building student data for a block that returns OrderedDicts from the
        ``generate_report_data`` sorts the columns as expected.
        """
        self.define_option_problem('Problem1')
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        state1 = OrderedDict()
        state1['some'] = 'state1'
        state1['more'] = 'state1!'
        state2 = {'some': 'state2', 'more': 'state2!'}
        mock_generate_report_data.return_value = iter([
            ('student', state1),
            ('student', state2),
        ])
        student_data, student_data_keys_list = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(self.course.location)],
        )
        assert len(student_data) == 2
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
                'some': 'state1',
                'more': 'state1!',
            },
            student_data[0],
        )
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
                'some': 'state2',
                'more': 'state2!',
            },
            student_data[1],
        )
        assert student_data[0]['state'] == student_data[1]['state']
        assert student_data_keys_list == ['username', 'title', 'location', 'some', 'more', 'block_key', 'state']

    def test_build_student_data_for_block_with_real_generate_report_data(self):
        """
        Ensure that building student data for a block that supports the
        ``generate_report_data`` method works as expected.
        """
        self.define_option_problem('Problem1')
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        student_data, student_data_keys_list = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(self.course.location)],
        )
        assert len(student_data) == 1
        assert_dict_contains_subset(
            self,
            {
                'username': 'student',
                'location': 'test_course > Section > Subsection > Problem1',
                'block_key': 'block-v1:edx+1.23x+test_course+type@problem+block@Problem1',
                'title': 'Problem1',
                'Answer ID': 'Problem1_2_1',
                'Answer': 'Option 1',
                'Correct Answer': 'Option 1',
                'Question': 'The correct answer is Option 1',
            },
            student_data[0],
        )
        assert 'state' in student_data[0]
        assert student_data_keys_list == ['username', 'title', 'location', 'Answer', 'Answer ID', 'Correct Answer',
                                          'Question', 'block_key', 'state']

    def test_build_student_data_for_multiple_problems(self):
        """
        Ensure that building student data works when supplied multiple usage keys.
        """
        problem1 = self.define_option_problem('Problem1')
        problem2 = self.define_option_problem('Problem2')
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        self.submit_student_answer(self.student.username, 'Problem2', ['Option 1'])
        student_data, _ = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(problem1.location), str(problem2.location)],
        )
        assert len(student_data) == 2
        for idx in range(1, 3):
            assert_dict_contains_subset(
                self,
                {
                    'username': 'student',
                    'location': f'test_course > Section > Subsection > Problem{idx}',
                    'block_key': f'block-v1:edx+1.23x+test_course+type@problem+block@Problem{idx}',
                    'title': f'Problem{idx}',
                    'Answer ID': f'Problem{idx}_2_1',
                    'Answer': 'Option 1',
                    'Correct Answer': 'Option 1',
                    'Question': 'The correct answer is Option 1',
                },
                student_data[idx - 1],
            )
            assert 'state' in student_data[(idx - 1)]

    @ddt.data(
        (['problem'], 5),
        (['other'], 0),
        (None, 5),
    )
    @ddt.unpack
    def test_build_student_data_with_filter(self, filters, filtered_count):
        """
        Ensure that building student data works when supplied multiple usage keys.
        """
        for idx in range(1, 6):
            self.define_option_problem(f'Problem{idx}')
            item = BlockFactory.create(
                parent_location=self.problem_section.location,
                parent=self.problem_section,
                display_name=f"Item{idx}",
                data=''
            )
            StudentModule.save_state(self.student, self.course.id, item.location, {})

        for idx in range(1, 6):
            self.submit_student_answer(self.student.username, f'Problem{idx}', ['Option 1'])

        student_data, _ = ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(self.course.location)],
            filter_types=filters,
        )
        assert len(student_data) == filtered_count

    @patch('lms.djangoapps.instructor_task.tasks_helper.grades.list_problem_responses')
    @patch('xmodule.capa_block.ProblemBlock.generate_report_data', create=True)
    def test_build_student_data_for_block_with_generate_report_data_not_implemented(
            self,
            mock_generate_report_data,
            mock_list_problem_responses,
    ):
        """
        Ensure that if ``generate_report_data`` raises a NotImplementedError,
        the report falls back to the alternative method.
        """
        problem = self.define_option_problem('Problem1')
        mock_generate_report_data.side_effect = NotImplementedError
        ProblemResponses._build_student_data(
            user_id=self.instructor.id,
            course_key=self.course.id,
            usage_key_str_list=[str(problem.location)],
        )
        mock_generate_report_data.assert_called_with(ANY, ANY)
        mock_list_problem_responses.assert_called_with(self.course.id, ANY, ANY)

    def test_success(self):
        task_input = {
            'problem_locations': str(self.course.location),
            'user_id': self.instructor.id
        }
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patch('lms.djangoapps.instructor_task.tasks_helper.grades'
                       '.ProblemResponses._build_student_data') as mock_build_student_data:
                mock_build_student_data.return_value = (
                    [
                        {'username': 'user0', 'state': 'state0'},
                        {'username': 'user1', 'state': 'state1'},
                        {'username': 'user2', 'state': 'state2'},
                    ],
                    ['username', 'state']
                )
                result = ProblemResponses.generate(
                    None, None, self.course.id, task_input, 'calculated'
                )
        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        links = report_store.links_for(self.course.id)

        assert len(links) == 1
        assert set(({'attempted': 3, 'succeeded': 3, 'failed': 0}).items()).issubset(set(result.items()))
        assert "report_name" in result

    @ddt.data(
        ('blkid', None, 'edx_1.23x_test_course_student_state_from_blkid_2020-01-01-0000.csv'),
        ('blkid', 'poll,survey', 'edx_1.23x_test_course_student_state_from_blkid_for_poll,survey_2020-01-01-0000.csv'),
        ('blkid1,blkid2', None, 'edx_1.23x_test_course_student_state_from_multiple_blocks_2020-01-01-0000.csv'),
        (
            'blkid1,blkid2',
            'poll,survey',
            'edx_1.23x_test_course_student_state_from_multiple_blocks_for_poll,survey_2020-01-01-0000.csv',
        ),
    )
    @ddt.unpack
    def test_file_names(self, problem_locations, problem_types_filter, file_name):
        task_input = {
            'problem_locations': problem_locations,
            'problem_types_filter': problem_types_filter,
            'user_id': self.instructor.id
        }
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'), \
                freeze_time('2020-01-01'):
            with patch('lms.djangoapps.instructor_task.tasks_helper.grades'
                       '.ProblemResponses._build_student_data') as mock_build_student_data:
                mock_build_student_data.return_value = (
                    [
                        {'username': 'user0', 'state': 'state0'},
                        {'username': 'user1', 'state': 'state1'},
                        {'username': 'user2', 'state': 'state2'},
                    ],
                    ['username', 'state']
                )
                result = ProblemResponses.generate(
                    None, None, self.course.id, task_input, 'calculated'
                )
        assert result.get('report_name') == file_name


@ddt.ddt
class TestProblemGradeReport(TestReportMixin, InstructorTaskModuleTestCase):
    """
    Test that the problem CSV generation works.
    """

    def setUp(self):
        super().setUp()
        self.initialize_course()
        # Add unicode data to CSV even though unicode usernames aren't
        # technically possible in openedx.
        self.student_1 = self.create_student('üser_1')
        self.student_2 = self.create_student('üser_2')
        self.csv_header_row = ['Student ID', 'Email', 'Username', 'Enrollment Status', 'Grade']

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @ddt.data(True, False)
    def test_no_problems(self, use_tempfile, _):
        """
        Verify that we see no grade information for a course with no graded
        problems.
        """
        with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
            result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert_dict_contains_subset(
            self,
            {'action_name': 'graded', 'attempted': 2, 'succeeded': 2, 'failed': 0},
            result
        )
        self.verify_rows_in_csv([
            dict(list(zip(
                self.csv_header_row,
                [str(self.student_1.id), self.student_1.email, self.student_1.username, ENROLLED_IN_COURSE, '0.0']
            ))),
            dict(list(zip(
                self.csv_header_row,
                [str(self.student_2.id), self.student_2.email, self.student_2.username, ENROLLED_IN_COURSE, '0.0']
            )))
        ])

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @ddt.data(True, False)
    def test_single_problem(self, use_tempfile, _):
        vertical = BlockFactory.create(
            parent_location=self.problem_section.location,
            category='vertical',
            metadata={'graded': True},
            display_name='Problem Vertical'
        )
        self.define_option_problem('Problem1', parent=vertical)

        self.submit_student_answer(self.student_1.username, 'Problem1', ['Option 1'])
        with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
            result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert_dict_contains_subset(
            self,
            {'action_name': 'graded', 'attempted': 2, 'succeeded': 2, 'failed': 0},
            result
        )
        problem_name = 'Homework 1: Subsection - Problem1'
        header_row = self.csv_header_row + [problem_name + ' (Earned)', problem_name + ' (Possible)']
        self.verify_rows_in_csv([
            dict(list(zip(
                header_row,
                [
                    str(self.student_1.id),
                    self.student_1.email,
                    self.student_1.username,
                    ENROLLED_IN_COURSE,
                    '0.01', '1.0', '2.0',
                ]
            ))),
            dict(list(zip(
                header_row,
                [
                    str(self.student_2.id),
                    self.student_2.email,
                    self.student_2.username,
                    ENROLLED_IN_COURSE,
                    '0.0', 'Not Attempted', '2.0',
                ]
            )))
        ])

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @ddt.data(True, False)
    def test_single_problem_verified_student_only(self, use_tempfile, _):
        with patch(
            'lms.djangoapps.instructor_task.tasks_helper.grades.problem_grade_report_verified_only',
            return_value=True,
        ):
            student_verified = self.create_student('user_verified', mode='verified')
            vertical = BlockFactory.create(
                parent_location=self.problem_section.location,
                category='vertical',
                metadata={'graded': True},
                display_name='Problem Vertical'
            )
            self.define_option_problem('Problem1', parent=vertical)

            self.submit_student_answer(self.student_1.username, 'Problem1', ['Option 1'])
            self.submit_student_answer(student_verified.username, 'Problem1', ['Option 1'])
            with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
                result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 1, 'succeeded': 1, 'failed': 0},
                result,
            )

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    @ddt.data(True, False)
    def test_inactive_enrollment_included(self, use_tempfile, _):
        """
        Students with inactive enrollments in a course should be included in Problem Grade Report.
        """
        inactive_student = self.create_student('inactive-student', 'inactive@example.com', enrollment_active=False)
        vertical = BlockFactory.create(
            parent_location=self.problem_section.location,
            category='vertical',
            metadata={'graded': True},
            display_name='Problem Vertical'
        )
        self.define_option_problem('Problem1', parent=vertical)

        self.submit_student_answer(self.student_1.username, 'Problem1', ['Option 1'])
        with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
            result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert_dict_contains_subset(
            self,
            {'action_name': 'graded', 'attempted': 3, 'succeeded': 3, 'failed': 0},
            result
        )
        problem_name = 'Homework 1: Subsection - Problem1'
        header_row = self.csv_header_row + [problem_name + ' (Earned)', problem_name + ' (Possible)']
        self.verify_rows_in_csv([
            dict(list(zip(
                header_row,
                [
                    str(self.student_1.id),
                    self.student_1.email,
                    self.student_1.username,
                    ENROLLED_IN_COURSE,
                    '0.01', '1.0', '2.0',
                ]
            ))),
            dict(list(zip(
                header_row,
                [
                    str(self.student_2.id),
                    self.student_2.email,
                    self.student_2.username,
                    ENROLLED_IN_COURSE,
                    '0.0', 'Not Attempted', '2.0',
                ]
            ))),
            dict(list(zip(
                header_row,
                [
                    str(inactive_student.id),
                    inactive_student.email,
                    inactive_student.username,
                    NOT_ENROLLED_IN_COURSE,
                    '0.0', 'Not Attempted', '2.0',
                ]
            )))
        ])


@ddt.ddt
class TestProblemReportSplitTestContent(TestReportMixin, TestConditionalContent, InstructorTaskModuleTestCase):
    """
    Test the problem report on a course that has split tests.
    """
    OPTION_1 = 'Option 1'
    OPTION_2 = 'Option 2'

    def setUp(self):
        super().setUp()
        self.problem_a_url = 'problem_a_url'
        self.problem_b_url = 'problem_b_url'
        self.define_option_problem(self.problem_a_url, parent=self.vertical_a)
        self.define_option_problem(self.problem_b_url, parent=self.vertical_b)

    @ddt.data(True, False)
    def test_problem_grade_report(self, use_tempfile):
        """
        Test that we generate the correct grade report when dealing with A/B tests.

        In order to verify that the behavior of the grade report is correct, we submit answers for problems
        that the student won't have access to. A/B tests won't restrict access to the problems, but it should
        not show up in that student's course tree when generating the grade report, hence the Not Available's
        in the grade report.
        """
        # student A will get 100%, student B will get 50% because
        # OPTION_1 is the correct option, and OPTION_2 is the
        # incorrect option
        self.submit_student_answer(self.student_a.username, self.problem_a_url, [self.OPTION_1, self.OPTION_1])
        self.submit_student_answer(self.student_a.username, self.problem_b_url, [self.OPTION_1, self.OPTION_1])

        self.submit_student_answer(self.student_b.username, self.problem_a_url, [self.OPTION_1, self.OPTION_2])
        self.submit_student_answer(self.student_b.username, self.problem_b_url, [self.OPTION_1, self.OPTION_2])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
                result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 2, 'succeeded': 2, 'failed': 0},
                result,
            )

        problem_names = ['Homework 1: Subsection - problem_a_url', 'Homework 1: Subsection - problem_b_url']
        header_row = ['Student ID', 'Email', 'Username', 'Enrollment Status', 'Grade']
        for problem in problem_names:
            header_row += [problem + ' (Earned)', problem + ' (Possible)']

        self.verify_rows_in_csv([
            dict(list(zip(
                header_row,
                [
                    str(self.student_a.id),
                    self.student_a.email,
                    self.student_a.username,
                    ENROLLED_IN_COURSE,
                    '1.0', '2.0', '2.0', 'Not Available', 'Not Available'
                ]
            ))),
            dict(list(zip(
                header_row,
                [
                    str(self.student_b.id),
                    self.student_b.email,
                    self.student_b.username,
                    ENROLLED_IN_COURSE,
                    '0.5', 'Not Available', 'Not Available', '1.0', '2.0'
                ]
            )))
        ])

    def test_problem_grade_report_valid_columns_order(self):
        """
        Test that in the CSV grade report columns are placed in the proper order
        """
        grader_num = 7

        self.course = CourseFactory.create(
            grading_policy={
                "GRADER": [{
                    "type": "Homework %d" % i,
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "HW %d" % i,
                    "weight": 1.0
                } for i in range(1, grader_num)]
            }
        )

        # Create users
        self.student_a = UserFactory.create(username='student_a', email='student_a@example.com')
        CourseEnrollmentFactory.create(user=self.student_a, course_id=self.course.id)
        self.student_b = UserFactory.create(username='student_b', email='student_b@example.com')
        CourseEnrollmentFactory.create(user=self.student_b, course_id=self.course.id)

        problem_vertical_list = []

        for i in range(1, grader_num):
            chapter_name = 'Chapter %d' % i
            problem_section_name = 'Problem section %d' % i
            problem_section_format = 'Homework %d' % i
            problem_vertical_name = 'Problem Unit %d' % i

            chapter = BlockFactory.create(parent_location=self.course.location,
                                          display_name=chapter_name)

            # Add a sequence to the course to which the problems can be added
            problem_section = BlockFactory.create(parent_location=chapter.location,
                                                  category='sequential',
                                                  metadata={'graded': True,
                                                            'format': problem_section_format},
                                                  display_name=problem_section_name)

            # Create a vertical
            problem_vertical = BlockFactory.create(
                parent_location=problem_section.location,
                category='vertical',
                display_name=problem_vertical_name
            )
            problem_vertical_list.append(problem_vertical)

        problem_names = []
        for i in range(1, grader_num):
            problem_url = 'test_problem_%d' % i
            self.define_option_problem(problem_url, parent=problem_vertical_list[i - 1])
            title = 'Homework %d 1: Problem section %d - %s' % (i, i, problem_url)
            problem_names.append(title)

        header_row = ['Student ID', 'Email', 'Username', 'Enrollment Status', 'Grade']
        for problem in problem_names:
            header_row += [problem + ' (Earned)', problem + ' (Possible)']

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
        assert self.get_csv_row_with_headers() == header_row


@ddt.ddt
@pytest.mark.usefixtures("override_descriptor_system")
class TestProblemReportCohortedContent(TestReportMixin, ContentGroupTestCase, InstructorTaskModuleTestCase):
    """
    Test the problem report on a course that has cohorted content.
    """

    def setUp(self):
        super().setUp()
        # construct cohorted problems to work on.
        self.add_course_content()
        vertical = BlockFactory.create(
            parent_location=self.problem_section.location,
            category='vertical',
            metadata={'graded': True},
            display_name='Problem Vertical'
        )
        self.define_option_problem(
            "Problem0",
            parent=vertical,
            group_access={self.course.user_partitions[0].id: [self.course.user_partitions[0].groups[0].id]}
        )
        self.define_option_problem(
            "Problem1",
            parent=vertical,
            group_access={self.course.user_partitions[0].id: [self.course.user_partitions[0].groups[1].id]}
        )

    def _format_user_grade(self, header_row, user, enrollment_status, grade):
        """
        Helper method that format the user grade
        Args:
            header_row(list): header row of csv containing Student ID, Email, Username etc
            user(object): Django user object
            grade(list): Users' grade list
        """
        return dict(list(zip(
            header_row,
            [
                str(user.id),
                user.email,
                user.username,
                enrollment_status,
            ] + grade
        )))

    @ddt.data(True, False)
    def test_cohort_content(self, use_tempfile):
        self.submit_student_answer(self.alpha_user.username, 'Problem0', ['Option 1', 'Option 1'])
        resp = self.submit_student_answer(self.alpha_user.username, 'Problem1', ['Option 1', 'Option 1'])
        assert resp.status_code == 404

        resp = self.submit_student_answer(self.beta_user.username, 'Problem0', ['Option 1', 'Option 2'])
        assert resp.status_code == 404
        self.submit_student_answer(self.beta_user.username, 'Problem1', ['Option 1', 'Option 2'])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patch(USE_ON_DISK_GRADE_REPORT, return_value=use_tempfile):
                result = ProblemGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 5, 'succeeded': 5, 'failed': 0},
                result,
            )
        problem_names = ['Homework 1: Subsection - Problem0', 'Homework 1: Subsection - Problem1']
        header_row = ['Student ID', 'Email', 'Username', 'Enrollment Status', 'Grade']
        for problem in problem_names:
            header_row += [problem + ' (Earned)', problem + ' (Possible)']

        user_grades = [
            {
                'user': self.staff_user,
                'enrollment_status': ENROLLED_IN_COURSE,
                'grade': ['0.0', 'Not Attempted', '2.0', 'Not Attempted', '2.0'],
            },
            {
                'user': self.alpha_user,
                'enrollment_status': ENROLLED_IN_COURSE,
                'grade': ['1.0', '2.0', '2.0', 'Not Available', 'Not Available'],
            },
            {
                'user': self.beta_user,
                'enrollment_status': ENROLLED_IN_COURSE,
                'grade': ['0.5', 'Not Available', 'Not Available', '1.0', '2.0'],
            },
            {
                'user': self.non_cohorted_user,
                'enrollment_status': ENROLLED_IN_COURSE,
                'grade': ['0.0', 'Not Available', 'Not Available', 'Not Available', 'Not Available'],
            },
            {
                'user': self.community_ta,
                'enrollment_status': ENROLLED_IN_COURSE,
                'grade': ['0.0', 'Not Attempted', '2.0', 'Not Available', 'Not Available'],
            },
        ]

        # Verify generated grades and expected grades match
        expected_grades = [self._format_user_grade(header_row, **user_grade) for user_grade in user_grades]
        self.verify_rows_in_csv(expected_grades)


@ddt.ddt
class TestCourseSurveyReport(TestReportMixin, InstructorTaskCourseTestCase):
    """
    Tests that Course Survey report generation works.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

        self.question1 = "question1"
        self.question2 = "question2"
        self.question3 = "question3"
        self.answer1 = "answer1"
        self.answer2 = "answer2"
        self.answer3 = "answer3"

        self.student1 = UserFactory()
        self.student2 = UserFactory()

        self.test_survey_name = 'TestSurvey'
        self.test_form = '<input name="field1"></input>'
        self.survey_form = SurveyForm.create(self.test_survey_name, self.test_form)

        self.survey1 = SurveyAnswer.objects.create(user=self.student1, form=self.survey_form, course_key=self.course.id,
                                                   field_name=self.question1, field_value=self.answer1)
        self.survey2 = SurveyAnswer.objects.create(user=self.student1, form=self.survey_form, course_key=self.course.id,
                                                   field_name=self.question2, field_value=self.answer2)
        self.survey3 = SurveyAnswer.objects.create(user=self.student2, form=self.survey_form, course_key=self.course.id,
                                                   field_name=self.question1, field_value=self.answer3)
        self.survey4 = SurveyAnswer.objects.create(user=self.student2, form=self.survey_form, course_key=self.course.id,
                                                   field_name=self.question2, field_value=self.answer2)
        self.survey5 = SurveyAnswer.objects.create(user=self.student2, form=self.survey_form, course_key=self.course.id,
                                                   field_name=self.question3, field_value=self.answer1)

    def test_successfully_generate_course_survey_report(self):
        """
        Test that successfully generates the course survey report.
        """
        task_input = {'features': []}
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = upload_course_survey_report(
                None, None, self.course.id,
                task_input, 'generating course survey report'
            )
        assert_dict_contains_subset(self, {'attempted': 2, 'succeeded': 2, 'failed': 0}, result)

    def test_generate_course_survey_report(self):
        """
        test to generate course survey report
        and then test the report authenticity.
        """

        task_input = {'features': []}
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = upload_course_survey_report(
                None, None, self.course.id,
                task_input, 'generating course survey report'
            )

        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        header_row = ",".join(['User ID', 'User Name', 'Email', self.question1, self.question2, self.question3])
        student1_row = ",".join([
            str(self.student1.id),
            self.student1.username,
            self.student1.email,
            self.answer1,
            self.answer2
        ])
        student2_row = ",".join([
            str(self.student2.id),
            self.student2.username,
            self.student2.email,
            self.answer3,
            self.answer2,
            self.answer1
        ])
        expected_data = [header_row, student1_row, student2_row]

        assert_dict_contains_subset(self, {'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self._verify_csv_file_report(report_store, expected_data)

    def _verify_csv_file_report(self, report_store, expected_data):
        """
        Verify course survey data.
        """
        report_csv_filename = report_store.links_for(self.course.id)[0][0]
        report_path = report_store.path_to(self.course.id, report_csv_filename)
        with report_store.storage.open(report_path) as csv_file:
            csv_file_data = csv_file.read()
            # Removing unicode signature (BOM) from the beginning
            csv_file_data = csv_file_data.decode("utf-8-sig")
            for data in expected_data:
                assert data in csv_file_data


@ddt.ddt
class TestStudentReport(TestReportMixin, InstructorTaskCourseTestCase):
    """
    Tests that CSV student profile report generation works.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_success(self):
        self.create_student('student', 'student@example.com')
        task_input = {'features': []}
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = upload_students_csv(None, None, self.course.id, task_input, 'calculated')
        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        links = report_store.links_for(self.course.id)

        assert len(links) == 1
        assert_dict_contains_subset(self, {'attempted': 1, 'succeeded': 1, 'failed': 0}, result)

    def test_custom_directory(self):
        self.create_student('student', 'student@example.com')
        directory_name = 'test_dir'
        task_input = {'features': [], 'upload_parent_dir': directory_name}
        patched_upload = patch('lms.djangoapps.instructor_task.tasks_helper.enrollments.upload_csv_to_report_store')

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patched_upload as mock_upload_report:
                upload_students_csv(None, None, self.course.id, task_input, 'calculated')

        mock_upload_report.assert_called_once_with(
            [[], []],
            'student_profile_info',
            self.course.id,
            ANY,
            parent_dir=directory_name
        )

    def test_custom_filename(self):
        self.create_student('student', 'student@example.com')
        filename = "test_filename"
        task_input = {'features': [], 'filename': filename}
        patched_upload = patch('lms.djangoapps.instructor_task.tasks_helper.enrollments.upload_csv_to_report_store')

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patched_upload as mock_upload_report:
                upload_students_csv(None, None, self.course.id, task_input, 'calculated')

        mock_upload_report.assert_called_once_with([[], []], filename, self.course.id, ANY, parent_dir='')

    @ddt.data(['student', 'student\xec'])
    def test_unicode_usernames(self, students):
        """
        Test that students with unicode characters in their usernames
        are handled.
        """
        for i, student in enumerate(students):
            self.create_student(username=student, email=f'student{i}@example.com')

        self.current_task = Mock()  # pylint: disable=attribute-defined-outside-init
        self.current_task.update_state = Mock()
        task_input = {
            'features': [
                'id', 'username', 'name', 'email', 'language', 'location',
                'year_of_birth', 'gender', 'level_of_education', 'mailing_address',
                'goals'
            ]
        }
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = self.current_task
            result = upload_students_csv(None, None, self.course.id, task_input, 'calculated')
        # This assertion simply confirms that the generation completed with no errors
        num_students = len(students)
        assert_dict_contains_subset(self, {'attempted': num_students, 'succeeded': num_students, 'failed': 0}, result)


class TestTeamStudentReport(TestReportMixin, InstructorTaskCourseTestCase):
    """
    Test the student report when including teams information.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(teams_configuration=_TEAMS_CONFIG)
        self.student1 = UserFactory.create()
        CourseEnrollment.enroll(self.student1, self.course.id)
        self.student2 = UserFactory.create()
        CourseEnrollment.enroll(self.student2, self.course.id)

    def _generate_and_verify_teams_column(self, username, expected_team):
        """ Run the upload_students_csv task and verify that the correct team was added to the CSV. """
        current_task = Mock()
        current_task.update_state = Mock()
        task_input = {
            'features': [
                'id', 'username', 'name', 'email', 'language', 'location',
                'year_of_birth', 'gender', 'level_of_education', 'mailing_address',
                'goals', 'team'
            ]
        }
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = current_task
            result = upload_students_csv(None, None, self.course.id, task_input, 'calculated')
            assert_dict_contains_subset(self, {'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
            report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
            report_csv_filename = report_store.links_for(self.course.id)[0][0]
            report_path = report_store.path_to(self.course.id, report_csv_filename)
            found_user = False
            with report_store.storage.open(report_path) as csv_file:
                for row in unicodecsv.DictReader(csv_file):
                    if row.get('username') == username:
                        assert row['team'] == expected_team
                        found_user = True
            assert found_user

    def test_team_column_no_teams(self):
        self._generate_and_verify_teams_column(self.student1.username, UNAVAILABLE)
        self._generate_and_verify_teams_column(self.student2.username, UNAVAILABLE)

    def test_team_column_with_teams(self):
        team1 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team1, user=self.student1)
        team2 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team2, user=self.student2)
        self._generate_and_verify_teams_column(self.student1.username, team1.name)
        self._generate_and_verify_teams_column(self.student2.username, team2.name)

    def test_team_column_with_deleted_team(self):
        team1 = CourseTeamFactory.create(course_id=self.course.id)
        membership1 = CourseTeamMembershipFactory.create(team=team1, user=self.student1)
        team2 = CourseTeamFactory.create(course_id=self.course.id)
        CourseTeamMembershipFactory.create(team=team2, user=self.student2)

        team1.delete()
        membership1.delete()

        self._generate_and_verify_teams_column(self.student1.username, UNAVAILABLE)
        self._generate_and_verify_teams_column(self.student2.username, team2.name)


@ddt.ddt
class TestListMayEnroll(TestReportMixin, InstructorTaskCourseTestCase):
    """
    Tests that generation of CSV files containing information about
    students who may enroll in a given course (but have not signed up
    for it yet) works.
    """

    def _create_enrollment(self, email):
        """
        Factory method for creating CourseEnrollmentAllowed objects.
        """
        return CourseEnrollmentAllowed.objects.create(
            email=email, course_id=self.course.id
        )

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_success(self):
        self._create_enrollment('user@example.com')
        task_input = {'features': []}
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = upload_may_enroll_csv(None, None, self.course.id, task_input, 'calculated')
        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        links = report_store.links_for(self.course.id)

        assert len(links) == 1
        assert_dict_contains_subset(self, {'attempted': 1, 'succeeded': 1, 'failed': 0}, result)

    def test_unicode_email_addresses(self):
        """
        Test handling of unicode characters in email addresses of students
        who may enroll in a course.
        """
        enrollments = ['student@example.com', 'ni\xf1o@example.com']
        for email in enrollments:
            self._create_enrollment(email)

        task_input = {'features': ['email']}
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = upload_may_enroll_csv(None, None, self.course.id, task_input, 'calculated')
        # This assertion simply confirms that the generation completed with no errors
        num_enrollments = len(enrollments)
        assert_dict_contains_subset(
            self,
            {'attempted': num_enrollments, 'succeeded': num_enrollments, 'failed': 0},
            result
        )


class MockDefaultStorage:
    """Mock django's DefaultStorage"""

    def __init__(self):
        pass

    def open(self, file_name):
        """Mock out DefaultStorage.open with standard python open"""
        return open(file_name)  # lint-amnesty, pylint: disable=bad-option-value, open-builtin  # lint-amnesty, pylint: disable=consider-using-with


@patch('lms.djangoapps.instructor_task.tasks_helper.misc.DefaultStorage', new=MockDefaultStorage)
class TestCohortStudents(TestReportMixin, InstructorTaskCourseTestCase):
    """
    Tests that bulk student cohorting works.
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()
        self.cohort_1 = CohortFactory(course_id=self.course.id, name='Cohort 1')
        self.cohort_2 = CohortFactory(course_id=self.course.id, name='Cohort 2')
        self.student_1 = self.create_student(username='student_1\xec', email='student_1@example.com')
        self.student_2 = self.create_student(username='student_2', email='student_2@example.com')
        self.csv_header_row = [
            'Cohort Name', 'Exists', 'Learners Added', 'Learners Not Found',
            'Invalid Email Addresses', 'Preassigned Learners',
        ]

    def _cohort_students_and_upload(self, csv_data):
        """
        Call `cohort_students_and_upload` with a file generated from `csv_data`.
        """
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(csv_data.encode('utf-8'))
            temp_file.flush()
            with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
                return cohort_students_and_upload(None, None, self.course.id, {'file_name': temp_file.name}, 'cohorted')

    def test_username(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,,Cohort 1\n'
            'student_2,,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_email(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            ',student_1@example.com,Cohort 1\n'
            ',student_2@example.com,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_username_and_email(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,student_1@example.com,Cohort 1\n'
            'student_2,student_2@example.com,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_prefer_email(self):
        """
        Test that `cohort_students_and_upload` greedily prefers 'email' over
        'username' when identifying the user.  This means that if a correct
        email is present, an incorrect or non-matching username will simply be
        ignored.
        """
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,student_1@example.com,Cohort 1\n'  # valid username and email
            'Invalid,student_2@example.com,Cohort 2'      # invalid username, valid email
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_non_existent_user(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'Invalid,,Cohort 1\n'
        )
        assert_dict_contains_subset(self, {'total': 1, 'attempted': 1, 'succeeded': 0, 'failed': 1}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '0', 'Invalid', '', '']))),
            ],
            verify_order=False
        )

    def test_non_existent_cohort(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            ',student_1@example.com,Does Not Exist\n'
            'student_2,,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 1, 'failed': 1}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Does Not Exist', 'False', '0', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_preassigned_user(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            ',example_email@example.com,Cohort 1'
        )
        assert_dict_contains_subset(
            self,
            {'total': 1, 'attempted': 1, 'succeeded': 0, 'failed': 0},
            result,
        )
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '0', '', '', 'example_email@example.com']))),
            ],
            verify_order=False
        )

    def test_invalid_email(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            ',student_1@,Cohort 1\n'
        )
        assert_dict_contains_subset(self, {'total': 1, 'attempted': 1, 'succeeded': 0, 'failed': 1}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '0', '', 'student_1@', '']))),
            ],
            verify_order=False
        )

    def test_too_few_commas(self):
        """
        A CSV file may be malformed and lack trailing commas at the end of a row.
        In this case, those cells take on the value None by the CSV parser.
        Make sure we handle None values appropriately.

        i.e.:
            header_1,header_2,header_3
            val_1,val_2,val_3  <- good row
            val_1,,  <- good row
            val_1    <- bad row; no trailing commas to indicate empty rows
        """
        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,\n'
            'student_2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 0, 'failed': 2}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['', 'False', '0', '', '', '']))),
            ],
            verify_order=False
        )

    def test_only_header_row(self):
        result = self._cohort_students_and_upload(
            'username,email,cohort'
        )
        assert_dict_contains_subset(self, {'total': 0, 'attempted': 0, 'succeeded': 0, 'failed': 0}, result)
        self.verify_rows_in_csv([])

    def test_carriage_return(self):
        """
        Test that we can handle carriage returns in our file.
        """
        result = self._cohort_students_and_upload(
            'username,email,cohort\r'
            'student_1\xec,,Cohort 1\r'
            'student_2,,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_carriage_return_line_feed(self):
        """
        Test that we can handle carriage returns and line feeds in our file.
        """
        result = self._cohort_students_and_upload(
            'username,email,cohort\r\n'
            'student_1\xec,,Cohort 1\r\n'
            'student_2,,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_move_users_to_new_cohort(self):
        membership1 = CohortMembership(course_user_group=self.cohort_1, user=self.student_1)
        membership1.save()
        membership2 = CohortMembership(course_user_group=self.cohort_2, user=self.student_2)
        membership2.save()

        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,,Cohort 2\n'
            'student_2,,Cohort 1'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'succeeded': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '1', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '1', '', '', '']))),
            ],
            verify_order=False
        )

    def test_move_users_to_same_cohort(self):
        membership1 = CohortMembership(course_user_group=self.cohort_1, user=self.student_1)
        membership1.save()
        membership2 = CohortMembership(course_user_group=self.cohort_2, user=self.student_2)
        membership2.save()

        result = self._cohort_students_and_upload(
            'username,email,cohort\n'
            'student_1\xec,,Cohort 1\n'
            'student_2,,Cohort 2'
        )
        assert_dict_contains_subset(self, {'total': 2, 'attempted': 2, 'skipped': 2, 'failed': 0}, result)
        self.verify_rows_in_csv(
            [
                dict(list(zip(self.csv_header_row, ['Cohort 1', 'True', '0', '', '', '']))),
                dict(list(zip(self.csv_header_row, ['Cohort 2', 'True', '0', '', '', '']))),
            ],
            verify_order=False
        )


@ddt.ddt
@patch('lms.djangoapps.instructor_task.tasks_helper.misc.DefaultStorage', new=MockDefaultStorage)
@pytest.mark.usefixtures("override_descriptor_system")
class TestGradeReport(TestReportMixin, InstructorTaskModuleTestCase):
    """
    Test that grade report has correct grade values.
    """

    def setUp(self):
        super().setUp()
        self.create_course()
        self.student = self.create_student('üser_1')

    def create_course(self):
        """
        Creates a course with various subsections for testing
        """
        in_the_past = datetime.now(UTC) - timedelta(days=5)
        in_the_future = datetime.now(UTC) + timedelta(days=5)
        self.course = CourseFactory.create(
            grading_policy={
                "GRADER": [
                    {
                        "type": "Homework",
                        "min_count": 4,
                        "drop_count": 0,
                        "weight": 1.0
                    },
                ],
            },
            metadata={"start": in_the_past}
        )
        self.chapter = BlockFactory.create(parent=self.course, category='chapter')

        self.problem_section = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'},
            display_name='Subsection'
        )
        self.define_option_problem('Problem1', parent=self.problem_section)
        self.hidden_section = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'},
            visible_to_staff_only=True,
            display_name='Hidden',
        )
        self.define_option_problem('Problem2', parent=self.hidden_section)
        self.unattempted_section = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'},
            display_name='Unattempted',
        )
        self.define_option_problem('Problem3', parent=self.unattempted_section)
        self.empty_section = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'},
            display_name='Empty',
        )
        self.unreleased_section = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework', 'start': in_the_future},
            display_name='Unreleased'
        )
        self.define_option_problem('Unreleased', parent=self.unreleased_section)

    @patch.dict(settings.FEATURES, {'DISABLE_START_DATES': False})
    def test_grade_report(self):
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 1, 'succeeded': 1, 'failed': 0},
                result,
            )
            self.verify_rows_in_csv(
                [
                    {
                        'Student ID': str(self.student.id),
                        'Email': self.student.email,
                        'Username': self.student.username,
                        'Grade': '0.13',
                        'Homework 1: Subsection': '0.5',
                        'Homework 2: Unattempted': 'Not Attempted',
                        'Homework 3: Empty': 'Not Attempted',
                        'Homework 4: Unreleased': 'Not Attempted',
                        'Homework (Avg)': str(0.5 / 4),
                    },
                ],
                ignore_other_columns=True,
            )

    def test_grade_report_custom_directory(self):
        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])

        directory_name = "test_dir"
        task_input = {
            "upload_parent_dir": directory_name
        }

        patched_upload = patch('lms.djangoapps.instructor_task.tasks_helper.grades.upload_csv_to_report_store')
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            with patched_upload as mock_upload_report:
                CourseGradeReport.generate(None, None, self.course.id, task_input, 'graded')

        mock_upload_report.assert_called_once_with(
            [ANY, ANY],
            'grade_report',
            self.course.id,
            ANY,
            parent_dir=directory_name
        )

    def test_grade_report_with_overrides(self):
        course_data = CourseData(self.student, course=self.course)
        subsection_grade = CreateSubsectionGrade(self.unattempted_section, course_data.structure, {}, {})
        grade_model = subsection_grade.update_or_create_model(self.student, force_update_subsections=True)

        _ = PersistentSubsectionGradeOverride.update_or_create_override(
            self.student,
            grade_model,
            earned_graded_override=2.0,
        )

        self.addCleanup(grade_model.delete)

        self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 1, 'succeeded': 1, 'failed': 0},
                result,
            )
            self.verify_rows_in_csv(
                [
                    {
                        'Student ID': str(self.student.id),
                        'Email': self.student.email,
                        'Username': self.student.username,
                        'Grade': '0.38',
                        'Homework 1: Subsection': '0.5',
                        'Homework 2: Unattempted': '1.0',
                        'Homework 3: Empty': 'Not Attempted',
                        'Homework 4: Unreleased': 'Not Attempted',
                        'Homework (Avg)': str(1.5 / 4),
                    },
                ],
                ignore_other_columns=True,
            )

    @patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
    def test_course_grade_with_verified_student_only(self, _get_current_task):
        """
        Tests that course grade report has expected data when it is generated only for
        verified learners.
        """
        with patch(
            'lms.djangoapps.instructor_task.tasks_helper.grades.course_grade_report_verified_only',
            return_value=True,
        ):
            student_1 = self.create_student('user_honor')
            student_verified = self.create_student('user_verified', mode='verified')
            vertical = BlockFactory.create(
                parent_location=self.problem_section.location,
                category='vertical',
                metadata={'graded': True},
                display_name='Problem Vertical'
            )
            self.define_option_problem('Problem4', parent=vertical)

            self.submit_student_answer(student_1.username, 'Problem4', ['Option 1'])
            self.submit_student_answer(student_verified.username, 'Problem4', ['Option 1'])
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert_dict_contains_subset(
                self,
                {'action_name': 'graded', 'attempted': 1, 'succeeded': 1, 'failed': 0},
                result,
            )

    @ddt.data(True, False)
    def test_fast_generation(self, create_non_zero_grade):
        if create_non_zero_grade:
            self.submit_student_answer(self.student.username, 'Problem1', ['Option 1'])
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'), \
                patch('lms.djangoapps.grades.course_data.get_course_blocks') as mock_course_blocks, \
                patch('lms.djangoapps.grades.subsection_grade.get_score') as mock_get_score:
            CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            assert not mock_course_blocks.called
            assert not mock_get_score.called


@ddt.ddt
@patch('lms.djangoapps.instructor_task.tasks_helper.misc.DefaultStorage', new=MockDefaultStorage)
class TestGradeReportEnrollmentAndCertificateInfo(TestReportMixin, InstructorTaskModuleTestCase):
    """
    Test that grade report has correct user enrollment, verification, and certificate information.
    """

    def setUp(self):
        super().setUp()

        today = datetime.now(UTC)
        course_factory_kwargs = {
            'start': today - timedelta(days=30),
            'end': today - timedelta(days=2),
            'certificate_available_date': today - timedelta(days=1)
        }

        self.initialize_course(course_factory_kwargs)

        self.create_problem()

        self.columns_to_check = [
            'Enrollment Track',
            'Verification Status',
            'Certificate Eligible',
            'Certificate Delivered',
            'Certificate Type'
        ]

    def create_problem(self, problem_display_name='test_problem', parent=None):
        """
        Create a multiple choice response problem.
        """
        if parent is None:
            parent = self.problem_section

        factory = MultipleChoiceResponseXMLFactory()
        args = {'choices': [False, True, False]}
        problem_xml = factory.build_xml(**args)
        BlockFactory.create(
            parent_location=parent.location,
            parent=parent,
            category="problem",
            display_name=problem_display_name,
            data=problem_xml
        )

    def _verify_csv_data(self, username, expected_data):
        """
        Verify grade report data.
        """
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
            report_csv_filename = report_store.links_for(self.course.id)[0][0]
            report_path = report_store.path_to(self.course.id, report_csv_filename)
            found_user = False
            with report_store.storage.open(report_path) as csv_file:
                for row in unicodecsv.DictReader(csv_file):
                    if row.get('Username') == username:
                        csv_row_data = [row[column] for column in self.columns_to_check]
                        assert csv_row_data == expected_data
                        found_user = True
            assert found_user

    def _create_user_data(self,
                          user_enroll_mode,
                          has_passed,
                          verification_status,
                          certificate_status,
                          certificate_mode):
        """
        Create user data to be used during grade report generation.
        """

        user = self.create_student('u1', mode=user_enroll_mode)

        if has_passed:
            self.submit_student_answer('u1', 'test_problem', ['choice_1'])

        CertificateAllowlistFactory.create(user=user, course_id=self.course.id)

        if user_enroll_mode in CourseMode.VERIFIED_MODES:
            SoftwareSecurePhotoVerificationFactory.create(user=user, status=verification_status)

        GeneratedCertificateFactory.create(
            user=user,
            course_id=self.course.id,
            status=certificate_status,
            mode=certificate_mode
        )

        return user

    @ddt.data(
        (
            'verified', False, 'approved', 'notpassing', 'honor',
            ['verified', 'ID Verified', 'Y', 'N', 'N/A']
        ),
        (
            'verified', False, 'approved', 'downloadable', 'verified',
            ['verified', 'ID Verified', 'Y', 'Y', 'verified']
        ),
        (
            'honor', True, 'approved', 'restricted', 'honor',
            ['honor', 'N/A', 'Y', 'N', 'N/A']
        ),
        (
            'verified', True, 'must_retry', 'downloadable', 'honor',
            ['verified', 'Not ID Verified', 'Y', 'Y', 'honor']
        ),
    )
    @ddt.unpack
    def test_grade_report_enrollment_and_certificate_info(
            self,
            user_enroll_mode,
            has_passed,
            verification_status,
            certificate_status,
            certificate_mode,
            expected_output
    ):

        user = self._create_user_data(
            user_enroll_mode,
            has_passed,
            verification_status,
            certificate_status,
            certificate_mode
        )

        self._verify_csv_data(user.username, expected_output)


@ddt.ddt
@override_settings(CERT_QUEUE='test-queue')
class TestCertificateGeneration(InstructorTaskModuleTestCase):
    """
    Test certificate generation task works.
    """

    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super().setUp()
        self.initialize_course()

    def test_certificate_generation_for_students(self):
        """
        Verify that certificates generated for all eligible students enrolled in a course.
        """
        # Create 10 students
        students = self._create_students(10)

        # Grant 2 students downloadable certs
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable,
                mode=CourseMode.VERIFIED
            )

        # Allowlist 5 students
        for student in students[2:7]:
            CertificateAllowlistFactory.create(user=student, course_id=self.course.id,)

        task_input = {'student_set': None}
        expected_results = {
            'action_name': 'certificates generated',
            'total': 10,
            'attempted': 8,
            'succeeded': 0,
            'failed': 0,
            'skipped': 2
        }
        with self.assertNumQueries(61):
            self.assertCertificatesGenerated(task_input, expected_results)

    @ddt.data(
        CertificateStatuses.downloadable,
        CertificateStatuses.generating,
        CertificateStatuses.notpassing,
        CertificateStatuses.audit_passing,
    )
    def test_certificate_generation_all_allowlisted(self, status):
        """
        Verify that certificates are generated for all allowlisted students,
        whether or not they already had certs generated for them.
        """
        # Create 5 students
        students = self._create_students(5)

        # Allowlist 3 students
        for student in students[:3]:
            CertificateAllowlistFactory.create(
                user=student, course_id=self.course.id
            )

        # Grant certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=status,
            )

        task_input = {'student_set': 'all_allowlisted'}

        # Only certificates for the 3 allowlisted students should have been run
        expected_results = {
            'action_name': 'certificates generated',
            'total': 3,
            'attempted': 3,
            'succeeded': 0,
            'failed': 0,
            'skipped': 0
        }
        self.assertCertificatesGenerated(task_input, expected_results)

    @ddt.data(
        (CertificateStatuses.downloadable, 2),
        (CertificateStatuses.generating, 2),
        (CertificateStatuses.notpassing, 4),
        (CertificateStatuses.audit_passing, 4),
    )
    @ddt.unpack
    def test_certificate_generation_allowlist_not_generated(self, status, expected_certs):
        """
        Verify that certificates are generated only for those students
        who do not have `downloadable` or `generating` certificates.
        """
        # Create 5 students
        students = self._create_students(5)

        # Grant certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=status,
            )

        # Allowlist 4 students
        for student in students[:4]:
            CertificateAllowlistFactory.create(
                user=student, course_id=self.course.id
            )

        task_input = {'student_set': 'allowlisted_not_generated'}

        # Certificates should only be generated for the allowlisted students
        # who do not yet have passing certificates.
        expected_results = {
            'action_name': 'certificates generated',
            'total': expected_certs,
            'attempted': expected_certs,
            'succeeded': 0,
            'failed': 0,
            'skipped': 0
        }
        self.assertCertificatesGenerated(
            task_input,
            expected_results
        )

    def test_certificate_generation_specific_student(self):
        """
        Tests generating a certificate for a specific student.
        """
        student = self.create_student(username="Hamnet", email="ham@ardenforest.co.uk")
        CertificateAllowlistFactory.create(user=student, course_id=self.course.id)
        task_input = {
            'student_set': 'specific_student',
            'specific_student_id': student.id
        }
        expected_results = {
            'action_name': 'certificates generated',
            'total': 1,
            'attempted': 1,
            'succeeded': 0,
            'failed': 0,
            'skipped': 0,
        }
        self.assertCertificatesGenerated(task_input, expected_results)

    def test_specific_student_not_enrolled(self):
        """
        Tests generating a certificate for a specific student if that student
        is not enrolled in the course.
        """
        student = self.create_student(username="jacques", email="antlers@ardenforest.co.uk")
        task_input = {
            'student_set': 'specific_student',
            'specific_student_id': student.id
        }
        expected_results = {
            'action_name': 'certificates generated',
            'total': 1,
            'attempted': 1,
            'succeeded': 0,
            'failed': 0,
            'skipped': 0,
        }
        self.assertCertificatesGenerated(task_input, expected_results)

    def test_certificate_regeneration_for_statuses_to_regenerate(self):
        """
        Verify that certificates are regenerated for all eligible students enrolled in a course whose generated
        certificate statuses lies in the list 'statuses_to_regenerate' given in task_input.
        """
        # Create 10 students
        students = self._create_students(10)

        # Grant downloadable certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable,
                mode=CourseMode.VERIFIED
            )

        # Grant error certs to 3 students
        for student in students[2:5]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.error,
                mode=CourseMode.VERIFIED
            )

        # Grant a deleted cert to the 6th student
        for student in students[5:6]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.deleted,
                mode=CourseMode.VERIFIED
            )

        # Allowlist 7 students
        for student in students[:7]:
            CertificateAllowlistFactory.create(user=student, course_id=self.course.id)

        # Certificates should be regenerated for students having generated certificates with status
        # 'downloadable' or 'error' which are total of 5 students in this test case
        task_input = {'statuses_to_regenerate': [CertificateStatuses.downloadable, CertificateStatuses.error]}

        expected_results = {
            'action_name': 'certificates generated',
            'total': 10,
            'attempted': 5,
            'succeeded': 0,
            'failed': 0,
            'skipped': 5
        }

        self.assertCertificatesGenerated(
            task_input,
            expected_results
        )

    def test_certificate_regeneration_with_expected_failures(self):
        """
        Verify that certificates are regenerated for all eligible students enrolled in a course whose generated
        certificate statuses lies in the list 'statuses_to_regenerate' given in task_input.
        """
        # Default grade for students
        default_grade = '-1'

        # Create 10 students
        students = self._create_students(10)

        # Grant downloadable certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant error certs to 3 students
        for student in students[2:5]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.error,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant a deleted cert to the 6th student
        for student in students[5:6]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.deleted,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant generating certs to 4 students
        for student in students[6:]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.generating,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Allowlist 7 students
        for student in students[:7]:
            CertificateAllowlistFactory.create(user=student, course_id=self.course.id)

        # Regenerated certificates for students having generated certificates with status
        # 'deleted' or 'generating'
        task_input = {'statuses_to_regenerate': [CertificateStatuses.deleted, CertificateStatuses.generating]}

        expected_results = {
            'action_name': 'certificates generated',
            'total': 10,
            'attempted': 5,
            'succeeded': 0,
            'failed': 0,
            'skipped': 5
        }

        self.assertCertificatesGenerated(task_input, expected_results)

    def test_certificate_regeneration_with_existing_unavailable_status(self):
        """
        Verify that certificates are regenerated for all eligible students enrolled in a course whose generated
        certificate status lies in the list 'statuses_to_regenerate' given in task_input. but the 'unavailable'
        status is not touched if it is not in the 'statuses_to_regenerate' list.
        """
        # Default grade for students
        default_grade = '-1'

        # Create 10 students
        students = self._create_students(10)

        # Grant downloadable certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant error certs to 3 students
        for student in students[2:5]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.error,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant unavailable certs to 2 students
        for student in students[5:7]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.unavailable,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Grant generating certs to 3 students
        for student in students[7:]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.generating,
                mode=CourseMode.VERIFIED,
                grade=default_grade
            )

        # Allowlist all students
        for student in students[:]:
            CertificateAllowlistFactory.create(user=student, course_id=self.course.id)

        # Regenerated certificates for students having generated certificates with status
        # 'downloadable', 'error' or 'generating'
        task_input = {
            'statuses_to_regenerate': [
                CertificateStatuses.downloadable,
                CertificateStatuses.error,
                CertificateStatuses.generating
            ]
        }

        expected_results = {
            'action_name': 'certificates generated',
            'total': 10,
            'attempted': 8,
            'succeeded': 0,
            'failed': 0,
            'skipped': 2
        }

        self.assertCertificatesGenerated(
            task_input,
            expected_results
        )

    def test_certificate_regeneration_for_students(self):
        """
        Verify that certificates are regenerated for all students passed in task_input.
        """
        # Create 10 students
        students = self._create_students(10)

        # Grant downloadable certs to 2 students
        for student in students[:2]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable,
                mode=CourseMode.VERIFIED
            )

        # Grant error certs to 3 students
        for student in students[2:5]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.error,
                mode=CourseMode.VERIFIED
            )

        # Grant a deleted cert to the 6th student
        for student in students[5:6]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.deleted,
                mode=CourseMode.VERIFIED
            )

        # Grant a notpassing cert to the 7th student
        for student in students[6:7]:
            GeneratedCertificateFactory.create(
                user=student,
                course_id=self.course.id,
                status=CertificateStatuses.notpassing,
                mode=CourseMode.VERIFIED
            )

        # Allowlist 7 students
        for student in students[:7]:
            CertificateAllowlistFactory.create(user=student, course_id=self.course.id)

        # Certificates should be regenerated for students having generated certificates with status
        # 'downloadable' or 'error' which are total of 5 students in this test case
        task_input = {'student_set': "all_allowlisted"}

        expected_results = {
            'action_name': 'certificates generated',
            'total': 7,
            'attempted': 7,
            'succeeded': 0,
            'failed': 0,
            'skipped': 0,
        }

        self.assertCertificatesGenerated(task_input, expected_results)

    def assertCertificatesGenerated(self, task_input, expected_results):
        """
        Generate certificates for the given task_input and compare with expected_results.
        """
        current_task = Mock()
        current_task.update_state = Mock()

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = current_task
            with patch('xmodule.capa.xqueue_interface.XQueueInterface.send_to_queue') as mock_queue:
                mock_queue.return_value = (0, "Successfully queued")
                result = generate_students_certificates(
                    None, None, self.course.id, task_input, 'certificates generated'
                )

        assert_dict_contains_subset(
            self,
            expected_results,
            result,
        )

    def _create_students(self, number_of_students):
        """
        Create Students for course.
        """
        return [
            self.create_student(
                username=f'student_{index}',
                email=f'student_{index}@example.com'
            )
            for index in range(number_of_students)
        ]


@ddt.ddt
class TestInstructorOra2Report(SharedModuleStoreTestCase):
    """
    Tests that ORA2 response report generation works.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        self.current_task = Mock()
        self.current_task.update_state = Mock()

    def tearDown(self):
        super().tearDown()
        if os.path.exists(settings.GRADES_DOWNLOAD['ROOT_PATH']):
            shutil.rmtree(settings.GRADES_DOWNLOAD['ROOT_PATH'])

    @ddt.data(
        ('lms.djangoapps.instructor_task.tasks_helper.misc.OraAggregateData.collect_ora2_data', upload_ora2_data),
        ('lms.djangoapps.instructor_task.tasks_helper.misc.OraAggregateData.collect_ora2_summary', upload_ora2_summary),
    )
    @ddt.unpack
    def test_report_fails_if_error(self, data_collector_module, upload_func):
        with patch(data_collector_module) as mock_collect_data:
            mock_collect_data.side_effect = KeyError

            with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
                mock_current_task.return_value = self.current_task

                response = upload_func(None, None, self.course.id, None, 'generated')
                self.assertEqual(response, UPDATE_STATUS_FAILED)

    def test_report_stores_results(self):
        with ExitStack() as stack:
            stack.enter_context(freeze_time('2001-01-01 00:00:00'))

            mock_current_task = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
            )
            mock_collect_data = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraAggregateData.collect_ora2_data')
            )
            mock_store_rows = stack.enter_context(
                patch('lms.djangoapps.instructor_task.models.DjangoStorageReportStore.store_rows')
            )

            mock_current_task.return_value = self.current_task

            test_header = ['field1', 'field2']
            test_rows = [['row1_field1', 'row1_field2'], ['row2_field1', 'row2_field2']]

            mock_collect_data.return_value = (test_header, test_rows)

            return_val = upload_ora2_data(None, None, self.course.id, None, 'generated')

            timestamp_str = datetime.now(UTC).strftime('%Y-%m-%d-%H%M')
            key = self.course.id
            filename = f'{key.org}_{key.course}_{key.run}_ORA_data_{timestamp_str}.csv'

            assert return_val == UPDATE_STATUS_SUCCEEDED
            mock_store_rows.assert_called_once_with(self.course.id, filename, [test_header] + test_rows, '')


class TestInstructorOra2AttachmentsExport(SharedModuleStoreTestCase):
    """
    Tests that ORA2 submission files export works.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        self.current_task = Mock()
        self.current_task.update_state = Mock()

    def test_export_fails_if_error_on_collect_step(self):
        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = self.current_task

            with patch(
                'lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.collect_ora2_submission_files'
            ) as mock_collect_data:
                mock_collect_data.side_effect = KeyError

                response = upload_ora2_submission_files(None, None, self.course.id, None, 'compressed')
                assert response == UPDATE_STATUS_FAILED

    def test_summary_report_stores_results(self):
        with freeze_time('2001-01-01 00:00:00'):
            test_header = ['field1', 'field2']
            test_rows = [['row1_field1', 'row1_field2'], ['row2_field1', 'row2_field2']]

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_current_task:
            mock_current_task.return_value = self.current_task

            with patch(
                'lms.djangoapps.instructor_task.tasks_helper.misc.OraAggregateData.collect_ora2_summary'
            ) as mock_collect_summary:
                mock_collect_summary.return_value = (test_header, test_rows)
                with patch(
                    'lms.djangoapps.instructor_task.models.DjangoStorageReportStore.store_rows'
                ) as mock_store_rows:
                    return_val = upload_ora2_summary(None, None, self.course.id, None, 'generated')

                    timestamp_str = datetime.now(UTC).strftime('%Y-%m-%d-%H%M')
                    key = self.course.id
                    filename = f'{key.org}_{key.course}_{key.run}_ORA_summary_{timestamp_str}.csv'

                    self.assertEqual(return_val, UPDATE_STATUS_SUCCEEDED)
                    mock_store_rows.assert_called_once_with(self.course.id, filename, [test_header] + test_rows, '')

    def test_export_fails_if_error_on_create_zip_step(self):
        with ExitStack() as stack:
            mock_current_task = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
            )
            mock_current_task.return_value = self.current_task

            stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.collect_ora2_submission_files')
            )
            create_zip_mock = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.create_zip_with_attachments')
            )

            create_zip_mock.side_effect = KeyError

            response = upload_ora2_submission_files(None, None, self.course.id, None, 'compressed')
            assert response == UPDATE_STATUS_FAILED

    def test_export_fails_if_error_on_upload_step(self):
        with ExitStack() as stack:
            mock_current_task = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
            )
            mock_current_task.return_value = self.current_task

            stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.collect_ora2_submission_files')
            )
            stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.create_zip_with_attachments')
            )
            upload_mock = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.upload_zip_to_report_store')
            )

            upload_mock.side_effect = KeyError

            response = upload_ora2_submission_files(None, None, self.course.id, None, 'compressed')
            assert response == UPDATE_STATUS_FAILED

    def test_task_stores_zip_with_attachments(self):
        with ExitStack() as stack:
            mock_current_task = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task')
            )
            mock_collect_files = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.collect_ora2_submission_files')
            )
            mock_create_zip = stack.enter_context(
                patch('lms.djangoapps.instructor_task.tasks_helper.misc.OraDownloadData.create_zip_with_attachments')
            )
            mock_store = stack.enter_context(
                patch('lms.djangoapps.instructor_task.models.DjangoStorageReportStore.store')
            )

            mock_current_task.return_value = self.current_task

            response = upload_ora2_submission_files(None, None, self.course.id, None, 'compressed')

            mock_collect_files.assert_called_once()
            mock_create_zip.assert_called_once()
            mock_store.assert_called_once()

            assert response == UPDATE_STATUS_SUCCEEDED
