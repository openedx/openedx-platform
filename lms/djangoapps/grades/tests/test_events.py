"""
Test that various events are fired for models in the grades app.
"""

from unittest import mock
from unittest.mock import patch

import pytest
from ccx_keys.locator import CCXLocator
from django.utils.timezone import now
from openedx_events.learning.data import (
    CcxCourseData,
    CcxCoursePassingStatusData,
    CourseData,
    CoursePassingStatusData,
    PersistentCourseGradeData,
    UserData,
    UserPersonalData,
)
from openedx_events.learning.signals import (
    CCX_COURSE_PASSING_STATUS_UPDATED,
    COURSE_PASSING_STATUS_UPDATED,
    PERSISTENT_GRADE_SUMMARY_CHANGED,
)
from openedx_events.testing import OpenEdxEventsTestMixin

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from common.test.utils import assert_dict_contains_subset
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.models import PersistentCourseGrade
from lms.djangoapps.grades.tests.utils import mock_passing_grade
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class PersistentGradeEventsTest(OpenEdxEventsTestMixin, SharedModuleStoreTestCase):
    """
    Tests for the Open edX Events associated with the persistent grade process through the update_or_create method.

    This class guarantees that the following events are sent during the user updates their grade, with
    the exact Data Attributes as the event definition stated:

        - PERSISTENT_GRADE_SUMMARY_CHANGED: sent after the user updates or creates the grade.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.persistent_grade_summary.changed.v1",
    ]

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.params = {
            "user_id": self.user.id,
            "course_id": self.course.id,
            "course_version": self.course.number,
            "course_edited_timestamp": now(),
            "percent_grade": 77.7,
            "letter_grade": "Great job",
            "passed": True,
        }
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_persistent_grade_event_emitted(self):
        """
        Test whether the persistent grade updated event is sent after the user updates creates or updates their grade.

        Expected result:
            - PERSISTENT_GRADE_SUMMARY_CHANGED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)

        PERSISTENT_GRADE_SUMMARY_CHANGED.connect(event_receiver)
        grade = PersistentCourseGrade.update_or_create(**self.params)
        self.assertTrue(self.receiver_called)  # noqa: PT009
        assert_dict_contains_subset(
            self,
            {
                "signal": PERSISTENT_GRADE_SUMMARY_CHANGED,
                "sender": None,
                "grade": PersistentCourseGradeData(
                    user_id=self.params["user_id"],
                    course=CourseData(
                        course_key=self.params["course_id"],
                    ),
                    course_edited_timestamp=self.params["course_edited_timestamp"],
                    course_version=self.params["course_version"],
                    grading_policy_hash='',
                    percent_grade=self.params["percent_grade"],
                    letter_grade=self.params["letter_grade"],
                    passed_timestamp=grade.passed_timestamp
                )
            },
            event_receiver.call_args.kwargs,
        )


class CoursePassingStatusEventsTest(OpenEdxEventsTestMixin, SharedModuleStoreTestCase):
    """
    Tests for Open edX passing status update event.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.passing.status.updated.v1",
    ]

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_course_passing_status_updated_emitted(self):
        """
        Test whether passing status updated event is sent after the grade is being updated for a user.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_PASSING_STATUS_UPDATED.connect(event_receiver)
        grade_factory = CourseGradeFactory()

        with mock_passing_grade():
            grade_factory.update(self.user, self.course)

        self.assertTrue(self.receiver_called)  # noqa: PT009
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_PASSING_STATUS_UPDATED,
                "sender": None,
                "course_passing_status": CoursePassingStatusData(
                    is_passing=True,
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.get_full_name() or self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CourseData(
                        course_key=self.course.id,
                    ),
                ),
            },
            event_receiver.call_args.kwargs,
        )


class CCXCoursePassingStatusEventsTest(
    OpenEdxEventsTestMixin, SharedModuleStoreTestCase
):
    """
    Tests for Open edX passing status update event in a CCX course.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.ccx.course.passing.status.updated.v1",
    ]

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.coach = AdminFactory.create()
        self.ccx = ccx = CustomCourseForEdX(
            course_id=self.course.id, display_name="Test CCX", coach=self.coach
        )
        ccx.save()
        self.ccx_locator = CCXLocator.from_course_locator(self.course.id, ccx.id)

        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_ccx_course_passing_status_updated_emitted(self):
        """
        Test whether passing status updated event is sent after the grade is being updated in CCX course.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        CCX_COURSE_PASSING_STATUS_UPDATED.connect(event_receiver)
        grade_factory = CourseGradeFactory()

        with mock_passing_grade():
            grade_factory.update(self.user, self.store.get_course(self.ccx_locator))

        self.assertTrue(self.receiver_called)  # noqa: PT009
        assert_dict_contains_subset(
            self,
            {
                "signal": CCX_COURSE_PASSING_STATUS_UPDATED,
                "sender": None,
                "course_passing_status": CcxCoursePassingStatusData(
                    is_passing=True,
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.get_full_name() or self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CcxCourseData(
                        ccx_course_key=self.ccx_locator,
                        master_course_key=self.course.id,
                        display_name="",
                        coach_email="",
                        start=None,
                        end=None,
                        max_students_allowed=self.ccx.max_student_enrollments_allowed,
                    ),
                ),
            },
            event_receiver.call_args.kwargs,
        )


class GradeEventContextFilterTest(SharedModuleStoreTestCase):
    """
    Tests that course_grade_passed_first_time invokes the GradeEventContextRequested
    filter and uses the returned context directly.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course = CourseFactory.create()

    @patch('lms.djangoapps.grades.events.tracker')
    @patch('lms.djangoapps.grades.events.contexts.course_context_from_course_id')
    @patch('lms.djangoapps.grades.events.GradeEventContextRequested.run_filter')
    def test_filter_called_with_context(
        self,
        mock_run_filter,
        mock_course_context_from_course_id,
        mock_tracker,
    ):
        """
        course_grade_passed_first_time should call
        GradeEventContextRequested.run_filter and use the returned context.
        """
        original_context = {"course_id": str(self.course.id)}
        filtered_context = {
            "course_id": str(self.course.id),
            "org": "test_org",
            "enterprise_uuid": "abc-123",
        }
        mock_course_context_from_course_id.return_value = original_context
        mock_run_filter.return_value = (filtered_context, self.user.id, self.course.id)

        from lms.djangoapps.grades.events import course_grade_passed_first_time

        course_grade_passed_first_time(self.user.id, self.course.id)

        mock_run_filter.assert_called_once_with(
            context=original_context,
            user_id=self.user.id,
            course_id=self.course.id,
        )
        mock_tracker.get_tracker.return_value.context.assert_called_once_with(
            'edx.course.grade.passed.first_time',
            filtered_context,
        )

    @patch('lms.djangoapps.grades.events.log')
    @patch('lms.djangoapps.grades.events.tracker')
    @patch('lms.djangoapps.grades.events.contexts.course_context_from_course_id')
    @patch('lms.djangoapps.grades.events.GradeEventContextRequested.run_filter')
    def test_filter_exception_is_logged_and_raised(
        self,
        mock_run_filter,
        mock_course_context_from_course_id,
        mock_tracker,
        mock_log,
    ):
        """
        If run_filter raises an exception, it should be logged and re-raised.
        """
        original_context = {"course_id": str(self.course.id)}
        mock_course_context_from_course_id.return_value = original_context
        mock_run_filter.side_effect = Exception("boom")

        from lms.djangoapps.grades.events import course_grade_passed_first_time

        with pytest.raises(Exception, match="boom"):
            course_grade_passed_first_time(self.user.id, self.course.id)

        mock_log.exception.assert_called_once_with(
            'GradeEventContextRequested failed for user_id=%s course_id=%s',
            self.user.id,
            self.course.id,
        )
        mock_tracker.get_tracker.return_value.context.assert_not_called()
