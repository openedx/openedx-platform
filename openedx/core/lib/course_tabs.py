"""
Tabs for courseware.
"""

from functools import cmp_to_key  # lint-amnesty, pylint: disable=wrong-import-order

from edx_django_utils.plugins import PluginManager

# Stevedore extension point namespaces
COURSE_TAB_NAMESPACE = 'openedx.course_tab'


class CourseTabPluginManager(PluginManager):
    """
    Manager for all of the course tabs that have been made available.

    All course tabs should implement `CourseTab`.
    """
    NAMESPACE = COURSE_TAB_NAMESPACE

    @classmethod
    def get_tab_types(cls):
        """
        Returns the list of available course tabs in their canonical order.
        """
        def compare_tabs(first_type, second_type):
            """Compares two course tabs, for use in sorting."""
            first_priority = first_type.priority
            second_priority = second_type.priority
            if first_priority != second_priority:
                if first_priority is None:
                    return 1
                elif second_priority is None:
                    return -1
                else:
                    return first_priority - second_priority
            first_type = first_type.type
            second_type = second_type.type
            if first_type < second_type:
                return -1
            elif first_type == second_type:
                return 0
            else:
                return 1
        tab_types = list(cls.get_available_plugins().values())
        tab_types.sort(key=cmp_to_key(compare_tabs))
        return tab_types
