Canonical MFE Configuration Endpoint
====================================

:Status: Accepted
:Supersedes: ADR 0001 — MFE Config API (partial — the configuration-endpoint role only)
:Date: 2026-04-09
:Deciders: Open edX Platform / API Working Group
:Technical Story: Open edX REST API Standards - Canonical MFE / front-end configuration endpoint

Context
-------

Open edX MFEs (and, under OEP-65, frontend-base modules) read their runtime
configuration from an HTTP endpoint rather than from build-time environment
variables. ADR 0001 introduced ``/api/mfe_config/v1`` for this, and it has served
that role since.

A second endpoint, ``/api/frontend_site_config/v1/``, was later added without a
backing ADR. The ``lms.djangoapps.mfe_config_api`` app therefore now exposes two
overlapping configuration endpoints with no documented statement of which is
canonical, while the original endpoint is already on a formal deprecation path
(``[DEPR]: MFE runtime config API``, #37255, also tracked at #37210, targeting the
Verawood (2026-04) named release). The exact code is summarised under
`Relevance in edx-platform`_.

Two problems follow from leaving this unstated:

* **No stated canonical endpoint.** New consumers cannot tell which of the two to
  target, and operators cannot tell which is authoritative.
* **Pressure to grow the surface in the wrong direction.** As MFEs need
  request-specific data (course context, the requesting user's roles and
  permissions), there is pressure either to add a *third* configuration endpoint
  or to fold per-user data into a configuration payload. The latter is especially
  unsafe here because both endpoints are unauthenticated and shared-cached. Because
  the platform's REST surface is only now being standardized, this is
  the moment to prevent that drift rather than entrench it.

Endpoints that MFEs depend on but that carry no documented, versioned contract
break consumers silently from one release to the next. The configuration/context
surface this ADR concerns is one such case, and the same failure mode recurs on
adjacent MFE-facing endpoints:

* **Authn MFE — ``/api/mfe_context`` optional fields not rendered (Tutor 20, Mar
  2026).** ``GET /api/mfe_context?is_register_page=true`` returns ``optionalFields``
  in its payload, but the Authn registration page renders only the required fields.
  Whether this is a defect or a configuration gap is itself unclear, because the
  endpoint publishes no contract to adjudicate against — exactly the undocumented
  MFE-context / configuration class of endpoint this ADR is about. `Forum thread
  <https://discuss.openedx.org/t/optional-registration-extra-fields-not-visible-on-authn-registration-page-tutor-20-authn-mfe/18633>`__
* **Discussions MFE — empty topic results on a fresh Teak install (Dec 2025).**
  ``GET /api/discussion/v1/threads/`` returned ``200 OK`` with an empty list for
  topic lookups (the MFE showed "No results found") because the forum client
  converted ``commentable_id(s)`` to a list only inside its search branch; the
  corrected handling shipped on ``master`` in #36820 — a refactor marked ``feat!``
  yet described as having "no impact on end-users", which the Teak line lacked.
  `Forum thread
  <https://discuss.openedx.org/t/teak-discussion-topics-disappear-immediately-after-clicking-api-returns-200-ok-with-empty-results/17728>`__
* **Discussions MFE — 500s across endpoints on Teak (Aug 2025).** Several discussion
  REST calls returned ``500`` because the comment client invoked the forum backend
  with an argument list that did not match the bundled ``openedx/forum`` version,
  until the forum version was bumped. `Forum thread
  <https://discuss.openedx.org/t/discussions-forum-500-on-multiple-apis-on-teak/16835>`__

Decision
--------

We will designate one canonical configuration endpoint, keep the configuration
surface free of per-user data, and avoid adding further configuration endpoints.

* **Canonical endpoint.** ``/api/frontend_site_config/v1/``
  (``FrontendSiteConfigView``) is the canonical endpoint for MFE / front-end
  runtime configuration, aligned with frontend-base's ``SiteConfig`` format under
  OEP-65. New consumers MUST target it.
* **Legacy endpoint on a deprecation path.** ``/api/mfe_config/v1``
  (``MFEConfigView``, ADR 0001) is legacy. Its deprecation follows OEP-21 and is
  tracked in #37255 and #37210, targeting the Verawood (2026-04) named release.
  Existing consumers migrate to the canonical endpoint.
* **No new configuration endpoints.** We will not introduce additional
  MFE-specific configuration endpoints. If configuration needs grow, the canonical
  endpoint is extended using URL-path versioning (for example ``/v2/``) rather than
  adding parallel surfaces. This is consistent with the sibling *Merge Similar
  Endpoints* ADR (#38166 / #38262).
* **User-contextual data is not configuration.** User roles, enrollments, and
  course-specific permissions MUST NOT be served as fields on a configuration
  payload. The configuration endpoints are unauthenticated and shared-cached, so
  per-user data cannot be served from them safely; and independent of caching, this
  data is a set of first-class resources, not configuration. It belongs at
  resource-oriented endpoints usable by any client; the *Code example* section
  below shows the contrast.
* **Client request-count concerns are solved on the client.** If resource-oriented
  design means an MFE issues more requests, that is addressed with client-side
  batching or a Backend-for-Frontend owned by the frontend — not by collapsing
  distinct resources into one multi-purpose endpoint. A BFF pattern already exists
  in the platform (the Learning MFE's courseware BFF), so this is established
  precedent rather than a new service.
* **Documentation and schema tooling are out of scope.** This ADR decides endpoint
  topology only; OpenAPI / schema coverage for the canonical endpoint, and for the
  REST surface generally, is decided in the *Standardize API Documentation & Schema
  Coverage* ADR (#38189, drf-spectacular).

Relationship to ADR 0001
------------------------

ADR 0001 ("MFE Config API") established ``/api/mfe_config/v1`` as the runtime
configuration mechanism for MFEs and argued — correctly, and still validly — that
runtime configuration is preferable to build-time environment variables that force
a rebuild on every change.

This ADR supersedes only the part of 0001 that makes ``/api/mfe_config/v1`` the
configuration surface of record. That role now belongs to
``/api/frontend_site_config/v1/``. Everything else in 0001 remains in effect: the
runtime-vs-build rationale, the no-authentication posture for public configuration,
and response caching — all of which also apply to the canonical endpoint. Per ADR
convention, 0001 carries a one-line ``Superseded (partially) by ADR 0040`` status
note pointing here, so the relationship is discoverable from either direction.

Relevance in edx-platform
-------------------------

Confirmed in the codebase (``master``):

* Both endpoints are served by ``lms.djangoapps.mfe_config_api`` and registered in
  ``lms/urls.py``::

      path('api/mfe_config/v1',
           include((mfe_config_urls, 'lms.djangoapps.mfe_config_api'),
                   namespace='mfe_config_api')),
      path('api/frontend_site_config/v1/',
           include((frontend_site_config_urls, 'lms.djangoapps.mfe_config_api'),
                   namespace='frontend_site_config')),

* In ``lms/djangoapps/mfe_config_api/views.py``:

  * ``MFEConfigView`` returns the legacy ``SCREAMING_SNAKE_CASE`` ``MFE_CONFIG``
    shape. Its docstring describes it as *"a temporary compatibility layer which
    will eventually be deprecated"* and links the DEPR ticket.
  * ``FrontendSiteConfigView`` returns ``FRONTEND_SITE_CONFIG`` in frontend-base's
    camelCase ``SiteConfig`` format, merged over a compatibility translation of the
    legacy ``MFE_CONFIG`` / ``MFE_CONFIG_OVERRIDES`` settings; its docstring notes
    that once legacy configuration is deprecated, the translation layer is removed
    and it *"will simply return FRONTEND_SITE_CONFIG as-is."*

* Both views declare ``permission_classes = [AllowAny]`` and wrap ``get`` in
  ``cache_page(settings.MFE_CONFIG_API_CACHE_TIMEOUT)`` — i.e. the configuration
  surface is public and shared-cached, which is why per-user data must not be served
  from it.

Code example (configuration endpoint vs. user-context resources)
----------------------------------------------------------------

**Canonical configuration request** — public, shared-cached, no authentication:

.. code-block:: http

   GET /api/frontend_site_config/v1/

.. code-block:: json

   {
     "siteName": "My Open edX Site",
     "baseUrl": "https://apps.example.com",
     "lmsBaseUrl": "https://courses.example.com",
     "loginUrl": "https://courses.example.com/login",
     "logoutUrl": "https://courses.example.com/logout"
   }

**User context as its own resource** — authenticated and per-user, so (unlike the
configuration endpoint) it is never served from a shared cache. The path and view
below are *illustrative*; their concrete shape is owned by the team that owns the
resource, not by this ADR. The point is the contrast in shape:

.. code-block:: python

   # lms/djangoapps/<resource_app>/views.py  (illustrative)
   class CourseUserPermissionsView(APIView):
       # GET /api/courses/{course_id}/permissions/me/
       authentication_classes = [JwtAuthentication, SessionAuthentication]
       permission_classes = [IsAuthenticated]

       def get(self, request, course_id):
           course_key = CourseKey.from_string(course_id)
           permissions = get_user_course_permissions(request.user, course_key)
           return Response(permissions, status=status.HTTP_200_OK)

Consequences
------------

Positive
~~~~~~~~

* One documented home for front-end configuration; the ambiguity between two
  endpoints is resolved.
* The REST surface stays resource-oriented. The platform avoids introducing an
  RPC-style, MFE-only endpoint at the moment it is standardizing its APIs under
  FC-0118.
* User-context resources, defined independently, become reusable by any client —
  CMS, mobile, and third-party integrators — not just MFEs.
* The canonical endpoint aligns with frontend-base's ``SiteConfig`` (OEP-65) and is
  straightforward to document under the schema-coverage ADR (#38189).

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* MFEs and other consumers still pointed at ``/api/mfe_config/v1`` must migrate to
  the canonical endpoint — real cross-team work, tracked under #37255 / #37210 and
  timed to the Verawood (2026-04) deprecation.
* Splitting user-context out of configuration can increase request counts for some
  front-end flows; the mitigation (client-side batching or a BFF) is additional
  frontend effort.
* ``/api/frontend_site_config/v1/`` has no prior ADR of its own. Adopting it as
  canonical means any gap between what it provides today and what consumers need
  must be closed by extending it (versioned), follow-up work, not part of this
  decision.

Alternatives Considered
-----------------------

* **Create a "new" consolidated** ``/api/mfe_config/v1`` **(the original draft of
  this ADR)**: Rejected. That path already exists under an accepted ADR (0001), and
  a second, forward-looking endpoint (``/api/frontend_site_config/v1/``) already
  exists. Declaring a "new" one duplicates surface and contradicts 0001.
* **Add a third, MFE-specific configuration endpoint**: Rejected. Three overlapping
  configuration endpoints worsen the exact ambiguity this ADR exists to remove.
* **Fold user roles / course permissions into the configuration endpoint**:
  Rejected. It violates resource-oriented design, and the configuration endpoints
  are unauthenticated and shared-cached, which makes serving per-user data from them
  unsafe regardless of shape.
* **Stand up a new Backend-for-Frontend now**: Deferred, not rejected outright. A
  BFF is a reasonable home for client-specific request shaping if front-end
  data-fetching grows substantially more complex (and one already exists for the
  Learning MFE), but introducing a new BFF service is out of scope here. The
  canonical endpoint plus resource-oriented endpoints meet current needs.
* **Bundle API documentation into this ADR (the original draft)**: Removed.
  Documentation and schema tooling are decided in #38189; re-deciding here would
  duplicate that effort. This ADR is scoped to configuration-endpoint consolidation
  only.

Rollout Plan
------------

1. Record ``/api/frontend_site_config/v1/`` as canonical in the front-end
   configuration documentation, and note ``/api/mfe_config/v1`` as legacy. Maintain
   a representative (not exhaustive) audit of which endpoints consumers use today in
   the API working-group wiki.
2. Point new MFEs / frontend-base modules and any new configuration needs at the
   canonical endpoint.
3. Migrate existing ``/api/mfe_config/v1`` consumers under the deprecation tracked
   in #37255 / #37210, following OEP-21 and timed to the Verawood (2026-04) release.
4. Where consumers need user roles or course-specific permissions, consume the
   relevant resource-oriented endpoints rather than expecting that data on a
   configuration payload.
5. Defer OpenAPI / schema documentation of the canonical endpoint to the
   schema-coverage ADR (#38189).

References
----------

* ADR 0001 — MFE Config API
  (``lms/djangoapps/mfe_config_api/docs/decisions/0001-mfe-config-api.rst``);
  partially superseded by this ADR.
* Implementation: ``lms/djangoapps/mfe_config_api/views.py`` (``MFEConfigView``,
  ``FrontendSiteConfigView``); endpoint registration in ``lms/urls.py``.
* `#37255 — [DEPR]: MFE runtime config API (formal deprecation; targets Verawood 2026-04) <https://github.com/openedx/openedx-platform/issues/37255>`_
* `#37210 — DEPR reference linked from MFEConfigView <https://github.com/openedx/openedx-platform/issues/37210>`_
* `#38189 — Standardize API Documentation & Schema Coverage ADR (drf-spectacular; related issue #38164) <https://github.com/openedx/openedx-platform/pull/38189>`_
* `#38166 / #38262 — Merge Similar Endpoints ADR (sibling) <https://github.com/openedx/openedx-platform/pull/38262>`_
* `#36820 — feat!: remove cs_comments_service support for forum's search APIs (Teak discussions regression) <https://github.com/openedx/openedx-platform/pull/36820>`_
* `#38137 — FC-0118 Open edX REST API standardization (umbrella) <https://github.com/openedx/openedx-platform/issues/38137>`_
* `#38280 — originating issue for this ADR <https://github.com/openedx/openedx-platform/issues/38280>`_
* `OEP-21: Deprecation and Removal Process <https://open-edx-proposals.readthedocs.io/en/latest/processes/oep-0021-proc-deprecation.html>`_
* `OEP-65: Frontend Composability <https://docs.openedx.org/projects/openedx-proposals/en/latest/architectural-decisions/oep-0065-arch-frontend-composability.html>`_ (frontend-base ``SiteConfig`` / module architecture)