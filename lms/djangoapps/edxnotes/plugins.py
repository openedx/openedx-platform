"""
Registers the "edX Notes" feature for the edX platform.
"""
from typing import Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_noop as _
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.tabs import EnrolledTab
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_apps.plugins import CourseApp
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore
from xmodule.tabs import CourseTab, CourseTabList  # lint-amnesty, pylint: disable=wrong-import-order

User = get_user_model()


class EdxNotesTab(EnrolledTab):
    """
    The representation of the edX Notes course tab type.
    """

    type = "edxnotes"
    title = _("Notes")
    view_name = "edxnotes"
    priority = 50

    @classmethod
    def is_enabled(cls, course, user=None):
        """Returns true if the edX Notes feature is enabled in the course.

        Args:
            course (CourseBlock): the course using the feature
            user (User): the user interacting with the course
        """
        if not super().is_enabled(course, user=user):
            return False

        if not settings.FEATURES.get("ENABLE_EDXNOTES"):
            return False

        if user and not user.is_authenticated:
            return False

        return course.edxnotes


class EdxNotesCourseApp(CourseApp):
    """
    Course app for edX notes.
    """

    app_id = "edxnotes"
    name = _("Notes")
    description = _("Allow learners to highlight passages and make notes right in the course.")
    documentation_links = {
        "learn_more_configuration": settings.EDXNOTES_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        EdX notes availability is currently globally controlled via a feature setting.
        """
        return settings.FEATURES.get("ENABLE_EDXNOTES", False)

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Get enabled/disabled status from modulestore.
        """
        return CourseOverview.get_from_id(course_key).edxnotes

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Enable/disable edxnotes in the modulestore.
        """
        course = get_course_by_id(course_key)
        course.edxnotes = enabled
        if enabled:
            notes_tab = CourseTabList.get_tab_by_id(course.tabs, 'edxnotes')
            if notes_tab is None:
                # If the course doesn't already have the notes tab, add it.
                notes_tab = CourseTab.load("edxnotes")
                course.tabs.append(notes_tab)
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Returns allowed operations for edxnotes app.
        """
        return {
            "enable": True,
            "configure": True,
        }
