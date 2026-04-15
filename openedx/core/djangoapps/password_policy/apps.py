"""
Configuration for password_policy Django app
"""

import logging

from dateutil.parser import parse as parse_date
from django.apps import AppConfig
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from edx_django_utils.plugins import PluginSettings

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType

log = logging.getLogger(__name__)


class PasswordPolicyConfig(AppConfig):
    """
    Configuration class for password_policy Django app
    """
    name = 'openedx.core.djangoapps.password_policy'
    verbose_name = _("Password Policy")

    plugin_app = {
        PluginSettings.CONFIG: {
            ProjectType.LMS: {
                SettingsType.PRODUCTION: {PluginSettings.RELATIVE_PATH: 'settings.production'},
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
                SettingsType.DEVSTACK: {PluginSettings.RELATIVE_PATH: 'settings.devstack'},
            },
            ProjectType.CMS: {
                SettingsType.PRODUCTION: {PluginSettings.RELATIVE_PATH: 'settings.production'},
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
                SettingsType.DEVSTACK: {PluginSettings.RELATIVE_PATH: 'settings.devstack'},
            }
        }
    }

    def ready(self):
        # Convert settings from strings to datetime objects, logging any problems
        self._parse_dates_safely(settings.PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG)

    def _parse_dates_safely(self, config):
        """
        Convert the string dates in a config file to datetime.datetime versions, logging any issues.
        """
        self._update_date_safely(config, 'STAFF_USER_COMPLIANCE_DEADLINE')
        self._update_date_safely(config, 'ELEVATED_PRIVILEGE_USER_COMPLIANCE_DEADLINE')
        self._update_date_safely(config, 'GENERAL_USER_COMPLIANCE_DEADLINE')

    def _update_date_safely(self, config, setting):
        """
        Updates a parsed datetime.datetime object for a given config setting name.
        """
        deadline = config.get(setting)
        try:
            if isinstance(deadline, str):
                config[setting] = parse_date(deadline)
        except (ValueError, OverflowError):
            log.exception("Could not parse %s password policy rollout value of '%s'.", setting, deadline)
            config[setting] = None
