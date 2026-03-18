"""Course app config for courseware apps."""
from typing import Dict, Optional

from django import urls
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_noop as _
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import modulestore
from xmodule.tabs import CourseTabList

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview, CourseTab

from openedx.core.djangoapps.course_apps.plugins import CourseApp
from openedx.core.lib.courses import get_course_by_id

User = get_user_model()

TEXTBOOK_ENABLED = settings.FEATURES.get("ENABLE_TEXTBOOK", False)


class ProgressCourseApp(CourseApp):
    """
    Course app config for progress app.
    """

    app_id = "progress"
    name = _("Progress")
    description = _("Keep learners engaged and on track throughout the course.")
    documentation_links = {
        "learn_more_configuration": settings.PROGRESS_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        The progress course app is always available.
        """
        return True

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        The progress course status is stored in the course block.
        """
        return not CourseOverview.get_from_id(course_key).hide_progress_tab

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        The progress course enabled/disabled status is stored in the course block.
        """
        course = get_course_by_id(course_key)
        course.hide_progress_tab = not enabled
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Returns the allowed operations for the app.
        """
        return {
            "enable": True,
            "configure": True,
        }


class DatesCourseApp(CourseApp):
    """Course app stub for course dates."""

    app_id = "dates"
    name = _("Dates")
    description = _("Provide learners a summary of important course dates.")
    documentation_links = {
        "learn_more_configuration": getattr(settings, "DATES_HELP_URL", ""),
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Dates app is available when explicitly enabled via settings.
        """
        return settings.ENABLE_DATES_COURSE_APP

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        The dates course status is stored in the course block.
        """
        course = get_course_by_id(course_key)
        dates_tab = CourseTabList.get_tab_by_id(course.tabs, 'dates')
        return bool(dates_tab and not dates_tab.is_hidden)

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        The dates course enabled/disabled status is stored in the course block.
        """
        course = get_course_by_id(course_key)
        dates_tab = CourseTabList.get_tab_by_id(course.tabs, 'dates')
        if enabled and dates_tab is None:
            dates_tab = CourseTab.load("dates")
            course.tabs.append(dates_tab)
        if dates_tab is not None:
            dates_tab.is_hidden = not enabled
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:  # pylint: disable=unused-argument
        """
        Returns the allowed operations for the app.
        """
        return {
            "enable": True,
            "configure": True,
        }


class TextbooksCourseApp(CourseApp):
    """
    Course app config for textbooks app.
    """

    app_id = "textbooks"
    name = _("Textbooks")
    description = _("Create and manage a library of course readings, textbooks, and chapters.")
    documentation_links = {
        "learn_more_configuration": settings.TEXTBOOKS_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        The textbook app can be made available globally using a value in features.
        """
        return TEXTBOOK_ENABLED

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Returns if the textbook app is globally enabled.
        """
        return len(CourseOverview.get_from_id(course_key).pdf_textbooks) > 0

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        The textbook app can be globally enabled/disabled.

        Currently, it isn't possible to enable/disable this app on a per-course basis.
        """
        raise ValueError("The textbook app can not be enabled/disabled for a single course.")

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Returns the allowed operations for the app.
        """
        return {
            # Either the app is available and configurable or not. You cannot disable it from the API yet.
            "enable": False,
            "configure": True,
        }

    @staticmethod
    def legacy_link(course_key: CourseKey):
        return urls.reverse('textbooks_list_handler', kwargs={'course_key_string': course_key})


class CalculatorCourseApp(CourseApp):
    """
    Course App config for calculator app.
    """

    app_id = "calculator"
    name = _("Calculator")
    description = _("Provide an in-course calculator for simple and complex calculations.")
    documentation_links = {
        "learn_more_configuration": settings.CALCULATOR_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Calculator is available for all courses.
        """
        return True

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Get calculator enabled status from course overview model.
        """
        return CourseOverview.get_from_id(course_key).show_calculator

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Update calculator enabled status in modulestore.
        """
        course = get_course_by_id(course_key)
        course.show_calculator = enabled
        modulestore().update_item(course, user.id)
        return enabled

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Get allowed operations for calculator app.
        """
        return {
            "enable": True,
            # There is nothing to configure for calculator yet.
            "configure": False,
        }


class ProctoringCourseApp(CourseApp):
    """
    Course App config for proctoring app.
    """

    app_id = "proctoring"
    name = _("Proctoring")
    description = _("Maintain exam integrity by enabling a proctoring solution for your course")
    documentation_links = {
        "learn_more_configuration": settings.PROCTORING_SETTINGS.get(
            'LINK_URLS', {}
        ).get('course_authoring_faq', ''),
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Returns true if the proctoring app is available for all courses.
        """
        return settings.FEATURES.get('ENABLE_PROCTORED_EXAMS')

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Get proctoring enabled status from course overview model.
        """
        return CourseOverview.get_from_id(course_key).enable_proctored_exams

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Don't allow proctored exam settings to be enabled from the card
        """
        raise ValueError("Proctoring cannot be enabled/disabled via this API.")

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Get allowed operations for proctoring app.
        """
        return {
            "enable": False,
            "configure": True,
        }


class CustomPagesCourseApp(CourseApp):
    """
    Course app config for custom pages app.
    """

    app_id = "custom_pages"
    name = _("Custom pages")
    description = _("Provide additional course content and resources with custom pages")
    documentation_links = {
        "learn_more_configuration": settings.CUSTOM_PAGES_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        The custom pages app is available for all courses.
        """
        return True

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:  # pylint: disable=unused-argument
        """
        Returns if the custom pages app is enabled.
        For now this feature is disabled without any manual setup
        """
        return False

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        The custom pages app can be globally enabled/disabled.

        Currently, it isn't possible to enable/disable this app on a per-course basis.
        """
        raise ValueError("The custom pages app can not be enabled/disabled for a single course.")

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Returns the allowed operations for the app.
        """
        return {
            # Either the app is available and configurable or not. You cannot disable it from the API yet.
            "enable": False,
            "configure": True,
        }

    @staticmethod
    def legacy_link(course_key: CourseKey):
        return urls.reverse('tabs_handler', kwargs={'course_key_string': course_key})


class ORASettingsApp(CourseApp):
    """
    Course App config for Flexible Peer Grading ORA app.
    """

    app_id = "ora_settings"
    name = _("Flexible Peer Grading for ORAs")
    description = _("Course level settings for Flexible Peer Grading Open Response Assessments.")
    documentation_links = {
        "learn_more_configuration": settings.ORA_SETTINGS_HELP_URL,
    }

    @classmethod
    def is_available(cls, course_key: CourseKey) -> bool:
        """
        Open response is available for course with at least one ORA.
        """
        oras = modulestore().get_items(course_key, qualifiers={'category': 'openassessment'})
        return len(oras) > 0

    @classmethod
    def is_enabled(cls, course_key: CourseKey) -> bool:
        """
        Get open response enabled status from course overview model.
        """
        course = get_course_by_id(course_key)
        return course.force_on_flexible_peer_openassessments

    @classmethod
    def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:
        """
        Update open response enabled status in modulestore. Always enable to avoid confusion that user can disable ora.
        """
        raise ValueError("Flexible Peer Grading cannot be enabled/disabled via this API.")

    @classmethod
    def get_allowed_operations(cls, course_key: CourseKey, user: Optional[User] = None) -> Dict[str, bool]:
        """
        Get allowed operations for open response app.
        """
        return {
            "enable": False,
            "configure": True,
        }
