"""
Settings validations for the theming app
"""


import os

from django.conf import settings
from django.core.checks import Error, Tags, register
from edx_toggles.toggles import SettingToggle


@register(Tags.compatibility)
def check_comprehensive_theme_settings(app_configs, **kwargs):  # pylint: disable=unused-argument
    """
    Checks the comprehensive theming theme directory settings.

    Raises compatibility Errors upon:
        - COMPREHENSIVE_THEME_DIRS is not a list
        - theme dir path is not a string
        - theme dir path is not an absolute path
        - path specified in COMPREHENSIVE_THEME_DIRS does not exist

    Returns:
        List of any Errors.
    """
    if not SettingToggle("ENABLE_COMPREHENSIVE_THEMING", default=False).is_enabled():
        # Only perform checks when comprehensive theming is enabled.
        return []

    errors = []

    # COMPREHENSIVE_THEME_DIR is no longer supported - support has been removed.
    if hasattr(settings, "COMPREHENSIVE_THEME_DIR"):
        theme_dir = settings.COMPREHENSIVE_THEME_DIR

        errors.append(
            Error(
                "COMPREHENSIVE_THEME_DIR setting has been removed in favor of COMPREHENSIVE_THEME_DIRS.",
                hint='Transfer the COMPREHENSIVE_THEME_DIR value to COMPREHENSIVE_THEME_DIRS.',
                obj=theme_dir,
                id='openedx.core.djangoapps.theming.E001',
            )
        )

    if hasattr(settings, "COMPREHENSIVE_THEME_DIRS"):
        theme_dirs = settings.COMPREHENSIVE_THEME_DIRS

        if not isinstance(theme_dirs, list):
            errors.append(
                Error(
                    "COMPREHENSIVE_THEME_DIRS must be a list.",
                    obj=theme_dirs,
                    id='openedx.core.djangoapps.theming.E004',
                )
            )
        if not all(isinstance(theme_dir, str) for theme_dir in theme_dirs):
            errors.append(
                Error(
                    "COMPREHENSIVE_THEME_DIRS must contain only strings.",
                    obj=theme_dirs,
                    id='openedx.core.djangoapps.theming.E005',
                )
            )
        if not all(theme_dir.startswith("/") for theme_dir in theme_dirs):
            errors.append(
                Error(
                    "COMPREHENSIVE_THEME_DIRS must contain only absolute paths to themes dirs.",
                    obj=theme_dirs,
                    id='openedx.core.djangoapps.theming.E006',
                )
            )
        if not all(os.path.isdir(theme_dir) for theme_dir in theme_dirs):
            errors.append(
                Error(
                    "COMPREHENSIVE_THEME_DIRS must contain valid paths.",
                    obj=theme_dirs,
                    id='openedx.core.djangoapps.theming.E007',
                )
            )

    return errors
