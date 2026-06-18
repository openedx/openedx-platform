"""
A management command, designed to be run once by Open edX Operators, to obfuscate learner PII from the
`certificates_generatedcertificate` and `certificates_historicalgeneratedcertificate` tables that should have been
purged during learner retirement.

A fix has been included in the retirement pipeline to properly purge this data during learner retirement. This can be
used to purge PII from accounts that have already been retired.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from lms.djangoapps.certificates.config import REDACT_CERTIFICATES_HISTORICAL_PII
from lms.djangoapps.certificates.models import GeneratedCertificate
from openedx.core.djangoapps.user_api.api import get_retired_user_ids

User = get_user_model()
log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This management command performs bulk updates on `GeneratedCertificate` instances and their django-simple-history
    audit rows in `certificates_historicalgeneratedcertificate`. This means that it will not invoke the custom save()
    function defined as part of the `GeneratedCertificate` model, and thus will not emit any Django signals throughout
    the system after the update occurs. This is desired behavior. We are using this management command to purge remnant
    PII, retired elsewhere in the system, that should have already been removed from the Certificates tables. We don't
    need updates to propogate to external systems (like the Credentials IDA).

    This management command functions by requesting a list of learners' user_ids whom have completed their journey
    through the retirement pipeline. The `get_retired_user_ids` utility function is responsible for filtering out any
    learners in the PENDING state, as they could still submit a request to cancel their account deletion request (and
    we don't want to remove any data that may still be good).

    Example usage:

    # Dry Run (preview changes):
    $ ./manage.py lms purge_pii_from_generatedcertificates --dry-run

    # Purge data:
    $ ./manage.py lms purge_pii_from_generatedcertificates
    """

    help = """
    Purges learners' full names from the `certificates_generatedcertificate` table and conditionally the
    `certificates_historicalgeneratedcertificate` table if their account has been successfully retired.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Shows a preview of what users would be affected by running this management command.",
        )

    def handle(self, *args, **options):
        retired_user_ids = get_retired_user_ids()
        if not options["dry_run"]:
            log.warning(
                f"Purging `name` from the certificate records of the following users: {retired_user_ids}"
            )
            GeneratedCertificate.objects.filter(user_id__in=retired_user_ids).update(name="")
            if REDACT_CERTIFICATES_HISTORICAL_PII.is_enabled():
                log.info(
                    f"Purging `name` from the historical certificate records of the following users: {retired_user_ids}"
                )
                GeneratedCertificate.history.filter(user_id__in=retired_user_ids).update(name="")
        else:
            log.info(
                "DRY RUN: running this management command would purge `name` data from the following users: "
                f"{retired_user_ids}"
            )
