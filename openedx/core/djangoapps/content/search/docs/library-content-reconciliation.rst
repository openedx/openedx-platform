Library component drift audit and targeted repair
=================================================

Problem and existing behavior
-----------------------------

``reconcile_indexes`` handles index schema/settings; ``reindex_studio`` queues
population or an index rebuild. Neither compares individual live component
records with canonical source serialization. This contribution adds an explicit
operator command and API for one Libraries V2 context. It does not replace the
existing reconciliation or reindex pathways.

Implementation
--------------

``reconcile_library_components`` validates the library and existing search-access
metadata, requires the existing library index/primary key, then calls the
stdlib-only ``reconcile_components`` policy. Source enumeration uses the existing
draft-components queryset in primary-key order and ``iterator(chunk_size=...)``.
The document is assembled with the existing library-block serializer, tags,
collections and units, matching the full rebuild fields including permissions.
Each source document is compared against ``get_document``. Only the SDK's
``document_not_found`` error is absence; missing indexes, invalid credentials,
timeouts and all other service failures abort.

Source and index scans each have an explicit cap (default 10,000) with separate
truncation indicators. One lookahead item detects truncation. Index inspection
uses the SDK documents endpoint, filtered to exactly the selected context and
``library_block`` type. Numeric offsets advance by returned page lengths, with
requests bounded by batch size and remaining lookahead budget. This avoids
search-result limits and distinct grouping, but offset pagination is not a
snapshot. No all-library list or whole-library document buffer is built.

Dry-run is the default. The report contains counts, never content bodies or
permission identifiers. A library must already have SearchAccess metadata so
normal serialization's get-or-create path finds an existing row. This is not a
database read-only transaction; concurrently deleting that access row may still
cause the serializer to recreate it. Operators should keep metadata stable.

Repair policy
-------------

* An explicit ``--repair`` is required for search writes.
* Missing and stale canonical component records are eligible. Unknown/corrupt
  identities and primary-key collisions with another context/type/key are skipped.
* Source and index records are both re-read immediately before each bounded batch.
  If either differs from the observed candidate, repair skips it as concurrent.
* ``add_documents`` replaces the complete record, including removal of obsolete
  fields; partial ``update_documents`` would leave such drift behind.
* Each submitted task must succeed before its batch is counted as repaired.
  Failure aborts; earlier successful batches remain applied and a later run can
  inspect them again.
* An active library-index rebuild is refused before scanning and before/after
  each write. The course index and temporary indexes are never written.
* Index-only records are independently checked against source, reported, and
  never deleted. Source-enumeration truncation cannot create false orphans because
  indexed keys are checked directly against source existence.
* Corrupt or wrong-context indexed usage keys count as unknown. Only key parsing
  failures are absorbed; related content/engine failures propagate.

Concurrency and completeness limits
-----------------------------------

This is a cautious repair tool, not an atomic cross-system reconciler. Component,
association, tags, permissions and index records do not share a transaction or
compare-and-swap primitive. A publication after the final reread, a previously
queued newer indexing task, or an index swap between the rebuild check and write
can still race. Strict repair correctness requires pausing content mutations,
draining indexing tasks and avoiding rebuilds during the operation. A repair
run should be followed by a dry-run after writes have settled. Offset pagination
may miss/revisit documents while additions occur; it never authorizes deletion.

A truncated report is not a complete-library health assertion. Increase
``--max-documents`` above the selected library's size and rerun under quiescence.
Persistent cursor/keyset support is future work. Collection and container repair
are deferred, because their deletion/draft semantics differ from components.
Automatic orphan deletion, scheduler/Celery integration, distributed fencing,
revision-aware conditional writes and repair audit persistence are also deferred.

Validation and contribution sequencing
--------------------------------------

The dependency-free policy suite tests batching, truncation, dry-run, no-delete,
identity collisions, removed fields, failures and source/index mutation races.
The isolated API adapter suite executes the actual function body with mocked
platform services and the pinned Meilisearch 0.43.0 Document/exception classes.
It checks filter scoping, page offsets/limits, full replacement, permission fields,
missing-document versus missing-index errors, rebuild checks and access metadata.
These are not full Django/database or live-engine integration tests.

Before proposing an upstream PR, add Django library fixture tests and run the
existing search suite with a real Meilisearch. Reproduce a missed indexing event,
verify repaired content through Studio, and confirm publication quiescence
procedures with maintainers. Start with this bounded operator-only scope; propose
container/collection repair and revision-fenced background operation separately.
