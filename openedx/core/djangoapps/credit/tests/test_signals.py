"""
Tests for minimum grade and credit requirement status
"""

from datetime import datetime, timedelta, timezone
from unittest import mock
from uuid import uuid4

import ddt
from zoneinfo import ZoneInfo
from django.test.client import RequestFactory
from opaque_keys.edx.keys import UsageKey
from openedx_events.data import EventsMetadata
from openedx_events.learning.data import ExamAttemptData, UserData, UserPersonalData
from openedx_events.learning.signals import (
    EXAM_ATTEMPT_ERRORED,
    EXAM_ATTEMPT_REJECTED,
    EXAM_ATTEMPT_RESET,
    EXAM_ATTEMPT_SUBMITTED,
    EXAM_ATTEMPT_VERIFIED
)

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.credit.api import get_credit_requirement_status, set_credit_requirements
from openedx.core.djangoapps.credit.models import CreditCourse, CreditProvider
from openedx.core.djangoapps.credit.signals.handlers import (
    listen_for_exam_errored,
    listen_for_exam_rejected,
    listen_for_exam_reset,
    listen_for_exam_submitted,
    listen_for_exam_verified,
    listen_for_grade_calculation
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import \
    ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_lms
@ddt.ddt
class TestMinGradedRequirementStatus(ModuleStoreTestCase):
    """Test cases to check the minimum grade requirement status updated.
    If user grade is above or equal to min-grade then status will be
    satisfied. But if student grade is less than and deadline is passed then
    user will be marked as failed.
    """
    VALID_DUE_DATE = datetime.now(ZoneInfo("UTC")) + timedelta(days=20)
    EXPIRED_DUE_DATE = datetime.now(ZoneInfo("UTC")) - timedelta(days=20)

    DATES = {
        'valid': VALID_DUE_DATE,
        'expired': EXPIRED_DUE_DATE,
        None: None,
    }

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='Robot', number='999', display_name='Test Course'
        )

        self.user = UserFactory()
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.client.login(username=self.user.username, password=self.user.password)

        # Enable the course for credit
        CreditCourse.objects.create(
            course_key=self.course.id,
            enabled=True,
        )

        # Configure a credit provider for the course
        CreditProvider.objects.create(
            provider_id="ASU",
            enable_integration=True,
            provider_url="https://credit.example.com/request",
        )

        requirements = [{
            "namespace": "grade",
            "name": "grade",
            "display_name": "Grade",
            "criteria": {"min_grade": 0.52},
        }]
        # Add a single credit requirement (final grade)
        set_credit_requirements(self.course.id, requirements)

        # Enroll user in verified mode.
        self.enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.VERIFIED)

    def assert_requirement_status(self, grade, due_date, expected_status):
        """ Verify the user's credit requirement status is as expected after simulating a grading calculation. """
        course_grade = mock.MagicMock()
        course_grade.percent = grade
        listen_for_grade_calculation(None, self.user, course_grade, self.course.id, due_date)
        req_status = get_credit_requirement_status(self.course.id, self.request.user.username, 'grade', 'grade')

        assert req_status[0]['status'] == expected_status

        if expected_status == 'satisfied':
            expected_reason = {'final_grade': grade}
            assert req_status[0]['reason'] == expected_reason

    @ddt.data(
        (0.6, 'valid'),
        (0.52, None),
    )
    @ddt.unpack
    def test_min_grade_requirement_with_valid_grade(self, grade, due_date_name):
        """Test with valid grades submitted before deadline"""
        self.assert_requirement_status(grade, self.DATES[due_date_name], 'satisfied')

    def test_grade_changed(self):
        """ Verify successive calls to update a satisfied grade requirement are recorded. """
        self.assert_requirement_status(0.6, self.VALID_DUE_DATE, 'satisfied')
        self.assert_requirement_status(0.75, self.VALID_DUE_DATE, 'satisfied')
        self.assert_requirement_status(0.70, self.VALID_DUE_DATE, 'satisfied')

    def test_min_grade_requirement_with_valid_grade_and_expired_deadline(self):
        """ Verify the status is set to failure if a passing grade is received past the submission deadline. """
        self.assert_requirement_status(0.70, self.EXPIRED_DUE_DATE, 'failed')

    @ddt.data(
        (0.50, None),
        (0.51, None),
        (0.40, 'valid'),
    )
    @ddt.unpack
    def test_min_grade_requirement_failed_grade_valid_deadline(self, grade, due_date_name):
        """Test with failed grades and deadline is still open or not defined."""
        self.assert_requirement_status(grade, self.DATES[due_date_name], None)

    def test_min_grade_requirement_failed_grade_expired_deadline(self):
        """Test with failed grades and deadline expire"""
        self.assert_requirement_status(0.22, self.EXPIRED_DUE_DATE, 'failed')

    @ddt.data(
        CourseMode.AUDIT,
        CourseMode.HONOR,
        CourseMode.CREDIT_MODE
    )
    def test_requirement_failed_for_non_verified_enrollment(self, mode):
        """Test with valid grades submitted before deadline with non-verified enrollment."""
        self.enrollment.update_enrollment(mode, True)
        self.assert_requirement_status(0.8, self.VALID_DUE_DATE, None)


@skip_unless_lms
@ddt.ddt
class TestExamEvents(ModuleStoreTestCase):
    """
    Test exam events
    """
    HANDLERS = {
        EXAM_ATTEMPT_ERRORED: listen_for_exam_errored,
        EXAM_ATTEMPT_REJECTED: listen_for_exam_rejected,
        EXAM_ATTEMPT_RESET: listen_for_exam_reset,
        EXAM_ATTEMPT_SUBMITTED: listen_for_exam_submitted,
        EXAM_ATTEMPT_VERIFIED: listen_for_exam_verified,
    }

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='TestX', number='999', display_name='Test Course'
        )
        self.subsection_key = UsageKey.from_string('block-v1:edX+TestX+Test_Course+type@sequential+block@subsection')

        self.user = UserFactory()

        # Enable the course for credit
        CreditCourse.objects.create(
            course_key=self.course.id,
            enabled=True,
        )

    @staticmethod
    def _get_exam_event_data(user, course, usage_key):
        """ create ExamAttemptData object for user """
        return ExamAttemptData(
            student_user=UserData(
                id=user.id,
                is_active=True,
                pii=UserPersonalData(
                    username=user.username,
                    email=user.email,
                ),
            ),
            course_key=course.id,
            usage_key=usage_key,
            exam_type='timed',
            requesting_user=None,
        )

    @staticmethod
    def _get_exam_event_metadata(event_signal):
        """ create metadata object for event """
        return EventsMetadata(
            event_type=event_signal.event_type,
            id=uuid4(),
            minorversion=0,
            source='openedx/lms/web',
            sourcehost='lms.test',
            time=datetime.now(timezone.utc)
        )

    @mock.patch('openedx.core.djangoapps.credit.signals.handlers.remove_credit_requirement_status', autospec=True)
    def test_exam_reset(self, mock_remove_credit_status):
        """
        Test exam reset event
        """
        event_data = self._get_exam_event_data(self.user, self.course, self.subsection_key)
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_RESET)

        listen_for_exam_reset(None, EXAM_ATTEMPT_RESET, event_metadata=event_metadata, exam_attempt=event_data)

        mock_remove_credit_status.assert_called_once_with(
            self.user.username, self.course.id, 'exam', str(self.subsection_key)
        )

    @ddt.data(
        (EXAM_ATTEMPT_ERRORED, 'failed'),
        (EXAM_ATTEMPT_REJECTED, 'failed'),
        (EXAM_ATTEMPT_VERIFIED, 'satisfied'),
        (EXAM_ATTEMPT_SUBMITTED, 'submitted'),
    )
    @ddt.unpack
    @mock.patch('openedx.core.djangoapps.credit.signals.handlers.set_credit_requirement_status', autospec=True)
    def test_exam_update_event(self, event_signal, expected_status, mock_set_credit_status):
        """
        Test exam events that update credit status
        """
        event_data = self._get_exam_event_data(self.user, self.course, self.subsection_key)
        event_metadata = self._get_exam_event_metadata(event_signal)

        handler = self.HANDLERS.get(event_signal)
        handler(None, event_signal, event_metadata=event_metadata, exam_attempt=event_data)

        mock_set_credit_status.assert_called_once_with(
            self.user.username, self.course.id, 'exam', str(self.subsection_key), status=expected_status
        )

    @ddt.data(
        EXAM_ATTEMPT_RESET,
        EXAM_ATTEMPT_REJECTED,
        EXAM_ATTEMPT_ERRORED,
        EXAM_ATTEMPT_VERIFIED,
        EXAM_ATTEMPT_SUBMITTED,
    )
    def test_exam_event_bad_user(self, event_signal):
        """
        Test exam event with a user that does not exist in the LMS
        """
        self.user.id = 999  # don't save to db so user doesn't exist
        event_data = self._get_exam_event_data(self.user, self.course, self.subsection_key)
        event_metadata = self._get_exam_event_metadata(event_signal)
        handler = self.HANDLERS.get(event_signal)

        with mock.patch('openedx.core.djangoapps.credit.signals.handlers.log.error') as mock_log:
            handler(None, event_signal, event_metadata=event_metadata, exam_attempt=event_data)
            mock_log.assert_called_once_with(
                'Error occurred while handling exam event for '
                f'{self.user.id} and content_id {self.subsection_key}. '
                'User does not exist!'
            )

    @ddt.data(
        EXAM_ATTEMPT_RESET,
        EXAM_ATTEMPT_REJECTED,
        EXAM_ATTEMPT_ERRORED,
        EXAM_ATTEMPT_VERIFIED,
        EXAM_ATTEMPT_SUBMITTED,
    )
    @mock.patch('openedx.core.djangoapps.credit.signals.handlers.remove_credit_requirement_status', autospec=True)
    @mock.patch('openedx.core.djangoapps.credit.signals.handlers.set_credit_requirement_status', autospec=True)
    def test_exam_event_non_credit_course(self, event_signal, mock_remove_credit_status, mock_set_credit_status):
        """
        Credit credit logic should not run on non-credit courses
        """
        non_credit_course = CourseFactory.create()
        event_data = self._get_exam_event_data(self.user, non_credit_course, self.subsection_key)
        event_metadata = self._get_exam_event_metadata(event_signal)
        handler = self.HANDLERS.get(event_signal)

        handler(None, event_signal, event_metadata=event_metadata, exam_attempt=event_data)

        mock_remove_credit_status.assert_not_called()
        mock_set_credit_status.assert_not_called()
