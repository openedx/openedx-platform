"""
Configuration for enterprise_support
"""


from django.apps import AppConfig


class EnterpriseSupportConfig(AppConfig):
    """
    Configuration class for enterprise_support
    """
    name = 'openedx.features.enterprise_support'

    def ready(self):
        # Import signals to activate signal handler for enterprise.
        from . import signals  # pylint: disable=unused-import  # noqa: F401
