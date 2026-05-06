"""
Helpers for accessing comprehensive theming related variables.

This file is imported at startup. Imports of models or things which import models will break startup on Django 1.9+. If
you need models here, please import them inside the function which uses them.
"""


import os
import re
from functools import lru_cache
from logging import getLogger

import crum
from django.conf import settings
from edx_toggles.toggles import SettingToggle

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers_dirs import (
    Theme,
    get_project_root_name_from_settings,
    get_theme_base_dirs_from_settings,
    get_theme_dirs,
    get_themes_unchecked,
)
from openedx.core.lib.cache_utils import request_cached

logger = getLogger(__name__)  # pylint: disable=invalid-name


@request_cached()
def get_template_path(relative_path, **kwargs):  # pylint: disable=unused-argument
    """
    The calculated value is cached for the lifetime of the current request.
    """
    return relative_path


def is_request_in_themed_site():
    """
    This is a proxy function to site_configuration.
    """
    return configuration_helpers.is_site_configuration_enabled()


def get_template_path_with_theme(relative_path):
    """
    Returns template path in current site's theme if it finds one there otherwise returns same path.

    Example:
        >> get_template_path_with_theme('header.html')
        '/red-theme/lms/templates/header.html'

    Parameters:
        relative_path (str): template's path relative to the templates directory e.g. 'footer.html'

    Returns:
        (str): template path in current site's theme
    """
    relative_path = os.path.normpath(relative_path)

    theme = get_current_theme()

    if not theme:
        return relative_path

    # strip `/` if present at the start of relative_path
    template_name = re.sub(r'^/+', '', relative_path)

    template_path = theme.template_path / template_name
    absolute_path = theme.path / "templates" / template_name
    if absolute_path.exists():
        return str(template_path)
    else:
        return relative_path


def get_all_theme_template_dirs():
    """
    Returns template directories for all the themes.

    Example:
        >> get_all_theme_template_dirs()
        [
            '/edx/app/edxapp/edx-platform/themes/red-theme/lms/templates/',
        ]

    Returns:
        (list): list of directories containing theme templates.
    """
    themes = get_themes()
    template_paths = []

    for theme in themes:
        template_paths.extend(theme.template_dirs)

    return template_paths


def get_project_root_name():
    """
    Return root name for the current project

    Example:
        >> get_project_root_name()
        'lms'
        # from studio
        >> get_project_root_name()
        'cms'

    Returns:
        (str): component name of platform e.g lms, cms
    """
    return get_project_root_name_from_settings(settings.PROJECT_ROOT)


def strip_site_theme_templates_path(uri):
    """
    Remove site template theme path from the uri.

    Example:
        >> strip_site_theme_templates_path('/red-theme/lms/templates/header.html')
        'header.html'

    Arguments:
        uri (str): template path from which to remove site theme path. e.g. '/red-theme/lms/templates/header.html'

    Returns:
        (str): template path with site theme path removed.
    """
    theme = get_current_theme()

    if not theme:
        return uri

    templates_path = "/".join([
        theme.theme_dir_name,
        get_project_root_name(),
        "templates"
    ])

    uri = re.sub(r'^/*' + templates_path + '/*', '', uri)
    return uri


def get_current_request():
    """
    Return current request instance.

    Returns:
         (HttpRequest): returns current request
    """
    return crum.get_current_request()


def get_current_site():
    """
    Return current site.

    Returns:
         (django.contrib.sites.models.Site): returns current site
    """
    request = get_current_request()
    if not request:
        return None
    return getattr(request, 'site', None)


def get_current_site_theme():
    """
    Return current site theme object. Returns None if theming is disabled.

    Returns:
         (ecommerce.theming.models.SiteTheme): site theme object for the current site.
    """
    # Return None if theming is disabled
    if not is_comprehensive_theming_enabled():
        return None

    request = get_current_request()
    if not request:
        return None
    return getattr(request, 'site_theme', None)


def get_current_theme():
    """
    Return current theme object. Returns None if theming is disabled.

    Returns:
         (ecommerce.theming.models.SiteTheme): site theme object for the current site.
    """
    # Return None if theming is disabled
    if not is_comprehensive_theming_enabled():
        return None

    site_theme = get_current_site_theme()
    if not site_theme:
        return None
    try:
        return Theme(
            name=site_theme.theme_dir_name,
            theme_dir_name=site_theme.theme_dir_name,
            themes_base_dir=get_theme_base_dir(site_theme.theme_dir_name),
            project_root=get_project_root_name()
        )
    except ValueError as error:
        # Log exception message and return None, so that open source theme is used instead
        logger.exception('Theme not found in any of the themes dirs. [%s]', error)
        return None


def current_request_has_associated_site_theme():
    """
    True if current request has an associated SiteTheme, False otherwise.

    Returns:
        True if current request has an associated SiteTheme, False otherwise
    """
    request = get_current_request()
    site_theme = getattr(request, 'site_theme', None)
    return bool(site_theme and site_theme.id)


def get_theme_base_dir(theme_dir_name, suppress_error=False):
    """
    Returns absolute path to the directory that contains the given theme.

    Args:
        theme_dir_name (str): theme directory name to get base path for
        suppress_error (bool): if True function will return None if theme is not found instead of raising an error
    Returns:
        (str): Base directory that contains the given theme
    """
    for themes_dir in get_theme_base_dirs():
        if theme_dir_name in get_theme_dirs(themes_dir):
            return themes_dir

    if suppress_error:
        return None

    raise ValueError(
        "Theme '{theme}' not found in any of the following themes dirs, \nTheme dirs: \n{dir}".format(  # noqa: UP032
            theme=theme_dir_name,
            dir=get_theme_base_dirs(),
        ))


def theme_exists(theme_name, themes_dir=None):
    """
    Returns True if a theme exists with the specified name.
    """
    for theme in get_themes(themes_dir=themes_dir):
        if theme.theme_dir_name == theme_name:
            return True
    return False


@lru_cache
def get_themes(themes_dir=None):
    """
    get a list of all themes known to the system.

    Args:
        themes_dir (str): (Optional) Path to themes base directory
    Returns:
        list of themes known to the system.
    """
    if not is_comprehensive_theming_enabled():
        return []
    if themes_dir:
        themes_dirs = [themes_dir]
    else:
        themes_dirs = get_theme_base_dirs_unchecked()
    return get_themes_unchecked(themes_dirs, settings.PROJECT_ROOT)


def get_theme_base_dirs_unchecked():
    """
    Return base directories that contains all the themes.

    Example:
        >> get_theme_base_dirs_unchecked()
        ['/edx/app/ecommerce/ecommerce/themes']

    Returns:
         (List of Paths): Base theme directory paths
    """
    theme_dirs = getattr(settings, "COMPREHENSIVE_THEME_DIRS", None)

    return get_theme_base_dirs_from_settings(theme_dirs)


def get_theme_base_dirs():
    """
    Return base directories that contains all the themes.
    Ensures comprehensive theming is enabled.

    Example:
        >> get_theme_base_dirs()
        ['/edx/app/ecommerce/ecommerce/themes']

    Returns:
         (List of Paths): Base theme directory paths
    """
    # Return an empty list if theming is disabled
    if not is_comprehensive_theming_enabled():
        return []
    return get_theme_base_dirs_unchecked()


def is_comprehensive_theming_enabled():
    """
    Returns boolean indicating whether comprehensive theming functionality is enabled or disabled.
    Example:
        >> is_comprehensive_theming_enabled()
        True

    Returns:
         (bool): True if comprehensive theming is enabled else False
    """
    ENABLE_COMPREHENSIVE_THEMING = SettingToggle("ENABLE_COMPREHENSIVE_THEMING", default=False)

    if ENABLE_COMPREHENSIVE_THEMING.is_enabled() and current_request_has_associated_site_theme():
        return True

    return ENABLE_COMPREHENSIVE_THEMING.is_enabled()


def get_config_value_from_site_or_settings(name, site=None, site_config_name=None):
    """
    Given a configuration setting name, try to get it from the site configuration and then fall back on the settings.

    If site_config_name is not specified then "name" is used as the key for both collections.

    Args:
        name (str): The name of the setting to get the value of.
        site: The site that we are trying to fetch the value for.
        site_config_name: The name of the setting within the site configuration.

    Returns:
        The value stored in the configuration.
    """
    from openedx.core.djangoapps.site_configuration.models import SiteConfiguration

    if site_config_name is None:
        site_config_name = name

    if site is None:
        site = get_current_site()

    site_configuration = None
    if site is not None:
        try:
            site_configuration = getattr(site, "configuration", None)
        except SiteConfiguration.DoesNotExist:
            pass

    value_from_settings = getattr(settings, name, None)
    if site_configuration is not None:
        return site_configuration.get_value(site_config_name, default=value_from_settings)
    else:
        return value_from_settings
