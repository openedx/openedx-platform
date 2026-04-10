"""
Define the content tagging Django App.
"""

from django.apps import AppConfig


class ContentTaggingConfig(AppConfig):
    """App config for the content tagging feature"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "openedx.core.djangoapps.content_tagging"

    def ready(self):
        # Connect signal handlers
        from . import handlers  # pylint: disable=unused-import  # noqa: F401
