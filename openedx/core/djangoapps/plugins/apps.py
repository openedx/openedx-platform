"""
Plugins Application Configuration

Signal handlers are connected here.
"""


from django.apps import AppConfig
from django.conf import settings
from edx_django_utils.plugins import PluginSettings, connect_plugin_receivers

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType


class PluginsConfig(AppConfig):
    """
    Application Configuration for Plugins.
    """

    name = 'openedx.core.djangoapps.plugins'

    plugin_app = {
        PluginSettings.CONFIG: {
            ProjectType.LMS: {
                SettingsType.PRODUCTION: {PluginSettings.RELATIVE_PATH: 'settings.production'},
            },
            ProjectType.CMS: {
                SettingsType.PRODUCTION: {PluginSettings.RELATIVE_PATH: 'settings.production'},
            },
        }
    }

    def ready(self):
        """
        Connect plugin receivers to their signals.
        """
        if settings.ROOT_URLCONF == 'lms.urls':
            project_type = ProjectType.LMS
        else:
            project_type = ProjectType.CMS

        connect_plugin_receivers(project_type)
