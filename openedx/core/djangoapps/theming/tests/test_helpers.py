"""
Test helpers for Comprehensive Theming.
"""


from unittest.mock import Mock, patch

from django.conf import settings
from django.test import TestCase, override_settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming import helpers as theming_helpers
from openedx.core.djangoapps.theming.helpers import (
    Theme,
    get_template_path_with_theme,
    get_theme_base_dir,
    get_themes,
    strip_site_theme_templates_path,
)
from openedx.core.djangoapps.theming.helpers_dirs import get_theme_dirs
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from openedx.core.djangolib.testing.utils import skip_unless_cms, skip_unless_lms


class TestHelpers(TestCase):
    """Test comprehensive theming helper functions."""

    def setUp(self):
        """
        Clear cache on get_theme methods.
        """
        super().setUp()

        get_themes.cache_clear()
        get_theme_dirs.cache_clear()

    def test_get_themes(self):
        """
        Tests template paths are returned from enabled theme.
        """
        expected_themes = [
            Theme('dark-theme', 'dark-theme', get_theme_base_dir('dark-theme'), settings.PROJECT_ROOT),
            Theme('edge.edx.org', 'edge.edx.org', get_theme_base_dir('edge.edx.org'), settings.PROJECT_ROOT),
            Theme('open-edx', 'open-edx', get_theme_base_dir('open-edx'), settings.PROJECT_ROOT),
            Theme('red-theme', 'red-theme', get_theme_base_dir('red-theme'), settings.PROJECT_ROOT),
            Theme('stanford-style', 'stanford-style', get_theme_base_dir('stanford-style'), settings.PROJECT_ROOT),
            Theme('test-theme', 'test-theme', get_theme_base_dir('test-theme'), settings.PROJECT_ROOT),
            Theme('empty-theme', 'empty-theme', get_theme_base_dir('empty-theme'), settings.PROJECT_ROOT),
        ]
        actual_themes = get_themes()
        self.assertCountEqual(expected_themes, actual_themes)  # noqa: PT009

    @override_settings(COMPREHENSIVE_THEME_DIRS=[settings.TEST_THEME.dirname()])
    def test_get_themes_2(self):
        """
        Tests template paths are returned from enabled theme.
        """
        expected_themes = [
            Theme('test-theme', 'test-theme', get_theme_base_dir('test-theme'), settings.PROJECT_ROOT),
        ]
        actual_themes = get_themes()
        self.assertCountEqual(expected_themes, actual_themes)  # noqa: PT009

    def test_get_value_returns_override(self):
        """
        Tests to make sure the get_value() operation returns a combined dictionary consisting
        of the base container with overridden keys from the site configuration
        """
        with patch('openedx.core.djangoapps.site_configuration.helpers.get_value') as mock_get_value:
            override_key = 'JWT_ISSUER'
            override_value = 'testing'
            mock_get_value.return_value = {override_key: override_value}
            jwt_auth = configuration_helpers.get_value('JWT_AUTH')
            assert jwt_auth[override_key] == override_value

    def test_is_comprehensive_theming_enabled(self):
        """
        Tests to make sure the is_comprehensive_theming_enabled function works as expected.
        Here are different scenarios that we need to test

        1. Theming is enabled and there is a SiteTheme record.
            is_comprehensive_theming_enabled should return True
        2. Theming is enabled and there is no SiteTheme record.
            is_comprehensive_theming_enabled should return False
        3. Theming is disabled, there is a SiteTheme record for the current site.
            is_comprehensive_theming_enabled should return False
        4. Theming is disabled, there is no SiteTheme record.
            is_comprehensive_theming_enabled should return False
        """
        # Theming is enabled, there is a SiteTheme record
        with patch(
            "openedx.core.djangoapps.theming.helpers.current_request_has_associated_site_theme",
            Mock(return_value=True),
        ):
            assert theming_helpers.is_comprehensive_theming_enabled()

        # Theming is enabled, there is not a SiteTheme record
        with patch(
            "openedx.core.djangoapps.theming.helpers.current_request_has_associated_site_theme",
            Mock(return_value=False),
        ):
            assert theming_helpers.is_comprehensive_theming_enabled()

        with override_settings(ENABLE_COMPREHENSIVE_THEMING=False):
            # Theming is disabled, there is a SiteTheme record
            with patch(
                "openedx.core.djangoapps.theming.helpers.current_request_has_associated_site_theme",
                Mock(return_value=True),
            ):
                assert not theming_helpers.is_comprehensive_theming_enabled()

            # Theming is disabled, there is no SiteTheme record
            with patch(
                "openedx.core.djangoapps.theming.helpers.current_request_has_associated_site_theme",
                Mock(return_value=False),
            ):
                assert not theming_helpers.is_comprehensive_theming_enabled()


@skip_unless_lms
class TestHelpersLMS(TestCase):
    """Test comprehensive theming helper functions."""

    @with_comprehensive_theme('red-theme')
    def test_get_template_path_with_theme_enabled(self):
        """
        Tests template paths are returned from enabled theme.
        """
        template_path = get_template_path_with_theme('header.html')
        assert template_path == 'red-theme/lms/templates/header.html'

    @with_comprehensive_theme('red-theme')
    def test_get_template_path_with_theme_for_missing_template(self):
        """
        Tests default template paths are returned if template is not found in the theme.
        """
        template_path = get_template_path_with_theme('course.html')
        assert template_path == 'course.html'

    def test_get_template_path_with_theme_disabled(self):
        """
        Tests default template paths are returned when theme is non theme is enabled.
        """
        template_path = get_template_path_with_theme('header.html')
        assert template_path == 'header.html'

    @with_comprehensive_theme('red-theme')
    def test_strip_site_theme_templates_path_theme_enabled(self):
        """
        Tests site theme templates path is stripped from the given template path.
        """
        template_path = strip_site_theme_templates_path('/red-theme/lms/templates/header.html')
        assert template_path == 'header.html'

    def test_strip_site_theme_templates_path_theme_disabled(self):
        """
        Tests site theme templates path returned unchanged if no theme is applied.
        """
        template_path = strip_site_theme_templates_path('/red-theme/lms/templates/header.html')
        assert template_path == '/red-theme/lms/templates/header.html'


@skip_unless_cms
class TestHelpersCMS(TestCase):
    """
    Test comprehensive theming helper functions.

    Note: There is no `test_get_template_path_with_theme_enabled` because there currently
    is no template to be themed.
    """
    @with_comprehensive_theme('red-theme')
    def test_get_template_path_with_theme_for_missing_template(self):
        """
        Tests default template paths are returned if template is not found in the theme.
        """
        template_path = get_template_path_with_theme('certificates.html')
        assert template_path == 'certificates.html'

    def test_get_template_path_with_theme_disabled(self):
        """
        Tests default template paths are returned when theme is non theme is enabled.
        """
        template_path = get_template_path_with_theme('login.html')
        assert template_path == 'login.html'

    @with_comprehensive_theme('red-theme')
    def test_strip_site_theme_templates_path_theme_enabled(self):
        """
        Tests site theme templates path is stripped from the given template path.
        """
        template_path = strip_site_theme_templates_path('/red-theme/cms/templates/login.html')
        assert template_path == 'login.html'

    def test_strip_site_theme_templates_path_theme_disabled(self):
        """
        Tests site theme templates path returned unchanged if no theme is applied.
        """
        template_path = strip_site_theme_templates_path('/red-theme/cms/templates/login.html')
        assert template_path == '/red-theme/cms/templates/login.html'
