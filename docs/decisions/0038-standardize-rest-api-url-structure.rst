Standardize REST API URL Structure
==================================

:Status: Accepted
:Date: 2026-08-12
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - URL structure and naming standardization for consistency

Context
=======

Open edX REST URLs follow no consistent pattern. One platform serves
``/api/user/v1/accounts``, ``/api/v1/course_runs/``, ``/api/mobile/v0.5/``,
``/api/course_home/outline/{course_key}``, ``/api/agreements/v1/agreement/``,
``/transcripts/upload``, and
``/courses/{course_key}/instructor/api/certificate_task/{action}``. The prefix,
the position of the version, pluralisation, word separators, and trailing slashes
all vary. They vary even between adjacent lines of the same URLconf:
``lms/urls.py`` declares ``path('api/mfe_config/v1', ...)`` immediately followed
by ``path('api/frontend_site_config/v1/', ...)``.

The cost is that clients must hardcode per-endpoint knowledge, tooling and
automated agents cannot infer the address of a resource they have not already
seen, and duplicate endpoints are hard to notice. Two examples show that the
inconsistency is not merely cosmetic.

**Pluralisation is load-bearing today.** ``GET /api/enrollment/v1/enrollment``
returns the caller's own enrollments and ``GET /api/enrollment/v1/enrollments/``
is a privileged admin list. They are different views in one namespace
(``openedx/core/djangoapps/enrollments/urls.py``), and v2 preserves both, so a
client that guesses wrong about the plural gets a different contract rather than
a 404.

**The LMS and CMS already collide.** ``api/courses/`` is mounted in both services
on unrelated implementations — ``lms.djangoapps.course_api.urls`` and
``cms.djangoapps.contentstore.api.urls`` — and both use ``v1``. The
`combined headless LMS+CMS`_ proposal migrates CMS endpoints into the LMS URLconf
one at a time, which is not possible where the target prefix is already occupied
by a different API at the same version. Left alone, ``/api/courses/v1/`` would
become the union of two independently owned contracts, in which a breaking change
to either forces a version bump shared with the other.

The `Open edX REST API Conventions`_ page, which `OEP-49`_ normatively defers to
for "URL structure, namespacing, and versioning", already specifies the
``/api/{API_NAME}/{VERSION}/...`` shape, plural resource nouns, ``snake_case``,
verbs out of base URLs, and major-only versions. It is not followed because it
lives on an unversioned wiki, is partly superseded by this ADR series, has no CI
enforcement, and leaves the cross-service question open ("By 'collisions', we
mean TBD"). This ADR restates the rules that still apply, settles what was left
open, and makes them enforceable.

Decision
========

Every REST API URL in the platform must match:

.. code-block:: text

   /api/{api_name}/v{N}/{collection}[/{identifier}[/{sub_collection}[/{sub_id}]]]/

   /api/enrollment/v2/enrollments/
   /api/authoring/v1/courses/{course_key}/team_members/{username}/

1. **Every programmatic endpoint lives under a leading** ``/api/``. The prefix
   must not appear mid-path, as it does in
   ``/courses/{course_key}/instructor/api/``. HTML views, XBlock runtime handlers,
   authn paths whose form is fixed by an external specification, and operational
   endpoints (``/heartbeat``, ``/status/``) are out of scope.
2. **The API name is singular and collections are plural** —
   ``/api/user/v1/accounts/``, ``/api/enrollment/v1/enrollments/``. The name
   identifies a subject area, of which there is one; a collection names a set of
   addressable things. Each resource gets two base URLs and only two:
   ``{collection}/`` for the set and ``{collection}/{identifier}/`` for a member.
   This also rules out stutter, since ``/api/course/v1/courses/`` reads correctly
   where ``/api/courses/v1/courses/`` does not.
3. **The API name describes the domain, not the code.** It must not be the
   implementing Django app's name, an artefact of package layout, or an MFE screen.
   Each concept gets one name platform-wide, and a generic name such as ``tasks``
   is not claimed by a single service.
4. **Resource names are concrete, lowercase** ``snake_case``. Not abstract
   (``items``, ``data``), not ``kebab-case``, and not the name of a view or a
   screen.
5. **A mount declares its own prefix.** An API URLconf must be included under its
   full ``api/{api_name}/v{N}/`` prefix, never under ``path('', include(...))`` as
   the XBlock v2 and content-search APIs are today. The platform's URL surface
   should be readable from the two project URLconfs plus each plugin app's
   ``PluginURLs.CONFIG``, not discoverable only by opening every app.
6. **One route resolves one address.** The trailing slash is required, neither
   optional (``re_path(r'^enrollments/?$')``) nor absent
   (``path('api/mfe_config/v1', ...)``); DRF routers do this by default. Every
   ``re_path`` is anchored with ``^`` and ``$``, because Django resolves it with
   ``re.search``: ``re_path(r'v1/reset_course_deadlines', ...)`` also matches under
   any prefix, and ``re_path(r"^init/?", ...)`` in ``learner_home`` matches any
   path that merely begins with ``init``.
7. **The version follows the API name and is major-only.** Never ``v0.5``, never
   leading as in ``/api/v1/course_runs/``, never absent, and never mixed with
   unversioned siblings in one namespace. Version semantics, compatibility, and
   deprecation remain :doc:`0037-api-versioning-strategy`'s scope; this ADR fixes
   only the version's position and form.
8. **Hierarchy is logical and capped at one level of nesting.** Nest a resource
   under a parent only when it cannot be addressed without that parent. Anything
   addressable by its own opaque key is a top-level collection, with the parent —
   and all filtering, sorting, pagination, and shaping — expressed in the query
   string: ``/api/content/v2/units/?course_key={course_key}``, per
   :doc:`0033-standardize-filter-sort-using-django-filter`,
   :doc:`0032-standardize-pagination-usage`, and
   :doc:`0036-normalize-deeply-nested-json-apis`. A parent key must never occupy
   the member-identifier slot, as it does in
   ``/api/instructor/v1/tasks/{course_key}``.
9. **Identifiers are opaque keys, resolved by shared path converters.**
   Usernames, course run keys, usage keys, and UUIDs — never database primary keys
   on an externally reachable API. A resource identified by a relationship uses a
   comma-delimited composite key, the form the enrollment API already uses for
   ``enrollments/{username},{course_key}``. Course keys are the non-deprecated
   forms — ``course-v1:`` and ``ccx-v1:`` — and the converter rejects deprecated
   ``Org/Course/Run`` keys. The requesting user is addressed as ``me``, rather than by an endpoint
   whose contract changes with the caller.
10. **Resource paths contain no verbs.** An operation is a ``POST`` to a noun:
    ``POST /api/instructor/v1/courses/{course_key}/certificate_tasks/`` rather
    than ``enable_certificate_generation`` (see
    :doc:`0031-merge-similar-endpoints`). A genuine non-resource operation may use
    a verb, but must be marked as such in its OpenAPI description.
11. **Django URL names follow the same conventions.** A name is ``snake_case``,
    descriptive, and unique within its namespace. The name is part of the URL
    contract, because ``reverse()`` depends on it, and it is as inconsistent as
    the paths: ``course-list`` and ``course-detail`` sit beside
    ``blocks_in_course``, ``courseenrollmentsapilist``, and ``blocked_message``.
    The version belongs in the path, not the name, which rules out
    ``enrollment-v2-retrieve`` and ``v1_course_access``. Uniqueness
    matters because reuse is fragile rather than broken: the enrollment API gives
    two ``re_path`` entries the name ``courseenrollment``, which resolves today
    only because Django disambiguates them by argument signature, and stops doing
    so the moment those signatures converge.
12. **The LMS and CMS share one URL namespace.** Either service may serve a path,
    or both may, but if both do it must resolve to the same contract — as
    ``/api/user/`` and ``/api/learning_sequences/`` already do. ``{api_name}``
    values are reserved platform-wide, including names claimed by plugin apps and
    by third-party URLconfs the platform mounts. Service-specific behaviour is
    distinguished in the name (``/api/authoring/v1/`` for Studio,
    ``/api/course/v1/`` for the LMS) rather than by overloading one name, and each
    endpoint has exactly one canonical address, which collapses dual mounts such
    as ``api/course_home/`` plus ``api/course_home/v1/``.

Existing endpoints are migrated under OEP-21 and never broken. Mount the
conforming path as an additional route to the same view, mark the legacy path
``deprecated: true`` in the OpenAPI schema, file a DEPR issue, keep both for at
least one named release, then remove the legacy route. A rename on its own is not
a contract change and requires no version bump.

Relevance in edx-platform
=========================

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Today
     - Target
   * - ``/api/courses/v1/courses/`` (LMS) and
       ``/api/courses/v1/validation/{course_id}/`` (Studio) — the collision
     - ``/api/course/v1/courses/`` and
       ``/api/authoring/v1/courses/{course_key}/validation/``
   * - ``/api/contentstore/v1/course_details/{course_id}``, plus
       ``course_settings``, ``course_index``, ``home``, and ``help_urls`` — screen
       names as resources, in a namespace named after the Django app
     - ``/api/authoring/v1/courses/{course_key}/details/`` and siblings
   * - ``/api/enrollment/v1/enrollment`` **and**
       ``/api/enrollment/v1/enrollments/``, plus
       ``/api/enrollment/v2/enrollment/unenroll/``
     - one ``/api/enrollment/v2/enrollments/``, with ``DELETE`` on
       ``enrollments/{username},{course_key}/`` for unenrollment
   * - ``/api/agreements/v1/agreement/`` — a plural namespace wrapping a singular
       collection (``router.register(r"agreement", ...)``)
     - ``/api/agreement/v1/agreements/``
   * - ``/api/instructor/v1/tasks/{course_key}`` — a course key in the
       member-identifier slot
     - ``/api/instructor/v1/courses/{course_key}/tasks/``, the shape
       ``/api/instructor/v2/courses/{course_key}/instructor_tasks`` already uses
   * - ``/api/v1/course_runs/``, ``/api/mobile/v0.5/``,
       ``/api/bulk_enroll/v1/bulk_enroll``, ``/api/content-staging/v1/``, and
       ``/api/notifications/mark-seen/{app}/``
     - ``/api/authoring/v1/course_runs/``, ``/api/mobile/v1/``,
       ``/api/enrollment/v1/bulk_enrollments/``, ``/api/content_staging/v1/``,
       ``/api/notification/v1/notifications/seen/``
   * - ``/api/credit/v1/`` beside ``/api/credit/request/``,
       ``/api/user_tours/v1/{username}`` beside
       ``/api/user_tours/discussion_tours/{tour_id}``, and
       ``/api/learner_home/v1/`` beside ``/api/learner_home/init`` — versioned and
       unversioned routes in one namespace
     - one versioning scheme per namespace
   * - ``path('', include('openedx.core.djangoapps.xblock.rest_api.urls'))`` in
       both services, which hides ``/api/xblock/v2/`` from the project URLconf
     - ``path('api/xblock/v2/', include(...))``, with the app's URLconf holding
       only the paths below the prefix
   * - ``/transcripts/upload``, ``/organizations`` (Studio), and
       ``/course_team/{course_key}`` — 45 of the 109 routes in ``cms/urls.py`` are
       ``*_handler`` views outside ``/api/``. Conversely
       ``/api/embargo/blocked-message/...`` is an HTML ``View`` *under* ``/api/``,
       dual-mounted at ``/embargo/``
     - ``/api/authoring/v1/courses/{course_key}/transcripts/``,
       ``/api/organization/v1/organizations/``; HTML views stay outside ``/api/``
       and are mounted once

Three properties of the platform shape how these rules apply.

**Opaque keys need a converter, but not a slash-tolerant one.** Endpoints put the
course key last today because ``COURSE_KEY_PATTERN``
(``openedx/core/constants.py``) admits deprecated ``Org/Course/Run`` keys, which
contain ``/`` and so cannot be matched by Django's ``<str:...>`` converter. New
APIs do not inherit that constraint: `openedx-platform#31134`_ removed Old Mongo
create and update operations, leaving only read-only access to static assets and
the root ``CourseBlock``, so no new deprecated-key course can be authored. New and
migrated APIs therefore accept non-deprecated keys only and reject deprecated ones
in the converter, which keeps mid-path nesting unambiguous. Only
endpoints that must keep serving pre-existing Old Mongo courses need the
slash-tolerant pattern. Either way a converter is required, and the platform has
only three, in two apps
(``openedx/core/djangoapps/xblock/rest_api/url_converters.py`` and
``openedx/core/djangoapps/content_libraries/rest_api/url_converters.py``), and
none is reusable.

**The URL surface is not visible in the two project URLconfs.** Beyond the
``path('', include(...))`` mounts, apps loaded through ``get_plugin_apps()``
declare full ``api/...`` paths in their own ``urls.py``: ``content_staging``,
``olx_rest_api``, ``content_libraries``, and ``lms.djangoapps.instructor`` are all
absent from the static ``INSTALLED_APPS`` lists and arrive via the plugin
mechanism. All four are core apps rather than optional extensions, so they should
be moved into ``INSTALLED_APPS`` and mounted explicitly under their own prefix in
``lms/urls.py`` and ``cms/urls.py``, which is what rule 5 asks of any API. The
conformance check still walks the composed resolver, because genuine third-party
plugins will always contribute routes the project URLconfs cannot show.

**One surface is unversioned deliberately.** ``course_home`` documents itself as
"a BFF ... not versioned because there is no guarantee of stability over time",
and ``lms/urls.py`` describes its ``v1`` mount as "just kept for transitional
reasons". This ADR does not overrule that. It requires only that such a surface
keep the ``/api/`` prefix and one canonical mount, and be marked ``x-internal``
in the OpenAPI schema so clients can tell it apart from a stable contract.

Code examples
=============

**Conforming URLconf**, mounted at ``api/course/v1/`` from ``lms/urls.py``:

.. code-block:: python

   # openedx/core/djangoapps/course/rest_api/v1/urls.py
   router = DefaultRouter()                       # trailing slashes by default
   router.register(r"courses", CourseViewSet, basename="course")

   urlpatterns = [
       path("", include(router.urls)),
       # one level of nesting; the parent is a plural collection with a labelled key
       path(
           "courses/<course_key:course_key>/team_members/<str:username>/",
           CourseTeamMemberViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
           name="course_team_member_detail",
       ),
   ]

**Shared opaque-key converter**, shipped in ``edx-drf-extensions`` alongside
the pagination and JWT classes and registered once per service with
``register_url_converters()``. Because it accepts non-deprecated keys only,
the regex is simply "no slash" rather than a transcription of
``COURSE_KEY_PATTERN``, and deprecated keys are rejected in ``to_python``:

.. code-block:: python

   # edx_rest_framework_extensions/url_converters.py
   class CourseKeyConverter:
       """Matches non-deprecated course keys (``course-v1:``, ``ccx-v1:``)."""

       regex = r'[^/]+'

       def to_python(self, value: str) -> CourseKey:
           try:
               course_key = CourseKey.from_string(value)
           except InvalidKeyError as exc:
               raise ValueError from exc          # Django turns this into a 404
           if course_key.deprecated:              # Org/Course/Run — Old Mongo only
               raise ValueError(f"deprecated course key: {value}")
           return course_key

       def to_url(self, value: CourseKey) -> str:
           return str(value)

Verified on Django 5.2.16: non-deprecated keys resolve and ``reverse()``
round-trips them, deprecated and malformed keys both 404, and two converters can
appear in one route. Views then receive a parsed ``CourseKey``, which removes
hand-written ``CourseKey.from_string`` handling from each view and turns a bad
key into a consistent 404 instead of the ad-hoc 400s that
:doc:`0029-standardize-error-responses` addresses.

**Conformance check.** ``openedx/core/tests/test_api_url_conventions.py`` walks
each service's composed resolver, strips regex anchors so that ``re_path`` and
``path`` routes compare alike, and matches every route under ``api/`` against
``r"^api/[a-z][a-z0-9_]*/v[0-9]+/(?:[a-z0-9_<>:,]+/)*$"``. That covers rules 1,
4, 6, and 7, with companion assertions for nesting depth (8), pattern anchoring
(6), URL-name style (11), and same-path/different-view pairs across the two
services (12). Pluralisation, domain-versus-app naming, and verb-freeness remain
review-time judgements. An ``ALLOWLIST`` holds today's violations, each with a
DEPR issue, and may only shrink, so adding a non-conforming route means editing
that list and surfacing the exception in review.

Consequences
============

* Pros

  * One predictable address shape, so a client that knows one endpoint can infer
    the next, and generated OpenAPI and SDKs get uniform operation IDs and
    groupings.
  * The combined headless LMS+CMS migration can proceed endpoint by endpoint
    without a naming collision blocking it.
  * The rules live in the repository they govern and are enforced by CI, rather
    than depending on whether an author found a wiki page.
  * Shared converters remove duplicated key parsing, and anchoring closes a class
    of accidental over-matching.

* Cons / Costs

  * A large existing surface is non-conforming, so convergence spans several named
    releases, and each migrated endpoint has two live addresses — with schema,
    tests, and documentation covering both — during its deprecation window.
  * Namespace renames (``contentstore`` → ``authoring``) touch MFEs, mobile
    clients, and unknown external integrations, so each needs its own DEPR issue.
  * Unsafe methods sent to a slashless path fail rather than redirect under
    ``APPEND_SLASH``, so client migrations must update paths and not rely on the
    redirect.
  * A renamed namespace inherits ``contentstore``'s five parallel versions
    (``v0``–``v4``); collapsing those is
    :doc:`0037-api-versioning-strategy`'s scope, not this ADR's.

Implementation Notes
====================

1. Register the shared converters from ``edx-drf-extensions``
   (``register_url_converters()``) in both services.
2. Add the conformance tests with the full allowlist, which freezes the current
   state and fails any new violation.
3. Record reserved ``{api_name}`` values in one registry, including plugin-app and
   third-party mounts, and require a new API to claim its name in the same pull
   request that adds the route.
4. Migrate the APIs already standardized under FC-0118 first
   (``/api/contentstore/v3/home/``, ``v3/course_details/``,
   ``v3/authoring_grading/``, ``v4/home/courses/``, and ``v1/xblock/``, plus
   ``/api/enrollment/v2/``), each keeping its current version number. Reconcile
   ``/api/authoring/v1/xblocks/`` with the existing Learning Core
   ``/api/xblock/v2/xblocks/`` rather than leaving two names for what looks like
   one API.
5. Resolve the ``/api/courses/`` collision before the combined-service migration
   reaches it, collapse the dual mounts, and work the allowlist down as endpoints
   are touched for other reasons.

Rejected Alternatives
=====================

* **Linking to the existing wiki page instead.** That is the status quo which
  produced the divergence: unversioned, unenforced, partly superseded, and still
  ``TBD`` on collisions.
* **Plural API names** (``/api/courses/v1/courses/``) produce stutter, and
  **singular collections** (``/api/enrollment/v1/enrollment/``) are already
  ambiguous in the platform today.
* **Hyphenated resource names** (``kebab-case``), as most general REST style
  guides recommend. The platform, Django URL names, and Python identifiers are
  ``snake_case``, and the existing Open edX convention mandates it.
* **Deep nesting** (``/api/organizations/{org}/courses/{key}/units/{key}/``).
  Each level adds a permission surface and a parse point, and couples children to
  parent addressing. **URLs mirroring
  the codebase** (``/api/contentstore/v1/``) publish an implementation detail as
  a public contract, so refactors become breaking changes.
* **Independent LMS and CMS namespaces.** Cheap now, but it forecloses serving
  both from one domain later. **Exempting the MFE BFF APIs** is what let
  ``course_home`` acquire two mounts and unanchored patterns; ``x-internal`` in
  the schema conveys the same warning without giving up address discipline.
* **Version in a header.** It contradicts :doc:`0037-api-versioning-strategy` and
  hides the version from logs and caches. **One platform-wide rewrite behind
  redirects** is also rejected: redirects are unsafe for non-GET methods, and a
  single cutover cannot be reviewed or reverted incrementally.

References
==========

* `Open edX REST API Conventions`_ — the pre-existing conventions, superseded in
  part by this ADR
* `OEP-49`_ — defers URL structure, namespacing, and versioning to the above
* `OEP-21`_ — the deprecation process every path migration follows
* `combined headless LMS+CMS`_ — the merge this ADR keeps viable
* `OEP-69 review`_ — where URL expectations were requested

.. _openedx-platform#31134: https://github.com/openedx/openedx-platform/pull/31134
.. _Open edX REST API Conventions: https://openedx.atlassian.net/wiki/spaces/AC/pages/18350757/Open+edX+REST+API+Conventions
.. _OEP-49: https://docs.openedx.org/projects/openedx-proposals/en/latest/best-practices/oep-0049-django-app-patterns.html
.. _OEP-21: https://docs.openedx.org/projects/openedx-proposals/en/latest/processes/oep-0021-proc-deprecation.html
.. _combined headless LMS+CMS: https://discuss.openedx.org/t/proposal-combined-headless-lms-cms/18880
.. _OEP-69 review: https://github.com/openedx/openedx-proposals/pull/805
