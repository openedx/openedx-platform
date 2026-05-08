"""
Entitlements Application Configuration

Signal handlers are connected here.
"""


from django.apps import AppConfig


class EntitlementsConfig(AppConfig):
    """
    Application Configuration for Entitlements.
    """
    name = 'common.djangoapps.entitlements'

    def ready(self):
        """
        Connect handlers to signals.
        """
        from . import signals  # pylint: disable=unused-import  # noqa: F401
        from .tasks import expire_old_entitlements  # pylint: disable=unused-import  # noqa: F401
