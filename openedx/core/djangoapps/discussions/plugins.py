"""
Course app configuration for discussions.
"""
from typing import Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_noop as _
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.course_apps.plugins import CourseApp

from .models import DiscussionsConfiguration

User = get_user_model()


class DiscussionCourseApp(CourseApp):
    """
    Course App config for Discussions.
    """

    app_id = "discussion"
    name = _("Discussion")
    description = _("Encourage participation and engagement in your course with discussions.")
    documentation_links = {
        "learn_more_configuration": settings.DISCUSSIONS_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Discussions is always available.
        """
        return True

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Discussions enable/disable status is stored in a separate model.
        """
        return DiscussionsConfiguration.is_enabled(course_key)

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Set discussion enabled status in DiscussionsConfiguration model.
        """
        configuration = DiscussionsConfiguration.get(course_key)
        if configuration.pk is None:
            raise ValueError("Can't enable/disable discussions for course before they are configured.")
        configuration.enabled = enabled
        configuration.save()
        return configuration.enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Return allowed operations for discussions app.
        """
        # Can only enable discussions for a course if discussions are configured.
        can_enable = DiscussionsConfiguration.get(course_key).pk is not None
        return {
            "enable": can_enable,
            "configure": True,
        }
