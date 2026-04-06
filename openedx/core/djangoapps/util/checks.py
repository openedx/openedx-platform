"""
Miscellaneous system checks
"""
from django.conf import settings
from django.core import checks

_DEVSTACK_SETTINGS_MODULES = [
    "lms.envs.devstack",
    "cms.envs.devstack",
]


@checks.register(checks.Tags.compatibility)
def warn_if_devstack_settings(**kwargs):
    """
    Raises a warning if we're using any Devstack settings file.
    """
    if settings.SETTINGS_MODULE in _DEVSTACK_SETTINGS_MODULES:
        return [
            checks.Warning(
                "Open edX Devstack is deprecated, so the Django settings module you are using "
                f"({settings.SETTINGS_MODULE}) will be removed from openedx/edx-platform in 2025. "
                "Please either migrate off of Devstack, or modify your Devstack fork to work with an externally-"
                "managed Django settings file. "
                "For details and discussion, see: https://github.com/openedx/public-engineering/issues/247.",
                id="openedx.core.djangoapps.util.W247",
            ),
        ]
    return []
