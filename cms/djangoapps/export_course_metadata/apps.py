"""
Define the export_course_metadata Django App.
"""

from django.apps import AppConfig


class ExportCourseMetadataConfig(AppConfig):
    """
    App for exporting a subset of course metadata
    """
    name = 'cms.djangoapps.export_course_metadata'

    def ready(self):
        """
        Connect signal handler that exports course metadata
        """
        from . import signals  # pylint: disable=unused-import  # noqa: F401
