"""Tests for the certificates Python Data Class. """
from django.test import TestCase

from lms.djangoapps.certificates.data import CertificateStatuses


class CertificateStatusAPITests(TestCase):
    """
    Test the APIs related to certificate status.
    """

    def test_is_refundable_status(self):
        """
        Test is a certificate has a refundable status.
        """
        assert not CertificateStatuses.is_refundable_status(CertificateStatuses.downloadable)
        assert CertificateStatuses.is_refundable_status(CertificateStatuses.notpassing)

    def test_is_passing_status(self):
        """
        Test is a certificate has a refundable status.
        """
        assert not CertificateStatuses.is_passing_status(
            CertificateStatuses.notpassing
        )
        assert CertificateStatuses.is_passing_status(
            CertificateStatuses.downloadable
        )
