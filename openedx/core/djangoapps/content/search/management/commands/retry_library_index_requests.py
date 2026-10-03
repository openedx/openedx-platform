"""Redispatch durable requests after a broker outage or exhausted worker retries."""

from django.core.management.base import BaseCommand
from django.db.models import F

from ...library_indexing import process_library_index_request
from ...models import LibraryIndexRequest


class Command(BaseCommand):
    help = "Redispatch a bounded batch of pending library indexing requests."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if options["limit"] <= 0:
            raise ValueError("--limit must be positive")
        pending = LibraryIndexRequest.objects.filter(
            requested_revision__gt=F("completed_revision"),
        ).order_by("updated_at").values_list("library_key", flat=True)[:options["limit"]]
        count = 0
        for library_key in pending:
            process_library_index_request.delay(library_key)
            count += 1
        self.stdout.write(f"Dispatched {count} pending library indexing requests.")
