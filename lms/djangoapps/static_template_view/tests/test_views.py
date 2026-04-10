"""
Tests for static templates
"""


from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context


class MarketingSiteViewTests(TestCase):
    """ Tests for the marketing site views """

    def _test_view(self, view_name, mimetype):
        """
        Gets a view and tests that it exists.
        """
        resp = self.client.get(reverse(view_name))
        assert resp.status_code == 200
        assert resp['Content-Type'] == mimetype

    def test_sitemap(self):
        """
        Test the sitemap view
        """
        self._test_view('sitemap_xml', 'application/xml')

    def test_about(self):
        """
        Test the about view
        """
        self._test_view('about', 'text/html')

    def test_about_with_site_configuration(self):
        """
        Test the about view with the header and content set in SiteConfiguration.
        """
        test_header = "Very Unique Test Header"
        test_content = "Very Unique Test Content"
        test_header_key = 'static_template_about_header'
        test_content_key = 'static_template_about_content'
        response = None
        configuration = {test_header_key: test_header, test_content_key: test_content}
        with with_site_configuration_context(configuration=configuration):
            response = self.client.get(reverse("about"))
        self.assertContains(response, test_header)
        self.assertContains(response, test_content)

    def test_about_with_site_configuration_and_html(self):
        """
        Test the about view with html in the header.
        """
        test_header = "<i>Very Unique Test Header</i>"
        test_content = "<i>Very Unique Test Content</i>"
        test_header_key = 'static_template_about_header'
        test_content_key = 'static_template_about_content'
        response = None
        configuration = {test_header_key: test_header, test_content_key: test_content}
        with with_site_configuration_context(configuration=configuration):
            response = self.client.get(reverse("about"))
        self.assertContains(response, test_header)
        self.assertContains(response, test_content)

    def test_404(self):
        """
        Test the 404 view.
        """
        url = reverse('render_404')
        resp = self.client.get(url)
        assert resp.status_code == 404
        assert resp['Content-Type'] == 'text/html; charset=utf-8'

    def test_500(self):
        """
        Test the 500 view.
        """
        url = reverse('render_500')
        resp = self.client.get(url)
        assert resp.status_code == 500
        assert resp['Content-Type'] == 'text/html; charset=utf-8'

        # check response with branding
        resp = self.client.get(url)
        self.assertContains(
            resp,
            'There has been a 500 error on the <em>{platform_name}</em> servers'.format(  # noqa: UP032
                platform_name=settings.PLATFORM_NAME
            ),
            status_code=500
        )
