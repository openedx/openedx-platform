"""
Tests for the `purge_references_to_pdf_certificates` management command.
"""

import uuid

from django.core.management import CommandError, call_command
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class PurgeReferencesToPDFCertificatesTests(ModuleStoreTestCase):
    """
    Tests for the `purge_references_to_pdf_certificates` management command.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course_run_1 = CourseFactory()
        self.course_run_2 = CourseFactory()
        self.course_run_3 = CourseFactory()
        self.cert_1 = GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course_run_1.id,
            download_url="http://example.com/1",
            download_uuid=uuid.uuid4(),
            grade=1.00,
        )
        self.cert_2 = GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course_run_2.id,
            download_url="http://example.com/2",
            download_uuid=uuid.uuid4(),
            grade=2.00,
        )
        self.cert_3 = GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course_run_3.id,
            download_url="http://example.com/3",
            download_uuid=uuid.uuid4(),
            grade=3.00,
        )

    def test_command_with_missing_certificate_ids(self):
        """
        Verify command with a missing certificate_ids param.
        """
        with self.assertRaises(CommandError):  # noqa: PT027
            call_command("purge_references_to_pdf_certificates")

    def test_management_command(self):
        """
        Verify the management command purges expected data from only the certs requested.
        """
        call_command(
            "purge_references_to_pdf_certificates",
            "--certificate_ids",
            self.cert_2.id,
            self.cert_3.id,
        )

        cert1_post = GeneratedCertificate.objects.get(id=self.cert_1.id)
        cert2_post = GeneratedCertificate.objects.get(id=self.cert_2.id)
        cert3_post = GeneratedCertificate.objects.get(id=self.cert_3.id)
        self.assertEqual(cert1_post.download_url, "http://example.com/1")  # noqa: PT009
        self.assertNotEqual(cert1_post.download_uuid, "")  # noqa: PT009

        self.assertEqual(cert2_post.download_url, "")  # noqa: PT009
        self.assertEqual(cert2_post.download_uuid, "")  # noqa: PT009

        self.assertEqual(cert3_post.download_url, "")  # noqa: PT009
        self.assertEqual(cert3_post.download_uuid, "")  # noqa: PT009

    def test_management_command_dry_run(self):
        """
        Verify that the management command does not purge any data when invoked with the `--dry-run` flag
        """
        expected_log_msg = (
            "[DRY RUN] Purging download_url and download_uri "
            f"from the following certificate records: {list(str(self.cert_3.id))}"
        )

        with LogCapture() as logger:
            call_command(
                "purge_references_to_pdf_certificates",
                "--dry-run",
                "--certificate_ids",
                self.cert_3.id,
            )

        cert3_post = GeneratedCertificate.objects.get(id=self.cert_3.id)
        self.assertEqual(cert3_post.download_url, "http://example.com/3")  # noqa: PT009
        self.assertNotEqual(cert3_post.download_uuid, "")  # noqa: PT009

        assert logger.records[0].msg == expected_log_msg
