"""
Tests for celery tasks defined in tasks module
"""


import contextlib
from unittest import mock

from ccx_keys.locator import CCXLocator

from common.djangoapps.student.roles import CourseCcxCoachRole
from common.djangoapps.student.tests.factories import AdminFactory
from lms.djangoapps.ccx.tasks import send_ccx_course_published
from lms.djangoapps.ccx.tests.factories import CcxFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import SignalHandler
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@contextlib.contextmanager
def mock_signal_receiver(signal):  # lint-amnesty, pylint: disable=missing-function-docstring
    receiver = mock.Mock()
    signal.connect(receiver)
    yield receiver
    signal.disconnect(receiver)


class TestSendCCXCoursePublished(ModuleStoreTestCase):
    """
    Unit tests for the send ccx course published task
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        course = self.course = CourseFactory.create(org="edX", course="999", display_name="Run 666")
        course2 = self.course2 = CourseFactory.create(org="edX", course="999a", display_name="Run 667")
        coach = AdminFactory.create()
        role = CourseCcxCoachRole(course.id)
        role.add_users(coach)
        self.ccx = CcxFactory(course_id=course.id, coach=coach)
        self.ccx2 = CcxFactory(course_id=course.id, coach=coach)
        self.ccx3 = CcxFactory(course_id=course.id, coach=coach)
        self.ccx4 = CcxFactory(course_id=course2.id, coach=coach)

    def call_fut(self, course_key):
        """
        Call the function under test
        """
        send_ccx_course_published(str(course_key))

    def test_signal_not_sent_for_ccx(self):
        """
        Check that course published signal is not sent when course key is for a ccx
        """
        course_key = CCXLocator.from_course_locator(self.course.id, self.ccx.id)
        with mock_signal_receiver(SignalHandler.course_published) as receiver:
            self.call_fut(course_key)
            assert receiver.call_count == 0

    def test_signal_sent_for_ccx(self):
        """
        Check that course published signal is sent when course key is not for a ccx.
        We have 4 ccx's, but only 3 are derived from the course id used here, so call
        count must be 3 to confirm that all derived courses and no more got the signal.
        """
        with mock_signal_receiver(SignalHandler.course_published) as receiver:
            self.call_fut(self.course.id)
            assert receiver.call_count == 3

    def test_course_overview_cached(self):
        """
        Check that course overview is cached after course published signal is sent
        """
        course_key = CCXLocator.from_course_locator(self.course.id, self.ccx.id)
        overview = CourseOverview.objects.filter(id=course_key)
        assert len(overview) == 0
        with mock_signal_receiver(SignalHandler.course_published) as receiver:
            self.call_fut(self.course.id)
            assert receiver.call_count == 3
            overview = CourseOverview.objects.filter(id=course_key)
            assert len(overview) == 1
