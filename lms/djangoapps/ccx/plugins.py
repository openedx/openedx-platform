"""
Registers the CCX feature for the edX platform.
"""


from django.conf import settings
from django.utils.translation import gettext_noop

from common.djangoapps.student.roles import CourseCcxCoachRole
from xmodule.tabs import CourseTab  # pylint: disable=wrong-import-order

from .permissions import VIEW_CCX_COACH_DASHBOARD


class CcxCourseTab(CourseTab):
    """
    The representation of the CCX course tab
    """

    type = "ccx_coach"
    priority = 310
    title = gettext_noop("CCX Coach")
    view_name = "ccx_coach_dashboard"
    is_dynamic = True    # The CCX view is dynamically added to the set of tabs when it is enabled

    @classmethod
    def is_enabled(cls, course, user=None):
        """
        Returns true if CCX has been enabled and the specified user is a coach
        """
        if not settings.FEATURES.get('CUSTOM_COURSES_EDX', False) or not course.enable_ccx:
            # If ccx is not enable do not show ccx coach tab.
            return False

        if hasattr(course.id, 'ccx') and bool(user.has_perm(VIEW_CCX_COACH_DASHBOARD, course)):
            return True

        # check if user has coach access.
        role = CourseCcxCoachRole(course.id)
        return role.has_user(user)
