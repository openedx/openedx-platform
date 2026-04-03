"""
A management command designed to be part of the retirement pipeline for any Open EdX operators
with users who still have legacy PDF certificates.

Once an external process has run to remove the four files comprising a legacy PDF certificate,
this management command will remove the reference to the file from the certificate record.

Note: it is important to retain the reference in the certificate table until
the files have been deleted, because that reference is the files' identifying descriptor.
"""

import logging
import shlex

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.certificates.models import (
    GeneratedCertificate,
    PurgeReferencestoPDFCertificatesCommandConfiguration,
)

User = get_user_model()
log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Doesn't invoke the custom save() function defined as part of the `GeneratedCertificate`
    model; perforce will emit no Django signals. This is desired behavior. We are
    using this management command to purge information that was never sent to any
    other systems, so we don't need to propagate updates.

    Example usage:

    # Dry Run (preview changes):
    $ ./manage.py lms purge_references_to_pdf_certificates --dry-run

    # Purge data:
    $ ./manage.py lms purge_references_to_pdf_certificates
    """

    help = """Purges references to PDF certificates. Intended to be run after the files have been deleted."""

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Shows a preview of what users would be affected by running this management command.",
        )
        parser.add_argument(
            "--certificate_ids",
            nargs="+",
            dest="certificate_ids",
            help="space-separated list of GeneratedCertificate IDs to clean up",
        )
        parser.add_argument(
            "--args-from-database",
            action="store_true",
            help=(
                "Use arguments from the PurgeReferencesToPDFCertificatesCommandConfiguration "
                "model instead of the command line"
            ),
        )

    def get_args_from_database(self):
        """
        Returns an options dictionary from the current CertificateGenerationCommandConfiguration model.
        """
        config = PurgeReferencestoPDFCertificatesCommandConfiguration.current()
        if not config.enabled:
            raise CommandError(
                "PurgeReferencestoPDFCertificatesCommandConfiguration is disabled, "
                "but --args-from-database was requested"
            )

        args = shlex.split(config.arguments)
        parser = self.create_parser("manage.py", "purge_references_to_pdf_certificates")

        return vars(parser.parse_args(args))

    def handle(self, *args, **options):
        # database args will override cmd line args
        if options["args_from_database"]:
            options = self.get_args_from_database()

        if options["dry_run"]:
            dry_run_string = "[DRY RUN] "
        else:
            dry_run_string = ""

        certificate_ids = options.get("certificate_ids")
        if not certificate_ids:
            raise CommandError("You must specify one or more certificate IDs")

        log.info(
            f"{dry_run_string}Purging download_url and download_uri "
            f"from the following certificate records: {certificate_ids}"
        )
        if not options["dry_run"]:
            GeneratedCertificate.objects.filter(id__in=certificate_ids).update(
                download_url="",
                download_uuid="",
            )
