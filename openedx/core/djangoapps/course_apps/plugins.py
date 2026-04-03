"""
Course Apps plugin base class and plugin manager.
"""
from abc import ABC, abstractmethod
from typing import Dict, Iterator, Optional

from edx_django_utils.plugins import PluginManager
from opaque_keys.edx.keys import CourseKey

# Stevedore extension point namespaces
COURSE_APPS_PLUGIN_NAMESPACE = "openedx.course_app"


class CourseApp(ABC):
    """
    Abstract base class for all course app plugins.
    """

    # A unique ID for the app.
    app_id: str = ""
    # A friendly name for the app.
    name: str = ""
    # A description for the app.
    description: str = ""
    # A map of documentation links for the app
    documentation_links: Dict = {
        # eg:
        # "learn_more_configuration": "https://..."
    }

    @classmethod
    @abstractmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Returns a boolean indicating this course app's availability for a given course.

        If an app is not available, it will not show up in the UI at all for that course,
        and it will not be possible to enable/disable/configure it.

        Args:
            course_key (CourseKey): Course key for course whose availability is being checked.

        Returns:
            bool: Availability status of app.
        """

    @classmethod
    @abstractmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Return if this course app is enabled for the provided course.

        Args:
            course_key (CourseKey): The course key for the course you
                want to check the status of.

        Returns:
            bool: The status of the course app for the specified course.
        """

    @classmethod
    @abstractmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Update the status of this app for the provided course and return the new status.

        Args:
            course_key (CourseKey): The course key for the course for which the app should be enabled.
            enabled (bool): The new status of the app.
            user (User): The user performing this operation.

        Returns:
            bool: The new status of the course app.
        """

    @classmethod
    @abstractmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional['User'] = None) -> Dict[str, bool]:
        """
        Returns a dictionary of available operations for this app.

        Not all apps will support being configured, and some may support
        other operations via the UI. This will list, the minimum whether
        the app can be enabled/disabled and whether it can be configured.

        Args:
            course_key (CourseKey): The course key for a course.
            user (User): The user for which the operation is to be tested.

        Returns:
            A dictionary that has keys like 'enable', 'configure' etc
            with values indicating whether those operations are allowed.
        """


class CourseAppsPluginManager(PluginManager):
    """
    Plugin manager to get all course all plugins.
    """

    NAMESPACE = COURSE_APPS_PLUGIN_NAMESPACE

    @classmethod
    def get_apps_available_for_course(cls, course_key: CourseKey) -> Iterator[CourseApp]:
        """
        Yields all course apps that are available for the provided course.

        Args:
            course_key (CourseKey): The course key for which the list of apps is to be yielded.

        Yields:
            CourseApp: A CourseApp plugin instance.
        """
        for plugin in super().get_available_plugins().values():
            if plugin.is_available(course_key):
                yield plugin
