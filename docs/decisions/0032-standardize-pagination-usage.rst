Standardize Pagination Across APIs
===================================

:Status: Proposed
:Date: 2026-04-08
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Pagination standardization for consistency and scalability

Context
-------

Open edX platform API endpoints use multiple, inconsistent pagination strategies. Some endpoints use ``limit``/``offset`` query parameters, others use ``page``/``page_size``, and several return complete result sets with no pagination at all. This inconsistency forces every API consumer — whether a frontend micro-frontend (MFE), a mobile client, an AI agent, or a third-party integration — to implement custom data-loading logic per endpoint.

The ``edx-drf-extensions`` library already provides a ``DefaultPagination`` class (a subclass of DRF's ``PageNumberPagination``) that standardizes on ``page``/``page_size`` parameters with a default page size of 10 and a maximum of 100. However, many endpoints either override this with ad-hoc pagination classes, use ``LimitOffsetPagination``, or bypass pagination entirely by returning raw lists or manually constructed JSON arrays.

Decision
--------

We will standardize all Open edX REST APIs to use the existing ``DefaultPagination`` class from ``edx-drf-extensions`` as the platform-wide pagination standard.

Implementation requirements:

* All list-type API endpoints MUST use ``DefaultPagination`` (or a subclass of it) from ``edx-drf-extensions``.
* Endpoints currently using ``LimitOffsetPagination`` MUST be migrated to ``DefaultPagination`` with appropriate versioning.
* Endpoints returning unpaginated result sets MUST be updated to return paginated responses.
* All paginated responses MUST include the standard envelope: ``count``, ``next``, ``previous``, ``num_pages``, ``current_page``, ``start``, and ``results``.
* Views that subclass ``APIView`` directly (rather than ``GenericAPIView`` or ``ListAPIView``) MUST manually invoke the pagination API to return paginated responses.
* Custom ``page_size`` overrides per endpoint are acceptable when justified (e.g., mobile APIs may use a smaller default), but MUST be implemented by subclassing ``DefaultPagination`` rather than using an unrelated pagination class.
* Maintain backward compatibility for all APIs during migration. If a fully compatible migration is not possible, a new API version MUST be created and the old version deprecated following the standard deprecation process.

Scope and Tree-Shaped Endpoints
-------------------------------

This ADR applies to **flat list endpoints** — endpoints whose response is a collection of sibling items with no hierarchical nesting between items.

**Tree-shaped endpoints** — where each item may contain an arbitrary subtree of child items (Course Blocks, Taxonomy, OLX structure, progress trees) — are out of scope for the standard item-count pagination envelope described here. Applying ``DefaultPagination`` to such endpoints is ill-defined: a "page size of 10" has no consistent meaning when items may contain hundreds of descendants, and paginating over a flat node set risks splitting parents from their children across page boundaries.

Tree-shaped endpoints MUST instead follow one of these patterns:

1. Return the complete structural representation (IDs, types, parent/child relationships, display names) unpaginated at a controlled depth, and paginate separately over node *content* via follow-up endpoints. The Course Blocks API's ``requested_fields`` behavior is the reference implementation of this pattern.
2. Return the tree to a fixed maximum depth, and provide explicit child-fetch URLs for any subtrees beyond that depth.

Response-shape conventions for these endpoints — minimal vs full views, field selection (``?fields=...``), and flattening of deeply nested JSON — are specified in ADR-0036 (*Reduce Deeply Nested JSON via Minimal/Flattened Views*), which is the canonical place for those decisions.

Where a tree endpoint exposes a flat list of node IDs alongside its structural representation (for example via ``?fields=id,type``), standard ``DefaultPagination`` over that flat ID list is appropriate and in scope.

Relevance in edx-platform
--------------------------

Current example patterns that should be migrated:

* **Completion API** (``/api/completion/v1/completion/``) — uses inconsistent pagination formats depending on request parameters; some paths return unpaginated results.
* **User Accounts API** (``/api/user/v1/accounts/``) — pagination behavior differs from other user-related APIs, making it difficult for consumers to use a single data-loading pattern.
* **Course Members API** (``/api/courses/v1/.../members/``) — returns all enrollments without pagination, relying on a ``COURSE_MEMBER_API_ENROLLMENT_LIMIT`` setting (default 1000) to cap results and raising ``OverEnrollmentLimitException`` instead of paginating.
* **Enrollment API** (``/api/enrollment/v1/``) — some list endpoints return full result sets without pagination support.
* **Course Blocks API** (``/api/courses/v2/blocks/``) — a tree-shaped endpoint. Out of scope for standard item-count pagination per the *Scope and Tree-Shaped Endpoints* section above; its ``requested_fields`` behavior is the reference pattern for structural queries over trees. Response-shape conventions for such endpoints are specified in ADR-0036.

Code example (target pagination usage)
---------------------------------------

**Example using DefaultPagination with a ListAPIView:**

.. code-block:: python

   # views.py
   from rest_framework.generics import ListAPIView
   from edx_rest_framework_extensions.paginators import DefaultPagination
   from .serializers import EnrollmentSerializer

   class EnrollmentListView(ListAPIView):
       """
       Returns a paginated list of enrollments for the authenticated user.

       Pagination parameters:
           - page (int): The page number to retrieve. Default is 1.
           - page_size (int): Number of results per page. Default is 10, max is 100.

       Response envelope:
           - count (int): Total number of results.
           - num_pages (int): Total number of pages.
           - current_page (int): The current page number.
           - next (str|null): URL for the next page, or null.
           - previous (str|null): URL for the previous page, or null.
           - start (int): The starting index of the current page.
           - results (list): The list of enrollment objects.
       """
       serializer_class = EnrollmentSerializer
       pagination_class = DefaultPagination

       def get_queryset(self):
           return CourseEnrollment.objects.filter(
               user=self.request.user,
               is_active=True,
           ).order_by('-created')

**Example subclassing DefaultPagination for a mobile endpoint with a smaller page size:**

.. code-block:: python

   # paginators.py
   from edx_rest_framework_extensions.paginators import DefaultPagination

   class MobileDefaultPagination(DefaultPagination):
       """
       Pagination tuned for mobile clients with smaller payloads.
       """
       page_size = 5
       max_page_size = 50

**Example using DefaultPagination with a plain APIView (manual invocation):**

.. code-block:: python

   # views.py
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from edx_rest_framework_extensions.paginators import DefaultPagination

   class CompletionListView(APIView):
       pagination_class = DefaultPagination

       def get(self, request):
           completions = BlockCompletion.objects.filter(
               user=request.user
           ).order_by('-modified')
           paginator = self.pagination_class()
           page = paginator.paginate_queryset(completions, request)
           serializer = CompletionSerializer(page, many=True)
           return paginator.get_paginated_response(serializer.data)

Consequences
------------

Positive
~~~~~~~~

* External systems and AI agents can implement a single, reusable data loader for all Open edX list endpoints.
* Consumers can reliably pre-calculate batch sizes using the ``count`` and ``num_pages`` fields in every paginated response.
* Eliminates unbounded response sizes that currently risk overloading clients and timing out requests (e.g., large enrollment or discussion lists).
* Enables consistent OpenAPI schema generation for all list endpoints.
* Leverages the already-existing ``DefaultPagination`` class, minimizing new code.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Endpoints that currently return full result sets (e.g., Course Members, Completion) will require consumers to implement pagination loops where they previously did not need to.
* Requires refactoring views that use ``APIView`` directly without DRF's generic pagination machinery.
* Migrating ``limit``/``offset`` endpoints to ``page``/``page_size`` is a breaking change for existing consumers of those specific endpoints and must be versioned.
* Some internal consumers (e.g., modulestore aggregation) may need to be updated to handle paginated results instead of full lists.

Alternatives Considered
-----------------------

* **Standardize on LimitOffsetPagination instead of PageNumberPagination**: Rejected because ``edx-drf-extensions`` already ships ``DefaultPagination`` based on ``PageNumberPagination``, and a significant portion of the platform already uses it — standardizing on it minimizes migration churn. Numbered pages are also easier for humans to reason about, bookmark, and share, and map directly onto existing MFE numbered-page UI controls. Note that the two styles have equivalent database query characteristics by default (both emit ``LIMIT ... OFFSET ...`` SQL via Django's core Paginator); the choice here is about ecosystem fit, not query cost.
* **Adopt CursorPagination as the platform standard**: Rejected because cursor-based pagination does not support random page access (jumping directly to page N), which would break existing MFE numbered-page controls and bookmarkable deep links. The ``CursorPagination`` response envelope (opaque ``next`` / ``previous`` cursors, no ``count``) also differs substantially from what existing Open edX consumers expect, so adoption would require coordinated client-side changes across MFEs and mobile rather than a gradual per-endpoint rollout. ``CursorPagination`` remains a reasonable per-endpoint choice for very large, append-only, or high-churn datasets where numbered pages are not needed.
* **Allow each API app to choose its own pagination style**: Rejected because this is the current state, and it is the root cause of the inconsistency this ADR aims to resolve.
* **Do nothing and document the differences**: Rejected because documentation alone does not reduce the integration burden on consumers or prevent future inconsistencies.

Rollout Plan
------------

1. Audit all list-type API endpoints in ``edx-platform`` to categorize them as: already using ``DefaultPagination``, using a different pagination class, or unpaginated.
2. Add a custom ``pylint`` or ``edx-lint`` check that warns when a ``ListAPIView`` or list-returning ``APIView`` does not specify ``DefaultPagination`` (or a subclass).
3. Migrate high-impact unpaginated endpoints first (Course Members, Completion, Enrollment).
4. Migrate ``limit``/``offset`` endpoints by introducing new API versions  that use ``DefaultPagination``, and deprecating the old versions.
5. Update MFEs and known external consumers to adopt the new pagination parameters where versions change.
6. Update API documentation and OpenAPI specs to reflect the standardized pagination envelope.

References
----------

* ``edx-drf-extensions`` ``DefaultPagination`` class: https://github.com/openedx/edx-drf-extensions/blob/master/edx_rest_framework_extensions/paginators.py
* Django REST Framework Pagination documentation: https://www.django-rest-framework.org/api-guide/pagination/
* ADR-0036 — Reduce Deeply Nested JSON via Minimal/Flattened Views (docs/decisions/0036-normalize-deeply-nested-json-apis.rst).
* Open edX REST API Standards: "Pagination" recommendations for API consistency.
* Open edX API Thoughts wiki: https://openedx.atlassian.net/wiki/spaces/AC/pages/16646635/API+Thoughts
