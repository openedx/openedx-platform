"""
LMS CCX application configuration
Signal handlers are connected here.
"""
from django.apps import AppConfig


class CCXConfig(AppConfig):
    """
    Application Configuration for CCX.
    """
    name = 'lms.djangoapps.ccx'

    def ready(self):
        """
        Connect signal handlers.
        """
        from . import tasks  # pylint: disable=unused-import  # noqa: F401
