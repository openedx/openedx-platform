"""Tests of comprehensive theming."""


from django.conf import settings
from django.contrib import staticfiles
from django.test import TestCase
from path import Path

from common.djangoapps import edxmako
from openedx.core.djangoapps.theming.helpers import get_themes
from openedx.core.djangoapps.theming.helpers_dirs import get_theme_dirs
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from openedx.core.lib.tempdir import create_symlink, delete_symlink, mkdtemp_clean


class TestComprehensiveTheming(TestCase):
    """Test comprehensive theming."""

    def setUp(self):
        super().setUp()

        # Clear the internal staticfiles caches, to get test isolation.
        staticfiles.finders.get_finder.cache_clear()

        # Clear cache on get_theme methods.
        get_themes.cache_clear()
        get_theme_dirs.cache_clear()

    @with_comprehensive_theme('red-theme')
    def test_red_footer(self):
        """
        Tests templates from theme are rendered if available.
        `red-theme` has header.html and footer.html so this test
        asserts presence of the content from header.html and footer.html
        """
        resp = self.client.get('/')
        assert resp.status_code == 200
        # This string comes from footer.html
        self.assertContains(resp, "super-ugly")

    def test_theme_outside_repo(self):
        # Need to create a temporary theme, and defer decorating the function
        # until it is done, which leads to this strange nested-function style
        # of test.

        # Make a temp directory as a theme.
        themes_dir = Path(mkdtemp_clean())
        tmp_theme = "temp_theme"
        template_dir = themes_dir / tmp_theme / "lms/templates"
        template_dir.makedirs()
        with open(template_dir / "footer.html", "w") as footer:
            footer.write("<footer>TEMPORARY THEME</footer>")

        dest_path = Path(settings.COMPREHENSIVE_THEME_DIRS[0]) / tmp_theme
        create_symlink(themes_dir / tmp_theme, dest_path)

        edxmako.paths.add_lookup('main', themes_dir, prepend=True)

        @with_comprehensive_theme(tmp_theme)
        def do_the_test(self):
            """A function to do the work so we can use the decorator."""
            resp = self.client.get('/')
            assert resp.status_code == 200
            self.assertContains(resp, "TEMPORARY THEME")

        do_the_test(self)
        # remove symlinks before running subsequent tests
        delete_symlink(dest_path)

    def test_default_logo_image(self):
        result = staticfiles.finders.find('images/logo.png')
        assert result == (settings.REPO_ROOT / 'lms/static/images/logo.png')

    @with_comprehensive_theme('red-theme')
    def test_overridden_logo_image(self):
        result = staticfiles.finders.find('red-theme/images/logo.png')
        assert result == (settings.REPO_ROOT / 'themes/red-theme/lms/static/images/logo.png')

    def test_default_favicon(self):
        """
        Test default favicon is served if no theme is applied
        """
        result = staticfiles.finders.find('images/favicon.ico')
        assert result == (settings.REPO_ROOT / 'lms/static/images/favicon.ico')

    @with_comprehensive_theme('red-theme')
    def test_overridden_favicon(self):
        """
        Test comprehensive theme override on favicon image.
        """
        result = staticfiles.finders.find('red-theme/images/favicon.ico')
        assert result == (settings.REPO_ROOT / 'themes/red-theme/lms/static/images/favicon.ico')
