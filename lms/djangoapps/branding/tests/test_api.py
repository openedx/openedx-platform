"""Tests of Branding API """


from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration

from ..api import _footer_business_links, get_footer, get_home_url, get_logo_url

test_config_disabled_contact_us = {   # pylint: disable=invalid-name
    "CONTACT_US_ENABLE": False,
}

test_config_custom_url_contact_us = {   # pylint: disable=invalid-name
    "CONTACT_US_ENABLE": True,
    "CONTACT_US_CUSTOM_LINK": "https://open.edx.org/",
}


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
