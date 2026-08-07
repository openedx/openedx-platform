"""
Tests for static templates
"""


from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class MarketingSiteViewTests(TestCase):
    """ Tests for the marketing site views """

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
