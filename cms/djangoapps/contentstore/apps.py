"""
Contentstore Application Configuration

Above-modulestore level signal handlers are connected here.
"""


from django.apps import AppConfig


class ContentstoreConfig(AppConfig):
    """
    Application Configuration for Contentstore.
    """
    name = 'cms.djangoapps.contentstore'

    def ready(self):
        """
        Connect handlers to signals.
        """
        # Can't import models at module level in AppConfigs, and models get
        # included from the signal handlers
        from .signals import handlers  # pylint: disable=unused-import  # noqa: F401
