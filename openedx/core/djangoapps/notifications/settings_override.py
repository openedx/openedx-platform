"""
Settings override module for notification configurations.

This module provides functionality to override notification configurations
via Django settings.
"""
from copy import deepcopy
from typing import Dict, Set, Any
from django.conf import settings


def _apply_overrides(
    default_config: Dict[str, Any],
    setting_name: str,
    allowed_keys: Set[str]
) -> Dict[str, Any]:
    """
    Internal helper to apply settings overrides to a default configuration dictionary.

    Args:
        default_config: The base dictionary to copy.
        setting_name: The name of the Django setting to check for overrides.
        allowed_keys: A set of keys that are permitted to be overridden.
    """
    config = deepcopy(default_config)
    overrides = getattr(settings, setting_name, {})
    for name, override_data in overrides.items():
        if name in config:
            # efficient filtering and updating
            valid_updates = {
                k: v for k, v in override_data.items()
                if k in allowed_keys
            }
            config[name].update(valid_updates)

    return config


def get_notification_types_config() -> Dict[str, Any]:
    """
    Get COURSE_NOTIFICATION_TYPES configuration with settings overrides applied.
    """
    from .base_notification import _COURSE_NOTIFICATION_TYPES as DEFAULT_TYPES

    return _apply_overrides(
        default_config=DEFAULT_TYPES,
        setting_name='NOTIFICATION_TYPES_OVERRIDE',
        allowed_keys={'web', 'email', 'push', 'non_editable', 'email_cadence'}
    )


def get_notification_apps_config() -> Dict[str, Any]:
    """
    Get COURSE_NOTIFICATION_APPS configuration with settings overrides applied.
    """
    from .base_notification import _COURSE_NOTIFICATION_APPS as DEFAULT_APPS

    return _apply_overrides(
        default_config=DEFAULT_APPS,
        setting_name='NOTIFICATION_APPS_OVERRIDE',
        allowed_keys={'web', 'email', 'push', 'non_editable', 'email_cadence'}
    )
