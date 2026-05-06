"""
Set up lookup paths for mako templates.
"""
import contextlib
import hashlib
import importlib.resources as resources
import os

from django.conf import settings
from mako.exceptions import TopLevelLookupException
from mako.lookup import TemplateLookup

from openedx.core.djangoapps.theming.helpers import get_template_path_with_theme, strip_site_theme_templates_path
from openedx.core.lib.cache_utils import request_cached

from . import LOOKUP


class TopLevelTemplateURI(str):
    """
    A marker class for template URIs used to signal the template lookup infrastructure that the template corresponding
    to the URI should be looked up straight in the standard edx-platform location instead of trying to locate an
    overridding template in the current theme first.
    """
    pass  # pylint: disable=unnecessary-pass


class DynamicTemplateLookup(TemplateLookup):
    """
    A specialization of the standard mako `TemplateLookup` class which allows
    for adding directories progressively.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__original_module_directory = self.template_args['module_directory']

    def __repr__(self):
        return "<{0.__class__.__name__} {0.directories}>".format(self)  # noqa: UP032

    def add_directory(self, directory, prepend=False):
        """
        Add a new directory to the template lookup path.
        """
        if prepend:
            self.directories.insert(0, os.path.normpath(directory))
        else:
            self.directories.append(os.path.normpath(directory))

        # Since the lookup path has changed, the compiled modules might be
        # wrong because now "foo.html" might be a completely different template,
        # and "foo.html.py" in the module directory has no way to know that.
        # Update the module_directory argument to point to a directory
        # specifically for this lookup path.
        unique = hashlib.md5((":".join(str(d) for d in self.directories)).encode()).hexdigest()
        self.template_args['module_directory'] = os.path.join(self.__original_module_directory, unique)

        # Also clear the internal caches. Ick.
        self._collection.clear()
        self._uri_cache.clear()

    def adjust_uri(self, uri, relativeto):
        """
        This method is called by mako when including a template in another template or when inheriting an existing mako
        template. The method adjusts the `uri` to make it relative to the calling template's location.

        This method is overridden to detect when a template from a theme tries to override the same template from a
        standard location, for example when the dashboard.html template is overridden in the theme while at the same
        time inheriting from the standard LMS dashboard.html template.

        When this self-inheritance is detected, the uri is wrapped in the TopLevelTemplateURI marker class to ensure
        that template lookup skips the current theme and looks up the built-in template in standard locations.
        """
        # Make requested uri relative to the calling uri.
        relative_uri = super().adjust_uri(uri, relativeto)
        # Is the calling template (relativeto) which is including or inheriting current template (uri)
        # located inside a theme?
        if relativeto != strip_site_theme_templates_path(relativeto):
            # Is the calling template trying to include/inherit itself?
            if relativeto == get_template_path_with_theme(relative_uri):
                return TopLevelTemplateURI(relative_uri)
        return relative_uri

    def get_template(self, uri):
        """
        Overridden method for locating a template in either the database or the site theme.

        If not found, template lookup will be done in comprehensive theme for current site
        by prefixing path to theme.
        e.g if uri is `main.html` then new uri would be something like this `/red-theme/lms/static/main.html`

        If still unable to find a template, it will fallback to the default template directories after stripping off
        the prefix path to theme.
        """
        if isinstance(uri, TopLevelTemplateURI):
            template = self._get_toplevel_template(uri)
        else:
            try:
                # Try to find themed template, i.e. see if current theme overrides the template
                template = super().get_template(get_template_path_with_theme(uri))
            except TopLevelLookupException:
                template = self._get_toplevel_template(uri)

        return template

    def _get_toplevel_template(self, uri):
        """
        Lookup a default/toplevel template, ignoring current theme.
        """
        # Strip off the prefix path to theme and look in default template dirs.
        return super().get_template(strip_site_theme_templates_path(uri))


def clear_lookups(namespace):
    """
    Remove mako template lookups for the given namespace.
    """
    if namespace in LOOKUP:
        del LOOKUP[namespace]


def add_lookup(namespace, directory, package=None, prepend=False):
    """
    Adds a new mako template lookup directory to the given namespace.

    If `package` is specified, `importlib.resources` is used to look up the directory
    inside the given package.  Otherwise `directory` is assumed to be a path
    in the filesystem.
    """
    templates = LOOKUP.get(namespace)
    if not templates:
        LOOKUP[namespace] = templates = DynamicTemplateLookup(
            module_directory=settings.MAKO_MODULE_DIR,
            output_encoding='utf-8',
            input_encoding='utf-8',
            default_filters=['decode.utf8'],
            encoding_errors='replace',
        )
    if package:
        with resources.as_file(resources.files(package.rsplit('.', 1)[0]) / directory) as dir_path:
            directory = str(dir_path)

    templates.add_directory(directory, prepend=prepend)


@request_cached()
def lookup_template(namespace, name):
    """
    Look up a Mako template by namespace and name.
    """
    return LOOKUP[namespace].get_template(name)


@contextlib.contextmanager
def save_lookups():
    """
    A context manager to save and restore the Mako template lookup path.

    Useful for testing.

    """
    # Make a copy of the list of directories for each namespace.
    namespace_dirs = {namespace: list(look.directories) for namespace, look in LOOKUP.items()}

    try:
        yield
    finally:
        # Get rid of all the lookups.
        LOOKUP.clear()

        # Re-create the lookups from our saved list.
        for namespace, directories in namespace_dirs.items():
            for directory in directories:
                add_lookup(namespace, directory)
