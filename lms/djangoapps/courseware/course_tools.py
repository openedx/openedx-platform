"""
Platform plugins to support course tools.
"""


import datetime

import pytz
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.courseware.utils import _use_new_financial_assistance_flow
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.course_experience.course_tools import CourseTool


class FinancialAssistanceTool(CourseTool):
    """
    The financial assistance tool.
    """
    @classmethod
    def analytics_id(cls):
        """
        Returns an id to uniquely identify this tool in analytics events.
        """
        return 'edx.tool.financial_assistance'

    @classmethod
    def is_enabled(cls, request, course_key):
        """
        Show this link for active courses where financial assistance is available, unless upgrade deadline has passed
        """
        now = datetime.datetime.now(pytz.UTC)
        try:
            course_overview = CourseOverview.objects.get(id=course_key)
        except CourseOverview.DoesNotExist:
            course_overview = None

        # hide link if financial assistance is not enabled
        if not settings.ENABLE_FINANCIAL_ASSISTANCE_FORM:
            return False

        # hide link for archived courses
        if course_overview is not None and course_overview.end is not None and now > course_overview.end:
            return False

        # hide link if not logged in or user not enrolled in the course
        if not request.user or not CourseEnrollment.is_enrolled(request.user, course_key):
            return False

        enrollment = CourseEnrollment.get_enrollment(request.user, course_key)

        # hide if we're no longer in an upsell mode (already upgraded)
        if enrollment.mode not in CourseMode.UPSELL_TO_VERIFIED_MODES:
            return False

        # hide if there's no course_upgrade_deadline, or one with a value in the past
        if enrollment.course_upgrade_deadline:
            if now > enrollment.course_upgrade_deadline:
                return False
        else:
            return False

        return bool(course_overview.eligible_for_financial_aid)

    @classmethod
    def title(cls, course_key=None):
        """
        Returns the title of this tool.
        """
        return _('Financial Assistance')

    @classmethod
    def icon_classes(cls, course_key=None):
        """
        Returns the icon classes needed to represent this tool.
        """
        return 'fa fa-info'

    @classmethod
    def url(cls, course_key):
        """
        Returns the URL for this tool for the specified course key.
        """
        if _use_new_financial_assistance_flow(str(course_key)):
            return reverse('financial_assistance_v2', args=[course_key])
        return reverse('financial_assistance')
