# Exact scoped search and complete bounded tenant tokens

## Problem and implementation boundary

The baseline silently slices organizations and access IDs to 1,000. Library permissions are first materialized as a Python list; valid grants beyond the cap disappear from search and aggregates. Removing the cap would create unbounded JWTs and permission enumeration. Organization interpolation using Python repr does not establish a consistently escaped filter literal contract.

This change gives large-account authors a backend path to any single authorized Libraries V2 context. It deliberately exposes overflow on all-context requests. It does not implement globally ranked, paginated all-library search and does not change the search engine.

## API contract

An authenticated request may supply exactly one `library_key`. A malformed or course key returns 400. A missing or unauthorized library returns 403 before token signing. Repeated `library_key` parameters return 400. A scope-required response is HTTP 409 with JSON fields `code: search_scope_required`, `required_scope: library_key`, and a detail message.

Authorization uses `get_libraries_for_user(user).filter(org__short_name=key.org, slug=key.slug).exists()`. This reuses the deployed Bridgekeeper/openedx-authz permission-filtered queryset, including whatever public-read/global-role semantics that queryset already implements. No client-supplied organization or numeric grant is trusted. The scoped path does not enumerate library access IDs or course roles.

The tenant token has a single explicit library-index search rule: `context_key = <JSON-escaped exact context key>`. It excludes the course index even for global staff. It does not broaden one authorized library to all libraries in the same organization. Client query filters cannot override the signed tenant rule. Returned legacy index-name metadata is preserved; new clients must use `authorized_indexes` and `library_index_name` for scoped requests.

Unscoped requests retain both indexes. Global staff retain existing unrestricted rules. Other users receive the complete union of organization staff/instructor roles and individual context grants, or a scope-required response. Count and byte limits are explicit. Reading at most 1,001 libraries/IDs detects overflow. Library overflow is conservative: even if some authorized libraries lack SearchAccess rows, the endpoint may request explicit scope instead of materializing all libraries.

## Size and lifetime limits

A filter must fit in 4,096 UTF-8 bytes. Organization/access-ID collections must each be at most 1,000 entries. The final signed JWT must fit in 8,192 bytes; duplicating one rule across two indexes can still cause final-JWT overflow, which returns 409 without returning the JWT. A scope with excessive encoded length is rejected too.

Scoped tokens last 300 seconds. Clients use `expires_at` for proactive refresh and retry authentication failure by requesting a fresh scoped token. Unscoped tokens preserve the seven-day baseline lifetime until frontend refresh support can be rolled out. This compatibility decision preserves a known long revocation window and must remain explicit.

Every issuance rechecks current permission. A token minted just before revocation may continue to return stale authorized hits for its remaining lifetime. There is also a permission-check/signing race bounded by the token lifetime. Neither SQL authorization nor short TTL establishes immediate revocation. Account disablement or grant revocation does not revoke an already-signed tenant token in this implementation. Immediate enforcement requires server-mediated requests or signing-key rotation with appropriate deployment impact.

## Result and aggregate correctness

The signed exact-context filter constrains hits, estimated hit counts, facet distributions, and facet search. Therefore the client must label scoped results and counts as belonging to that library. Meilisearch approximate counts and eventual index consistency remain baseline limitations. No token, pagination, facet, or count is deliberately limited to an arbitrary subset of the user’s valid grants; oversized all-context scope returns an error.

Do not compute all-library totals by summing several scoped facet responses without a separate correctness design. Global ranking, duplicate handling, pagination, engine hit-count caps, simultaneous updates and revocations, and consistent snapshots require server-side coordination. Recommended follow-up is an authorization-aware server search/projection service with bounded permission-set materialization and a separately specified aggregate contract, validated with library-search correctness benchmarks.

## Verification scope

Standalone tests import the production `access_rules` module directly and execute production `models` functions under Django 5.2 against SQLite. A minimal application configuration avoids CMS signal/service bootstrap. Two boundary doubles stand in for the platform course-role provider and permission-filtered library-queryset provider. Tests populate 1,501 library grants, verify a single SQL `LIMIT 1` scoped check for the last grant, reject an unauthorized same-organization library, recheck after deletion, and ensure `LIMIT 1001` on unscoped overflow. These establish implementation/SQL behavior, not the correctness of the deployed authz provider.

Upstream CMS APIClient tests remain the authority for authentication, full permission semantics, token issuance and HTTP handling. Added tests cover forbidden and malformed scopes, repeated parameters, index restriction, final token size, count overflow and expiry compatibility. These view/model tests passed in the complete CMS environment. Additional integration tests populate 1,501 real legacy library grants and invoke the actual platform authorization queryset, including scoped issuance, unscoped overflow and grant deletion. An opt-in Meilisearch 1.36.0 test verifies signed-token restrictions on hits and facets, rejection of a conflicting client filter, five-minute lifetime and engine rejection of an expired signed token. The live test creates and removes a disposable index and signing key. Broader openedx-authz role matrices and production-database query plans remain review/activation work.

## Contribution and rollout sequence

1. Maintainer discussion: agree whether HTTP 409 should ship immediately or behind a coordinated rollout flag; confirm filter/JWT budgets and endpoint query shape.
2. Backend PR: rule escaping/bounds, explicit overflow, bounded enumeration and exact scoped token path with API/model tests. No schema migration.
3. Frontend PR: library selection, explicit scope/count labels, 409 handling, five-minute token refresh and expired-token retry.
4. Service CI: real Meilisearch tests for adversarial org strings, exact context hits/facets, no-access union, final token budgets and rejected cross-index searches; full CMS/authz permission matrices and query plans on the production database.
5. Deploy backend and frontend together for oversized accounts; monitor 409 rate, scoped issuance/denials, token byte sizes, authz query latency and expiry refresh failures. Avoid logging JWTs or private context/grant sets.
6. Separately design all-library aggregation and revocation-enforced search; benchmark it against exact-library SQL/token issuance and authorization correctness.

Rollback removes the scoped query behavior and restores existing issuance. Existing signed scoped tokens remain valid until expiry unless the signing key is rotated. No database migration or destructive repair is introduced.
