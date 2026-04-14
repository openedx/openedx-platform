"""
Unit tests for the calendar sync plugins.
"""


import crum
import ddt
from django.test import RequestFactory
from edx_toggles.toggles.testutils import override_waffle_flag

from openedx.features.calendar_sync.plugins import CalendarSyncToggleTool
from openedx.features.course_experience import CALENDAR_SYNC_FLAG, RELATIVE_DATES_FLAG
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseUserType,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class TestCalendarSyncToggleTool(SharedModuleStoreTestCase):
    """
    Test the calendar sync toggle tool.
    """
    @classmethod
    def setUpClass(cls):
        """ Set up any course data """
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    @ddt.data(
        [CourseUserType.ANONYMOUS, False],
        [CourseUserType.ENROLLED, True],
        [CourseUserType.UNENROLLED, False],
        [CourseUserType.UNENROLLED_STAFF, False],
    )
    @ddt.unpack
    @override_waffle_flag(CALENDAR_SYNC_FLAG, active=True)
    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_calendar_sync_toggle_tool_is_enabled(self, user_type, should_be_enabled):
        request = RequestFactory().request()
        request.user = self.create_user_for_course(self.course, user_type)
        self.addCleanup(crum.set_current_request, None)
        crum.set_current_request(request)
        assert CalendarSyncToggleTool.is_enabled(request, self.course.id) == should_be_enabled
