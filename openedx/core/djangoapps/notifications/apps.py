"""
Config for notifications app
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """
    Config for notifications app
    """
    name = 'openedx.core.djangoapps.notifications'
    verbose_name = 'Notifications'

    def ready(self):
        """
        Import signals
        """
        # pylint: disable=unused-import
        from . import handlers  # noqa: F401
        from .email import tasks  # noqa: F401
