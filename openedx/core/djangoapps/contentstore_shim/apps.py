"""
contentstore_shim Django application initialization.
"""

from django.apps import AppConfig


class ContentstoreShimConfig(AppConfig):
    """
    Configuration for the contentstore_shim Django application.
    """

    name = "openedx.core.djangoapps.contentstore_shim"
    verbose_name = "Contentstore Shim (course Files stored in openedx_content)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """
        Connect signal handlers.
        """
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
