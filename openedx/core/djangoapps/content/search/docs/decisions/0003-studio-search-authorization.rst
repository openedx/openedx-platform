Studio Search Authorization
###########################

Status
******

Draft


Context
*******

To search, the authoring MFE fetches a per-user Meilisearch `tenant token`_ from the CMS — a signed JWT carrying an enforced search filter — and queries Meilisearch directly with it, so the CMS backend never enters the query path. The MFE caches that token, re-minting only on a full page reload, on library creation, or when the cache goes stale.

Today that token carries **one** filter -- ``org IN [...] OR access_id IN [...]``, built from the user's course/org access -- stamped onto **both** the course index (``studio_content``) and the library index (``studio_library_content``). This does not express domain granularity, so a user holding ``view_course`` on an org but not ``view_library`` on that org gets the same filter on both indexes.

A tenant token's ``searchRules`` (`reference
<https://www.meilisearch.com/docs/learn/security/tenant_token_reference#search-rules>`_)
are keyed **only by index name** (literal, partial-wildcard, or ``*``, with specific keys overriding ``*``); each key carries one enforced filter. The enforced filter is selected purely by the index name in the request URL -- there is no routing dimension for "which UI surface asked."

What spurs this change is the migration to `openedx-authz`_, the platform's granular authorization library. It defines per-domain permissions as first-class actions (e.g. ``courses.view_course`` and ``content_libraries.view_library``) and exposes functions to resolve the platform/org/course/library scopes on which a given user holds a given permission.

.. _tenant token: https://www.meilisearch.com/docs/learn/security/tenant_token_reference
.. _openedx-authz: https://github.com/openedx/openedx-authz


Decision
********

Adopt **resource-level (index-aligned) authorization**: keep the one-token, browser-to-Meilisearch-direct model, but split the single shared filter into two index-aligned filters, each built from the scopes the user holds for that index's permission.

* ``studio_content`` -> gated on ``courses.view_course``
* ``studio_library_content`` -> gated on ``content_libraries.view_library``

For each index, the mint endpoint enumerates the user's platform, org, and course/library scopes where they hold that index's permission, renders them into the ``org IN [...] OR access_id IN [...]`` filter, and writes it under that index's own ``searchRules`` key -- so the two keys carry different filters instead of today's single shared one. Meilisearch applies each index's filter when the browser queries it, exactly as today; only the filter computation changes. This is a **pure mint-side change** -- no proxy, no new mechanism, no JWT-shape change.

Reuse ``view_course`` / ``view_library`` rather than minting new search-specific permissions: search visibility *is* view access. Introduce a search-specific permission only if a future need ever arises to hide something from search while keeping it directly viewable.

**Why this is sufficient for every Studio search surface today:** each query targets exactly one index -- there is no server-side federated/multi-index search (``StudioSearchView`` only mints the token; the browser queries one named index at a time) -- so each query is gated by exactly that index's filter.

Consequences
************

Positive
========

* Gets Studio onto real per-domain authorization with only a mint-side change.
* Stays browser-to-Meilisearch-direct: the CMS never enters the query path, honoring the "Django stays out of the query path" constraint from `openedx-search ADR 0001`_ (Decision 7).
* Uses the single token already minted today; ``searchRules`` already support a distinct filter per index key.
* Semantically clean: one permission per index, reusing existing view permissions.

Negative / limits
=================

* **Per-index, not per-surface.** This cannot express the same document resolving differently per surface. That is not a gap for Studio today (no surface needs it), but it is the boundary: a future *purpose-based* requirement would need a different mechanism for that surface.
* **~1-hour effective revocation lag.** The token's JWT expiry is 7 days, but it is ~1 hour or less in practice. The backend re-resolves scopes and mints a fresh token on **every** fetch (no backend scope cache), and the authoring MFE caches the minted token in memory for at most ~1 hour. A role change is therefore picked up on **(a)** a full page reload, **(b)** creating a new library (which explicitly invalidates the cached token), or **(c)** ~1 hour elapsing. Shortenable further by lowering the cache TTL, independent of the filter model.


Rejected / Deferred Alternatives
********************************

All of the following solve *per-surface* gating, which Studio does not require today. They are recorded so the cost of each is understood *if* a future purpose-based requirement ever lands. Ordered by how cleanly they solve per-surface gating.

Keep the current single shared filter (status quo)
==================================================

Rejected: expresses no per-domain granularity -- the whole motivation. The split in the Decision is the minimal fix.

Backend proxy (per-request resolution)
======================================

Route every search through a CMS view that resolves scopes per request from openedx-authz, renders the filter, and relays to Meilisearch with the backend's own key; back it with a short-TTL per-user scope cache invalidated on role change (Celery), so freshness is event-driven with TTL as a safety net. The **only** model that cleanly expresses per-surface gating *and* gives sub-second revocation.

This option would violate `openedx-search ADR 0001`_ Decision 7 -- it puts Django back on the hot path (the ADR cites past edx.org performance problems), reintroduces the per-query round-trip the token avoids, and adds an authz/search cache-invalidation wire plus a privileged server-side Meilisearch key.

Per-permission tokens
=====================

Mint a map of tokens keyed by purpose, each a frozen filter for one permission; each surface uses the token matching its permission. Keeps browser-to-Meilisearch-direct and ADR-compliant.

This would lead to linear fan-out (one token becomes N, mint cost per surface load), the 8 KiB budget multiplied by N, the same ~1-hour lag across all N, and the surface-to-permission taxonomy leaks to the client. A lazy, short-TTL-per-surface variant takes the sharpest edges off -- minting on demand with minutes-scale TTL -- and is the least-bad token-direct path *if* per-surface gating is ever needed and `openedx-search ADR 0001`_ Decision 7 is treated as binding.

RBAC-with-joins (Meilisearch foreign filters)
=============================================

Model grants as rows in a second Meilisearch access index joined to content at query time via a `foreign filter <https://www.meilisearch.com/docs/capabilities/filtering_sorting_faceting/advanced/filtering_by_joined_data>`_; the token carries only identity. Maps well onto the openedx-authz Casbin tuple model and genuinely improves per-domain granularity, freshness (revoke = row delete), and the JWT budget (the resolved access-id list leaves the token).

It does *not* solve per-surface by itself (the ``_foreign`` predicate is either frozen in the token or built per request by a trusted server -- i.e. the proxy in disguise); it adds a heavy sync burden (materialize and keep the grant graph synced as a second source of truth); a very large grant graph makes the per-query join more expensive and can bump Meilisearch's result/processing ceilings at scale; and ``_foreign()`` is a **Meilisearch-specific** capability, so building the authorization model on it deepens engine coupling and works against future search-backend migration or abstraction contrary to openedx-search's stated cross-engine direction.


References
**********

* Meilisearch -- `tenant token`_ payload reference (``searchRules``: index-keyed, one filter each; wildcard + specific-override precedence). Underlying spec: `Tenant tokens (specification #0089)`_.
* Meilisearch -- `Role-based access control with joins`_ and `Filtering by joined data (_foreign)`_ (foreign-filter access table).
* edx-platform -- content/search `0001-meilisearch.rst`_ and `0002-separate-course-and-library-indexes.rst`_; the ``SearchAccess`` model and ``api.py`` filter/token-mint path.
* openedx-search -- `openedx-search ADR 0001`_ (`PR #1`_): Decision 3 (Typesense + Meilisearch both first-class), Decision 7 (Django stays out of the query path).
* `openedx-authz`_ -- the granular authorization library supplying the ``courses.view_course`` / ``content_libraries.view_library`` permissions and the ``get_scopes_for_user_and_permission`` scope resolver this ADR consumes.

.. _openedx-search ADR 0001: https://github.com/openedx/openedx-search/blob/main/docs/decisions/0001-purpose-of-this-repo.rst
.. _PR #1: https://github.com/openedx/openedx-search/pull/1
.. _Tenant tokens (specification #0089): https://github.com/meilisearch/specifications/blob/main/text/0089-tenant-tokens.md
.. _Role-based access control with joins: https://www.meilisearch.com/docs/capabilities/security/advanced/rbac_with_joins
.. _Filtering by joined data (_foreign): https://www.meilisearch.com/docs/capabilities/filtering_sorting_faceting/advanced/filtering_by_joined_data
.. _0001-meilisearch.rst: ./0001-meilisearch.rst
.. _0002-separate-course-and-library-indexes.rst: ./0002-separate-course-and-library-indexes.rst
