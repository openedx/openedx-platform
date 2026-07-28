"""
Configuration for CCX connector
"""


from django.apps import AppConfig


class CCXConnectorConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.ccxcon'
    verbose_name = "CCX Connector"

    def ready(self):
        from . import signals  # pylint: disable=unused-import  # noqa: F401
