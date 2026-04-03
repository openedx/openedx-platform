"""
Discussion Application Configuration

Signal handlers are connected here.
"""


from django.apps import AppConfig
from edx_django_utils.plugins import PluginSettings, PluginURLs

from openedx.core.constants import COURSE_ID_PATTERN
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType


class DiscussionConfig(AppConfig):
    """
    Application Configuration for Discussion.
    """

    name = 'lms.djangoapps.discussion'
    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: fr'^courses/{COURSE_ID_PATTERN}/discussion/forum/',
                PluginURLs.RELATIVE_PATH: 'urls',
            }
        },
        PluginSettings.CONFIG: {
            ProjectType.CMS: {
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
            },
            ProjectType.LMS: {
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
            },
        }
    }

    def ready(self):
        """
        Connect handlers to send notifications about discussions.
        """
        from .rest_api import tasks  # pylint: disable=unused-import
        from .signals import handlers  # pylint: disable=unused-import
