"""
Course app configuration for live.
"""
from typing import Dict, Optional  # noqa: UP035

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_noop as _
from lti_consumer.models import LtiConfiguration
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.course_apps.plugins import CourseApp

from .models import CourseLiveConfiguration

User = get_user_model()


class LiveCourseApp(CourseApp):
    """
    Course App config for Live.
    """

    app_id = "live"
    name = _("Live")
    description = _("Enable in-platform video conferencing by configuring live")
    documentation_links = {
        "learn_more_configuration": settings.COURSE_LIVE_HELP_URL
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Live is available
        """
        return True

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Live enable/disable status is stored in a separate model.
        """
        return CourseLiveConfiguration.is_enabled(course_key)

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: User) -> bool:
        """
        Set live enabled status in CourseLiveConfiguration model.
        """
        configuration, _ = CourseLiveConfiguration.objects.get_or_create(course_key=course_key)
        configuration.enabled = enabled
        if not configuration.lti_configuration:
            configuration.lti_configuration = LtiConfiguration.objects.create(
                config_store=LtiConfiguration.CONFIG_ON_DB
            )
        configuration.save()
        return configuration.enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:  # noqa: UP006, UP045  # pylint: disable=line-too-long
        """
        Return allowed operations for live app.
        """
        # Can only enable live for a course if live is configured.
        can_enable = CourseLiveConfiguration.get(course_key) is not None
        return {
            "enable": can_enable,
            "configure": True,
        }
