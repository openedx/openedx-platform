"""
Static file finders for Django.
https://docs.djangoproject.com/en/1.8/ref/settings/#std:setting-STATICFILES_FINDERS
Yes, this interface is private and undocumented, but we need to access it anyway.

In order to deploy Open edX in production, it's important to be able to collect
and process static assets: images, CSS, JS, fonts, etc. Django's collectstatic
system is the accepted way to do that in Django-based projects, but that system
doesn't handle every kind of collection and processing that web developers need.
Other open source projects like `Django-Pipeline`_ and `Django-Require`_ hook
into Django's collectstatic system to provide features like minification,
compression, Sass pre-processing, and require.js optimization for assets before
they are pushed to production. To make sure that themed assets are collected
and served by the system (in addition to core assets), we need to extend this
interface, as well.

.. _Django-Pipeline: https://django-pipeline.readthedocs.org/
.. _Django-Require: https://github.com/etianen/django-require
"""


import os
from collections import OrderedDict

from django.contrib.staticfiles import utils
from django.contrib.staticfiles.finders import BaseFinder

from openedx.core.djangoapps.theming.helpers import get_themes
from openedx.core.djangoapps.theming.storage import ThemeStorage


class ThemeFilesFinder(BaseFinder):  # pylint: disable=abstract-method
    """
    A static files finder that looks in the directory of each theme as
    specified in the source_dir attribute.
    """
    storage_class = ThemeStorage
    source_dir = 'static'

    def __init__(self, *args, **kwargs):
        # The list of themes that are handled
        self.themes = []
        # Mapping of theme names to storage instances
        self.storages = OrderedDict()

        themes = get_themes()
        for theme in themes:
            theme_storage = self.storage_class(
                location=os.path.join(theme.path, self.source_dir),
                prefix=theme.theme_dir_name,
            )

            self.storages[theme.theme_dir_name] = theme_storage
            if theme.theme_dir_name not in self.themes:
                self.themes.append(theme.theme_dir_name)

        super().__init__(*args, **kwargs)

    def list(self, ignore_patterns):
        """
        List all files in all app storages.
        """
        for storage in self.storages.values():
            if storage.exists(''):  # check if storage location exists
                for path in utils.get_files(storage, ignore_patterns):
                    yield path, storage

    def find(self, path, *args, **kwargs):  # pylint: disable=redefined-builtin
        """
        Looks for files in the theme directories.
        """
        if 'all' in kwargs:
            # Note this method signature where we accept all and find_all is being used so that we can be
            # compatible with both Django 4.2 and Django 5.2 at the same time.  After we have fully
            # dropped Django 4.2 support, the method signature can be updated to just consume the
            # `find_all` paramater.
            find_all = kwargs.get('all', False)
        elif 'find_all' in kwargs:
            find_all = kwargs.get('find_all', False)
        else:
            find_all = args[0] if args else False

        matches = []
        theme_dir_name = path.split("/", 1)[0]

        themes = {t.theme_dir_name: t for t in get_themes()}
        # if path is prefixed by theme name then search in the corresponding storage other wise search all storages.
        if theme_dir_name in themes:
            theme = themes[theme_dir_name]
            path = "/".join(path.split("/")[1:])
            match = self.find_in_theme(theme.theme_dir_name, path)
            if match:
                if not find_all:
                    return match
                matches.append(match)
        return matches

    def find_in_theme(self, theme, path):
        """
        Find a requested static file in an theme's static locations.
        """
        storage = self.storages.get(theme, None)
        if storage:
            # only try to find a file if the source dir actually exists
            if storage.exists(path):
                matched_path = storage.path(path)
                if matched_path:
                    return matched_path
