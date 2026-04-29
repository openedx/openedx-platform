"""
Django App Configuration for the course_date_signals app
"""

from django.apps import AppConfig


class CourseDatesSignalsConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.course_date_signals'

    def ready(self):
        """
        Connect handlers to signals.
        """
        from . import handlers  # pylint: disable=unused-import, unused-variable  # noqa: F401
