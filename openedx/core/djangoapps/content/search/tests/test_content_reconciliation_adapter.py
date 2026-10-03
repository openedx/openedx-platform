"""Isolated adapter contract tests with the pinned Meilisearch SDK.

Execute the real API function body with mocked platform services. This validates
adapter behavior, not Django ORM/database integration or a live Meilisearch.
"""
# ruff: noqa: PT009, PT027
import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from meilisearch.errors import MeilisearchApiError
from meilisearch.models.document import Document, DocumentsResults

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("isolated_search.content_reconciliation", ROOT / "content_reconciliation.py")
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POLICY
SPEC.loader.exec_module(POLICY)


class LibraryKey:
    """Minimal stand-in for an already-parsed library key."""

    def __str__(self):
        return "lib:org:one"


class ComponentKey:
    """Minimal usage key satisfying the API's parsing/context contract."""

    def __init__(self, name):
        self.name = name
        self.context_key = LIBRARY

    def __str__(self):
        return self.name

    @classmethod
    def from_string(cls, key):
        return cls(key)


LIBRARY = LibraryKey()


def doc(key="a", **fields):
    return {"id": key, "usage_key": key, "context_key": str(LIBRARY), "type": "library_block", **fields}


class NotFound(Exception):
    """Stand-in for Django ObjectDoesNotExist."""


def engine_error(code):
    # Use the real SDK exception class, without constructing an HTTP response.
    err = MeilisearchApiError.__new__(MeilisearchApiError)
    Exception.__init__(err, code)
    err.code = code
    return err


class AdapterTest(unittest.TestCase):
    """Verify the actual API adapter, with only external dependencies replaced."""

    def setUp(self):
        self.lib = MagicMock()
        self.index = MagicMock(primary_key="id")
        self.client = MagicMock()
        self.client.get_index.return_value = self.index
        self.access = MagicMock()
        self.access.objects.filter.return_value.exists.return_value = True
        self.lib.get_library_components.return_value.order_by.return_value.iterator.return_value = ["a"]
        self.lib.get_library_components.return_value.filter.return_value.exists.return_value = True
        self.lib.get_component_from_usage_key.side_effect = lambda key: types.SimpleNamespace(pk=str(key))
        self.lib.LibraryXBlockMetadata.from_component.side_effect = lambda key, comp: types.SimpleNamespace(
            usage_key=ComponentKey(comp if isinstance(comp, str) else comp.pk),
        )
        self.lib.ContentLibraryBlockNotFound = NotFound
        self.index.get_document.return_value = Document(doc())
        self.index.get_documents.return_value = DocumentsResults({"results": [], "offset": 0, "limit": 100, "total": 0})
        self.rebuild = MagicMock(return_value=None)
        self.wait = MagicMock()
        self.namespace = {
            "__package__": "isolated_search", "LibraryLocatorV2": LibraryKey, "UsageKey": ComponentKey,
            "lib_api": self.lib, "STUDIO_LIBRARY_INDEX_NAME": "library", "INDEX_PRIMARY_KEY": "id",
            "DocType": types.SimpleNamespace(library_block="library_block"),
            "Fields": types.SimpleNamespace(usage_key="usage_key"), "InvalidKeyError": ValueError,
            "MeilisearchApiError": MeilisearchApiError, "_get_meilisearch_client": lambda: self.client,
            "_get_running_rebuild_index_name": self.rebuild, "_wait_for_meili_task": self.wait,
            "searchable_doc_for_library_block": lambda metadata: doc(str(metadata.usage_key), access_id=7),
            "searchable_doc_tags": lambda key: {"tags": {}},
            "searchable_doc_collections": lambda key: {"collections": {}},
            "searchable_doc_containers": lambda key, group: {group: {}},
        }
        tree = ast.parse((ROOT / "api.py").read_text())
        target = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "reconcile_library_components")
        exec(compile(ast.Module(body=[target], type_ignores=[]), "api.py", "exec"), self.namespace)
        self.function = self.namespace["reconcile_library_components"]
        exceptions = types.ModuleType("django.core.exceptions")
        exceptions.ObjectDoesNotExist = NotFound
        models = types.ModuleType("isolated_search.models")
        models.SearchAccess = self.access
        self.modules = patch.dict(sys.modules, {"django.core.exceptions": exceptions, "isolated_search.models": models})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_dry_run_document_sdk_and_no_writes(self):
        report = self.function(LIBRARY)
        self.assertEqual(report.stale, 1)
        self.index.add_documents.assert_not_called()
        self.access.objects.create.assert_not_called()
        self.assertEqual(self.index.get_documents.call_args.args[0]["filter"],
                         'context_key = "lib:org:one" AND type = "library_block"')

    def test_repair_full_document_and_wait(self):
        report = self.function(LIBRARY, repair=True)
        self.assertEqual(report.repaired, 1)
        written = self.index.add_documents.call_args.args[0][0]
        self.assertEqual(written["access_id"], 7)
        self.assertEqual(written["tags"], {})
        self.assertEqual(written["units"], {})
        self.index.update_documents.assert_not_called()
        self.wait.assert_called_once_with(self.index.add_documents.return_value)

    def test_only_document_not_found_is_absence(self):
        self.index.get_document.side_effect = engine_error("document_not_found")
        self.assertEqual(self.function(LIBRARY).missing, 1)

    def test_missing_index_not_treated_as_missing_document(self):
        self.index.get_document.side_effect = engine_error("index_not_found")
        with self.assertRaises(MeilisearchApiError):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()

    def test_engine_authorization_failure_aborts(self):
        self.index.get_document.side_effect = engine_error("invalid_api_key")
        with self.assertRaises(MeilisearchApiError):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()

    def test_active_rebuild_refused(self):
        self.rebuild.return_value = "library_new"
        with self.assertRaises(RuntimeError):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()

    def test_rebuild_started_before_write_refused(self):
        self.rebuild.side_effect = [None, "library_new"]
        with self.assertRaises(RuntimeError):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()

    def test_missing_access_dry_run_refused_without_creation(self):
        self.access.objects.filter.return_value.exists.return_value = False
        with self.assertRaises(ValueError):
            self.function(LIBRARY)
        self.client.get_index.assert_not_called()

    def test_paginated_documents_bounded_offsets(self):
        self.lib.get_library_components.return_value.order_by.return_value.iterator.return_value = []
        self.index.get_documents.side_effect = [
            DocumentsResults({"results": [doc("a"), doc("b")], "offset": 0, "limit": 2, "total": 4}),
            DocumentsResults({"results": [doc("c")], "offset": 2, "limit": 1, "total": 4}),
        ]
        report = self.function(LIBRARY, batch_size=2, max_documents=2)
        self.assertTrue(report.index_truncated)
        self.assertEqual([call.args[0]["offset"] for call in self.index.get_documents.call_args_list], [0, 2])
        self.assertEqual([call.args[0]["limit"] for call in self.index.get_documents.call_args_list], [2, 1])

    def test_malformed_index_key_reported_unknown(self):
        self.lib.get_library_components.return_value.order_by.return_value.iterator.return_value = []
        self.index.get_documents.side_effect = [
            DocumentsResults({"results": [doc(usage_key=42)], "offset": 0, "limit": 100, "total": 1}),
            DocumentsResults({"results": [], "offset": 1, "limit": 100, "total": 1}),
        ]
        report = self.function(LIBRARY)
        self.assertEqual(report.unknown, 1)
        self.assertEqual(report.index_only, 0)

    def test_wrong_primary_key_refused(self):
        self.index.primary_key = "other"
        with self.assertRaises(ValueError):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()

    def test_builder_failure_propagates_not_orphan(self):
        def fail_builder(_):
            raise NotFound("related source read failed")
        self.namespace["searchable_doc_for_library_block"] = fail_builder
        with self.assertRaises(NotFound):
            self.function(LIBRARY, repair=True)
        self.index.add_documents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
