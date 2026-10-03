"""CMS source/command integration and an opt-in disposable Meilisearch check."""
# pylint: disable=protected-access
import copy
import json
import os
from io import StringIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from meilisearch import Client
from meilisearch.errors import MeilisearchApiError
from meilisearch.models.document import Document, DocumentsResults
from opaque_keys.edx.locator import LibraryUsageLocatorV2
from openedx_content import models_api as content_models
from organizations.models import Organization
from requests import Response

from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangolib.testing.utils import skip_unless_cms

try:
    from .. import api
    from ..documents import meili_id_from_opaque_key
    from ..models import SearchAccess
except RuntimeError:
    # The app is installed in CMS only; the classes below are skipped in LMS.
    pass


def engine_error(code):
    """Build the SDK exception received at the external engine boundary."""
    response = Response()
    response.status_code = 404
    response._content = json.dumps({"message": code, "code": code, "type": "invalid_request"}).encode()
    return MeilisearchApiError(code, response)


class LibraryFixtures:
    # Django TestCase subclasses invoke this fixture initializer in setUp.
    # pylint: disable=attribute-defined-outside-init
    """Create actual library/component models; no source API or ORM doubles."""

    def create_library_fixtures(self):
        """Create canonical source records in two independent Libraries V2 contexts."""
        with override_settings(MEILISEARCH_ENABLED=False):
            org = Organization.objects.create(name="Reconciliation test", short_name="repairtest")
            self.library = library_api.create_library(org=org, slug="selected", title="Selected library")
            self.other_library = library_api.create_library(org=org, slug="other", title="Other library")
            self.first = library_api.create_library_block(self.library.key, "html", "first")
            self.second = library_api.create_library_block(self.library.key, "html", "second")
            self.other = library_api.create_library_block(self.other_library.key, "html", "other")
            library_api.create_library_collection(
                self.library.key, collection_key="collection", title="A collection", created_by=None,
            )
            library_api.update_library_collection_items(
                self.library.key, collection_key="collection", opaque_keys=[self.first.usage_key],
            )
            taxonomy = tagging_api.create_taxonomy(name="Subject", orgs=[org], allow_multiple=True)
            tagging_api.add_tag_to_taxonomy(taxonomy, tag="Algebra")
            tagging_api.tag_object(str(self.first.usage_key), taxonomy, tags=["Algebra"])
        for library in (self.library, self.other_library):
            SearchAccess.objects.get_or_create(context_key=library.key)
        self.expected = [self.canonical(block) for block in (self.first, self.second, self.other)]
        orphan_key = LibraryUsageLocatorV2(self.library.key, "html", "orphan")
        self.orphan = {
            **copy.deepcopy(self.expected[0]),
            "id": meili_id_from_opaque_key(orphan_key), "usage_key": str(orphan_key),
        }

    def canonical(self, block):
        """Use the same public document serializers as existing indexing paths."""
        doc = api.searchable_doc_for_library_block(block)
        doc.update(api.searchable_doc_tags(block.usage_key))
        doc.update(api.searchable_doc_collections(block.usage_key))
        doc.update(api.searchable_doc_containers(block.usage_key, "units"))
        return doc

    def run_command(self, *arguments):
        output = StringIO()
        call_command("reconcile_library_search", str(self.library.key), *arguments, stdout=output)
        return json.loads(output.getvalue())


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
class TestContentReconciliation(LibraryFixtures, TestCase):
    """Real CMS queries/serialization/commands with only the engine replaced."""

    def setUp(self):
        super().setUp()
        self.create_library_fixtures()
        self.documents = {doc["id"]: copy.deepcopy(doc) for doc in self.expected}
        self.index = MagicMock(primary_key="id")
        self.index.get_document.side_effect = self.read_document
        self.index.get_documents.side_effect = self.read_page
        self.index.add_documents.side_effect = self.replace_documents
        self.client = MagicMock()
        self.client.get_index.return_value = self.index
        client_patch = patch.object(api, "_get_meilisearch_client", return_value=self.client)
        wait_patch = patch.object(api, "_wait_for_meili_task")
        client_patch.start()
        wait_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(wait_patch.stop)
        self.addCleanup(content_models.Container.reset_cache)

    def read_document(self, document_id):
        if document_id not in self.documents:
            raise engine_error("document_not_found")
        return Document(copy.deepcopy(self.documents[document_id]))

    def read_page(self, options):
        docs = [doc for doc in self.documents.values()
                if doc["context_key"] == str(self.library.key) and doc["type"] == "library_block"]
        offset, limit = options["offset"], options["limit"]
        return DocumentsResults({
            "results": copy.deepcopy(docs[offset:offset + limit]),
            "offset": offset, "limit": limit, "total": len(docs),
        })

    def replace_documents(self, documents):
        for doc in documents:
            self.documents[doc["id"]] = copy.deepcopy(doc)

    def corrupt_documents(self):
        del self.documents[self.expected[0]["id"]]
        self.documents[self.expected[1]["id"]]["display_name"] = "Stale title"
        self.documents[self.expected[1]["id"]]["obsolete"] = True
        self.documents[self.orphan["id"]] = copy.deepcopy(self.orphan)

    def test_dry_run_reports_real_component_drift_without_writes(self):
        self.corrupt_documents()
        before = copy.deepcopy(self.documents)
        access_count = SearchAccess.objects.count()
        report = self.run_command()
        assert (report["missing"], report["stale"], report["index_only"]) == (1, 1, 1)
        assert report["repaired"] == 0
        assert self.documents == before
        assert SearchAccess.objects.count() == access_count

    def test_repair_restores_canonical_documents_and_preserves_other_records(self):
        self.corrupt_documents()
        report = self.run_command("--repair", "--batch-size", "1")
        assert report["repaired"] == 2
        for doc in self.expected:
            assert self.documents[doc["id"]] == doc
        assert self.documents[self.orphan["id"]] == self.orphan
        assert self.expected[0]["tags"]
        assert self.expected[0]["collections"]
        assert self.expected[0]["access_id"] == SearchAccess.objects.get(context_key=self.library.key).id
        clean = self.run_command()
        assert (clean["missing"], clean["stale"], clean["index_only"]) == (0, 0, 1)

    def test_command_rejects_invalid_library_key(self):
        with pytest.raises(CommandError):
            call_command("reconcile_library_search", "not-a-library")

    def test_command_reports_missing_library_without_a_traceback(self):
        with pytest.raises(CommandError):
            call_command("reconcile_library_search", "lib:repairtest:missing")

    def test_caps_report_both_source_and_index_truncation(self):
        report = self.run_command("--max-documents", "1", "--batch-size", "1")
        assert report["scanned"] == 1
        assert report["source_truncated"] is True
        assert report["index_truncated"] is True

    def test_command_refuses_actual_rebuild_lock(self):
        before = copy.deepcopy(self.documents)
        with api._index_rebuild_lock(api.STUDIO_LIBRARY_INDEX_NAME):
            with pytest.raises(CommandError, match="rebuild in progress"):
                self.run_command("--repair")
        assert self.documents == before

    def test_engine_errors_abort_without_being_treated_as_missing(self):
        before = copy.deepcopy(self.documents)
        self.index.get_document.side_effect = engine_error("index_not_found")
        with pytest.raises(MeilisearchApiError):
            self.run_command("--repair")
        assert self.documents == before

    def test_missing_access_metadata_is_not_created_by_dry_run(self):
        SearchAccess.objects.filter(context_key=self.library.key).delete()
        with pytest.raises(CommandError, match="access metadata is missing"):
            self.run_command()
        assert not SearchAccess.objects.filter(context_key=self.library.key).exists()

    def test_missing_index_aborts_instead_of_reporting_missing_content(self):
        self.client.get_index.side_effect = engine_error("index_not_found")
        with pytest.raises(MeilisearchApiError):
            self.run_command()

    def test_wrong_primary_key_refuses_repair(self):
        before = copy.deepcopy(self.documents)
        self.index.primary_key = "usage_key"
        with pytest.raises(CommandError, match="Reconcile index settings"):
            self.run_command("--repair")
        assert self.documents == before

    def test_corrupt_indexed_usage_key_is_unknown_and_preserved(self):
        malformed = {**self.orphan, "usage_key": "invalid-key"}
        self.documents[malformed["id"]] = malformed
        report = self.run_command("--repair")
        assert report["unknown"] == 1
        assert report["index_only"] == 0
        assert self.documents[malformed["id"]] == malformed

    def test_primary_key_collision_cannot_replace_another_library_document(self):
        collision = {**self.expected[2], "id": self.expected[0]["id"]}
        self.documents[collision["id"]] = collision
        report = self.run_command("--repair")
        assert report["unknown"] == 1
        assert report["repaired"] == 0
        assert self.documents[collision["id"]] == collision

    def test_duplicate_usage_key_with_wrong_id_is_unknown_and_preserved(self):
        malformed = {**self.expected[0], "id": "noncanonical-id"}
        self.documents[malformed["id"]] = malformed
        report = self.run_command("--repair")
        assert report["unknown"] == 1
        assert report["repaired"] == 0
        assert self.documents[malformed["id"]] == malformed
        self.index.add_documents.assert_not_called()

    def test_failed_engine_write_aborts_repair(self):
        self.corrupt_documents()
        before = copy.deepcopy(self.documents)
        self.index.add_documents.side_effect = engine_error("internal")
        with pytest.raises(MeilisearchApiError):
            self.run_command("--repair")
        assert self.documents == before


@skip_unless_cms
@pytest.mark.skipif(not os.environ.get("OPENEDX_SEARCH_LIVE_URL"), reason="Disposable Meilisearch URL not supplied")
@override_settings(MEILISEARCH_ENABLED=True)
class TestLiveContentReconciliation(LibraryFixtures, TestCase):
    """One small, explicit live-engine repair with no background mutations."""

    def test_live_missing_stale_orphan_and_other_library(self):
        self.create_library_fixtures()
        self.addCleanup(content_models.Container.reset_cache)
        client = Client(
            os.environ["OPENEDX_SEARCH_LIVE_URL"],
            os.environ.get("OPENEDX_SEARCH_LIVE_KEY", settings.MEILISEARCH_API_KEY),
        )
        name = "reconciliation_test_" + uuid4().hex
        client.wait_for_task(client.create_index(name, {"primaryKey": "id"}).task_uid)
        try:
            index = client.get_index(name)
            client.wait_for_task(index.update_filterable_attributes(["context_key", "type"]).task_uid)
            client.wait_for_task(index.add_documents(self.expected).task_uid)
            client.wait_for_task(index.delete_document(self.expected[0]["id"]).task_uid)
            stale = {**self.expected[1], "display_name": "Stale title", "obsolete": True}
            client.wait_for_task(index.add_documents([stale, self.orphan]).task_uid)
            before = [vars(index.get_document(doc["id"])) for doc in (stale, self.orphan, self.expected[2])]
            with patch.object(api, "_get_meilisearch_client", return_value=client), \
                    patch.object(api, "STUDIO_LIBRARY_INDEX_NAME", name):
                dry = self.run_command()
                assert (dry["missing"], dry["stale"], dry["index_only"]) == (1, 1, 1)
                assert dry["repaired"] == 0
                assert [vars(index.get_document(doc["id"]))
                        for doc in (stale, self.orphan, self.expected[2])] == before
                repaired = self.run_command("--repair", "--batch-size", "1")
                assert repaired["repaired"] == 2
                clean = self.run_command()
                assert (clean["missing"], clean["stale"], clean["index_only"]) == (0, 0, 1)
            for doc in (*self.expected, self.orphan):
                assert vars(index.get_document(doc["id"])) == doc
        finally:
            client.wait_for_task(client.delete_index(name).task_uid)
