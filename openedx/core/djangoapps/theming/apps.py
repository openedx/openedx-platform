# pylint: disable=missing-module-docstring

from django.apps import AppConfig
from edx_django_utils.plugins import PluginURLs

from openedx.core.djangoapps.plugins.constants import ProjectType

plugin_urls_config = {PluginURLs.NAMESPACE: 'theming', PluginURLs.REGEX: r'^theming/'}


class ThemingConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.theming'
    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.CMS: plugin_urls_config,
            ProjectType.LMS: plugin_urls_config,
        }
    }
    verbose_name = "Theming"

    def ready(self):
        # settings validations related to theming.
        from . import checks  # pylint: disable=unused-import  # noqa: F401
