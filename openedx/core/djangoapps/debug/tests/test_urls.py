"""
Tests that the debug views are only routed in development mode.

These run under both LMS and CMS settings, so they cover both URLconfs.
"""
import pytest
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import Resolver404, resolve

from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.debug.views import show_reference_template

# Present in common/templates, so it is in both the LMS and CMS lookup paths.
TEMPLATE_PATH = '/template/xblock_wrapper.html'


class ShowReferenceTemplateUrlTest(UrlResetMixin, TestCase):
    """
    The reference template view must not be reachable when DEBUG is off.
    """

    @override_settings(DEBUG=False)
    def test_not_routed_without_debug(self):
        self.reset_urls()

        with pytest.raises(Resolver404):
            resolve(TEMPLATE_PATH)

        payload = '"><script>alert(1)</script>'
        response = self.client.get(TEMPLATE_PATH, {'content': payload})
        assert response.status_code == 404
        assert payload not in response.content.decode()

    # cms/urls.py's DEBUG block needs a transcripts BASE_URL, which test settings lack.
    @override_settings(
        DEBUG=True,
        VIDEO_TRANSCRIPTS_SETTINGS={**settings.VIDEO_TRANSCRIPTS_SETTINGS, 'BASE_URL': '/video-transcripts/'},
    )
    def test_routed_with_debug(self):
        self.reset_urls()

        assert resolve(TEMPLATE_PATH).func is show_reference_template
