"""
Registers the CCX feature for the edX platform.
"""


from django.conf import settings
from django.utils.translation import gettext_noop

from common.djangoapps.student.roles import CourseCcxCoachRole
from xmodule.tabs import CourseTab  # pylint: disable=wrong-import-order

from .permissions import VIEW_CCX_COACH_DASHBOARD
from .toggles import use_ccx_coach_mfe
from .utils import get_ccx_coach_dashboard_url


class CcxCourseTab(CourseTab):
    """
    The representation of the CCX course tab
    """

    type = "ccx_coach"
    priority = 310
    title = gettext_noop("CCX Coach")
    view_name = "ccx_coach_dashboard"
    is_dynamic = True    # The CCX view is dynamically added to the set of tabs when it is enabled

    def __init__(self, tab_dict):
        # Link to the CCX Coach MFE or the legacy dashboard, depending on the flag.
        def link_func(course, reverse_func):
            if use_ccx_coach_mfe(course.id):
                return get_ccx_coach_dashboard_url(course.id)
            return reverse_func(self.view_name, args=[str(course.id)])

        tab_dict['link_func'] = link_func
        super().__init__(tab_dict)

    @classmethod
    def is_enabled(cls, course, user=None):
        """
        Returns true if CCX has been enabled and the specified user is a coach
        """
        if not settings.CUSTOM_COURSES_EDX or not course.enable_ccx:
            # If ccx is not enable do not show ccx coach tab.
            return False

        if hasattr(course.id, 'ccx') and bool(user.has_perm(VIEW_CCX_COACH_DASHBOARD, course)):
            return True

        # check if user has coach access.
        role = CourseCcxCoachRole(course.id)
        return role.has_user(user)
