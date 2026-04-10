"""
Test helpers for Comprehensive Theming.
"""


import contextlib
import os
import os.path
import re
from functools import wraps
from unittest.mock import patch

from django.conf import settings
from django.contrib.sites.models import Site

from common.djangoapps import edxmako
from openedx.core.djangoapps.theming.models import SiteTheme


def with_comprehensive_theme(theme_dir_name):
    """
    A decorator to run a test with a comprehensive theming enabled.
    Arguments:
        theme_dir_name (str): directory name of the site for which we want comprehensive theming enabled.
    """
    # This decorator creates Site and SiteTheme models for given domain
    def _decorator(func):                       # pylint: disable=missing-docstring
        @wraps(func)
        def _decorated(*args, **kwargs):        # pylint: disable=missing-docstring
            # make a domain name out of directory name
            domain = "{theme_dir_name}.org".format(theme_dir_name=re.sub(r"\.org$", "", theme_dir_name))
            site, __ = Site.objects.get_or_create(domain=domain, name=domain)
            site_theme, __ = SiteTheme.objects.get_or_create(site=site, theme_dir_name=theme_dir_name)

            with patch('openedx.core.djangoapps.theming.helpers.get_current_site_theme',
                       return_value=site_theme):
                with patch('openedx.core.djangoapps.theming.helpers.get_current_site', return_value=site):
                    return func(*args, **kwargs)
        return _decorated
    return _decorator


@contextlib.contextmanager
def with_comprehensive_theme_context(theme=None):
    """
    A function to run a test as if request was made to the given theme.

    Arguments:
        theme (str): name if the theme or None if no theme is applied

    """
    if theme:
        domain = '{theme}.org'.format(theme=re.sub(r"\.org$", "", theme))
        site, __ = Site.objects.get_or_create(domain=domain, name=theme)
        site_theme, __ = SiteTheme.objects.get_or_create(site=site, theme_dir_name=theme)

        with patch('openedx.core.djangoapps.theming.helpers.get_current_site_theme',
                   return_value=site_theme):
            with patch('openedx.core.djangoapps.theming.helpers.get_current_site', return_value=site):
                yield
    else:
        yield


def dump_theming_info():
    """Dump a bunch of theming information, for debugging."""
    for namespace, lookup in edxmako.LOOKUP.items():
        print("--- {}: {}".format(namespace, lookup.template_args['module_directory']))
        for directory in lookup.directories:
            print(f"  {directory}")

    print("=" * 80)
    for dirname, __, filenames in os.walk(settings.MAKO_MODULE_DIR):
        print(f"{dir} ----------------")
        for filename in sorted(filenames):
            if filename.endswith(".pyc"):
                continue
            with open(os.path.join(dirname, filename)) as f:
                content = len(f.read())
            print("    %s: %d" % (filename, content))  # noqa: UP031
