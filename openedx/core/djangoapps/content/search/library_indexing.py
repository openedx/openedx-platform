"""Durable library-wide indexing with an explicit, recoverable broker boundary."""

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from kombu.exceptions import OperationalError
from meilisearch.errors import MeilisearchError
from opaque_keys.edx.locator import LibraryLocatorV2

from .library_indexing_batches import index_library_in_batches
from .models import LibraryIndexRequest

log = logging.getLogger(__name__)


def _dispatch(library_key):
    """A broker outage must not undo a committed content change."""
    try:
        process_library_index_request.delay(library_key)
    except OperationalError:
        log.exception("Library indexing dispatch failed; durable request remains pending for %s", library_key)


def request_library_index(library_key):
    """Record intent in the caller's transaction and dispatch only after commit."""
    library_key = str(LibraryLocatorV2.from_string(str(library_key)))
    with transaction.atomic():
        request, _ = LibraryIndexRequest.objects.get_or_create(library_key=library_key)
        request = LibraryIndexRequest.objects.select_for_update().get(pk=request.pk)
        request.requested_revision += 1
        request.save(update_fields=["requested_revision", "updated_at"])
        transaction.on_commit(lambda: _dispatch(library_key))


@shared_task(
    autoretry_for=(MeilisearchError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_library_index_request(library_key):
    """Serialize writes for one library and acknowledge only completed engine tasks.

    The conservative row lock spans the engine call. Duplicate broker deliveries
    become no-ops; a crashed worker leaves the revision pending. This lock requires
    a database supporting row locks (the production MySQL/PostgreSQL backends).
    """
    from openedx.core.djangoapps.content_libraries.api import (  # pylint: disable=import-outside-toplevel
        ContentLibraryNotFound,
    )


    with transaction.atomic():
        request = LibraryIndexRequest.objects.select_for_update().filter(library_key=library_key).first()
        if request is None or request.completed_revision == request.requested_revision:
            return
        try:
            index_library_in_batches(
                LibraryLocatorV2.from_string(library_key),
                batch_size=getattr(settings, "LIBRARY_SEARCH_INDEX_BATCH_SIZE", 100),
            )
        except ContentLibraryNotFound:
            # Deletion may precede delivery even if its cancellation signal was lost.
            request.delete()
            return
        request.completed_revision = request.requested_revision
        request.save(update_fields=["completed_revision", "updated_at"])
