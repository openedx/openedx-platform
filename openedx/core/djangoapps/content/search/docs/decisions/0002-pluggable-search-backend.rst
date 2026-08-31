Pluggable Search Backend for Studio Content Search
##################################################

Status
******

Draft

Supersedes `ADR 0001 <0001-meilisearch.rst>`_ in part: it keeps that ADR's
rejection of ``edx-search`` and of Elasticsearch/OpenSearch, and revisits only
its choice of Meilisearch as the single supported engine.


Context
*******

What ADR 0001 decided, and what it left open
============================================

`ADR 0001 <0001-meilisearch.rst>`_ chose Meilisearch for the then-new
``content/search`` app. It is worth being precise about what that ADR committed
to, because it anticipated this one:

* Its status is still **Draft**. It was never accepted.
* Decision 1 adopted Meilisearch "as an experiment and to evaluate it more
  thoroughly."
* Decision 3 committed to keeping "the Meilisearch-specific code isolated to the
  new ``content/search`` Django app, so it's relatively easy to swap out later
  if this experiment doesn't pan out."

This ADR is that swap being proposed, not a reversal of a settled decision. The
isolation ADR 0001 asked for was largely achieved. Counting engine references
across the app:

* ``api.py`` holds 105 of them and is where essentially all engine coupling
  lives.
* ``handlers.py`` has 21, but 16 are the ``meilisearch_enabled`` settings gate
  and the rest are prose — a rename and one indirection.
* ``documents.py`` has 17, all either prose or the ``meili_id_...`` and
  ``_meili_access_id_...`` helpers, whose behaviour is a key-slugging rule
  rather than an engine call.
* ``tasks.py`` has 16, of which 14 are ``MeilisearchError`` in retry handling.
  This is genuine coupling, and needs the backend to supply its own retryable
  exception type.
* ``views.py``, ``urls.py``, ``models.py`` and ``plain_text_math.py`` have
  effectively none.

What changed since
==================

The platform gained a second search engine. ``edx-search`` now ships a Typesense
backend (``search/typesense.py``, contributed as FC-0091), which serves Course
Discovery and Learning-MFE courseware search. It is a different subsystem with a
different index and no overlap with ``content/search`` — the two do not share
code and cannot share an index.

The consequence is that an operator who wants both courseware search on
Typesense and Studio content search must deploy and operate **two** search
engines for one platform. That is the problem this ADR addresses. It is a cost
borne by operators, and it grows as more of the platform moves onto Typesense.

High availability, and where its licence landed
===============================================

ADR 0001's first stated concern was that Meilisearch "doesn't (yet) support High
Availability via replication, although this is planned and under development."
That has been resolved, but not in a way that helps an Open edX operator.

Meilisearch now splits into two editions. The Community Edition is MIT-licensed
and free; the Enterprise Edition is licensed under BUSL-1.1 and shipped as
separate binaries and Docker images (``getmeili/meilisearch-enterprise``).
Replicated sharding — the feature that answers ADR 0001's concern — requires
Enterprise Edition v1.37 or later. A self-hosted Community Edition deployment
still has no replication, and therefore still has no high availability.

This matters here more than it would elsewhere, because it is the same problem
ADR 0001 was written to escape. That ADR's opening argument against
Elasticsearch is that "in 2021, the license of Elasticsearch changed from Apache
2.0 to a more restrictive license," and that this "is problematic for many Open
edX operators." Meilisearch's high-availability story has since arrived behind a
BUSL-1.1 licence. An operator who cannot or will not take a commercial licence
is left where they started: running the platform's Studio search on a
single-node engine with no replication path.

Typesense's clustering is not gated this way. It is GPL-3.0, and its
Raft-backed replication is part of the ordinary self-hosted product — the same
``typesense/typesense`` image, configured with a shared nodes file.

One further point from ADR 0001 should be set aside rather than reused: its
second concern, the absence of boolean operators in keyword search, is **not** a
Typesense advantage. Typesense supports boolean operators in filters, as
Meilisearch already does, and no difference in the keyword query itself has been
established.

Meilisearch remains a good engine, and for a single-node deployment that never
needs replication it remains a reasonable choice. The argument here is about how
many engines the platform obliges an operator to run, and about whether the
answer to the platform's high-availability question is allowed to be a
commercial licence.

The abstraction risk, taken seriously
=====================================

ADR 0001's own context is the strongest objection to this proposal. It records
that ``edx-search`` formerly used django-haystack as a cross-engine abstraction,
and that this "was ripped out after the package was abandoned upstream and it
became an obstacle to upgrades and efficiently utilizing Elasticsearch (the
abstraction layer imposed significant limits)."

That history is a warning about a *general* search abstraction attempting to
span engines with different models. The proposal below is deliberately not that.
The interface is narrow, internal, and shaped by the operations
``content/search`` actually performs — it is not a general-purpose search API,
carries no ambition to serve other apps, and can be changed freely because
nothing outside this app depends on it.

Feasibility
===========

A capability-by-capability review found no parity blocker, and each mapping was
executed against a running Typesense 30.2 rather than inferred from
documentation:

* ``distinctAttribute`` maps to ``group_by``; the ordered
  ``searchableAttributes`` list to ``query_by`` plus explicit
  ``query_by_weights``; ``sortableAttributes`` to per-field ``sort``; and the
  ``rankingRules`` entry that puts ``"sort"`` ahead of relevance needs no
  counterpart, because an explicit ``sort_by`` already outranks text matching.
* The ``create``/``swap_indexes``/``delete`` rebuild becomes a collection alias
  repoint, which is simpler and reclaims disk immediately.
* ``delete_documents(filter=...)`` maps to a ``filter_by`` delete.
* The tenant-token property ADR 0001 specifically credited Meilisearch for —
  minting a restricted, permission-scoped key locally so the browser can query
  the engine directly without routing through Django — holds for Typesense.
  Scoped search keys are an HMAC of a parent key over a rule carrying
  ``filter_by`` and ``expires_at``, and require no API call to create. The
  browser-direct architecture is preserved.
* ``_wait_for_meili_task`` and the task-polling layer around every write
  disappear, because Typesense writes are synchronous.

An explicit Typesense schema covering library blocks, collections and containers
was written and validated against a live server. Three points found there are
recorded because they will otherwise be rediscovered painfully:

* A ``content\..*`` string wildcard field, the pattern ``edx-search`` uses,
  rejects the array sub-fields that container documents carry. Explicit
  ``string[]`` overrides are required ahead of the wildcard.
* ``max_facet_values`` defaults to 10 and also caps the reported total, so a
  truncated facet list is not detectable from the response. Every faceted
  request must set it explicitly. Meilisearch's equivalent defaults to 100.
* ``documents.py`` serialises datetimes with ``.timestamp()``, which yields a
  float, so those fields must be declared ``float`` rather than the ``int64``
  ``edx-search`` uses for its own datetimes.


Decision
********

1. We will introduce a **narrow backend interface** within ``content/search``,
   covering only the operations the app performs today: index lifecycle
   (create, alias/swap, delete), document upsert and delete-by-filter, search
   with filters, facets and sorting, faceted-value lookup for the tag tree, and
   minting a permission-scoped client credential.
2. We will implement that interface for **both Meilisearch and Typesense**, and
   select between them by setting. The Meilisearch implementation is the
   existing code behind the new interface, and remains supported.
3. Meilisearch stays the **default**, so no existing deployment changes
   behaviour by upgrading. Choosing Typesense is opt-in.
4. The interface is **internal to** ``content/search``. It is not a general
   search abstraction, is not offered to other apps, and explicitly does not
   attempt to replace or extend ``edx-search``.
5. We will keep the Authoring MFE's ``search-manager`` on one engine-agnostic
   client seam rather than branching per engine throughout the component tree.
6. We will not attempt to unify the ``content/search`` index with the
   ``edx-search`` courseware index. They serve different subsystems, carry
   different fields, and index different content (published versus draft).


Consequences
************

1. An operator who already runs Typesense for courseware search or Course
   Discovery can run Studio content search on it too, and drop Meilisearch. An
   operator who prefers Meilisearch keeps it, unchanged and by default.
2. ``content/search`` acquires an internal indirection it does not have today.
   This is a real cost. It is bounded by keeping the interface small and
   private to the app, and by the fact that both implementations live in-tree,
   so a change to the interface can be made across both at once — the failure
   mode that made django-haystack painful, an abandoned external abstraction,
   does not apply.
3. The task-polling layer becomes conditional rather than unconditional, since
   Meilisearch still needs it. It is confined to the Meilisearch
   implementation.
4. The Authoring MFE's ``search-manager`` is written against the raw
   ``meilisearch`` JavaScript client rather than an InstantSearch adapter, so a
   meaningful share of it must be reworked to sit behind a client seam. This is
   the largest single piece of work, and larger than the backend change.
5. Result grouping changes shape: Typesense returns ``grouped_hits`` where
   Meilisearch's ``distinctAttribute`` returns flat ``hits``. The seam has to
   normalise this.
6. Highlighting and cropping do not map exactly. Typesense decides per field
   whether to snippet and how much context to keep, where Meilisearch crops to a
   token count per attribute. Result cards need a visual pass, not a rename.


Alternatives Considered
***********************

Leave ``content/search`` on Meilisearch only
============================================

The status quo. It is a defensible choice on its merits — Meilisearch works, and
its earlier HA gap has closed. It is rejected only because it obliges operators
who have already adopted Typesense elsewhere in the platform to run a second
engine indefinitely, with no path off it.

Replace Meilisearch with Typesense outright
===========================================

Simpler than a pluggable backend: no interface, no indirection, one code path.
Rejected because it forces the change on every operator currently running
Meilisearch for Studio search, to solve a problem only some of them have. The
indirection in Decision 1 is the price of not doing that.

Route Studio search through the existing ``edx-search`` Typesense backend
=========================================================================

Investigated and rejected on the evidence. The ``courseware_content`` documents
that backend produces lack the fields Studio search needs — breadcrumbs,
block type, the tag hierarchy, publish status, sortable display name, usage and
context keys — and index published content only, while Studio authors drafts.
``edx-search``'s own documentation states it supports Course Discovery and
Learning-MFE courseware search, and that other use cases are not supported.

Extend ``edx-search`` to cover Studio search
============================================

Rejected for the same reasons ADR 0001 rejected it, which have not changed: the
``edx-search`` API is a mix of abstractions and direct engine usage, and
``content/search`` was deliberately built outside it.
