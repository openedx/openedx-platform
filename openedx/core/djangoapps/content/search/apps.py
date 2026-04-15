"""
Define the content search Django App.
"""

from django.apps import AppConfig


class ContentSearchConfig(AppConfig):
    """App config for the content search feature"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "openedx.core.djangoapps.content.search"

    def ready(self):
        # Connect signal handlers
        from . import handlers  # pylint: disable=unused-import  # noqa: F401
