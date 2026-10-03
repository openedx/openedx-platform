"""Durable outbox tests; engine calls are mocked at the existing API boundary."""

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from kombu.exceptions import OperationalError

from openedx.core.djangoapps.content_libraries.api import ContentLibraryNotFound

from ..library_indexing import process_library_index_request, request_library_index
from ..models import LibraryIndexRequest


class LibraryIndexingTests(TestCase):
    """Verify durable revisions and dispatch across transaction boundaries."""
    library_key = "lib:org:library"

    def request(self):
        with patch("openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                request_library_index(self.library_key)

    def test_dispatch_only_after_commit(self):
        with patch(
            "openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay",
        ) as delay:
            with self.captureOnCommitCallbacks(execute=True):
                request_library_index(self.library_key)
                delay.assert_not_called()
            delay.assert_called_once_with(self.library_key)

    def test_rollback_discards_intent_and_dispatch(self):
        with patch(
            "openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay",
        ) as delay:
            with self.captureOnCommitCallbacks(execute=True):
                with pytest.raises(RuntimeError), transaction.atomic():  # noqa: PT012
                    request_library_index(self.library_key)
                    raise RuntimeError("publication rolled back")
            delay.assert_not_called()
        assert not LibraryIndexRequest.objects.exists()

    def test_broker_failure_preserves_pending_request(self):
        with patch("openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay",
                   side_effect=OperationalError("broker unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                request_library_index(self.library_key)
        request = LibraryIndexRequest.objects.get()
        assert (request.requested_revision, request.completed_revision) == (1, 0)

    @patch("openedx.core.djangoapps.content.search.library_indexing.index_library_in_batches")
    def test_coalesce_and_duplicate_delivery(self, upsert):
        self.request()
        self.request()
        process_library_index_request.run(self.library_key)
        process_library_index_request.run(self.library_key)
        upsert.assert_called_once()
        assert upsert.call_args.kwargs["batch_size"] == 100
        request = LibraryIndexRequest.objects.get()
        assert (request.requested_revision, request.completed_revision) == (2, 2)

    @patch("openedx.core.djangoapps.content.search.library_indexing.index_library_in_batches")
    def test_failed_engine_call_remains_pending_and_can_retry(self, upsert):
        self.request()
        upsert.side_effect = ConnectionError("engine unavailable")
        # Call the original task body so this assertion tests persistence, not Celery retry machinery.
        with pytest.raises(ConnectionError):
            process_library_index_request._orig_run(self.library_key)  # pylint: disable=protected-access
        assert LibraryIndexRequest.objects.get().completed_revision == 0
        upsert.side_effect = None
        process_library_index_request.run(self.library_key)
        assert LibraryIndexRequest.objects.get().completed_revision == 1

    @patch("openedx.core.djangoapps.content.search.library_indexing.index_library_in_batches")
    def test_new_request_after_completion_runs_again(self, upsert):
        self.request()
        process_library_index_request.run(self.library_key)
        self.request()
        process_library_index_request.run(self.library_key)
        assert upsert.call_count == 2
        assert LibraryIndexRequest.objects.get().completed_revision == 2

    @patch("openedx.core.djangoapps.content.search.library_indexing.index_library_in_batches")
    def test_cancelled_request_is_noop(self, upsert):
        self.request()
        LibraryIndexRequest.objects.all().delete()
        process_library_index_request.run(self.library_key)
        upsert.assert_not_called()

    @patch("openedx.core.djangoapps.content.search.library_indexing.index_library_in_batches")
    def test_missing_library_cancels_pending_request(self, upsert):
        self.request()
        upsert.side_effect = ContentLibraryNotFound()
        process_library_index_request.run(self.library_key)
        assert not LibraryIndexRequest.objects.exists()

    def test_recovery_command_only_dispatches_pending_with_limit(self):
        self.request()
        LibraryIndexRequest.objects.create(library_key="lib:org:complete", requested_revision=1, completed_revision=1)
        LibraryIndexRequest.objects.create(library_key="lib:org:other", requested_revision=1)
        with patch(
            "openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay",
        ) as delay:
            call_command("retry_library_index_requests", limit=1)
        assert delay.call_count == 1

    def test_recovery_rejects_unbounded_negative_limit(self):
        with pytest.raises(ValueError, match="limit must be positive"):
            call_command("retry_library_index_requests", limit=-1)
