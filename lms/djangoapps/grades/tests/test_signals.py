"""
Tests for the score change signals defined in the courseware models module.
"""


import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import ddt
import pytest
import pytz
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator
from submissions.models import score_reset, score_set

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.track.event_transaction_utils import get_event_transaction_id, get_event_transaction_type
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from ..constants import ScoreDatabaseTableEnum
from ..signals.handlers import (
    disconnect_submissions_signal_receiver,
    listen_for_course_grade_passed_first_time,
    listen_for_failing_grade,
    listen_for_passing_grade,
    problem_raw_score_changed_handler,
    submissions_score_reset_handler,
    submissions_score_set_handler,
)
from ..signals.signals import PROBLEM_RAW_SCORE_CHANGED

UUID_REGEX = re.compile('{hex}{{8}}-{hex}{{4}}-{hex}{{4}}-{hex}{{4}}-{hex}{{12}}'.format(hex='[0-9a-f]'))

FROZEN_NOW_DATETIME = datetime.now().replace(tzinfo=pytz.UTC)
FROZEN_NOW_TIMESTAMP = to_timestamp(FROZEN_NOW_DATETIME)

SUBMISSIONS_SCORE_SET_HANDLER = 'submissions_score_set_handler'
SUBMISSIONS_SCORE_RESET_HANDLER = 'submissions_score_reset_handler'
HANDLERS = {
    SUBMISSIONS_SCORE_SET_HANDLER: submissions_score_set_handler,
    SUBMISSIONS_SCORE_RESET_HANDLER: submissions_score_reset_handler,
}

SUBMISSION_SET_KWARGS = 'submission_set_kwargs'
SUBMISSION_RESET_KWARGS = 'submission_reset_kwargs'
SUBMISSION_KWARGS = {
    SUBMISSION_SET_KWARGS: {
        'points_possible': 10,
        'points_earned': 5,
        'anonymous_user_id': 'anonymous_id',
        'course_id': 'CourseID',
        'item_id': 'i4x://org/course/usage/123456',
        'created_at': FROZEN_NOW_TIMESTAMP,
    },
    SUBMISSION_RESET_KWARGS: {
        'anonymous_user_id': 'anonymous_id',
        'course_id': 'CourseID',
        'item_id': 'i4x://org/course/usage/123456',
        'created_at': FROZEN_NOW_TIMESTAMP,
    },
}

PROBLEM_RAW_SCORE_CHANGED_KWARGS = {
    'raw_earned': 1.0,
    'raw_possible': 2.0,
    'weight': 4,
    'user_id': 'UserID',
    'course_id': 'CourseID',
    'usage_id': 'i4x://org/course/usage/123456',
    'only_if_higher': False,
    'score_deleted': True,
    'modified': FROZEN_NOW_TIMESTAMP,
    'score_db_table': ScoreDatabaseTableEnum.courseware_student_module,
    'grader_response': None
}

PROBLEM_WEIGHTED_SCORE_CHANGED_KWARGS = {
    'sender': None,
    'weighted_earned': 2.0,
    'weighted_possible': 4.0,
    'user_id': 'UserID',
    'course_id': 'CourseID',
    'usage_id': 'i4x://org/course/usage/123456',
    'only_if_higher': False,
    'score_deleted': True,
    'modified': FROZEN_NOW_TIMESTAMP,
    'score_db_table': ScoreDatabaseTableEnum.courseware_student_module,
    'grader_response': None
}


@ddt.ddt
class ScoreChangedSignalRelayTest(TestCase):
    """
    Tests to ensure that the courseware module correctly catches
    (a) score_set and score_reset signals from the Submissions API
    (b) LMS PROBLEM_RAW_SCORE_CHANGED signals
    and recasts them as LMS PROBLEM_WEIGHTED_SCORE_CHANGED signals.

    This ensures that listeners in the LMS only have to handle one type
    of signal for all scoring events regardless of their origin.
    """
    SIGNALS = {
        'score_set': score_set,
        'score_reset': score_reset,
    }

    def setUp(self):
        """
        Configure mocks for all the dependencies of the render method
        """
        super().setUp()
        self.signal_mock = self.setup_patch(
            'lms.djangoapps.grades.signals.signals.PROBLEM_WEIGHTED_SCORE_CHANGED.send',
            None,
        )
        self.user_mock = MagicMock()
        self.user_mock.id = 42
        self.get_user_mock = self.setup_patch(
            'lms.djangoapps.grades.signals.handlers.user_by_anonymous_id',
            self.user_mock
        )

    def setup_patch(self, function_name, return_value):
        """
        Patch a function with a given return value, and return the mock
        """
        mock = MagicMock(return_value=return_value)
        new_patch = patch(function_name, new=mock)
        new_patch.start()
        self.addCleanup(new_patch.stop)
        return mock

    @ddt.data(
        [SUBMISSIONS_SCORE_SET_HANDLER, SUBMISSION_SET_KWARGS, 5, 10],
        [SUBMISSIONS_SCORE_RESET_HANDLER, SUBMISSION_RESET_KWARGS, 0, 0],
    )
    @ddt.unpack
    def test_score_set_signal_handler(self, handler_name, kwargs, earned, possible):
        """
        Ensure that on receipt of a score_(re)set signal from the Submissions API,
        the signal handler correctly converts it to a PROBLEM_WEIGHTED_SCORE_CHANGED
        signal.

        Also ensures that the handler calls user_by_anonymous_id correctly.
        """
        local_kwargs = SUBMISSION_KWARGS[kwargs].copy()
        handler = HANDLERS[handler_name]
        handler(None, **local_kwargs)
        expected_set_kwargs = {
            'sender': None,
            'weighted_possible': possible,
            'weighted_earned': earned,
            'user_id': self.user_mock.id,
            'anonymous_user_id': 'anonymous_id',
            'course_id': 'CourseID',
            'usage_id': 'i4x://org/course/usage/123456',
            'modified': FROZEN_NOW_TIMESTAMP,
            'score_db_table': 'submissions',
        }
        if kwargs == SUBMISSION_RESET_KWARGS:
            expected_set_kwargs['score_deleted'] = True
        self.signal_mock.assert_called_once_with(**expected_set_kwargs)
        self.get_user_mock.assert_called_once_with(local_kwargs['anonymous_user_id'])

    def test_tnl_6599_zero_possible_bug(self):
        """
        Ensure that, if coming from the submissions API, signals indicating a
        a possible score of 0 are swallowed for reasons outlined in TNL-6559.
        """
        local_kwargs = SUBMISSION_KWARGS[SUBMISSION_SET_KWARGS].copy()
        local_kwargs['points_earned'] = 0
        local_kwargs['points_possible'] = 0
        submissions_score_set_handler(None, **local_kwargs)
        self.signal_mock.assert_not_called()

    @ddt.data(
        [SUBMISSIONS_SCORE_SET_HANDLER, SUBMISSION_SET_KWARGS],
        [SUBMISSIONS_SCORE_RESET_HANDLER, SUBMISSION_RESET_KWARGS]
    )
    @ddt.unpack
    def test_score_set_missing_kwarg(self, handler_name, kwargs):
        """
        Ensure that, on receipt of a score_(re)set signal from the Submissions API
        that does not have the correct kwargs, the courseware model does not
        generate a signal.
        """
        handler = HANDLERS[handler_name]
        for missing in SUBMISSION_KWARGS[kwargs]:
            local_kwargs = SUBMISSION_KWARGS[kwargs].copy()
            del local_kwargs[missing]

            with pytest.raises(KeyError):
                handler(None, **local_kwargs)
            self.signal_mock.assert_not_called()

    @ddt.data(
        [SUBMISSIONS_SCORE_SET_HANDLER, SUBMISSION_SET_KWARGS],
        [SUBMISSIONS_SCORE_RESET_HANDLER, SUBMISSION_RESET_KWARGS]
    )
    @ddt.unpack
    def test_score_set_bad_user(self, handler_name, kwargs):
        """
        Ensure that, on receipt of a score_(re)set signal from the Submissions API
        that has an invalid user ID, the courseware model does not generate a
        signal.
        """
        handler = HANDLERS[handler_name]
        self.get_user_mock = self.setup_patch('lms.djangoapps.grades.signals.handlers.user_by_anonymous_id', None)
        handler(None, **SUBMISSION_KWARGS[kwargs])
        self.signal_mock.assert_not_called()

    def test_raw_score_changed_signal_handler(self):
        problem_raw_score_changed_handler(None, **PROBLEM_RAW_SCORE_CHANGED_KWARGS)
        expected_set_kwargs = PROBLEM_WEIGHTED_SCORE_CHANGED_KWARGS.copy()
        self.signal_mock.assert_called_with(**expected_set_kwargs)

    def test_raw_score_changed_score_deleted_optional(self):
        local_kwargs = PROBLEM_RAW_SCORE_CHANGED_KWARGS.copy()
        del local_kwargs['score_deleted']
        problem_raw_score_changed_handler(None, **local_kwargs)
        expected_set_kwargs = PROBLEM_WEIGHTED_SCORE_CHANGED_KWARGS.copy()
        expected_set_kwargs['score_deleted'] = False
        self.signal_mock.assert_called_with(**expected_set_kwargs)

    @ddt.data(
        ['score_set', SUBMISSION_KWARGS[SUBMISSION_SET_KWARGS]['points_earned'],
         SUBMISSION_SET_KWARGS],
        ['score_reset', 0,
         SUBMISSION_RESET_KWARGS]
    )
    @ddt.unpack
    def test_disconnect_manager(self, signal_name, weighted_earned, kwargs):
        """
        Tests to confirm the disconnect_submissions_signal_receiver context manager is working correctly.
        """
        signal = self.SIGNALS[signal_name]
        kwargs = SUBMISSION_KWARGS[kwargs].copy()
        handler_mock = self.setup_patch('lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send',
                                        None)

        # Receiver connected before we start
        signal.send(None, **kwargs)
        handler_mock.assert_called_once()
        # Make sure the correct handler was called
        assert handler_mock.call_args[1]['weighted_earned'] == weighted_earned
        handler_mock.reset_mock()

        # Disconnect is functioning
        with disconnect_submissions_signal_receiver(signal):
            signal.send(None, **kwargs)
            handler_mock.assert_not_called()
            handler_mock.reset_mock()

        # And we reconnect properly afterwards
        signal.send(None, **kwargs)
        handler_mock.assert_called_once()
        assert handler_mock.call_args[1]['weighted_earned'] == weighted_earned

    def test_disconnect_manager_bad_arg(self):
        """
        Tests that the disconnect context manager errors when given an invalid signal.
        """
        with pytest.raises(ValueError):
            with disconnect_submissions_signal_receiver(PROBLEM_RAW_SCORE_CHANGED):
                pass


class CourseEventsSignalsTest(ModuleStoreTestCase):
    """
    Tests to ensure that the courseware module correctly catches
    course grades passed/failed signal and emit course related event
    """
    SIGNALS = {
        'score_set': score_set,
        'score_reset': score_reset,
    }

    def setUp(self):
        """
        Configure mocks for all the dependencies of the render method
        """
        super().setUp()
        self.user_mock = MagicMock()
        self.user_mock.id = 42
        self.get_user_mock = self.setup_patch(
            'lms.djangoapps.grades.signals.handlers.user_by_anonymous_id',
            self.user_mock
        )
        self.course_id = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )
        self.user = UserFactory.create(username="Bob", email="bob@example.com", password="edx")
        self.client.login(username=self.user.username, password="edx")

    def setup_patch(self, function_name, return_value):
        """
        Patch a function with a given return value, and return the mock
        """
        mock = MagicMock(return_value=return_value)
        new_patch = patch(function_name, new=mock)
        new_patch.start()
        self.addCleanup(new_patch.stop)
        return mock

    def test_course_grade_passed_first_time_signal_handler(self):
        """
        Ensure that on course grade passed first tim signal, course grade passed first time event is triggered
        """
        handler = listen_for_course_grade_passed_first_time

        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            handler(None, self.user_mock.id, self.course_id)
        self._assert_tracker_emitted_course_grade_passed_first_time_event(
            tracker_mock,
            self.user_mock.id,
            self.course_id
        )

    def _assert_tracker_emitted_course_grade_passed_first_time_event(self, tracker_mock, user_id, course_id):
        """
        Helper function to ensure that the mocked event tracker
        was called with the expected info based on the course grade passed first time.
        """
        tracker_mock.emit.assert_called_with(
            'edx.course.grade.passed.first_time',
            {
                'user_id': str(user_id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
            }
        )

    def test_now_passed_signal_handler(self):
        """
        Ensure that on course now passed signal, course now passed event is triggered
        """
        handler = listen_for_passing_grade

        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            handler(None, self.user_mock, self.course_id)
        self._assert_tracker_emitted_course_now_passed_event(tracker_mock, self.user_mock, self.course_id)

    def _assert_tracker_emitted_course_now_passed_event(self, tracker_mock, user, course_id):
        """
        Helper function to ensure that the mocked event tracker
        was called with the expected info based on passed course.
        """
        tracker_mock.emit.assert_called_with(
            'edx.course.grade.now_passed',
            {
                'user_id': str(user.id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
            }
        )

    def test_now_failed_signal_handler(self):
        """
        Ensure that on course now failed signal, course now failed event is triggered
        """
        handler = listen_for_failing_grade

        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            handler(None, self.user_mock, self.course_id)
        self._assert_tracker_emitted_course_now_failed_event(tracker_mock, self.user_mock, self.course_id)

    def _assert_tracker_emitted_course_now_failed_event(self, tracker_mock, user, course_id):
        """
        Helper function to ensure that the mocked event tracker
        was called with the expected info based on failed course.
        """
        tracker_mock.emit.assert_called_with(
            'edx.course.grade.now_failed',
            {
                'user_id': str(user.id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
            }
        )

    @override_settings(OUTCOME_SURVEYS_EVENTS_ENABLED=True)
    @patch('lms.djangoapps.grades.events.segment.track')
    @patch('lms.djangoapps.grades.signals.signals.SCHEDULE_FOLLOW_UP_SEGMENT_EVENT_FOR_COURSE_PASSED_FIRST_TIME.send')
    def test_segment_event_on_course_grade_passed_first_time(self, signal_mock, segment_track_mock):
        course = CourseOverviewFactory()
        enrollment = CourseEnrollmentFactory(
            is_active=True,
            mode='verified',
            course=course,
            user=self.user
        )
        params = {
            "user_id": self.user.id,
            "course_id": course.id,
            "course_version": "JoeMcEwing",
            "course_edited_timestamp": datetime(
                year=2016,
                month=8,
                day=1,
                hour=18,
                minute=53,
                second=24,
                microsecond=354741,
                tzinfo=pytz.UTC,
            ),
            "percent_grade": 77.7,
            "letter_grade": "Great job",
            "passed": True,
        }
        __ = PersistentCourseGrade.update_or_create(**params)
        segment_track_mock.assert_called_with(
            self.user.id,
            'edx.course.learner.passed.first_time',
            {
                'LMS_ENROLLMENT_ID': enrollment.id,
                'COURSE_TITLE': course.display_name,
                'COURSE_ORG_NAME': course.org,
            }
        )

        assert signal_mock.call_count == 1
        signal_mock.assert_called_with(
            sender=None,
            user_id=self.user.id,
            course_id=course.id,
            event_properties={
                'LMS_ENROLLMENT_ID': enrollment.id,
                'COURSE_TITLE': course.display_name,
                'COURSE_ORG_NAME': course.org,
            }
        )
