"""
Comprehensive Theming support for Django's collectstatic functionality.
See https://docs.djangoproject.com/en/1.8/ref/contrib/staticfiles/
"""


import os.path
import posixpath
import re
from urllib.parse import unquote, urldefrag, urlsplit  # pylint: disable=import-error

from django.conf import settings
from django.contrib.staticfiles.finders import find
from django.contrib.staticfiles.storage import ManifestFilesMixin, StaticFilesStorage
from django.utils._os import safe_join
from pipeline.storage import PipelineMixin

from openedx.core.djangoapps.theming.helpers import (
    get_current_theme,
    get_project_root_name,
    get_theme_base_dir,
    get_themes,
    is_comprehensive_theming_enabled
)


class ThemeMixin:
    """
    Comprehensive theme aware Static files storage.
    """
    # prefix for file path, this prefix is added at the beginning of file path before saving static files during
    # collectstatic command.
    # e.g. having "edx.org" as prefix will cause files to be saved as "edx.org/images/logo.png"
    # instead of "images/logo.png"
    prefix = None

    def __init__(self, **kwargs):

        self.prefix = kwargs.pop('prefix', None)
        super().__init__(**kwargs)

    def url(self, name):
        """
        Returns url of the asset, themed url will be returned if the asset is themed otherwise default
        asset url will be returned.

        Args:
            name: name of the asset, e.g. 'images/logo.png'

        Returns:
            url of the asset, e.g. '/static/red-theme/images/logo.png' if current theme is red-theme and logo
            is provided by red-theme otherwise '/static/images/logo.png'
        """
        prefix = ''
        theme = get_current_theme()

        # get theme prefix from site address if if asset is accessed via a url
        if theme:
            prefix = theme.theme_dir_name

        # get theme prefix from storage class, if asset is accessed during collectstatic run
        elif self.prefix:
            prefix = self.prefix

        # join theme prefix with asset name if theme is applied and themed asset exists
        if prefix and self.themed(name, prefix):
            name = os.path.join(prefix, name)

        return super().url(name)

    def themed(self, name, theme):
        """
        Returns True if given asset override is provided by the given theme otherwise returns False.
        Args:
            name: asset name e.g. 'images/logo.png'
            theme: theme name e.g. 'red-theme', 'edx.org'

        Returns:
            True if given asset override is provided by the given theme otherwise returns False
        """
        if not is_comprehensive_theming_enabled():
            return False

        # in debug mode check static asset from within the project directory
        if settings.DEBUG:
            themes_location = get_theme_base_dir(theme, suppress_error=True)
            # Nothing can be themed if we don't have a theme location or required params.
            if not all((themes_location, theme, name)):
                return False

            themed_path = "/".join([
                themes_location,
                theme,
                get_project_root_name(),
                "static/"
            ])
            name = name[1:] if name.startswith("/") else name
            path = safe_join(themed_path, name)
            return os.path.exists(path)
        # in live mode check static asset in the static files dir defined by "STATIC_ROOT" setting
        else:
            return self.exists(os.path.join(theme, name))


class ThemeStorage(ThemeMixin, StaticFilesStorage):
    pass


class ThemeManifestFilesMixin(ManifestFilesMixin):
    """
    Comprehensive theme aware ManifestFilesMixin.
    Main purpose of subclassing ManifestFilesMixin is to override the following methods.
    1 - _url
    2 - url_converter

    _url:
        This method takes asset name as argument and is responsible for adding hash to the name to support caching.
        This method is called during both collectstatic command and live server run.

        When called during collectstatic command that name argument will be asset name inside STATIC_ROOT,
        for non themed assets it will be the usual path (e.g. 'images/logo.png') but for themed asset it will
        also contain themes dir prefix (e.g. 'red-theme/images/logo.png'). So, here we check whether the themed asset
        exists or not, if it exists we pass the same name up in the MRO chain for further processing and if it does not
        exists we strip theme name and pass the new asset name to the MRO chain for further processing.

        When called during server run, we get the theme dir for the current site using `get_current_theme` and
        make sure to prefix theme dir to the asset name. This is done to ensure the usage of correct hash in file name.
        e.g. if our red-theme overrides 'images/logo.png' and we do not prefix theme dir to the asset name, the hash for
        '{platform-dir}/lms/static/images/logo.png' would be used instead of
        '{themes_base_dir}/red-theme/images/logo.png'

    url_converter:
        This function returns another function that is responsible for hashing urls that appear inside assets
        (e.g. url("images/logo.png") inside css). The method defined in the superclass adds a hash to file and returns
        relative url of the file.
        e.g. for url("../images/logo.png") it would return url("../images/logo.790c9a5340cb.png"). However we would
        want it to return absolute url (e.g. url("/static/images/logo.790c9a5340cb.png")) so that it works properly
        with themes.

        The overridden method here simply comments out the line that convert absolute url to relative url,
        hence absolute urls are used instead of relative urls.
    """

    def _processed_asset_name(self, name):
        """
        Returns either a themed or unthemed version of the given asset name,
        depending on several factors.

        See the class docstring for more info.
        """
        theme = get_current_theme()
        if theme and theme.theme_dir_name not in name:
            # during server run, append theme name to the asset name if it is not already there
            # this is ensure that correct hash is created and default asset is not always
            # used to create hash of themed assets.
            name = os.path.join(theme.theme_dir_name, name)
        parsed_name = urlsplit(unquote(name))
        clean_name = parsed_name.path.strip()
        asset_name = name
        if not self.exists(clean_name):
            # if themed asset does not exists then use default asset
            theme = name.split("/", 1)[0]
            # verify that themed asset was accessed
            if theme in [theme.theme_dir_name for theme in get_themes()]:
                asset_name = "/".join(name.split("/")[1:])

        return asset_name

    def _url(self, hashed_name_func, name, force=False, hashed_files=None):
        """
        This override method swaps out `name` with a processed version.

        See the class docstring for more info.
        """
        processed_asset_name = self._processed_asset_name(name)
        return super()._url(hashed_name_func, processed_asset_name, force, hashed_files)

    def url_converter(self, name, hashed_files, template=None):
        """
        This is an override of url_converter from ManifestFilesMixin.
        It changes one line near the end of the method (see the NOTE) in order
        to return absolute urls instead of relative urls.  This behavior is
        necessary for theme overrides, as we get 404 on assets with relative
        urls on a themed site.
        """
        if template is None:
            template = self.default_template

        def converter(matchobj):
            """
            Convert the matched URL to a normalized and hashed URL.
            This requires figuring out which files the matched URL resolves
            to and calling the url() method of the storage.
            """
            matches = matchobj.groupdict()
            matched = matches["matched"]
            url = matches["url"]

            # Ignore absolute/protocol-relative and data-uri URLs.
            if re.match(r"^[a-z]+:", url):
                return matched

            # Ignore absolute URLs that don't point to a static file (dynamic
            # CSS / JS?). Note that STATIC_URL cannot be empty.
            if url.startswith("/") and not url.startswith(settings.STATIC_URL):
                return matched

            # Strip off the fragment so a path-like fragment won't interfere.
            url_path, fragment = urldefrag(url)

            # Ignore URLs without a path
            if not url_path:
                return matched

            if url_path.startswith("/"):
                # Otherwise the condition above would have returned prematurely.
                assert url_path.startswith(settings.STATIC_URL)
                target_name = url_path[len(settings.STATIC_URL):]
            else:
                # We're using the posixpath module to mix paths and URLs conveniently.
                source_name = name if os.sep == "/" else name.replace(os.sep, "/")
                target_name = posixpath.join(posixpath.dirname(source_name), url_path)

            # Determine the hashed name of the target file with the storage backend.
            hashed_url = self._url(
                self._stored_name,
                unquote(target_name),
                force=True,
                hashed_files=hashed_files,
            )

            # NOTE:
            # The line below was commented out so that absolute urls are used instead of relative urls to make themed
            # assets work correctly.
            #
            # The line is commented and not removed to make future django upgrade easier and show exactly what is
            # changed in this method override
            #
            # transformed_url = "/".join(
            #  url_path.split("/")[:-1] + hashed_url.split("/")[-1:]
            # )

            transformed_url = hashed_url  # This line was added.

            # Restore the fragment that was stripped off earlier.
            if fragment:
                transformed_url += ("?#" if "?#" in url else "#") + fragment

            # Return the hashed version to the file
            matches["url"] = unquote(transformed_url)
            return template % matches

        return converter


class ThemePipelineMixin(PipelineMixin):
    """
    Mixin to make sure themed assets are also packaged and used along with non themed assets.
    if a source asset for a particular package is not present then the default asset is used.

    e.g. in the following package and for 'red-theme'
    'style-vendor': {
        'source_filenames': [
            'js/vendor/afontgarde/afontgarde.css',
            'css/vendor/font-awesome.css',
            'css/vendor/jquery.qtip.min.css',
            'css/vendor/responsive-carousel/responsive-carousel.css',
            'css/vendor/responsive-carousel/responsive-carousel.slide.css',
        ],
        'output_filename': 'css/lms-style-vendor.css'
    }
    'red-theme/css/vendor/responsive-carousel/responsive-carousel.css' will be used of it exists otherwise
    'css/vendor/responsive-carousel/responsive-carousel.css' will be used to create 'red-theme/css/lms-style-vendor.css'
    """
    packing = True

    def post_process(self, paths, dry_run=False, **options):
        """
        This post_process hook is used to package all themed assets.
        """
        if dry_run:
            return
        themes = get_themes()

        for theme in themes:
            css_packages = self.get_themed_packages(theme.theme_dir_name, settings.PIPELINE['STYLESHEETS'])

            from pipeline.packager import Packager
            packager = Packager(storage=self, css_packages=css_packages)
            for package_name in packager.packages['css']:
                package = packager.package_for('css', package_name)
                output_file = package.output_filename
                if self.packing:
                    packager.pack_stylesheets(package)
                paths[output_file] = (self, output_file)
                yield output_file, output_file, True

        super_class = super()
        if hasattr(super_class, 'post_process'):
            yield from super_class.post_process(paths.copy(), dry_run, **options)

    @staticmethod
    def get_themed_packages(prefix, packages):
        """
        Update paths with the themed assets,
        Args:
            prefix: theme prefix for which to update asset paths e.g. 'red-theme', 'edx.org' etc.
            packages: packages to update

        Returns: list of updated paths and a boolean indicating whether any path was path or not
        """
        themed_packages = {}
        for name in packages:
            # collect source file names for the package
            source_files = []
            for path in packages[name].get('source_filenames', []):
                # if themed asset exists use that, otherwise use default asset.
                if find(os.path.join(prefix, path)):
                    source_files.append(os.path.join(prefix, path))
                else:
                    source_files.append(path)

            themed_packages[name] = {
                'output_filename': os.path.join(prefix, packages[name].get('output_filename', '')),
                'source_filenames': source_files,
            }
        return themed_packages
