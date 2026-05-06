"""
Tests for Certificates app utility functions
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import ddt
from django.test import TestCase
from pytz import utc

from lms.djangoapps.certificates.utils import has_html_certificates_enabled, should_certificate_be_visible
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.data import CertificatesDisplayBehaviors  # pylint: disable=wrong-import-order

_TODAY = datetime.now(utc)
_LAST_MONTH = _TODAY - timedelta(days=30)
_LAST_WEEK = _TODAY - timedelta(days=7)
_NEXT_WEEK = _TODAY + timedelta(days=7)


@ddt.ddt
class CertificateUtilityTests(TestCase):
    """
    Tests for course certificate utility functions
    """
    def setUp(self):
        super().setUp()
        self.course_overview = CourseOverviewFactory.create()

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': False})
    def test_has_html_certificates_enabled_from_course_overview_cert_html_view_disabled(self):
        """
        Test to ensure we return the correct value when the `CERTIFICATES_HTML_VIEW` setting is disabled.
        """
        assert not has_html_certificates_enabled(self.course_overview)

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_has_html_certificates_enabled_from_course_overview_enabled(self):
        """
        Test to ensure we return the correct value when the HTML certificates are enabled in a course-run.
        """
        self.course_overview.cert_html_view_enabled = True
        self.course_overview.save()

        assert has_html_certificates_enabled(self.course_overview)

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_has_html_certificates_enabled_from_course_overview_disabled(self):
        """
        Test to ensure we return the correct value when the HTML certificates are disabled in a course-run.
        """
        self.course_overview.cert_html_view_enabled = False
        self.course_overview.save()

        assert not has_html_certificates_enabled(self.course_overview)

    @ddt.data(
        # Test certificates_show_before_end
        (CertificatesDisplayBehaviors.EARLY_NO_INFO, True, False, _LAST_MONTH, False, True),
        (CertificatesDisplayBehaviors.END, True, False, _LAST_MONTH, False, True),
        (CertificatesDisplayBehaviors.END_WITH_DATE, True, False, _NEXT_WEEK, False, True),

        # Test that EARLY_NO_INFO
        (CertificatesDisplayBehaviors.EARLY_NO_INFO, True, True, _LAST_MONTH, False, True),
        (CertificatesDisplayBehaviors.EARLY_NO_INFO, False, False, _LAST_MONTH, False, True),

        # Test END_WITH_DATE
        (CertificatesDisplayBehaviors.END_WITH_DATE, False, False, _LAST_MONTH, False, True),
        (CertificatesDisplayBehaviors.END_WITH_DATE, False, False, _LAST_WEEK, False, True),
        (CertificatesDisplayBehaviors.END_WITH_DATE, False, False, _NEXT_WEEK, False, False),
        (CertificatesDisplayBehaviors.END_WITH_DATE, False, False, None, False, False),

        # Test END
        (CertificatesDisplayBehaviors.END, False, False, _LAST_MONTH, False, False),
        (CertificatesDisplayBehaviors.END, False, True, _LAST_MONTH, False, True),

        # Test self_paced
        (CertificatesDisplayBehaviors.END, False, False, _LAST_MONTH, False, False),
        (CertificatesDisplayBehaviors.END, False, False, _LAST_MONTH, True, True),
    )
    @ddt.unpack
    def test_should_certificate_be_visible(
        self,
        certificates_display_behavior,
        certificates_show_before_end,
        has_ended,
        certificate_available_date,
        self_paced,
        expected_value
    ):
        """Test whether the certificate should be visible to user given multiple usecases"""
        assert should_certificate_be_visible(
            certificates_display_behavior,
            certificates_show_before_end,
            has_ended,
            certificate_available_date,
            self_paced
        ) == expected_value
