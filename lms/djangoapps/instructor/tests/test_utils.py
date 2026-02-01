"""
Unit tests for instructor.utils module.

Tests for student enrollment utility functions that can be used
in both synchronous and asynchronous contexts.
"""

from unittest.mock import Mock, patch

import ddt
from django.core.exceptions import ValidationError
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE,
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    EnrollStatusChange,
)
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor.enrollment import EmailEnrollmentState
from lms.djangoapps.instructor.utils import (
    _determine_enroll_state_transition,
    _determine_unenroll_state_transition,
    process_single_student_enrollment,
    process_student_enrollment_batch,
)


@ddt.ddt
class TestDetermineEnrollStateTransition(TestCase):
    """
    Test the _determine_enroll_state_transition function.
    """

    @ddt.data(
        # User not registered, allowed to enroll
        (
            {"user": False, "enrollment": False, "allowed": False},
            {"enrollment": False, "allowed": True},
            UNENROLLED_TO_ALLOWEDTOENROLL,
        ),
        # User not registered, not allowed
        (
            {"user": False, "enrollment": False, "allowed": False},
            {"enrollment": False, "allowed": False},
            DEFAULT_TRANSITION_STATE,
        ),
        # User registered, was enrolled, still enrolled
        (
            {"user": True, "enrollment": True, "allowed": False},
            {"enrollment": True, "allowed": False},
            ENROLLED_TO_ENROLLED,
        ),
        # User registered, was not enrolled, now enrolled
        (
            {"user": True, "enrollment": False, "allowed": False},
            {"enrollment": True, "allowed": False},
            UNENROLLED_TO_ENROLLED,
        ),
        # User registered, was allowed, now enrolled
        (
            {"user": True, "enrollment": False, "allowed": True},
            {"enrollment": True, "allowed": False},
            ALLOWEDTOENROLL_TO_ENROLLED,
        ),
        # User registered, not enrolled
        (
            {"user": True, "enrollment": False, "allowed": False},
            {"enrollment": False, "allowed": False},
            DEFAULT_TRANSITION_STATE,
        ),
    )
    @ddt.unpack
    def test_determine_enroll_state_transition(self, before_state: dict, after_state: dict, expected_transition: str):
        """Test state transition determination for enrollment."""
        result = _determine_enroll_state_transition(before_state, after_state)

        self.assertEqual(result, expected_transition)


@ddt.ddt
class TestDetermineUnenrollStateTransition(TestCase):
    """
    Test the _determine_unenroll_state_transition function.
    """

    @ddt.data(
        # User was enrolled
        (
            {"enrollment": True, "allowed": False},
            ENROLLED_TO_UNENROLLED,
        ),
        # User was allowed to enroll
        (
            {"enrollment": False, "allowed": True},
            ALLOWEDTOENROLL_TO_UNENROLLED,
        ),
        # User was neither enrolled nor allowed
        (
            {"enrollment": False, "allowed": False},
            UNENROLLED_TO_UNENROLLED,
        ),
    )
    @ddt.unpack
    def test_determine_unenroll_state_transition(self, before_state: dict, expected_transition: str):
        """Test state transition determination for unenrollment."""
        result = _determine_unenroll_state_transition(before_state)

        self.assertEqual(result, expected_transition)


class TestProcessSingleStudentEnrollment(TestCase):
    """
    Test the process_single_student_enrollment function.
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        self.user = UserFactory.create(username="testuser", email="test@example.com")
        self.request_user = UserFactory.create(username="instructor", email="instructor@example.com")
        self.email_params = {"course_name": "Test Course", "course_url": "http://example.com/course"}

    @patch("lms.djangoapps.instructor.utils.enroll_email")
    def test_process_single_student_enrollment_success(self, mock_enroll_email: Mock):
        """Test successful enrollment of a single student."""
        before_state = EmailEnrollmentState(self.course_key, self.user.email)
        before_state.enrollment = False
        before_state.user = True
        before_state.allowed = False
        after_state = EmailEnrollmentState(self.course_key, self.user.email)
        after_state.enrollment = True
        after_state.user = True
        after_state.allowed = False
        mock_enroll_email.return_value = (before_state, after_state, None)

        result = process_single_student_enrollment(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifier=self.user.email,
            auto_enroll=False,
            email_students=True,
            reason="Test enrollment",
            email_params=self.email_params,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["identifier"], self.user.email)
        self.assertEqual(result["state_transition"], UNENROLLED_TO_ENROLLED)
        self.assertIn("before", result)
        self.assertIn("after", result)

    @patch("lms.djangoapps.instructor.utils.unenroll_email")
    def test_process_single_student_unenrollment_success(self, mock_unenroll_email: Mock):
        """Test successful unenrollment of a single student."""
        before_state = EmailEnrollmentState(self.course_key, self.user.email)
        before_state.enrollment = True
        before_state.user = True
        before_state.allowed = False
        after_state = EmailEnrollmentState(self.course_key, self.user.email)
        after_state.enrollment = False
        after_state.user = True
        after_state.allowed = False
        mock_unenroll_email.return_value = (before_state, after_state)

        result = process_single_student_enrollment(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.unenroll,
            identifier=self.user.email,
            auto_enroll=False,
            email_students=True,
            reason="Test unenrollment",
            email_params=self.email_params,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["identifier"], self.user.email)
        self.assertEqual(result["state_transition"], ENROLLED_TO_UNENROLLED)

    @patch("lms.djangoapps.instructor.utils.validate_email")
    def test_process_single_student_enrollment_invalid_email(self, mock_validate_email: Mock):
        """Test enrollment with invalid email address."""
        mock_validate_email.side_effect = ValidationError("Invalid email")

        result = process_single_student_enrollment(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifier="invalid-email",
            auto_enroll=False,
            email_students=True,
            reason="Test enrollment",
            email_params=self.email_params,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["identifier"], "invalid-email")
        self.assertTrue(result["invalidIdentifier"])
        self.assertEqual(result["error_type"], "invalid_identifier")
        self.assertEqual(result["error_message"], "Invalid email address")

    @patch("lms.djangoapps.instructor.utils.enroll_email")
    def test_process_single_student_enrollment_general_error(
        self,
        mock_enroll_email: Mock,
    ):
        """Test enrollment with general exception."""
        mock_enroll_email.side_effect = Exception("Database error")

        result = process_single_student_enrollment(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifier=self.user.email,
            auto_enroll=False,
            email_students=True,
            reason="Test enrollment",
            email_params=self.email_params,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["identifier"], self.user.email)
        self.assertTrue(result["error"])
        self.assertEqual(result["error_type"], "general_error")
        self.assertEqual(result["error_message"], "Database error")


class TestProcessStudentEnrollmentBatch(TestCase):
    """
    Test the process_student_enrollment_batch function.
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        self.user1 = UserFactory.create(username="testuser1", email="test1@example.com")
        self.user2 = UserFactory.create(username="testuser2", email="test2@example.com")
        self.request_user = UserFactory.create(username="instructor", email="instructor@example.com")

    @patch("lms.djangoapps.instructor.utils.get_course_by_id")
    @patch("lms.djangoapps.instructor.utils.get_email_params")
    @patch("lms.djangoapps.instructor.utils.process_single_student_enrollment")
    def test_process_student_enrollment_batch_success(
        self, mock_process_single: Mock, mock_get_course: Mock, mock_get_email_params: Mock
    ):
        """Test batch processing with all successful enrollments."""
        mock_get_course.return_value = Mock(display_name_with_default="Test Course")
        mock_get_email_params.return_value = {"course_name": "Test Course"}
        mock_process_single.side_effect = [
            {
                "identifier": self.user1.email,
                "success": True,
                "before": {},
                "after": {},
                "state_transition": UNENROLLED_TO_ENROLLED,
            },
            {
                "identifier": self.user2.email,
                "success": True,
                "before": {},
                "after": {},
                "state_transition": UNENROLLED_TO_ENROLLED,
            },
        ]
        identifiers = [self.user1.email, self.user2.email]

        result = process_student_enrollment_batch(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifiers=identifiers,
            auto_enroll=False,
            email_students=True,
            reason="Batch enrollment test",
            secure=True,
        )

        self.assertEqual(result["action"], EnrollStatusChange.enroll)
        self.assertFalse(result["auto_enroll"])
        self.assertEqual(result["total_students"], 2)
        self.assertEqual(result["successful_operations"], 2)
        self.assertEqual(result["failed_operations"], 0)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(mock_process_single.call_count, 2)

    @patch("lms.djangoapps.instructor.utils.get_course_by_id")
    @patch("lms.djangoapps.instructor.utils.get_email_params")
    @patch("lms.djangoapps.instructor.utils.process_single_student_enrollment")
    def test_process_student_enrollment_batch_mixed_results(
        self,
        mock_process_single: Mock,
        mock_get_email_params: Mock,
        mock_get_course: Mock,
    ):
        """Test batch processing with mixed success and failure."""
        mock_get_course.return_value = Mock(display_name_with_default="Test Course")
        mock_get_email_params.return_value = {"course_name": "Test Course"}
        mock_process_single.side_effect = [
            {
                "identifier": self.user1.email,
                "success": True,
                "before": {},
                "after": {},
                "state_transition": UNENROLLED_TO_ENROLLED,
            },
            {
                "identifier": "invalid@",
                "success": False,
                "invalidIdentifier": True,
                "error_type": "invalid_identifier",
                "error_message": "Invalid email address",
            },
        ]
        identifiers = [self.user1.email, "invalid@"]

        result = process_student_enrollment_batch(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifiers=identifiers,
            auto_enroll=False,
            email_students=True,
            reason="Batch enrollment test",
            secure=True,
        )

        self.assertEqual(result["total_students"], 2)
        self.assertEqual(result["successful_operations"], 1)
        self.assertEqual(result["failed_operations"], 1)
        self.assertEqual(len(result["results"]), 2)

    @patch("lms.djangoapps.instructor.utils.get_course_by_id")
    @patch("lms.djangoapps.instructor.utils.get_email_params")
    @patch("lms.djangoapps.instructor.utils.process_single_student_enrollment")
    def test_process_student_enrollment_batch_with_progress_callback(
        self,
        mock_process_single: Mock,
        mock_get_email_params: Mock,
        mock_get_course: Mock,
    ):
        """Test batch processing with progress callback."""
        mock_get_course.return_value = Mock(display_name_with_default="Test Course")
        mock_get_email_params.return_value = {"course_name": "Test Course"}
        mock_process_single.return_value = {
            "identifier": self.user1.email,
            "success": True,
            "before": {},
            "after": {},
            "state_transition": UNENROLLED_TO_ENROLLED,
        }
        identifiers = [self.user1.email, self.user2.email]
        progress_calls = []

        def progress_callback(current, total, results):
            progress_calls.append((current, total, len(results)))

        result = process_student_enrollment_batch(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifiers=identifiers,
            auto_enroll=False,
            email_students=False,
            reason=None,
            secure=True,
            progress_callback=progress_callback,
        )

        self.assertEqual(len(progress_calls), 2)
        self.assertEqual(progress_calls[0], (1, 2, 1))
        self.assertEqual(progress_calls[1], (2, 2, 2))
        self.assertEqual(result["successful_operations"], 2)

    @patch("lms.djangoapps.instructor.utils.process_single_student_enrollment")
    def test_process_student_enrollment_batch_no_email(self, mock_process_single: Mock):
        """Test batch processing without sending emails."""
        mock_process_single.return_value = {
            "identifier": self.user1.email,
            "success": True,
            "before": {},
            "after": {},
            "state_transition": UNENROLLED_TO_ENROLLED,
        }
        identifiers = [self.user1.email]

        result = process_student_enrollment_batch(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifiers=identifiers,
            auto_enroll=False,
            email_students=False,
            reason=None,
            secure=True,
        )

        self.assertEqual(result["successful_operations"], 1)
        call_kwargs = mock_process_single.call_args[1]
        self.assertEqual(call_kwargs["email_params"], {})

    @patch("lms.djangoapps.instructor.utils.get_course_by_id")
    @patch("lms.djangoapps.instructor.utils.get_email_params")
    @patch("lms.djangoapps.instructor.utils.process_single_student_enrollment")
    def test_process_student_enrollment_batch_empty_list(self, mock_process_single: Mock, _: Mock, __: Mock):
        """Test batch processing with empty identifier list."""
        result = process_student_enrollment_batch(
            request_user=self.request_user,
            course_key=self.course_key,
            action=EnrollStatusChange.enroll,
            identifiers=[],
            auto_enroll=False,
            email_students=False,
            reason=None,
            secure=True,
        )

        self.assertEqual(result["total_students"], 0)
        self.assertEqual(result["successful_operations"], 0)
        self.assertEqual(result["failed_operations"], 0)
        self.assertEqual(len(result["results"]), 0)
        mock_process_single.assert_not_called()
