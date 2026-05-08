"""
Theming aware template loaders.
"""


from django.template.loaders.filesystem import Loader as FilesystemLoader

from common.djangoapps.edxmako.makoloader import MakoLoader
from openedx.core.djangoapps.theming.helpers import get_all_theme_template_dirs, get_current_request, get_current_theme


class ThemeTemplateLoader(MakoLoader):
    """
    Filesystem Template loaders to pickup templates from theme directory based on the current site.
    """
    is_usable = True
    _accepts_engine_in_init = True

    def __init__(self, *args):
        MakoLoader.__init__(self, ThemeFilesystemLoader(*args))  # pylint: disable=no-value-for-parameter


class ThemeFilesystemLoader(FilesystemLoader):
    """
    Filesystem Template loaders to pickup templates from theme directory based on the current site.
    """
    is_usable = True
    _accepts_engine_in_init = True

    def get_dirs(self):
        """
        Override get_dirs method.

        Make the theme templates a priority, avoiding cashing templates for django ones.
        """
        dirs = super().get_dirs()
        theme_dirs = self.get_theme_template_sources()
        if theme_dirs:
            dirs = theme_dirs + dirs
        return dirs

    @staticmethod
    def get_theme_template_sources():
        """
        Return template sources for the given theme and if request object is None (this would be the case for
        management commands) return template sources for all themes.
        """
        if not get_current_request():
            # if request object is not present, then this method is being called inside a management
            # command and return all theme template sources for compression
            return get_all_theme_template_dirs()
        else:
            # template is being accessed by a view, so return templates sources for current theme
            theme = get_current_theme()
            return theme and theme.template_dirs
