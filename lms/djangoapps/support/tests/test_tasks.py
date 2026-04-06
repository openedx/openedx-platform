"""
Unit tests for reset_student_course task
"""

from unittest.mock import Mock, call, patch

from django.conf import settings
from django.core import mail

from common.djangoapps.student.models.course_enrollment import CourseEnrollment
from common.djangoapps.student.roles import SupportStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tests.test_submitting_problems import TestSubmittingProblems
from lms.djangoapps.support.models import CourseResetAudit
from lms.djangoapps.support.tasks import reset_student_course
from lms.djangoapps.support.tests.factories import CourseResetAuditFactory, CourseResetCourseOptInFactory
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from xmodule.modulestore.tests.factories import BlockFactory
from xmodule.video_block import VideoBlock


class ResetStudentCourse(TestSubmittingProblems):
    """ Test reset_student_course task """
    USERNAME = "support"
    EMAIL = "support@example.com"
    PASSWORD = "support"

    def setUp(self):
        """
        Set permissions, create a course and learner, enroll learner and opt into course reset
        """
        super().setUp()
        self.user = UserFactory(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        SupportStaffRole().add_users(self.user)
        self.course_id = str(self.course.id)
        self.enrollment = CourseEnrollment.objects.filter(user=self.student_user, course_id=self.course.id).first()
        self.opt_in = CourseResetCourseOptInFactory.create(course_id=self.course.id)
        self.audit = CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            reset_by=self.user,
            status=CourseResetAudit.CourseResetStatus.ENQUEUED
        )
        self.p1 = ''
        self.p2 = ''
        self.p3 = ''
        self.video = ''

        # Patch BlockCompletion for the whole test
        completion_patcher = patch('lms.djangoapps.support.tasks.BlockCompletion')
        self.mock_block_completion = completion_patcher.start()
        self.addCleanup(completion_patcher.stop)

        # Patch clear_user_course_grades for the whole test
        grades_patcher = patch('lms.djangoapps.support.tasks.clear_user_course_grades')
        self.mock_clear_user_course_grades = grades_patcher.start()
        self.addCleanup(grades_patcher.stop)

    @property
    def mock_clear_block_completion(self):
        """ Helper property to access the two-mock-layers-deep clear_learning_context_completion """
        return self.mock_block_completion.objects.clear_learning_context_completion

    def basic_setup(self):
        """
        Set up a simple course for testing basic grading functionality.
        """
        grading_policy = {
            "GRADER": [{
                "type": "Homework",
                "min_count": 1,
                "drop_count": 0,
                "short_label": "HW",
                "weight": 1.0
            }],
            "GRADE_CUTOFFS": {
                'A': .9,
                'B': .33
            }
        }
        self.add_grading_policy(grading_policy)

        # set up a simple course with four problems
        homework = self.add_graded_section_to_course('homework')
        vertical = BlockFactory.create(
            parent_location=homework.location,
            category='vertical',
            display_name='Unit 1',
        )

        self.p1 = self.add_dropdown_to_section(vertical.location, 'p1', 1)
        self.p2 = self.add_dropdown_to_section(vertical.location, 'p2', 1)
        self.p3 = self.add_dropdown_to_section(vertical.location, 'p3', 1)
        video_sample_xml = """
        <video display_name="Test Video"
                youtube="1.0:p2Q6BrNhdh8,0.75:izygArpw-Qo,1.25:1EeWXzPdhSA,1.5:rABDYkeK0x8"
                show_captions="false"
                from="1.0"
                to="60.0">
            <source src="http://www.example.com/file.mp4"/>
            <track src="http://www.example.com/track"/>
        </video>
        """
        video_data = VideoBlock.parse_video_xml(video_sample_xml)
        video_data.pop('source')
        self.video = BlockFactory.create(
            category='video',
            parent_location=vertical.location,
            **video_data
        )

        self.refresh_course()

    def assert_email_sent_successfully(self, expected):
        """
        Verify that the course reset email has been sent to the user.
        """
        from_email = configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
        sent_message = mail.outbox[-1]
        body = sent_message.body

        assert expected['subject'] in sent_message.subject
        assert expected['body'] in body
        assert sent_message.from_email == from_email
        assert len(sent_message.to) == 1
        assert self.student_user.email in sent_message.to

    def test_reset_student_course(self):
        """ Test that it resets student attempts  """
        with patch(
                'lms.djangoapps.support.tasks.reset_student_attempts',
        ) as mock_reset_student_attempts:
            self.basic_setup()
            reset_student_course(self.course_id, self.student_user.email, self.user.email)

            mock_reset_student_attempts.assert_has_calls([
                call(
                    self.course.id,
                    self.student_user,
                    self.p1.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                ),
                call(
                    self.course.id,
                    self.student_user,
                    self.p2.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                ),
                call(
                    self.course.id,
                    self.student_user,
                    self.p3.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                ),
                call(
                    self.course.id,
                    self.student_user,
                    self.video.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                )
            ])

            self.mock_clear_block_completion.assert_called_once_with(self.student_user, self.course.id)
            self.mock_clear_user_course_grades.assert_called_once_with(self.student_user.id, self.course.id)
            course_reset_audit = CourseResetAudit.objects.get(course_enrollment=self.enrollment)
            self.assertIsNotNone(course_reset_audit.completed_at)
            self.assertEqual(course_reset_audit.status, CourseResetAudit.CourseResetStatus.COMPLETE)
            self.assert_email_sent_successfully({
                'subject': f'The course { self.course.display_name } has been reset !',
                'body': f'Your progress in course { self.course.display_name } has been reset on your behalf.'
            })

    def test_reset_student_course_student_module_not_found(self):

        with patch(
                'lms.djangoapps.support.tasks.reset_student_attempts',
                Mock(side_effect=StudentModule.DoesNotExist())
        ) as mock_reset_student_attempts:
            self.basic_setup()
            reset_student_course(self.course_id, self.student_user.email, self.user.email)
            mock_reset_student_attempts.assert_has_calls([
                call(
                    self.course.id,
                    self.student_user,
                    self.p1.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                ),
                call(
                    self.course.id,
                    self.student_user,
                    self.p2.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                ),
                call(
                    self.course.id,
                    self.student_user,
                    self.p3.location,
                    self.user,
                    delete_module=True,
                    emit_signals_and_events=False,
                )
            ])

            self.mock_clear_block_completion.assert_called_once_with(self.student_user, self.course.id)
            self.mock_clear_user_course_grades.assert_called_once_with(self.student_user.id, self.course.id)
            course_reset_audit = CourseResetAudit.objects.get(course_enrollment=self.enrollment)
            self.assertRaises(StudentModule.DoesNotExist, mock_reset_student_attempts)
            self.assertIsNotNone(course_reset_audit.completed_at)
            self.assertEqual(course_reset_audit.status, CourseResetAudit.CourseResetStatus.COMPLETE)

    @patch('lms.djangoapps.support.tasks.reset_student_attempts')
    def test_reset_student_course_fail(self, mock_reset_student_attempts):
        with patch(
                'lms.djangoapps.support.tasks.get_blocks',
                Mock(side_effect=Exception())
        ):
            reset_student_course(self.course_id, self.student_user.email, self.user.email)
            mock_reset_student_attempts.assert_not_called()
            self.mock_clear_block_completion.assert_not_called()
            self.mock_clear_user_course_grades.assert_not_called()
            course_reset_audit = CourseResetAudit.objects.get(course_enrollment=self.enrollment)
            self.assertIsNone(course_reset_audit.completed_at)
            self.assertEqual(course_reset_audit.status, CourseResetAudit.CourseResetStatus.FAILED)

    def test_reset_student_attempts_raise_exception(self):
        with patch(
                'lms.djangoapps.support.tasks.reset_student_attempts',
                Mock(side_effect=Exception())
        ) as mock_reset_student_attempts:
            self.basic_setup()
            reset_student_course(self.course_id, self.student_user.email, self.user.email)
            mock_reset_student_attempts.assert_called_once()
            self.mock_clear_block_completion.assert_not_called()
            self.mock_clear_user_course_grades.assert_not_called()
            course_reset_audit = CourseResetAudit.objects.get(course_enrollment=self.enrollment)
            self.assertIsNone(course_reset_audit.completed_at)
            self.assertEqual(course_reset_audit.status, CourseResetAudit.CourseResetStatus.FAILED)
