"""
Tests for toggles, where there is logic beyond enable/disable.
"""

import ddt
from django.test import TestCase, override_settings

from lms.djangoapps.branding.toggles import use_catalog_mfe


@ddt.ddt
class TestBrandingToggles(TestCase):
    """
    Tests for toggles, where there is logic beyond enable/disable.
    """

    @ddt.data(True, False)
    def test_use_catalog_mfe(self, enabled):
        """
        Test the use_catalog_mfe toggle.
        """
        with override_settings(ENABLE_CATALOG_MICROFRONTEND=enabled):
            assert use_catalog_mfe() == enabled
