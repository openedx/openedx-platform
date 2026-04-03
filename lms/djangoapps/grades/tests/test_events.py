"""
Test that various events are fired for models in the grades app.
"""

from unittest import mock

from ccx_keys.locator import CCXLocator
from django.utils.timezone import now
from openedx_events.learning.data import (
    CcxCourseData,
    CcxCoursePassingStatusData,
    CourseData,
    CoursePassingStatusData,
    PersistentCourseGradeData,
    PersistentSubsectionGradeData,
    UserData,
    UserPersonalData,
    XBlockWithScoringData,
)
from openedx_events.learning.signals import (
    CCX_COURSE_PASSING_STATUS_UPDATED,
    COURSE_PASSING_STATUS_UPDATED,
    PERSISTENT_GRADE_SUMMARY_CHANGED,
    PERSISTENT_SUBSECTION_GRADE_CHANGED,
)
from openedx_events.tests.utils import OpenEdxEventsTestMixin
from opaque_keys.edx.locator import BlockUsageLocator

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.models import (
    BlockRecord,
    BlockRecordList,
    PersistentCourseGrade,
    PersistentSubsectionGrade,
)
from lms.djangoapps.grades.tests.utils import mock_passing_grade
from lms.djangoapps.grades.transformer import GradesTransformer
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from common.test.utils import assert_dict_contains_subset


class PersistentGradeEventsTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the persistant grade process through the update_or_create method.

    This class guarantees that the following events are sent during the user updates their grade, with
    the exact Data Attributes as the event definition stated:

        - PERSISTENT_GRADE_SUMMARY_CHANGED: sent after the user updates or creates the grade.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.persistent_grade_summary.changed.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

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
        self.assertTrue(self.receiver_called)
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


class PersistentSubsectionGradeEventsTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the persistent subsection grade process.

    This class guarantees that the following events are sent during the user updates their grade, with
    the exact Data Attributes as the event definition stated:

        - PERSISTENT_SUBSECTION_GRADE_CHANGED: sent after the user updates or creates the grade.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.persistent_subsection_grade.changed.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.subsection_usage_key = BlockUsageLocator(
            course_key=self.course.id,
            block_type='sequential',
            block_id='subsection_12345',
        )
        self.problem_locator_a = BlockUsageLocator(
            course_key=self.course.id,
            block_type='problem',
            block_id='problem_abc',
        )
        self.problem_locator_b = BlockUsageLocator(
            course_key=self.course.id,
            block_type='problem',
            block_id='problem_def',
        )
        self.record_a = BlockRecord(locator=self.problem_locator_a, weight=1, raw_possible=10, graded=False)
        self.record_b = BlockRecord(locator=self.problem_locator_b, weight=1, raw_possible=10, graded=True)
        self.block_records = BlockRecordList([self.record_a, self.record_b], self.course.id)
        self.params = {
            "user_id": self.user.id,
            "usage_key": self.subsection_usage_key,
            "course_version": self.course.number,
            "subtree_edited_timestamp": now(),
            "earned_all": 6.0,
            "possible_all": 12.0,
            "earned_graded": 6.0,
            "possible_graded": 8.0,
            "visible_blocks": self.block_records,
            "first_attempted": now(),
        }
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_persistent_subsection_grade_event_emitted(self):
        """
        Test whether the persistent subsection grade updated event is sent after the user updates creates or
        updates their grade.

        Expected result:
            - PERSISTENT_SUBSECTION_GRADE_CHANGED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)

        PERSISTENT_SUBSECTION_GRADE_CHANGED.connect(event_receiver)
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        self.assertTrue(self.receiver_called)

        grading_policy_hash = GradesTransformer.grading_policy_hash(self.course)
        visible_blocks = [
            XBlockWithScoringData(
                usage_key=self.record_a.locator,
                block_type=self.record_a.locator.block_type,
                graded=self.record_a.graded,
                raw_possible=self.record_a.raw_possible,
                weight=self.record_a.weight,
            ),
            XBlockWithScoringData(
                usage_key=self.record_b.locator,
                block_type=self.record_b.locator.block_type,
                graded=self.record_b.graded,
                raw_possible=self.record_b.raw_possible,
                weight=self.record_b.weight,
            ),
        ]

        assert_dict_contains_subset(
            self,
            {
                "signal": PERSISTENT_SUBSECTION_GRADE_CHANGED,
                "sender": None,
                "grade": PersistentSubsectionGradeData(
                    user_id=self.params["user_id"],
                    course=CourseData(
                        course_key=self.course.id,
                    ),
                    subsection_edited_timestamp=self.params["subtree_edited_timestamp"],
                    grading_policy_hash=grading_policy_hash,
                    usage_key=self.subsection_usage_key,
                    weighted_graded_earned=self.params["earned_graded"],
                    weighted_graded_possible=self.params["possible_graded"],
                    weighted_total_earned=self.params["earned_all"],
                    weighted_total_possible=self.params["possible_all"],
                    first_attempted=self.params["first_attempted"],
                    visible_blocks=visible_blocks,
                    visible_blocks_hash=str(grade.visible_blocks_id),
                )
            },
            event_receiver.call_args.kwargs,
        )


class CoursePassingStatusEventsTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for Open edX passing status update event.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.passing.status.updated.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.
        """
        super().setUpClass()
        cls.start_events_isolation()

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

        self.assertTrue(self.receiver_called)
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
    SharedModuleStoreTestCase, OpenEdxEventsTestMixin
):
    """
    Tests for Open edX passing status update event in a CCX course.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.ccx.course.passing.status.updated.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.
        """
        super().setUpClass()
        cls.start_events_isolation()

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

        self.assertTrue(self.receiver_called)
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
