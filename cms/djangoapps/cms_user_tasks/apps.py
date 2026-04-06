"""
CMS user tasks application configuration
Signal handlers are connected here.
"""


from django.apps import AppConfig


class CmsUserTasksConfig(AppConfig):
    """
    Application Configuration for cms_user_tasks.
    """
    name = 'cms.djangoapps.cms_user_tasks'

    def ready(self):
        """
        Connect signal handlers.
        """
        from . import signals  # pylint: disable=unused-import  # noqa: F401
