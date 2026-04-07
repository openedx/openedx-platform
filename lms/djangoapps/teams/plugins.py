"""
Definition of the course team feature.
"""
from typing import Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_noop as _
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.tabs import EnrolledTab
from lms.djangoapps.teams.waffle import ENABLE_TEAMS_APP
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_apps.plugins import CourseApp
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

from . import is_feature_enabled

User = get_user_model()


class TeamsTab(EnrolledTab):
    """
    The representation of the course teams view type.
    """

    type = "teams"
    title = _("Teams")
    view_name = "teams_dashboard"
    priority = 60

    @classmethod
    def is_enabled(cls, course, user=None):
        """Returns true if the teams feature is enabled in the course.

        Args:
            course (CourseBlock): the course using the feature
            user (User): the user interacting with the course
        """
        if not super().is_enabled(course, user=user):
            return False

        return is_feature_enabled(course)


class TeamsCourseApp(CourseApp):
    """
    Course app for teams.
    """

    app_id = "teams"
    name = _("Teams")
    description = _("Leverage teams to allow learners to connect by topic of interest.")
    documentation_links = {
        "learn_more_configuration": settings.TEAMS_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        The teams app is currently available globally based on a feature setting.
        """
        if not ENABLE_TEAMS_APP.is_enabled():
            return False
        return settings.FEATURES.get("ENABLE_TEAMS", False)

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Returns the enabled status of teams.

        Args:
            course_key (CourseKey): The course for which to fetch the status of teams
        """
        return CourseOverview.get_from_id(course_key).teams_enabled

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: User) -> bool:
        """
        Returns the enabled status of teams.
        Args:
            course_key (CourseKey): The course for which to set the status of teams
            enabled (bool): The new satus for the app.
            user (User): The user performing the operation

        Returns:
            (bool): the new status of the app
        """
        course = get_course_by_id(course_key)
        course.teams_configuration.is_enabled = enabled
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Return allowed operations for teams app.
        """
        return {
            "enable": True,
            "configure": True,
        }
