"""
Platform plugins to support Calendar Sync toggle.
"""


from django.urls import reverse
from django.utils.translation import gettext as _

from common.djangoapps.student.models import CourseEnrollment
from openedx.features.calendar_sync.api import SUBSCRIBE, UNSUBSCRIBE
from openedx.features.calendar_sync.models import UserCalendarSyncConfig
from openedx.features.course_experience import CALENDAR_SYNC_FLAG, RELATIVE_DATES_FLAG
from openedx.features.course_experience.course_tools import CourseTool, HttpMethod


class CalendarSyncToggleTool(CourseTool):
    """
    The Calendar Sync toggle tool.
    """
    http_method = HttpMethod.POST
    link_title = _('Calendar Sync')
    toggle_data = {'toggle_data': ''}

    @classmethod
    def analytics_id(cls):
        """
        Returns an id to uniquely identify this tool in analytics events.
        """
        return 'edx.calendar-sync'

    @classmethod
    def is_enabled(cls, request, course_key):
        """
        The Calendar Sync toggle tool is limited to user enabled through a waffle flag.
        Staff always has access.
        """
        if not (CALENDAR_SYNC_FLAG.is_enabled(course_key) and RELATIVE_DATES_FLAG.is_enabled(course_key)):
            return False

        if CourseEnrollment.is_enrolled(request.user, course_key):
            if UserCalendarSyncConfig.is_enabled_for_course(request.user, course_key):
                cls.link_title = _('Unsubscribe from calendar updates')
                cls.toggle_data['toggle_data'] = UNSUBSCRIBE
            else:
                cls.link_title = _('Subscribe to calendar updates')
                cls.toggle_data['toggle_data'] = SUBSCRIBE
            return True
        return False

    @classmethod
    def title(cls):  # pylint: disable=arguments-differ
        """
        Returns the title of this tool.
        """
        return cls.link_title

    @classmethod
    def icon_classes(cls):  # pylint: disable=arguments-differ
        """
        Returns the icon classes needed to represent this tool.
        """
        return 'fa fa-calendar'

    @classmethod
    def url(cls, course_key):
        """
        Returns the URL for this tool for the specified course key.
        """
        return reverse('openedx.calendar_sync', args=[course_key])

    @classmethod
    def data(cls):
        """
        Additional data to send with a form submission
        """
        return cls.toggle_data
