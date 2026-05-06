"""
Tests for the InstructorService
"""
import json
from unittest import mock

import pytest
from django.core.exceptions import ObjectDoesNotExist
from opaque_keys import InvalidKeyError

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor.access import allow_access
from lms.djangoapps.instructor.services import InstructorService
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class InstructorServiceTests(SharedModuleStoreTestCase):
    """
    Tests for the InstructorService
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.email = 'escalation@test.com'
        cls.course = CourseFactory.create(proctoring_escalation_email=cls.email)
        cls.section = BlockFactory.create(parent=cls.course, category='chapter')
        cls.subsection = BlockFactory.create(parent=cls.section, category='sequential')
        cls.unit = BlockFactory.create(parent=cls.subsection, category='vertical')
        cls.problem = BlockFactory.create(parent=cls.unit, category='problem')
        cls.unit_2 = BlockFactory.create(parent=cls.subsection, category='vertical')
        cls.problem_2 = BlockFactory.create(parent=cls.unit_2, category='problem')
        cls.complete_error_prefix = ('Error occurred while attempting to complete student attempt for '
                                     'user {user} for content_id {content_id}. ')

    def setUp(self):
        super().setUp()

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.service = InstructorService()
        self.module_to_reset = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem.location,
            state=json.dumps({'attempts': 2}),
        )

    @mock.patch('lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send')
    @mock.patch('lms.djangoapps.instructor.tasks.update_exam_completion_task.apply_async', autospec=True)
    def test_reset_student_attempts_delete(self, mock_completion_task, _mock_signal):  # noqa: PT019
        """
        Test delete student state.
        """

        # make sure the attempt is there
        assert StudentModule.objects.filter(student=self.module_to_reset.student, course_id=self.course.id,
                                            module_state_key=self.module_to_reset.module_state_key).count() == 1

        self.service.delete_student_attempt(
            self.student.username,
            str(self.course.id),
            str(self.subsection.location),
            requesting_user=self.student,
        )

        # make sure the module has been deleted
        assert StudentModule.objects.filter(student=self.module_to_reset.student, course_id=self.course.id,
                                            module_state_key=self.module_to_reset.module_state_key).count() == 0

        # Assert we send update completion with 0.0
        mock_completion_task.assert_called_once_with((self.student.username, str(self.subsection.location), 0.0))

    def test_reset_bad_content_id(self):
        """
        Negative test of trying to reset attempts with bad content_id
        """

        result = self.service.delete_student_attempt(  # pylint: disable=assignment-from-none
            self.student.username,
            str(self.course.id),
            'foo/bar/baz',
            requesting_user=self.student,
        )
        assert result is None

    def test_reset_bad_user(self):
        """
        Negative test of trying to reset attempts with bad user identifier
        """

        result = self.service.delete_student_attempt(  # pylint: disable=assignment-from-none
            'bad_student',
            str(self.course.id),
            'foo/bar/baz',
            requesting_user=self.student,
        )
        assert result is None

    def test_reset_non_existing_attempt(self):
        """
        Negative test of trying to reset attempts with bad user identifier
        """

        result = self.service.delete_student_attempt(  # pylint: disable=assignment-from-none
            self.student.username,
            str(self.course.id),
            str(self.problem_2.location),
            requesting_user=self.student,
        )
        assert result is None

    @mock.patch('lms.djangoapps.instructor.tasks.update_exam_completion_task.apply_async', autospec=True)
    def test_complete_student_attempt_success(self, mock_completion_task):
        """
        Assert update_exam_completion task is triggered
        """
        self.service.complete_student_attempt(self.student.username, str(self.subsection.location))
        mock_completion_task.assert_called_once_with((self.student.username, str(self.subsection.location), 1.0))

    def test_is_user_staff(self):
        """
        Test to assert that the user is staff or not
        """
        result = self.service.is_course_staff(
            self.student,
            str(self.course.id)
        )
        assert not result

        # allow staff access to the student
        allow_access(self.course, self.student, 'staff')
        result = self.service.is_course_staff(
            self.student,
            str(self.course.id)
        )
        assert result

    def test_report_suspicious_attempt(self):
        """
        Test to verify that the create_zendesk_ticket() is called
        """
        requester_name = "edx-proctoring"
        email = "edx-proctoring@edx.org"
        subject = "Proctored Exam Review: {review_status}".format(review_status="Suspicious")

        body = "A proctored exam attempt for {exam_name} in {course_name} by username: {student_username} was " \
               "reviewed as {review_status} by the proctored exam review provider.\n" \
               "Review link: {url}"
        args = {
            'exam_name': 'test_exam',
            'student_username': 'test_student',
            'url': 'not available',
            'course_name': self.course.display_name,
            'review_status': 'Suspicious',
        }
        expected_body = body.format(**args)
        tags = ["proctoring"]

        with mock.patch("lms.djangoapps.instructor.services.create_zendesk_ticket") as mock_create_zendesk_ticket:
            self.service.send_support_notification(
                course_id=str(self.course.id),
                exam_name=args['exam_name'],
                student_username=args["student_username"],
                review_status="Suspicious",
                review_url=None,
            )

        mock_create_zendesk_ticket.assert_called_with(requester_name, email, subject, expected_body, tags)
        # Now check sending a notification with a review link
        args['url'] = 'http://review/url'
        with mock.patch("lms.djangoapps.instructor.services.create_zendesk_ticket") as mock_create_zendesk_ticket:
            self.service.send_support_notification(
                course_id=str(self.course.id),
                exam_name=args['exam_name'],
                student_username=args["student_username"],
                review_status="Suspicious",
                review_url=args['url'],
            )
        expected_body = body.format(**args)
        mock_create_zendesk_ticket.assert_called_with(requester_name, email, subject, expected_body, tags)

    def test_get_proctoring_escalation_email_from_course_key(self):
        """
        Test that it returns the correct proctoring escalation email from a course key object
        """
        email = self.service.get_proctoring_escalation_email(self.course.id)
        assert email == self.email

    def test_get_proctoring_escalation_email_from_course_id(self):
        """
        Test that it returns the correct proctoring escalation email from a course id string
        """
        email = self.service.get_proctoring_escalation_email(str(self.course.id))
        assert email == self.email

    def test_get_proctoring_escalation_email_no_course(self):
        """
        Test that it raises an exception if the course is not found
        """
        with pytest.raises(ObjectDoesNotExist):
            self.service.get_proctoring_escalation_email('a/b/c')

    def test_get_proctoring_escalation_email_invalid_key(self):
        """
        Test that it raises an exception if the course_key is invalid
        """
        with pytest.raises(InvalidKeyError):
            self.service.get_proctoring_escalation_email('invalid key')
