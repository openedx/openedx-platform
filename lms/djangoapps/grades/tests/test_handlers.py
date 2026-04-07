"""
Tests for the grades handlers
"""
from datetime import datetime, timezone
from unittest import mock
from uuid import uuid4

import ddt
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_events.data import EventsMetadata
from openedx_events.learning.data import ExamAttemptData, UserData, UserPersonalData
from openedx_events.learning.signals import EXAM_ATTEMPT_REJECTED, EXAM_ATTEMPT_VERIFIED

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.grades.signals.handlers import (
    exam_attempt_rejected_event_handler,
    exam_attempt_verified_event_handler,
)

from ..constants import GradeOverrideFeatureEnum


@ddt.ddt
class ExamCompletionEventBusTests(TestCase):
    """
    Tests for exam events from the event bus
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = CourseKey.from_string('course-v1:edX+TestX+Test_Course')
        cls.subsection_id = 'block-v1:edX+TestX+Test_Course+type@sequential+block@subsection'
        cls.usage_key = UsageKey.from_string(cls.subsection_id)
        cls.student_user = UserFactory(
            username='student_user',
        )

    @staticmethod
    def _get_exam_event_data(student_user, course_key, usage_key, exam_type, requesting_user=None):
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
            requesting_user=requesting_user_data,
            exam_type=exam_type,
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

    @ddt.data(
        True,
        False
    )
    @mock.patch('lms.djangoapps.grades.api.should_override_grade_on_rejected_exam')
    @mock.patch('lms.djangoapps.grades.api.undo_override_subsection_grade')
    def test_exam_attempt_verified_event_handler(self, override_enabled, mock_undo_override, mock_should_override):
        mock_should_override.return_value = override_enabled

        exam_event_data = self._get_exam_event_data(self.student_user,
                                                    self.course_key,
                                                    self.usage_key,
                                                    exam_type='proctored')
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_VERIFIED)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }
        exam_attempt_verified_event_handler(None, EXAM_ATTEMPT_VERIFIED, ** event_kwargs)

        if override_enabled:
            mock_undo_override.assert_called_once_with(
                self.student_user.id,
                self.course_key,
                self.usage_key,
                GradeOverrideFeatureEnum.proctoring
            )
        else:
            mock_undo_override.assert_not_called()

    @mock.patch('lms.djangoapps.grades.api.override_subsection_grade')
    def test_exam_attempt_rejected_event_handler(self, mock_override):
        exam_event_data = self._get_exam_event_data(self.student_user,
                                                    self.course_key,
                                                    self.usage_key,
                                                    exam_type='proctored')
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_REJECTED)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }
        exam_attempt_rejected_event_handler(None, EXAM_ATTEMPT_REJECTED, ** event_kwargs)

        override_grade_value = 0.0

        mock_override.assert_called_once_with(
            self.student_user.id,
            self.course_key,
            self.usage_key,
            earned_all=override_grade_value,
            earned_graded=override_grade_value,
            feature=GradeOverrideFeatureEnum.proctoring,
            overrider=None,
            comment=None,
        )
