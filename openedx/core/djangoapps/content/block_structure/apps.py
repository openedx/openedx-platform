"""
Configuration for block_structure djangoapp
"""


from django.apps import AppConfig


class BlockStructureConfig(AppConfig):
    """
    block_structure django app.
    """
    name = 'openedx.core.djangoapps.content.block_structure'

    def ready(self):
        """
        Define tasks to perform at app loading time

        * Connect signal handlers
        * Register celery tasks

        These happen at import time.  Hence the unused imports
        """
        from . import signals, tasks  # pylint: disable=unused-import, unused-variable  # noqa: F401
