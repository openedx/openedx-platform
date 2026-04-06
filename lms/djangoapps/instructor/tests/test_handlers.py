"""
Unit tests for instructor signals
"""
from datetime import datetime, timezone
from unittest import mock
from uuid import uuid4

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_events.data import EventsMetadata
from openedx_events.learning.data import ExamAttemptData, UserData, UserPersonalData
from openedx_events.learning.signals import EXAM_ATTEMPT_RESET, EXAM_ATTEMPT_SUBMITTED

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor import enrollment
from lms.djangoapps.instructor.handlers import handle_exam_completion, handle_exam_reset


class ExamCompletionEventBusTests(TestCase):
    """
    Tests completion events from the event bus.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = CourseKey.from_string('course-v1:edX+TestX+Test_Course')
        cls.subsection_id = 'block-v1:edX+TestX+Test_Course+type@sequential+block@subsection'
        cls.subsection_key = UsageKey.from_string(cls.subsection_id)
        cls.student_user = UserFactory(
            username='student_user',
        )

    @staticmethod
    def _get_exam_event_data(student_user, course_key, usage_key, requesting_user=None):
        """ create ExamAttemptData object for exam based event """
        if requesting_user:
            requesting_user_data = UserData(
                id=requesting_user.id,
                is_active=True,
                pii=None
            )
        else:
            requesting_user_data = None

        return ExamAttemptData(
            student_user=UserData(
                id=student_user.id,
                is_active=True,
                pii=UserPersonalData(
                    username=student_user.username,
                    email=student_user.email,
                ),
            ),
            course_key=course_key,
            usage_key=usage_key,
            exam_type='timed',
            requesting_user=requesting_user_data,
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
            time=datetime.now(timezone.utc)  # noqa: UP017
        )

    @mock.patch('lms.djangoapps.instructor.tasks.update_exam_completion_task.apply_async', autospec=True)
    def test_submit_exam_completion_event(self, mock_task_apply):
        """
        Assert update completion task is scheduled
        """
        exam_event_data = self._get_exam_event_data(self.student_user, self.course_key, self.subsection_key)
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_SUBMITTED)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }
        handle_exam_completion(None, EXAM_ATTEMPT_SUBMITTED, **event_kwargs)
        mock_task_apply.assert_called_once_with(('student_user', self.subsection_id, 1.0))

    @mock.patch('lms.djangoapps.instructor.tasks.update_exam_completion_task.apply_async', autospec=True)
    @mock.patch('lms.djangoapps.instructor.enrollment.reset_student_attempts', autospec=True)
    def test_exam_reset_event(self, mock_reset, mock_task_apply):
        """
        Assert problem state and completion are reset
        """
        staff_user = UserFactory(
            username='staff_user',
            is_staff=True,
        )

        exam_event_data = self._get_exam_event_data(
            self.student_user,
            self.course_key,
            self.subsection_key,
            requesting_user=staff_user
        )
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_RESET)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }

        # reset signal
        handle_exam_reset(None, EXAM_ATTEMPT_RESET, **event_kwargs)

        # make sure problem attempts have been deleted
        mock_reset.assert_called_once_with(
            self.course_key,
            self.student_user,
            self.subsection_key,
            requesting_user=staff_user,
            delete_module=True,
        )

        # Assert we update completion with 0.0
        mock_task_apply.assert_called_once_with(('student_user', self.subsection_id, 0.0))

    def test_exam_reset_bad_user(self):
        staff_user = UserFactory(
            username='staff_user',
            is_staff=True,
        )

        # not a user
        bad_user_data = ExamAttemptData(
            student_user=UserData(
                id=999,
                is_active=True,
                pii=UserPersonalData(
                    username='user_dne',
                    email='user_dne@example.com',
                ),
            ),
            course_key=self.course_key,
            usage_key=self.subsection_key,
            exam_type='timed',
            requesting_user=UserData(
                id=staff_user.id,
                is_active=True,
                pii=None
            ),
        )
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_RESET)
        event_kwargs = {
            'exam_attempt': bad_user_data,
            'metadata': event_metadata
        }

        # reset signal
        with mock.patch('lms.djangoapps.instructor.handlers.log.error') as mock_log:
            handle_exam_reset(None, EXAM_ATTEMPT_RESET, **event_kwargs)
            mock_log.assert_called_once_with(
                'Error occurred while attempting to reset student attempt for user_id '
                f'999 for content_id {bad_user_data.usage_key}. '
                'User does not exist!'
            )

    def test_exam_reset_bad_requesting_user(self):
        # requesting user is not a user
        bad_user_data = ExamAttemptData(
            student_user=UserData(
                id=self.student_user.id,
                is_active=True,
                pii=UserPersonalData(
                    username='user_dne',
                    email='user_dne@example.com',
                ),
            ),
            course_key=self.course_key,
            usage_key=self.subsection_key,
            exam_type='timed',
            requesting_user=UserData(
                id=999,
                is_active=True,
                pii=None
            ),
        )
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_RESET)
        event_kwargs = {
            'exam_attempt': bad_user_data,
            'metadata': event_metadata
        }

        # reset signal
        with mock.patch('lms.djangoapps.instructor.handlers.log.error') as mock_log:
            handle_exam_reset(None, EXAM_ATTEMPT_RESET, **event_kwargs)
            mock_log.assert_called_once_with(
                'Error occurred while attempting to reset student attempt. Requesting user_id '
                '999 does not exist!'
            )

    @mock.patch(
        'lms.djangoapps.instructor.enrollment.reset_student_attempts',
        side_effect=enrollment.sub_api.SubmissionError
    )
    def test_module_reset_failure(self, mock_reset):
        staff_user = UserFactory(
            username='staff_user',
            is_staff=True,
        )

        exam_event_data = self._get_exam_event_data(
            self.student_user,
            self.course_key,
            self.subsection_key,
            requesting_user=staff_user
        )
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_RESET)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }

        with mock.patch('lms.djangoapps.instructor.handlers.log.error') as mock_log:
            # reset signal
            handle_exam_reset(None, EXAM_ATTEMPT_RESET, **event_kwargs)
            mock_log.assert_called_once_with(
                'Error occurred while attempting to reset module state for user_id '
                f'{self.student_user.id} for content_id {self.subsection_id}.'
            )
