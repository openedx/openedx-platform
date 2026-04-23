"""
Define the content search Django App.
"""

from django.apps import AppConfig


class ContentSearchConfig(AppConfig):
    """App config for the content search feature"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "openedx.core.djangoapps.content.search"
    label = "search"

    def ready(self):
        # Connect signal handlers
        # Connect post_migrate for Meilisearch index reconciliation.
        # No sender= argument here; the handler filters by sender.label internally.
        from django.db.models.signals import post_migrate  # pylint: disable=import-outside-toplevel

        from . import handlers  # pylint: disable=unused-import  # noqa: F401
        post_migrate.connect(handlers.handle_post_migrate)
