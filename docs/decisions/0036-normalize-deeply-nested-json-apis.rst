
Reduce Deeply Nested JSON via Minimal/Flattened Views
=====================================================

:Status: Accepted
:Date: 2026-04-08
:Deciders: API Working Group

Context
=======

Some APIs return deeply nested JSON payloads (course structures, block trees, progress views).
This makes payloads hard to parse, increases response size, and slows clients and automated agents.

Decision
========

1. Provide a "minimal" representation option for complex resources:

   * Query param example: ``?view=minimal`` or ``?fields=...``.

2. Normalize/flatten overly nested structures where possible:

   * Prefer references/IDs with follow-up endpoints over embedding entire trees by
     default. This is the recommended pattern for server-to-server integrations and
     automated clients (e.g., AI agents), where minimizing coupling to a specific
     nested shape matters more than minimizing request count.

     **Exception — frontend / MFE clients:** when a client needs to render a complete
     view in a single page load and the nested data is *always* needed together, an
     embedded full representation is acceptable to avoid costly sequential requests.
     In these cases, use an explicit opt-in such as ``?view=full`` or
     ``?fields=<explicit-list>`` so the heavy payload is never the default and clients
     can still request only what they need.

3. Document response shapes in OpenAPI, including minimal vs full variants.

.. note::
   Endpoints that expose a flat list of nodes (e.g. a search result set or an enrollment
   list) are also subject to :doc:`0032-standardize-pagination-usage`.  For *tree-shaped*
   responses, see the `Interaction with Pagination (ADR 0032)`_ section below.

Relevance in edx-platform
=========================

* **Deeply nested payloads**: The course blocks API (``lms/djangoapps/course_api/blocks/views.py``)
  returns a tree of blocks with ``root`` and ``blocks`` (dict keyed by usage ID), each block
  containing ``id``, ``type``, ``display_name``, ``children``, ``student_view_data``, etc.
  Full trees can be large and hard to parse.
* **Existing flexibility**: Blocks API already supports ``requested_fields`` and
  ``block_types_filter`` to reduce payload; a ``view=minimal`` or ``fields=...``
  convention would align with this ADR.
* **Modulestore/OLX**: ``openedx/core/djangoapps/olx_rest_api/views.py`` returns
  nested block OLX; providing a minimal (e.g. IDs + types only) view would help
  clients that only need structure.

Interaction with Pagination (ADR 0032)
======================================

Tree-shaped endpoints are **out of scope** for ADR 0032's flat-list ``DefaultPagination``
requirement, because "page N of a tree" is not well-defined when the tree structure itself
provides the navigation context.  However, unbounded child lists within a tree still pose a
performance risk.  When a node may have a large number of direct children (e.g. a course
chapter with hundreds of units, or a library with thousands of components), one of the
following two patterns MUST be used:

**Pattern A — Depth cap + child-fetch URLs (recommended for tree fidelity)**

Return the full structural representation up to a fixed maximum depth (default ``depth=2``).
For subtrees beyond that depth, return the child usage ID and a follow-up URL instead of
embedding the full subtree:

.. code-block:: json

   {
     "id": "block-v1:edX+Demo+2026+type@chapter+block@week1",
     "type": "chapter",
     "display_name": "Week 1",
     "children": [
       {
         "id": "block-v1:...",
         "type": "sequential",
         "display_name": "Lesson 1",
         "children_url": "/api/courses/v1/blocks/block-v1:.../?depth=1"
       }
     ]
   }

This preserves tree semantics while bounding response size.  The depth default and maximum
MUST be documented and honoured via a ``?depth=N`` query parameter.

**Pattern B — Flat paginated child list (recommended when order/count matters more than
structure)**

When clients primarily need to enumerate children (e.g. a library component picker or a
block-type filter), expose a flat paginated sub-resource following ADR 0032:

.. code-block:: text

   GET /api/courses/v1/blocks/<parent_id>/children/?page=1&page_size=50

This separates structural navigation (tree) from bulk enumeration (paginated flat list).

**Guidance for** ``?fields=...`` **with large child sets**

If a client requests a field that would expand a large child collection (e.g.
``?fields=children.student_view_data``), the server MUST NOT silently return all children
unbounded.  Apply whichever pattern above is appropriate for the endpoint and document the
behaviour in OpenAPI.  Returning HTTP 400 with a descriptive message is preferable to
silently truncating or returning an oversized payload.

The Course Blocks API (``/api/courses/v1/blocks/``) is the canonical reference case: it
already supports ``?depth=N`` and ``requested_fields``.  Any extension of that API MUST
continue to honour depth caps and MUST NOT add unbounded field expansions.

Code example
============

**Query params for minimal representation:**

New endpoints should use ``fields`` (explicit field selection) and/or ``view``
(named preset) consistently. The existing Blocks API uses ``requested_fields``
for backwards compatibility; new work should follow the ``fields`` convention.

.. code-block:: text

   GET /api/courses/v1/blocks/<usage_id>/?depth=1&fields=id,type,display_name
   GET /api/course_structure/v1/?view=minimal

**Response shape (minimal vs full):**

.. code-block:: json

   // minimal (?view=minimal or ?fields=id,type,display_name,children)
   {
     "root": "block-v1:...",
     "blocks": {
       "block-v1:...": { "id": "...", "type": "chapter", "display_name": "Week 1", "children": ["..."] }
     }
   }

   // full: same structure but with student_view_data, completion, block_counts, etc.

**Prefer IDs + follow-up over embedding:**

.. code-block:: text

   GET /api/courses/v1/blocks/  → returns block IDs and types
   GET /api/courses/v1/blocks/<id>/  → returns full block when needed

Consequences
============

* Pros

  * Improved performance and developer ergonomics.
  * Easier integration for external services and AI agents.

* Cons / Costs

  * Must maintain multiple representations: each ``view`` preset or ``fields``
    variant requires a separate serializer code path, its own test coverage, and a
    documented schema entry in OpenAPI. All variants must be kept in sync whenever
    the underlying data model changes (new fields, renamed keys, deprecated sub-objects).
    Without versioning discipline this becomes a source of subtle divergence bugs
    and undocumented breaking changes.

Implementation Notes
====================

* Start with endpoints called out in the standardization notes (course structure, contentstore index,
  xblock, progress).
* Measure payload size reduction and client performance improvements.

References
==========

* :doc:`0032-standardize-pagination-usage` — Pagination standardization ADR. Tree-shaped
  endpoints are explicitly out of scope for ADR 0032's flat-list pagination requirement;
  this ADR (0036) governs how deep-structure responses should bound child list sizes at
  those same endpoints.
* `Open edX REST API Conventions — “Multiple Formats” <https://openedx.atlassian.net/wiki/spaces/AC/pages/18350757/Open+edX+REST+API+Conventions#10.-Multiple-Formats>`_
