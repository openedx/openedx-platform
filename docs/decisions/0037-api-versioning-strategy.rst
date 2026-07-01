API Versioning Strategy — Versioned Endpoints with CI-Enforced Compatibility
=============================================================================

:Status: Accepted
:Date: 2026-04-08
:Deciders: API Working Group

Context
=======

Open edX has multiple API versions in parallel (e.g., v0/v1/v2/v3) which creates confusion about
which version is stable or deprecated and increases the risk that external systems rely on outdated
contracts. The platform currently mixes versioned endpoints (``/api/enrollment/v1/``,
``/api/user/v2/``) with unversioned ones (``/api/course_experience/``), making it unclear which
endpoint clients should use and what the deprecation timeline looks like.

Decision
========

1. **All new APIs must be versioned.** Every new API endpoint must include an explicit version in
   its URL path (e.g., ``/api/foo/v1/``). Unversioned paths are not permitted for new APIs.

2. **The highest version number is the one clients should prefer.** When multiple versions of an
   endpoint exist simultaneously, clients must target the highest available version. Older versions
   are kept only during an active deprecation window.

3. **Automated CI tooling must detect backwards-incompatible OpenAPI changes.** Any PR that
   introduces a backwards-incompatible change to the OpenAPI spec (e.g., removed fields, changed
   types, removed endpoints) must bump the API version. CI checks enforce this automatically by
   diffing the OpenAPI schema against the base branch and failing the build if a breaking change is
   detected without a corresponding version increment.

4. **Follow the OEP-0021 deprecation process when removing old versions:**

   * File a DEPR issue in the ``openedx/public-engineering`` project to track the deprecation.
   * Mark old versions as deprecated in the OpenAPI schema (using the ``deprecated: true`` flag)
     and in the endpoint's docstring.
   * Provide a migration guide pointing clients to the new version.
   * Set and communicate a removal timeline aligned with the Open edX release cycle (minimum one
     named release, typically ~6 months).
   * Complete the deprecation by removing the old version's URL route and implementation code once
     the timeline has elapsed.

Relevance in edx-platform
=========================

* **Current mix**: LMS and CMS use both versioned and non-versioned API paths.
  Examples: ``api/enrollment/v1/``, ``api/val/v0/``, ``api/instructor/v1/`` and
  ``v2/``, ``api/user/v1/`` and ``api/user/v2/``, ``api/mfe_config/v1``,
  ``api/course_experience/`` (no version in path), ``api/xblock/v2/``,
  ``api/libraries/v2/`` (see ``lms/urls.py``,
  ``openedx/core/djangoapps/user_authn/urls_common.py``).
* **Confusion**: Multiple versions (v0, v1, v2, v3) without a single "default"
  make it unclear which endpoint clients should use. Unversioned paths provide no deprecation
  contract at all.
* **Existing unversioned endpoints**: These are out of scope for an immediate migration, but new
  work on those services should add versioned paths following this ADR.

Code example (routing pattern)
=============================

**Standard versioned endpoint:**

.. code-block:: python

   # urls.py
   urlpatterns = [
       path("api/courses/v1/", include("course_api.v1.urls")),  # current stable
   ]

**When introducing a breaking change:**

.. code-block:: text

   1. Increment the version: add /api/courses/v2/ with the new contract.
   2. Register v2 in OpenAPI. Mark v1 as deprecated (deprecated: true) with a removal date.
   3. File a DEPR issue to track the deprecation timeline (openedx/public-engineering).
   4. After the deprecation period has elapsed, remove the v1 URL route and implementation.

Consequences
============

* Pros

  * Clear, explicit versioning contract — clients always know what version they are targeting.
  * The highest version number is unambiguous: clients can always upgrade to the latest.
  * Automated CI enforcement prevents accidental breaking changes from reaching clients without
    a corresponding version bump.
  * Formal DEPR issue tracking provides accountability and visibility for all active deprecations.

* Cons / Costs

  * Existing unversioned endpoints (e.g., ``api/course_experience/``) require a future migration
    plan to add versioning.
  * CI tooling for OpenAPI diff checks requires initial setup investment.
  * Teams must increment version numbers and update URL routing when making breaking changes.

Rejected Alternatives
=====================

* **Non-versioned endpoints as the default "stable" surface**: An earlier draft proposed treating
  unversioned paths (e.g., ``/api/courses/``) as the default stable entry point, with versioned
  paths created only for breaking changes and the unversioned URL kept as a forwarding alias to
  the latest version. This was rejected because unversioned URL aliases pointing to the latest
  implementation create ambiguity for clients and tooling, do not provide a clear deprecation
  contract, and make it difficult to reason about backwards compatibility.

References
==========

* `OEP-0021: Deprecation and Removal <https://docs.openedx.org/projects/openedx-proposals/en/latest/processes/oep-0021-proc-deprecation.html>`_
* "Versioning confusion / deprecated versions" recommendation in the Open edX REST API
  standardization notes.
