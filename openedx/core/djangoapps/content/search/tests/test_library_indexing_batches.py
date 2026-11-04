"""Verify real streaming batches and durable replay through the worker boundary."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import TestCase

from openedx.core.djangoapps.content_libraries.api import ContentLibraryNotFound

from ..library_indexing import process_library_index_request, request_library_index
from ..library_indexing_batches import index_library_in_batches
from ..models import LibraryIndexRequest


class LibraryIndexBatchTests(TestCase):
    """Exercise lazy batches and replay after partial engine failures."""
    library_key = "lib:org:library"

    def setUp(self):
        super().setUp()
        self.querysets = [Mock(), Mock(), Mock()]
        self.items = [
            [SimpleNamespace(id=f"block-{number}") for number in range(3)],
            [SimpleNamespace(id=f"container-{number}") for number in range(2)],
            [SimpleNamespace(id=f"collection-{number}", collection_code=str(number)) for number in range(2)],
        ]
        # Fresh iterator each invocation: retries must read authoritative source again.
        for queryset, items in zip(self.querysets, self.items, strict=True):
            queryset.iterator.side_effect = lambda chunk_size, items=items: iter(items)

        def document(item, **kwargs):
            return {"id": item.id}

        lib_patcher = patch.multiple(
            "openedx.core.djangoapps.content.search.library_indexing_batches.lib_api",
            create=True,
            get_library_components=Mock(return_value=self.querysets[0]),
            get_library_containers=Mock(return_value=self.querysets[1]),
            get_library_collections=Mock(return_value=self.querysets[2]),
            LibraryXBlockMetadata=SimpleNamespace(from_component=lambda key, component: component),
            library_container_locator=Mock(side_effect=lambda key, container: container),
            library_collection_locator=Mock(side_effect=lambda key, code: self.items[2][int(code)]),
        )
        self.write = Mock()
        api_patcher = patch.multiple(
            "openedx.core.djangoapps.content.search.api",
            create=True,
            searchable_doc_for_library_block=Mock(side_effect=document),
            searchable_doc_for_container=Mock(side_effect=document),
            searchable_doc_for_collection=Mock(side_effect=document),
            STUDIO_LIBRARY_INDEX_NAME="studio_library",
            _update_index_docs=self.write,
        )
        lib_patcher.start()
        api_patcher.start()
        self.addCleanup(lib_patcher.stop)
        self.addCleanup(api_patcher.stop)

    def request(self):
        with patch("openedx.core.djangoapps.content.search.library_indexing.process_library_index_request.delay"):
            request_library_index(self.library_key)

    def test_bounded_batches_include_all_document_types(self):
        index_library_in_batches(self.library_key, batch_size=2)
        calls = self.write.call_args_list
        assert [len(call.args[1]) for call in calls] == [2, 2, 2, 1]
        assert [doc["id"] for call in calls for doc in call.args[1]] == [
            item.id for items in self.items for item in items
        ]
        for queryset in self.querysets:
            queryset.iterator.assert_called_once_with(chunk_size=2)

    def test_empty_library_submits_no_batch(self):
        for queryset in self.querysets:
            queryset.iterator.side_effect = lambda chunk_size: iter(())
        index_library_in_batches(self.library_key, batch_size=2)
        self.write.assert_not_called()

    def test_exact_boundary_submits_no_empty_tail(self):
        self.querysets[2].iterator.side_effect = lambda chunk_size: iter(self.items[2][:1])
        index_library_in_batches(self.library_key, batch_size=2)
        assert [len(call.args[1]) for call in self.write.call_args_list] == [2, 2, 2]

    def test_invalid_batch_sizes_fail_before_source_reads(self):
        for batch_size in (0, -1, True, 2.5, "2"):
            with pytest.raises(ValueError, match="positive integer"):
                index_library_in_batches(self.library_key, batch_size=batch_size)
        for queryset in self.querysets:
            queryset.iterator.assert_not_called()

    def test_failed_batch_stops_later_reads(self):
        self.write.side_effect = ConnectionError("batch failed")
        with pytest.raises(ConnectionError):
            index_library_in_batches(self.library_key, batch_size=2)
        assert self.write.call_count == 1
        self.querysets[1].iterator.assert_not_called()
        self.querysets[2].iterator.assert_not_called()

    def test_partial_failure_leaves_revision_pending_and_replays(self):
        self.request()
        self.write.side_effect = [None, ConnectionError("second batch failed")]
        with self.settings(LIBRARY_SEARCH_INDEX_BATCH_SIZE=2):
            with pytest.raises(ConnectionError):
                process_library_index_request._orig_run(self.library_key)  # pylint: disable=protected-access
            assert LibraryIndexRequest.objects.get().completed_revision == 0
            first_batch = self.write.call_args_list[0].args[1]
            self.write.reset_mock(side_effect=True)
            process_library_index_request.run(self.library_key)
        assert self.write.call_args_list[0].args[1] == first_batch
        assert self.write.call_count == 4
        assert LibraryIndexRequest.objects.get().completed_revision == 1

    def test_missing_library_after_partial_write_cancels_intent(self):
        self.request()
        self.write.side_effect = [None, ContentLibraryNotFound()]
        with self.settings(LIBRARY_SEARCH_INDEX_BATCH_SIZE=2):
            process_library_index_request.run(self.library_key)
        assert not LibraryIndexRequest.objects.exists()
        assert self.write.call_count == 2
