"""Module with the course app configuration for the Wiki."""
from typing import TYPE_CHECKING, Dict, Optional

from django.conf import settings
from django.utils.translation import gettext_noop as _
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview, CourseOverviewTab
from openedx.core.djangoapps.course_apps.plugins import CourseApp
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tabs import CourseTab, CourseTabList  # lint-amnesty, pylint: disable=wrong-import-order

# Import the User model only for type checking since importing it at runtime
# will prevent the app from starting since the model is imported before
# Django's machinery is ready.
if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    User = get_user_model()

WIKI_ENABLED = settings.WIKI_ENABLED


class WikiCourseApp(CourseApp):
    """
    Course app for the Wiki.
    """

    app_id = "wiki"
    name = _("Wiki")
    description = _("Enable learners to access, and collaborate on course-related information.")
    documentation_links = {
        "learn_more_configuration": settings.WIKI_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Returns if the app is available for the course.

        The wiki is available for all courses or none of them depending on the Django setting.
        """
        return WIKI_ENABLED

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Returns if the wiki is enabled for the course.
        """
        try:
            wiki_tab = CourseOverview.get_from_id(course_key).tab_set.get(tab_id='wiki')
            return not wiki_tab.is_hidden
        except CourseOverviewTab.DoesNotExist:
            return False

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Enabled/disables the wiki tab in the course.
        """
        course = get_course_by_id(course_key)
        wiki_tab = CourseTabList.get_tab_by_id(course.tabs, 'wiki')
        if wiki_tab is None:
            if not enabled:
                return False
            # If the course doesn't already have the wiki tab, add it.
            wiki_tab = CourseTab.load("wiki")
            course.tabs.append(wiki_tab)
        wiki_tab.is_hidden = not enabled
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional['User'] = None) -> Dict[str, bool]:
        """
        Returns the operations you can perform on the wiki.
        """
        return {
            "enable": True,
            "configure": True,
        }
