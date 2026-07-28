"""
Tests for comprehensive themes.
"""


from django.conf import settings
from django.contrib import staticfiles
from django.test import TestCase
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from openedx.core.djangolib.testing.utils import skip_unless_lms  # pylint: disable=unused-import


@skip_unless_lms
class TestComprehensiveThemeLMS(TestCase):
    """
    Test html, sass and static file overrides for comprehensive themes.
    """

    def setUp(self):
        """
        Clear static file finders cache and register cleanup methods.
        """
        super().setUp()
        self.TEST_PASSWORD = 'Password1234'
        self.user = UserFactory(password=self.TEST_PASSWORD)

        # Clear the internal staticfiles caches, to get test isolation.
        staticfiles.finders.get_finder.cache_clear()

    def _login(self):
        """ Log into LMS. """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    @with_comprehensive_theme("test-theme")
    def test_footer(self):
        """
        Test that theme footer is used instead of default footer.
        """
        resp = self.client.get('/')
        assert resp.status_code == 200
        # This string comes from header.html of test-theme
        self.assertContains(resp, "This is a footer for test-theme.")

    @with_comprehensive_theme("test-theme")
    def test_logo_image(self):
        """
        Test that theme logo is used instead of default logo.
        """
        result = staticfiles.finders.find('test-theme/images/logo.png')
        assert result == (settings.TEST_THEME / 'lms/static/images/logo.png')

    @with_comprehensive_theme("test-theme")
    def test_override_block_in_parent(self):
        """
        Test that theme title is used instead of parent title.
        """
        self._login()
        dashboard_url = reverse('dashboard')
        resp = self.client.get(dashboard_url)
        assert resp.status_code == 200
        # This string comes from the 'pagetitle' block of the overriding theme.
        self.assertContains(resp, "Overridden Title!")

    @with_comprehensive_theme("test-theme")
    def test_override_block_in_grandparent(self):
        """
        Test that theme title is used instead of parent's parent's title.
        """
        self._login()
        dashboard_url = reverse('dashboard')
        resp = self.client.get(dashboard_url)
        assert resp.status_code == 200
        # This string comes from the 'bodyextra' block of the overriding theme.
        self.assertContains(resp, "Overriden Body Extra!")

    @with_comprehensive_theme("test-theme")
    def test_parent_content_in_self_inherited_template(self):
        """
        Test that parent's body is present in self inherited template.
        """
        self._login()
        dashboard_url = reverse('dashboard')
        resp = self.client.get(dashboard_url)
        assert resp.status_code == 200
        # This string comes from the default dashboard.html template.
        self.assertContains(resp, "Explore courses")

    @with_comprehensive_theme("test-theme")
    def test_include_default_template(self):
        """
        Test that theme template can include template which is not part of the theme.
        """
        self._login()
        courses_url = reverse('courses')
        resp = self.client.get(courses_url)
        assert resp.status_code == 200
        # The courses.html template includes the error-message.html template.
        # Verify that the error message is included in the output.
        self.assertContains(resp, "this module is temporarily unavailable")

    @with_comprehensive_theme("test-theme")
    def test_include_overridden_template(self):
        """
        Test that theme template can include template which is overridden in the active theme.
        """
        self._login()
        courses_url = reverse('courses')
        resp = self.client.get(courses_url)
        assert resp.status_code == 200
        # The courses.html template includes the progress.html file, which is overriden in the theme.
        self.assertContains(resp, "This overrides the courseware/progress.html template.")

    @with_comprehensive_theme("test-theme")
    def test_include_custom_template(self):
        """
        Test that theme template can include template which is only present in the theme, but has no standard LMS
        equivalent.
        """
        self._login()
        courses_url = reverse('courses')
        resp = self.client.get(courses_url)
        assert resp.status_code == 200
        # The courses.html template includes the test-theme.custom.html file.
        # Verify its contents are present in the output.
        self.assertContains(resp, "This is a custom template.")


@skip_unless_lms
class TestComprehensiveThemeDisabledLMS(TestCase):
    """
        Test Sass compilation order and sass overrides for comprehensive themes.
    """

    def setUp(self):
        """
        Clear static file finders cache.
        """
        super().setUp()

        # Clear the internal staticfiles caches, to get test isolation.
        staticfiles.finders.get_finder.cache_clear()

    def test_logo(self):
        """
        Test that default logo is picked in case of no comprehensive theme.
        """
        result = staticfiles.finders.find('images/logo.png')
        assert result == (settings.REPO_ROOT / 'lms/static/images/logo.png')


@skip_unless_lms
class TestStanfordTheme(TestCase):
    """
    Test html, sass and static file overrides for stanford theme.
    These tests are added to ensure expected behavior after USE_CUSTOM_THEME is removed and
    a new theme 'stanford-style' is added instead.
    """

    def setUp(self):
        """
        Clear static file finders cache and register cleanup methods.
        """
        super().setUp()

        # Clear the internal staticfiles caches, to get test isolation.
        staticfiles.finders.get_finder.cache_clear()

    @with_comprehensive_theme("stanford-style")
    def test_footer(self):
        """
        Test stanford theme footer.
        """
        resp = self.client.get('/')
        assert resp.status_code == 200
        # This string comes from header.html of test-theme
        self.assertContains(resp, "footer overrides for stanford theme go here")

    @with_comprehensive_theme("stanford-style")
    def test_logo_image(self):
        """
        Test custom logo.
        """
        result = staticfiles.finders.find('stanford-style/images/logo.png')
        assert result == (settings.REPO_ROOT / 'themes/stanford-style/lms/static/images/logo.png')

    @with_comprehensive_theme("stanford-style")
    def test_favicon_image(self):
        """
        Test correct favicon for custom theme.
        """
        result = staticfiles.finders.find('stanford-style/images/favicon.ico')
        assert result == (settings.REPO_ROOT / 'themes/stanford-style/lms/static/images/favicon.ico')

    @with_comprehensive_theme("stanford-style")
    def test_index_page(self):
        """
        Test custom theme overrides for index page.
        """
        resp = self.client.get('/')
        assert resp.status_code == 200
        # This string comes from header.html of test-theme
        self.assertContains(resp, "Free courses from <strong>Stanford</strong>")
