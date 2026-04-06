"""
Configuration for CCX connector
"""


from django.apps import AppConfig


class CCXConnectorConfig(AppConfig):  # lint-amnesty, pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.ccxcon'
    verbose_name = "CCX Connector"

    def ready(self):
        from . import signals  # lint-amnesty, pylint: disable=unused-import  # noqa: F401
