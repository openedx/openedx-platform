"""
Django App Configuration for the course_date_signals app
"""

from django.apps import AppConfig


class CourseDatesSignalsConfig(AppConfig):  # lint-amnesty, pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.course_date_signals'

    def ready(self):
        """
        Connect handlers to signals.
        """
        from . import handlers  # lint-amnesty, pylint: disable=unused-import, unused-variable  # noqa: F401
