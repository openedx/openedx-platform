"""Bounded, source-authoritative library component reconciliation.

This module deliberately has no Django imports so its repair policy can be tested
without a platform runtime. The platform adapters live in ``api.py``.
"""

from dataclasses import asdict, dataclass
from itertools import islice


@dataclass
class ContentDriftReport:
    """Counters only: reports never expose component bodies or access identifiers."""

    scanned: int = 0
    missing: int = 0
    stale: int = 0
    unchanged: int = 0
    repaired: int = 0
    skipped_concurrent: int = 0
    index_only: int = 0
    unknown: int = 0
    source_truncated: bool = False
    index_truncated: bool = False

    def as_dict(self):
        """Return a JSON-serializable summary."""
        return asdict(self)


def validate_reconciliation_limits(batch_size, max_documents):
    """Require integer scan/batch limits and reject booleans explicitly."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if not isinstance(max_documents, int) or isinstance(max_documents, bool) or max_documents < 1:
        raise ValueError("max_documents must be positive")


def _valid_component_identity(doc, context_key):
    """Require a component identity scoped to the selected library."""
    return (
        doc is not None and doc.get("context_key") == context_key
        and doc.get("type") == "library_block" and bool(doc.get("id"))
        and bool(doc.get("usage_key"))
    )


def _inspect_indexed_components(report, *, context_key, indexed_documents, read_source, max_documents):
    """Inspect bounded indexed identities without authorizing any deletion."""
    for position, doc in enumerate(islice(indexed_documents, max_documents + 1)):
        if position == max_documents:
            report.index_truncated = True
            break
        if not _valid_component_identity(doc, context_key):
            report.unknown += 1
        else:
            canonical = read_source(doc["usage_key"])
            if canonical is None:
                report.index_only += 1
            elif canonical["id"] != doc["id"]:
                report.unknown += 1


def reconcile_components(
    *, context_key, source_keys, read_source, read_index, indexed_documents,
    write_batch, repair=False, batch_size=100, max_documents=10000,
):
    """Compare canonical source documents and cautiously replace drifted entries.

    ``read_source`` must return None for missing/deleted draft components and
    propagate all other failures. ``read_index`` likewise treats only an explicit
    document-not-found result as absence. Source keys and index documents must be
    lazy bounded streams. Full-document equality detects removed fields as drift.
    Index-only documents are reported, never deleted. Each scan has its own cap.
    """
    validate_reconciliation_limits(batch_size, max_documents)
    report = ContentDriftReport()
    pending = []

    def flush():
        docs = []
        for key, expected, observed in pending:
            # Revalidate immediately before the bounded write, not at scan time.
            latest = read_source(key)
            current = read_index(expected["id"])
            if latest != expected or current != observed:
                report.skipped_concurrent += 1
            else:
                docs.append(latest)
        if docs:
            write_batch(docs)  # Must wait for task success; exceptions abort the run.
            report.repaired += len(docs)
        pending.clear()

    for position, key in enumerate(islice(source_keys, max_documents + 1)):
        if position == max_documents:
            report.source_truncated = True
            break
        report.scanned += 1
        expected = read_source(key)
        if not _valid_component_identity(expected, context_key) or expected["usage_key"] != str(key):
            report.unknown += 1
            continue
        observed = read_index(expected["id"])
        # A colliding/corrupt primary key must not replace another context/type.
        if observed is not None and (
            not _valid_component_identity(observed, context_key) or observed["usage_key"] != expected["usage_key"]
        ):
            report.unknown += 1
            continue
        if expected == observed:
            report.unchanged += 1
            continue
        if observed is None:
            report.missing += 1
        else:
            report.stale += 1
        if repair:
            pending.append((key, expected, observed))
            if len(pending) == batch_size:
                flush()
    if pending:
        flush()

    _inspect_indexed_components(
        report, context_key=context_key, indexed_documents=indexed_documents,
        read_source=read_source, max_documents=max_documents,
    )
    return report
