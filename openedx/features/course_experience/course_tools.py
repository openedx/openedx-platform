"""
Support for course tool plugins.
"""


from enum import Enum

from edx_django_utils.plugins import PluginManager

# Stevedore extension point namespace
COURSE_TOOLS_NAMESPACE = 'openedx.course_tool'


class HttpMethod(Enum):
    """ Enum for HTTP Methods """
    DELETE = 'DELETE'
    GET = 'GET'
    OPTIONS = 'OPTIONS'
    POST = 'POST'
    PUT = 'PUT'


class CourseTool:
    """
    This is an optional base class for Course Tool plugins.

    Plugin implementations inside this repo should subclass CourseTool to get
    useful default behavior, and to add clarity to the code.  This base class is
    not a requirement, and plugin implementations outside of this repo should
    simply follow the contract defined below.
    """
    http_method = HttpMethod.GET

    @classmethod
    def analytics_id(cls):
        """
        Returns an id to uniquely identify this tool in analytics events.

        For example, 'edx.bookmarks'.  New tools may warrant doc updates for the new id.
        """
        raise NotImplementedError("Must specify an id to enable course tool eventing.")

    @classmethod
    def is_enabled(cls, request, course_key):  # pylint: disable=unused-argument
        """
        Returns true if this tool is enabled for the specified course key.
        """
        return True

    @classmethod
    def title(cls, course_key):
        """
        Returns the title for the course tool.
        """
        raise NotImplementedError("Must specify a title for a course tool.")

    @classmethod
    def icon_classes(cls, course_key):
        """
        Returns the icon classes needed to represent this tool.

        For example, return an icon from font-awasome.css, like 'fa fa-star'.
        """
        raise NotImplementedError("Must specify an icon for a course tool.")

    @classmethod
    def url(cls, course_key):
        """
        Returns the URL for this tool for the specified course key.
        """
        raise NotImplementedError("Must specify a url for a course tool.")

    @classmethod
    def data(cls):
        """
        Additional data to send with a form submission
        """
        if cls.http_method == HttpMethod.POST:
            return {}
        else:
            return None


class CourseToolsPluginManager(PluginManager):
    """
    Manager for all of the course tools that have been made available.

    Course tool implementation can subclass `CourseTool` or can implement
    the required class methods themselves.
    """
    NAMESPACE = COURSE_TOOLS_NAMESPACE

    @classmethod
    def get_course_tools(cls):
        """
        Returns the list of available course tools in their canonical order.
        """
        course_tools = list(cls.get_available_plugins().values())
        course_tools.sort(key=lambda course_tool: course_tool.title())
        return course_tools

    @classmethod
    def get_enabled_course_tools(cls, request, course_key):
        """
        Returns the course tools applicable to the current user and course.
        """
        course_tools = CourseToolsPluginManager.get_course_tools()
        return [tool for tool in course_tools if tool.is_enabled(request, course_key)]
