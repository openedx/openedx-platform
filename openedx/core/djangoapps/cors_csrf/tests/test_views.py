"""Tests for cross-domain request views. """


import json

import ddt
from config_models.models import cache
from django.conf import settings
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from openedx.core.djangolib.testing.utils import skip_unless_lms

# cors_csrf is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from ..models import XDomainProxyConfiguration


@skip_unless_lms
@ddt.ddt
class XDomainProxyTest(TestCase):
    """Tests for the xdomain proxy end-point. """

    def setUp(self):
        """Clear model-based config cache. """
        super().setUp()
        try:
            self.url = reverse('xdomain_proxy')
        except NoReverseMatch:
            self.skipTest('xdomain_proxy URL is not configured')

        cache.clear()

    def test_xdomain_proxy_disabled(self):
        self._configure(False)
        response = self._load_page()
        assert response.status_code == 404

    @ddt.data(None, ['    '], [' ', ' '])
    def test_xdomain_proxy_enabled_no_whitelist(self, whitelist):
        self._configure(True, whitelist=whitelist)
        response = self._load_page()
        assert response.status_code == 404

    @ddt.data(
        (['example.com'], ['example.com']),
        (['example.com', 'sub.example.com'], ['example.com', 'sub.example.com']),
        (['   example.com    '], ['example.com']),
        (['     ', 'example.com'], ['example.com']),
    )
    @ddt.unpack
    def test_xdomain_proxy_enabled_with_whitelist(self, whitelist, expected_whitelist):
        self._configure(True, whitelist=whitelist)
        response = self._load_page()
        self._check_whitelist(response, expected_whitelist)

    def _configure(self, is_enabled, whitelist=None):
        """Enable or disable the end-point and configure the whitelist. """
        config = XDomainProxyConfiguration.current()
        config.enabled = is_enabled

        if whitelist:
            config.whitelist = "\n".join(whitelist)

        config.save()
        cache.clear()

    def _load_page(self):
        """Load the end-point. """
        return self.client.get(reverse('xdomain_proxy'))

    def _check_whitelist(self, response, expected_whitelist):
        """Verify that the domain whitelist is rendered on the page. """
        rendered_whitelist = json.dumps({
            domain: '*'
            for domain in expected_whitelist
        })
        self.assertContains(response, 'xdomain.min.js')
        self.assertContains(response, rendered_whitelist)
