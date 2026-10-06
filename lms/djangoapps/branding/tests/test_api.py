"""Tests of Branding API """


import re
from collections.abc import Callable
from unittest import mock

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration

from ..api import (
    EnterpriseLearnerPortalLink,
    _footer_business_links,
    get_enterprise_learner_portal_link,
    get_footer,
    get_header_logo,
    get_home_url,
    get_learner_dashboard_url,
    get_learner_display_username,
    get_logo_url,
    should_show_order_history,
)

test_config_disabled_contact_us = {   # pylint: disable=invalid-name
    "CONTACT_US_ENABLE": False,
}

test_config_custom_url_contact_us = {   # pylint: disable=invalid-name
    "CONTACT_US_ENABLE": True,
    "CONTACT_US_CUSTOM_LINK": "https://open.edx.org/",
}

TEST_PASSWORD = "Password1234"
PORTAL_USERNAME = "portal_learner"


OVERRIDDEN_DISPLAY_USERNAME = "Test Org Learner"

OVERRIDDEN_PORTAL_LINK = {
    "url": "https://portal.example.com/test-org",
    "logo": "https://portal.example.com/test-org/logo.png",
    "name": "Test Org",
}


def override_learner_display_username(
    prev_fn: Callable[..., str],  # pylint: disable=unused-argument
    user: AbstractBaseUser,  # pylint: disable=unused-argument
) -> str:
    """
    Alternative implementation of ``get_learner_display_username`` used by the tests below.

    A real override is expected to return ``prev_fn(user=user)`` for any learner it does not
    claim; this one claims every learner, because whether the chain falls through correctly is
    ``pluggable_override``'s behavior rather than this repo's.
    """
    return OVERRIDDEN_DISPLAY_USERNAME


def override_enterprise_learner_portal_link(
    prev_fn: Callable[..., EnterpriseLearnerPortalLink | None],  # pylint: disable=unused-argument
) -> EnterpriseLearnerPortalLink | None:
    """
    Alternative implementation of ``get_enterprise_learner_portal_link`` used by the tests below.

    Takes no arguments, reading the current viewer from crum the way a real plugin override
    does. Claims unconditionally, for the reason given above.
    """
    return OVERRIDDEN_PORTAL_LINK


class TestHeader(TestCase):
    """Test API end-point for retrieving the header. """

    def test_cdn_urls_for_logo(self):
        # Ordinarily, we'd use `override_settings()` to override STATIC_URL,
        # which is what the staticfiles storage backend is using to construct the URL.
        # Unfortunately, other parts of the system are caching this value on module
        # load, which can cause other tests to fail.  To ensure that this change
        # doesn't affect other tests, we patch the `url()` method directly instead.
        cdn_url = "http://cdn.example.com/static/image.png"
        with mock.patch('lms.djangoapps.branding.api.staticfiles_storage.url', return_value=cdn_url):
            logo_url = get_logo_url()

        assert logo_url == cdn_url

    def test_home_url(self):
        expected_url = get_home_url()
        assert reverse('dashboard') == expected_url


class TestFooter(TestCase):
    """Test retrieving the footer. """
    maxDiff = None

    @mock.patch.dict('django.conf.settings.MKTG_URLS', {
        "ROOT": "https://edx.org",
        "ENTERPRISE": "/enterprise"
    })
    @override_settings(ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS={}, PLATFORM_NAME='\xe9dX')
    def test_footer_business_links_no_marketing_query_params(self):
        """
        Enterprise marketing page values returned should be a concatenation of ROOT and
        ENTERPRISE marketing url values when ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS
        is not set.
        """

        business_links = _footer_business_links()
        assert business_links[0]['url'] == 'https://edx.org/enterprise'

    @mock.patch.dict('django.conf.settings.MKTG_URLS', {
        "ROOT": "https://edx.org",
        "ABOUT": "/about-us",
        "NEWS": "/news-announcements",
        "CONTACT": "/contact",
        "CAREERS": '/careers',
        "FAQ": "/student-faq",
        "BLOG": "/edx-blog",
        "DONATE": "/donate",
        "JOBS": "/jobs",
        "SITE_MAP": "/sitemap",
        "TRADEMARKS": "/trademarks",
        "TOS_AND_HONOR": "/edx-terms-service",
        "PRIVACY": "/edx-privacy-policy",
        "ACCESSIBILITY": "/accessibility",
        "AFFILIATES": '/affiliate-program',
        "MEDIA_KIT": "/media-kit",
        "ENTERPRISE": "https://business.edx.org"
    })
    @override_settings(PLATFORM_NAME='\xe9dX')
    def test_get_footer(self):
        actual_footer = get_footer(is_secure=True)
        business_url = 'https://business.edx.org/?utm_campaign=edX.org+Referral&utm_source=edX.org&utm_medium=Footer'
        facebook_url = 'http://www.facebook.com/EdxOnline'
        linkedin_url = 'http://www.linkedin.com/company/edx'
        twitter_url = 'https://twitter.com/edXOnline'
        reddit_url = 'http://www.reddit.com/r/edx'
        expected_footer = {
            'copyright': '\xa9 \xe9dX.  All rights reserved except where noted. '
                         ' edX, Open edX and their respective logos are '
                         'registered trademarks of edX Inc.',
            'navigation_links': [
                {'url': 'https://edx.org/about-us', 'name': 'about', 'title': 'About'},
                {'url': 'https://business.edx.org', 'name': 'enterprise', 'title': '\xe9dX for Business'},
                {'url': 'https://edx.org/edx-blog', 'name': 'blog', 'title': 'Blog'},
                {'url': 'https://edx.org/news-announcements', 'name': 'news', 'title': 'News'},
                {'url': 'https://example.support.edx.org/hc/en-us', 'name': 'help-center', 'title': 'Help Center'},
                {'url': '/support/contact_us', 'name': 'contact', 'title': 'Contact'},
                {'url': 'https://edx.org/careers', 'name': 'careers', 'title': 'Careers'},
                {'url': 'https://edx.org/donate', 'name': 'donate', 'title': 'Donate'}
            ],
            'business_links': [
                {'url': 'https://edx.org/about-us', 'name': 'about', 'title': 'About'},
                {'url': business_url, 'name': 'enterprise', 'title': '\xe9dX for Business'},
                {'url': 'https://edx.org/affiliate-program', 'name': 'affiliates', 'title': 'Affiliates'},
                {'url': 'https://open.edx.org', 'name': 'openedx', 'title': 'Open edX'},
                {'url': 'https://edx.org/careers', 'name': 'careers', 'title': 'Careers'},
                {'url': 'https://edx.org/news-announcements', 'name': 'news', 'title': 'News'},

            ],
            'more_info_links': [
                {'url': 'https://edx.org/edx-terms-service',
                 'name': 'terms_of_service_and_honor_code',
                 'title': 'Terms of Service & Honor Code'},
                {'url': 'https://edx.org/edx-privacy-policy', 'name': 'privacy_policy', 'title': 'Privacy Policy'},
                {'url': 'https://edx.org/accessibility',
                 'name': 'accessibility_policy',
                 'title': 'Accessibility Policy'},
                {'url': 'https://edx.org/trademarks', 'name': 'trademarks', 'title': 'Trademark Policy'},
                {'url': 'https://edx.org/sitemap', 'name': 'sitemap', 'title': 'Sitemap'},

            ],
            'connect_links': [
                {'url': 'https://edx.org/edx-blog', 'name': 'blog', 'title': 'Blog'},
                # pylint: disable=line-too-long
                {'url': f'{settings.LMS_ROOT_URL}/support/contact_us', 'name': 'contact', 'title': 'Contact Us'},
                {'url': 'https://example.support.edx.org/hc/en-us', 'name': 'help-center', 'title': 'Help Center'},
                {'url': 'https://edx.org/media-kit', 'name': 'media_kit', 'title': 'Media Kit'},
                {'url': 'https://edx.org/donate', 'name': 'donate', 'title': 'Donate'}
            ],
            'legal_links': [
                {'url': 'https://edx.org/edx-terms-service',
                 'name': 'terms_of_service_and_honor_code',
                 'title': 'Terms of Service & Honor Code'},
                {'url': 'https://edx.org/edx-privacy-policy', 'name': 'privacy_policy', 'title': 'Privacy Policy'},
                {'url': 'https://edx.org/accessibility',
                 'name': 'accessibility_policy',
                 'title': 'Accessibility Policy'},
                {'url': 'https://edx.org/sitemap', 'name': 'sitemap', 'title': 'Sitemap'},
                {'name': 'media_kit',
                 'title': 'Media Kit',
                 'url': 'https://edx.org/media-kit'}
            ],
            'social_links': [
                {'url': facebook_url, 'action': 'Like \xe9dX on Facebook', 'name': 'facebook',
                 'icon-class': 'fa-facebook-square', 'title': 'Facebook'},
                {'url': twitter_url, 'action': 'Follow \xe9dX on Twitter', 'name': 'twitter',
                 'icon-class': 'fa-twitter-square', 'title': 'Twitter'},
                {'url': linkedin_url, 'action': 'Follow \xe9dX on LinkedIn', 'name': 'linkedin',
                 'icon-class': 'fa-linkedin-square', 'title': 'LinkedIn'},
                {'url': '#', 'action': 'Follow \xe9dX on Instagram', 'name': 'instagram',
                 'icon-class': 'fa-instagram', 'title': 'Instagram'},
                {'url': reddit_url, 'action': 'Subscribe to the \xe9dX subreddit',
                 'name': 'reddit', 'icon-class': 'fa-reddit-square', 'title': 'Reddit'}
            ],
            'mobile_links': [],
            'logo_image': '/static/images/logo.png',
            'openedx_link': {
                'url': 'https://open.edx.org',
                'image': 'https://logos.openedx.org/open-edx-logo-tag.png',
                'title': 'Powered by Open edX'
            },
            'edx_org_link': {
                'url': 'https://www.edx.org/?'
                       'utm_medium=affiliate_partner'
                       '&utm_source=opensource-partner'
                       '&utm_content=open-edx-partner-footer-link'
                       '&utm_campaign=open-edx-footer',
                'text': 'Take free online courses at edX.org',
            },
        }
        assert actual_footer == expected_footer

    @with_site_configuration(configuration=test_config_disabled_contact_us)
    def test_get_footer_disabled_contact_form(self):
        """
        Test retrieving the footer with disabled contact form.
        """
        actual_footer = get_footer(is_secure=True)
        assert any((l['name'] == 'contact') for l in actual_footer['connect_links']) is False
        assert any((l['name'] == 'contact') for l in actual_footer['navigation_links']) is False

    @with_site_configuration(configuration=test_config_custom_url_contact_us)
    def test_get_footer_custom_contact_url(self):
        """
        Test retrieving the footer with custom contact form url.
        """
        actual_footer = get_footer(is_secure=True)
        contact_us_link = [l for l in actual_footer['connect_links'] if l['name'] == 'contact'][0]
        assert contact_us_link['url'] == test_config_custom_url_contact_us['CONTACT_US_CUSTOM_LINK']

        navigation_link_contact_us = [l for l in actual_footer['navigation_links'] if l['name'] == 'contact'][0]
        assert navigation_link_contact_us['url'] == test_config_custom_url_contact_us['CONTACT_US_CUSTOM_LINK']


DISPLAY_USERNAME_OVERRIDE = "lms.djangoapps.branding.tests.test_api.override_learner_display_username"
PORTAL_LINK_OVERRIDE = "lms.djangoapps.branding.tests.test_api.override_enterprise_learner_portal_link"

# ``should_show_order_history()`` is False whenever no order history MFE is configured, so the
# tests below pin a known URL rather than leaning on whatever ``lms.envs.test`` happens to set.
# That keeps "suppressed because this learner has a portal" distinguishable from "never rendered
# at all".
ORDER_HISTORY_URL = "https://orders.example.com"


class TestLearnerHeaderHelpers(TestCase):
    """Test the pluggable header helpers consumed by the navigation templates."""

    def setUp(self):
        super().setUp()
        self.portal_user = UserFactory.create(username=PORTAL_USERNAME)

    def test_learner_display_username_default(self):
        """Without an override the helper returns the learner's own username."""
        assert get_learner_display_username(user=self.portal_user) == PORTAL_USERNAME

    @override_settings(OVERRIDE_GET_LEARNER_DISPLAY_USERNAME=DISPLAY_USERNAME_OVERRIDE)
    def test_learner_display_username_overridden(self):
        assert get_learner_display_username(user=self.portal_user) == OVERRIDDEN_DISPLAY_USERNAME

    def test_enterprise_learner_portal_link_default(self):
        assert get_enterprise_learner_portal_link() is None

    @override_settings(OVERRIDE_GET_ENTERPRISE_LEARNER_PORTAL_LINK=PORTAL_LINK_OVERRIDE)
    def test_enterprise_learner_portal_link_overridden(self):
        assert get_enterprise_learner_portal_link() == OVERRIDDEN_PORTAL_LINK

    def test_header_logo_default(self):
        """With no portal, the logo block describes the platform's own logo."""
        logo = get_header_logo()

        assert logo == {
            "url": get_home_url(),
            "image": get_logo_url(),
            "alt": f"{settings.PLATFORM_NAME} Home Page",
        }

    @override_settings(OVERRIDE_GET_ENTERPRISE_LEARNER_PORTAL_LINK=PORTAL_LINK_OVERRIDE)
    def test_header_logo_with_portal(self):
        """
        With a portal, all three values come from it together.

        Asserted as a whole dict rather than field by field: the point of returning them
        as one unit is that a caller cannot end up with a portal logo beside a platform
        link, so a partial assertion would miss the bug this shape exists to prevent.
        """
        logo = get_header_logo()

        assert logo == {
            "url": OVERRIDDEN_PORTAL_LINK["url"],
            "image": OVERRIDDEN_PORTAL_LINK["logo"],
            "alt": f"{OVERRIDDEN_PORTAL_LINK['name']} Dashboard",
        }

    def test_header_logo_honors_is_secure(self):
        """``is_secure`` still reaches the platform logo lookup."""
        assert get_header_logo(is_secure=False)["image"] == get_logo_url(is_secure=False)

    def test_learner_dashboard_url_default(self):
        assert get_learner_dashboard_url() == get_home_url()

    @override_settings(OVERRIDE_GET_ENTERPRISE_LEARNER_PORTAL_LINK=PORTAL_LINK_OVERRIDE)
    def test_learner_dashboard_url_with_portal(self):
        assert get_learner_dashboard_url() == OVERRIDDEN_PORTAL_LINK["url"]

    @override_settings(ORDER_HISTORY_MICROFRONTEND_URL=ORDER_HISTORY_URL)
    def test_should_show_order_history_default(self):
        assert should_show_order_history() is True

    @override_settings(
        ORDER_HISTORY_MICROFRONTEND_URL=ORDER_HISTORY_URL,
        OVERRIDE_GET_ENTERPRISE_LEARNER_PORTAL_LINK=PORTAL_LINK_OVERRIDE,
    )
    def test_should_show_order_history_with_portal(self):
        """A portal learner obtains content through the portal, so order history does not apply."""
        assert should_show_order_history() is False

    @override_settings(ORDER_HISTORY_MICROFRONTEND_URL=None)
    def test_should_show_order_history_without_microfrontend(self):
        """With no order history microfrontend configured there is nowhere to link, portal or not."""
        assert should_show_order_history() is False


@override_settings(ORDER_HISTORY_MICROFRONTEND_URL=ORDER_HISTORY_URL)
class TestLearnerHeaderTemplates(TestCase):
    """Test that the header templates render the values returned by the pluggable helpers."""

    def setUp(self):
        super().setUp()
        self.portal_user = UserFactory.create(username=PORTAL_USERNAME, password=TEST_PASSWORD)

    def _render(self, url):
        """Return the markup of a page that renders the site header, failing if it did not render."""
        response = self.client.get(url)
        assert response.status_code == 200
        return response.content.decode("utf-8")

    def _render_authenticated_header(self, user):
        """Return the markup of a logged-in page, which renders both the logo header and the dropdown."""
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        return self._render(url=reverse("dashboard"))

    def _displayed_name(self, content):
        """Return the name the user dropdown displays, which is what the helper feeds."""
        match = re.search(r'<span data-hj-suppress class="username">([^<]*)</span>', content)
        assert match, "the user dropdown did not render"
        return match.group(1)

    def _assert_platform_header(self, content, username):
        """Assert the header renders the way it does with no portal in play."""
        # The learner's own username is the name displayed in the dropdown. Keyed off the
        # dropdown's own markup, because the username also appears in the page's JS config
        # and in the profile link, neither of which goes through the helper.
        assert self._displayed_name(content=content) == username
        assert OVERRIDDEN_DISPLAY_USERNAME not in content
        # The site logo links to the platform home page.
        assert f'<a href="{get_home_url()}">' in content
        # The dropdown's dashboard link points at the platform dashboard.
        assert f'<a href="{get_home_url()}" role="menuitem">' in content
        # Order history is offered.
        assert "Order History" in content
        assert OVERRIDDEN_PORTAL_LINK["url"] not in content

    def test_header_without_overrides(self):
        self._assert_platform_header(
            content=self._render_authenticated_header(user=self.portal_user),
            username=PORTAL_USERNAME,
        )

    def test_anonymous_header_without_overrides(self):
        """The logo header renders for logged-out visitors too, where there is no learner at all."""
        content = self._render(url=reverse("root"))
        assert f'<a href="{get_home_url()}">' in content
        assert OVERRIDDEN_PORTAL_LINK["url"] not in content

    @override_settings(
        OVERRIDE_GET_LEARNER_DISPLAY_USERNAME=DISPLAY_USERNAME_OVERRIDE,
        OVERRIDE_GET_ENTERPRISE_LEARNER_PORTAL_LINK=PORTAL_LINK_OVERRIDE,
    )
    def test_header_with_overrides(self):
        content = self._render_authenticated_header(user=self.portal_user)
        # The override's display username stands in for the learner's own username.
        assert self._displayed_name(content=content) == OVERRIDDEN_DISPLAY_USERNAME
        # The site logo is replaced by the portal logo and links to the portal.
        assert f'<a href="{OVERRIDDEN_PORTAL_LINK["url"]}">' in content
        assert OVERRIDDEN_PORTAL_LINK["logo"] in content
        assert f'{OVERRIDDEN_PORTAL_LINK["name"]} Dashboard' in content
        assert f'<a href="{get_home_url()}">' not in content
        # The dropdown's dashboard link points at the portal instead of the platform dashboard.
        assert f'<a href="{OVERRIDDEN_PORTAL_LINK["url"]}" role="menuitem">' in content
        assert f'<a href="{get_home_url()}" role="menuitem">' not in content
        # Order history is suppressed.
        assert "Order History" not in content


class TestDeprecatedUserDropdownTemplate(TestCase):
    """
    Test the deprecated ``lms/templates/user_dropdown.html``.

    Nothing in this repo includes it -- it is reachable only through
    ``navigation/navigation.html``, which is deprecated and included by nothing -- so it is
    rendered directly here rather than through a view. Only its Bootstrap arm is rendered,
    because the other arm calls a ``navigation_dropdown_menu_links()`` def supplied by the
    parent template.
    """

    def setUp(self):
        super().setUp()
        self.portal_user = UserFactory.create(username=PORTAL_USERNAME)

    def _render(self, user):
        """Return the markup of the deprecated dropdown as rendered for ``user``."""
        request = RequestFactory().get("/")
        request.user = user
        return render_to_string(
            template_name="user_dropdown.html",
            dictionary={"uses_bootstrap": True, "user": user, "request": request},
        )

    def test_dropdown_without_override(self):
        assert PORTAL_USERNAME in self._render(user=self.portal_user)

    @override_settings(OVERRIDE_GET_LEARNER_DISPLAY_USERNAME=DISPLAY_USERNAME_OVERRIDE)
    def test_dropdown_with_override(self):
        markup = self._render(user=self.portal_user)
        assert OVERRIDDEN_DISPLAY_USERNAME in markup
        assert PORTAL_USERNAME not in markup
