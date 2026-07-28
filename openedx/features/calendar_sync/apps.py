"""
Define the calendar_sync Django App.
"""

# -*- coding: utf-8 -*-


from django.apps import AppConfig


class UserCalendarSyncConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.features.calendar_sync'

    def ready(self):
        super().ready()

        # noinspection PyUnresolvedReferences
        import openedx.features.calendar_sync.signals  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
