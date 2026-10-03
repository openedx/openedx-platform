"""Dependency-free policy tests; runnable with unittest outside Django settings."""

# unittest is intentional: this policy suite runs without pytest/Django installed.
# ruff: noqa: PT009, PT027
import importlib.util
import sys
import unittest
from pathlib import Path

# Load just the policy file, not the platform's Django application graph.
_SPEC = importlib.util.spec_from_file_location(
    "content_reconciliation_policy", Path(__file__).parents[1] / "content_reconciliation.py",
)
_POLICY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _POLICY
_SPEC.loader.exec_module(_POLICY)
reconcile_components = _POLICY.reconcile_components


def document(key="a", **fields):
    return {"id": key, "usage_key": key, "context_key": "lib:org:one", "type": "library_block", **fields}


class ReconcilePolicyTest(unittest.TestCase):
    """Exercise bounded writes, ambiguous identity and observed mutation races."""

    def setUp(self):
        self.writes = []

    def run_reconcile(self, sources=None, indexed=None, **kwargs):
        """Run the policy against in-memory source/index boundaries."""
        sources = {"a": document()} if sources is None else sources
        indexed = {} if indexed is None else indexed
        self.writes = []

        def write_batch(docs):
            self.writes.append(docs)
            indexed.update({doc["id"]: doc for doc in docs})

        options = {
            "context_key": "lib:org:one", "source_keys": iter(sources),
            "read_source": sources.get, "read_index": indexed.get,
            "indexed_documents": iter(list(indexed.values())), "write_batch": write_batch,
        }
        options.update(kwargs)
        return reconcile_components(**options)

    def test_indexed_usage_key_with_noncanonical_id_is_unknown(self):
        report = self.run_reconcile(indexed={"noncanonical": document(id="noncanonical")}, repair=True)
        self.assertEqual(report.unknown, 1)
        self.assertEqual(report.repaired, 1)  # Restore the absent canonical ID only.
        self.assertEqual(self.writes, [[document()]])

    def test_dry_run_default(self):
        report = self.run_reconcile()
        self.assertEqual(report.missing, 1)
        self.assertEqual(report.repaired, 0)
        self.assertEqual(self.writes, [])

    def test_missing_repair(self):
        report = self.run_reconcile(repair=True)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(self.writes, [[document()]])

    def test_stale_removed_field_replaced(self):
        report = self.run_reconcile(indexed={"a": document(obsolete="remove me")}, repair=True)
        self.assertEqual((report.stale, report.repaired), (1, 1))
        self.assertNotIn("obsolete", self.writes[0][0])

    def test_unchanged_no_write(self):
        report = self.run_reconcile(indexed={"a": document()}, repair=True)
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(self.writes, [])

    def test_batch_bound(self):
        report = self.run_reconcile(sources={str(n): document(str(n)) for n in range(7)}, repair=True, batch_size=3)
        self.assertEqual(report.repaired, 7)
        self.assertEqual([len(batch) for batch in self.writes], [3, 3, 1])

    def test_source_bound_explicit(self):
        report = self.run_reconcile(sources={str(n): document(str(n)) for n in range(7)}, max_documents=2)
        self.assertEqual(report.scanned, 2)
        self.assertTrue(report.source_truncated)

    def test_index_bound_explicit(self):
        report = self.run_reconcile(sources={}, indexed={str(n): document(str(n)) for n in range(7)}, max_documents=2)
        self.assertEqual(report.index_only, 2)
        self.assertTrue(report.index_truncated)

    def test_index_only_never_deleted(self):
        report = self.run_reconcile(sources={}, indexed={"a": document()}, repair=True)
        self.assertEqual(report.index_only, 1)
        self.assertEqual(self.writes, [])

    def test_unenumerated_source_is_not_orphan(self):
        report = self.run_reconcile(
            sources={"a": document(), "b": document("b")},
            indexed={"b": document("b")}, max_documents=1,
        )
        self.assertTrue(report.source_truncated)
        self.assertEqual(report.index_only, 0)

    def test_foreign_context_collision_skipped(self):
        report = self.run_reconcile(indexed={"a": document(context_key="lib:org:other")}, repair=True)
        self.assertEqual(report.unknown, 2)
        self.assertEqual(self.writes, [])

    def test_wrong_key_collision_skipped(self):
        report = self.run_reconcile(indexed={"a": document(usage_key="other")}, repair=True)
        self.assertGreaterEqual(report.unknown, 1)
        self.assertEqual(self.writes, [])

    def test_source_change_before_write_skipped(self):
        reads = iter([document(), document(display_name="new")])
        report = self.run_reconcile(repair=True, read_source=lambda _: next(reads))
        self.assertEqual(report.skipped_concurrent, 1)
        self.assertEqual(self.writes, [])

    def test_source_deleted_before_write_skipped(self):
        reads = iter([document(), None])
        report = self.run_reconcile(repair=True, read_source=lambda _: next(reads))
        self.assertEqual(report.skipped_concurrent, 1)
        self.assertEqual(self.writes, [])

    def test_index_change_before_write_skipped(self):
        reads = iter([None, document(display_name="new")])
        report = self.run_reconcile(repair=True, read_index=lambda _: next(reads))
        self.assertEqual(report.skipped_concurrent, 1)
        self.assertEqual(self.writes, [])

    def test_transport_error_not_missing(self):
        def failed_read(_):
            raise ConnectionError("engine unavailable")
        with self.assertRaises(ConnectionError):
            self.run_reconcile(read_index=failed_read, repair=True)
        self.assertEqual(self.writes, [])

    def test_failed_write_propagates(self):
        def failed_write(_):
            raise RuntimeError("task failed")
        with self.assertRaises(RuntimeError):
            self.run_reconcile(repair=True, write_batch=failed_write)

    def test_invalid_limits(self):
        for kwargs in ({"batch_size": 0}, {"batch_size": 1001}, {"max_documents": 0},
                       {"batch_size": True}, {"batch_size": 1.5}, {"max_documents": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_reconcile(**kwargs)


if __name__ == "__main__":
    unittest.main()
