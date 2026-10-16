"""Inspect and optionally repair component drift in one library."""

import json

from django.core.management import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import LibraryLocatorV2

from openedx.core.djangoapps.content_libraries.api import ContentLibraryNotFound

from ... import api


class Command(BaseCommand):
    """Default to inspection, requiring explicit --repair for document writes."""

    help = "Inspect component search drift in one library; --repair replaces missing/stale documents."

    def add_arguments(self, parser):
        parser.add_argument("library_key", help="Libraries V2 key, e.g. lib:org:slug")
        parser.add_argument("--repair", action="store_true", help="Write repairs (default: dry run)")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--max-documents", type=int, default=10000, help="Cap each source/index scan")

    def handle(self, *args, **options):
        if not api.is_meilisearch_enabled():
            raise CommandError("Meilisearch is disabled")
        try:
            key = LibraryLocatorV2.from_string(options["library_key"])
            report = api.reconcile_library_components(
                key, repair=options["repair"], batch_size=options["batch_size"],
                max_documents=options["max_documents"],
            )
        except (InvalidKeyError, ContentLibraryNotFound, ValueError, RuntimeError) as err:
            raise CommandError(str(err)) from err
        self.stdout.write(json.dumps({"repair": options["repair"], **report.as_dict()}, sort_keys=True))
