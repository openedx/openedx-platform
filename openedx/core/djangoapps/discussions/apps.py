"""
Configure the django app
"""
from django.apps import AppConfig
from edx_django_utils.plugins import PluginSettings, PluginURLs

from openedx.core.djangoapps.plugins.constants import ProjectType


class DiscussionsConfig(AppConfig):
    """
    Configure the discussions django app
    """
    name = 'openedx.core.djangoapps.discussions'
    plugin_app = {
        PluginURLs.CONFIG: {
            # TODO: Remove the LMS path once its usage has been removed from frontend-app-course-authoring.
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: r'^discussions/api/',
                PluginURLs.RELATIVE_PATH: 'urls',
            },
            ProjectType.CMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: r'^api/discussions/',
                PluginURLs.RELATIVE_PATH: 'urls',
            },
        },
        PluginSettings.CONFIG: {
        },
    }

    def ready(self):
        from . import handlers  # pylint: disable=unused-import
