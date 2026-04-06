"""
Instructor Application Configuration
"""


from django.apps import AppConfig
from django.conf import settings
from edx_django_utils.plugins import PluginSettings, PluginURLs
from edx_proctoring.runtime import set_runtime_service

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType


class InstructorConfig(AppConfig):
    """
    Application Configuration for Instructor.
    """
    name = 'lms.djangoapps.instructor'

    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: '',
                PluginURLs.RELATIVE_PATH: 'urls',
            }
        },
        PluginSettings.CONFIG: {
            ProjectType.LMS: {
                SettingsType.DEVSTACK: {PluginSettings.RELATIVE_PATH: 'settings.devstack'},
                SettingsType.PRODUCTION: {PluginSettings.RELATIVE_PATH: 'settings.production'},
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
                SettingsType.TEST: {PluginSettings.RELATIVE_PATH: 'settings.test'},
            }
        }
    }

    def ready(self):
        from . import handlers  # pylint: disable=unused-import,import-outside-toplevel  # noqa: F401
        if settings.FEATURES.get('ENABLE_SPECIAL_EXAMS'):
            from .services import InstructorService
            set_runtime_service('instructor', InstructorService())
