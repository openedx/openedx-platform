"""
ProgramEnrollments Application Configuration
"""


from django.apps import AppConfig
from edx_django_utils.plugins import PluginURLs

from openedx.core.djangoapps.plugins.constants import ProjectType


class ProgramEnrollmentsConfig(AppConfig):
    """
    Application configuration for ProgramEnrollment
    """
    name = 'lms.djangoapps.program_enrollments'

    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: 'programs_api',
                PluginURLs.REGEX: 'api/program_enrollments/',
                PluginURLs.RELATIVE_PATH: 'rest_api.urls',
            }
        },
    }

    def ready(self):
        """
        Connect handlers to signals.
        """
        from lms.djangoapps.program_enrollments import (  # pylint: disable=unused-import
            signals,
            tasks,
        )
