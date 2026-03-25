Standardize RESTful Endpoint Structure Using DRF ViewSets
=========================================================

:Status: Proposed
:Date: 2026-03-19
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - RESTful endpoint structure standardization using DRF ViewSets

Context
-------

Many Open edX platform API endpoints are currently implemented as separate, individual
class-based or function-based views for each HTTP action. Instead of using Django REST
Framework (DRF) ViewSets to group related operations into a single, cohesive class, each
action (list, retrieve, create, update, delete) is handled by its own standalone view.
This fragmented approach leads to significant code duplication, inconsistent behavior
across related endpoints, and an API layer that is difficult to extend or maintain.

Additionally, some endpoints currently return both JSON and HTML responses depending on
the request context. Serving both response formats from a single endpoint violates the
principle of separation of concerns, creates confusion for external consumers and
automated systems, and increases the complexity of both the view logic and client-side
handling.

Decision
--------

We will refactor all fragmented Open edX REST API endpoints to use DRF ViewSets,
consolidating related actions into unified, well-structured view classes. Additionally,
all API endpoints will be standardized to return only JSON responses, with all HTML
rendering logic relocated to dedicated Micro Frontend (MFE) pages.

Implementation requirements:

* All related API actions (list, retrieve, create, update, delete) **MUST** be
  consolidated into a single DRF ViewSet per resource.
* Endpoints currently returning both JSON and HTML **MUST** be refactored to return only
  JSON. HTML rendering must be moved to a dedicated MFE page.
* ViewSets **MUST** be registered using DRF Routers to ensure consistent, predictable
  URL patterns.
* All ViewSets **MUST** use explicit serializers for both request validation and response
  formatting (per ADR 0025).
* Multi-method handler functions (e.g., a single method handling DELETE, POST, and PUT)
  **MUST** be refactored into properly documented, action-specific methods within a
  ViewSet.
* Backward compatibility **MUST** be maintained during migration, using
  versioned endpoints or deprecation notices. If a backwards incompatible change is
  required, that change MUST be handled by creating a new version of the API and
  transitioning to that API using the deprecation process.

Relevance in edx-platform
-------------------------

Current patterns that should be migrated:

* **Enrollment API** (``/api/enrollment/v1/``) - currently split across three
  independent ``APIView`` classes, each handling a distinct part of the enrollment
  resource:

  * ``EnrollmentListView(APIView, ApiKeyPermissionMixIn)`` - handles
    ``GET /api/enrollment/v1/enrollment`` (list all enrollments for the current user)
    and ``POST /api/enrollment/v1/enrollment`` (enroll a user in a course, with support
    for mode, enrollment attributes, and enterprise consent).
  * ``UnenrollmentView(APIView)`` - handles
    ``POST /api/enrollment/v1/unenrollment``, a privileged service-only endpoint that
    unenrolls a single user from all courses as part of the user retirement pipeline.
  * ``EnrollmentAllowedView(APIView)`` - handles retrieval and creation of
    ``CourseEnrollmentAllowed`` records for a given user email and course ID; restricted
    to admin users via ``permissions.IsAdminUser``.
  * These three views operate on the same enrollment resource and should be unified into
    a single ``EnrollmentViewSet``.

* **Assets Handling Endpoints** - currently exhibit two distinct issues:

  * A single handler function mixes DELETE, POST, and PUT operations without clear
    separation of concerns.
  * The endpoint checks the ``HTTP_ACCEPT`` header and returns either JSON or HTML
    depending on the request, mixing response formats in a single view.
  * **Resolution:** Refactor into a properly documented ``AssetsViewSet`` with distinct
    action methods. Create a dedicated MFE page for all HTML rendering; the endpoint
    returns only JSON.

Illustrative Example
--------------------

The following shows the structural pattern being replaced and the target pattern.
Full implementation details will be addressed during the migration of each endpoint.

**Before - Enrollment API (current fragmented pattern):**

.. code-block:: python

    # Three separate APIView classes for one logical resource
    class EnrollmentListView(APIView, ApiKeyPermissionMixIn): ...
    class UnenrollmentView(APIView): ...         # privileged, retirement pipeline only
    class EnrollmentAllowedView(APIView): ...    # admin-only, CourseEnrollmentAllowed records

**After - Consolidated into a single ViewSet:**

.. code-block:: python

    class EnrollmentViewSet(viewsets.ViewSet):
        # viewsets.ViewSet used intentionally — enrollment logic routes through
        # the api module, not direct ORM, so ModelViewSet is not appropriate.

        def list(self, request): ...
        def create(self, request): ...

        @action(detail=False, methods=["post"], url_path="unenrollment",
                permission_classes=[ApiKeyHeaderPermission])
        def unenroll(self, request): ...         # preserves privileged-only restriction

        @action(detail=False, methods=["get", "post"], url_path="allowed",
                permission_classes=[permissions.IsAdminUser],
                throttle_classes=[EnrollmentUserThrottle])
        def allowed(self, request): ...          # reuses existing CourseEnrollmentAllowedSerializer

    router = DefaultRouter()
    router.register(r"enrollment", EnrollmentViewSet, basename="enrollment")

**Before - Assets handler (current mixed-format pattern):**

.. code-block:: python

    # Single function-based view — returns HTML or JSON depending on HTTP_ACCEPT,
    # with GET, POST, PUT, and DELETE all dispatched inside handle_assets().
    @login_required
    @ensure_csrf_cookie
    def assets_handler(request, course_key_string=None, asset_key_string=None):
        return handle_assets(request, course_key_string, asset_key_string)

**After - Dedicated ViewSet, JSON only:**

.. code-block:: python

    class AssetsViewSet(viewsets.ViewSet):
        # HTML rendering moved to Studio MFE.
        # Reuses existing asset_storage_handlers service functions.
        def list(self, request, course_key_string): ...     # GET  - paginated asset list
        def create(self, request, course_key_string): ...   # POST - upload asset
        def update(self, request, course_key_string, pk): ... # PUT  - update lock state
        def destroy(self, request, course_key_string, pk): ...# DELETE - remove asset

Consequences
------------

Positive
~~~~~~~~

* Consistent, discoverable API structure that is easier for developers and third-party
  integrators to understand and consume.
* Significant reduction in code duplication by consolidating related operations into a
  single ViewSet class.
* Improved maintainability - changes to a resource's API logic are localized to one
  ViewSet rather than scattered across multiple view files.
* DRF Routers automatically generate standard URL patterns, reducing manual URL
  configuration and human error.
* Clear separation of concerns: API endpoints serve data (JSON only); MFEs handle all
  HTML rendering.
* Improved compatibility with AI systems, automated testing frameworks, and third-party
  integrations that expect predictable, JSON-only responses.
* Enables automatic API schema generation and documentation via ``drf-spectacular``.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Refactoring existing fragmented views into ViewSets requires non-trivial upfront
  development effort.
* Existing URL patterns may change during migration, requiring updates to client-side
  code, documentation, and any hardcoded references.
* HTML-rendering logic must be migrated to MFE pages, which requires coordination with
  frontend teams and may extend the migration timeline.
* Teams unfamiliar with DRF ViewSets and Routers will require onboarding before
  contributing to migrated endpoints.

Alternatives Considered
-----------------------

* **Keep fragmented APIView classes:** Rejected. Maintains code duplication,
  inconsistency, and high maintenance burden across related operations.
* **Use DRF GenericAPIView with mixins:** Partially considered but rejected as the
  primary approach. While mixins reduce some duplication, they do not provide the same
  level of structural consolidation or automatic router integration as ViewSets.
* **Continue serving both JSON and HTML from the same endpoint:** Rejected. Mixing
  response formats in a single endpoint violates separation of concerns, increases
  complexity, and creates an unreliable contract for API consumers.

Rollout Plan
------------

1. Audit existing API endpoints to identify all fragmented view patterns and endpoints
   with mixed JSON/HTML responses.
2. Prioritize high-impact resources for migration: Enrollment API, Assets endpoints, and
   any other endpoints identified in the audit.
3. Coordinate with frontend teams to create corresponding MFE pages for any endpoints
   currently serving HTML.
4. Refactor identified endpoints into DRF ViewSets, registered via DRF Routers. Ensure
   all ViewSets use explicit serializers per ADR 0025.
5. Update and expand test coverage to validate correct behavior of all refactored
   ViewSet actions and confirm JSON-only responses.
6. Publish deprecation notices for any legacy URL patterns that will be replaced,
   providing clear migration guidance to internal and external API consumers.
7. Update API documentation to reflect the new ViewSet-based structure, URL patterns,
   and JSON-only response contracts.

References
----------

* Django REST Framework documentation - ViewSets:
  https://www.django-rest-framework.org/api-guide/viewsets/
* Django REST Framework documentation - Routers:
  https://www.django-rest-framework.org/api-guide/routers/
  