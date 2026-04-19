Standardize API Documentation & Schema Coverage
=================================================================

:Status: Proposed
:Date: 2026-03-18
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Documentation standardization for discoverability

Context
-------

Many Open edX views lack proper OpenAPI schema decorators and machine-readable
documentation. This makes it difficult for AI and external tools to
auto-discover endpoints, creates integration challenges for external developers,
and leads to the emergence of duplicate or overlapping endpoints.

The platform currently uses `api-doc-tools`_ (``edx-api-doc-tools``), an
Open edX-maintained library that wraps `drf-yasg`_ and exposes the ``@schema``,
``@schema_for``, ``parameter``, ``query_parameter``, and ``path_parameter``
decorators, along with URL helpers (``make_api_info`` / ``make_docs_urls``) that
serve a Swagger UI at ``/api-docs``. drf-yasg only targets OpenAPI 2.0 and is
largely unmaintained upstream. Replacing drf-yasg inside api-doc-tools with
drf-spectacular was investigated and found infeasible due to the significant
divergence in their decorator contracts. Direct adoption of drf-spectacular is
therefore the right path forward.

.. _api-doc-tools: https://github.com/openedx/api-doc-tools/
.. _drf-yasg: https://github.com/axnsan12/drf-yasg

Decision
--------

We will standardize all Open edX REST APIs to use **drf-spectacular** with **@extend_schema decorators** for complete machine-readable documentation.

Implementation requirements:

* Use drf-spectacular for all API endpoints with @extend_schema decorators.
* Document request/response schemas, status codes, and error conditions.
* Include comprehensive descriptions and examples for complex endpoints.
* Ensure all endpoints have machine-readable OpenAPI coverage.
* Maintain consistent documentation patterns across services.

Relevance in edx-platform
-------------------------

Current patterns that should be migrated:

* **Discussion topics API** (``^v0/course/{settings.COURSE_KEY_PATTERN}/sync_discussion_topics$``) lacks OpenAPI-compliant schema and has incomplete documentation.
* **Course content APIs** have missing or incomplete schema definitions.
* **User management endpoints** lack proper request/response documentation.

Code example (target documentation usage)
-----------------------------------------

**Example APIView with comprehensive drf-spectacular documentation:**

.. code-block:: python

   # views.py
   from drf_spectacular.utils import extend_schema, OpenApiRequest, OpenApiResponse
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from .serializers import TopicSerializer

   class ExampleTopicAPIView(APIView):
       """
       API endpoint for managing discussion topics.
       """

       @extend_schema(
           summary="List discussion topics",
           description="Returns a paginated list of discussion topics for the specified course.",
           responses={
               200: OpenApiResponse(
                   response=TopicSerializer(many=True),
                   description="List of discussion topics retrieved successfully"
               ),
               400: OpenApiResponse(
                   description="Bad request - invalid parameters"
               ),
               403: OpenApiResponse(
                   description="Permission denied - user lacks access"
               ),
               404: OpenApiResponse(
                   description="Course not found"
               ),
           },
           parameters=[
               {
                   "name": "course_id",
                   "in": "path",
                   "required": True,
                   "schema": {"type": "string"},
                   "description": "Course identifier"
               }
           ]
       )
       def get(self, request):
           """Retrieve discussion topics for the current course."""
           return Response({"results": []})

       @extend_schema(
           summary="Create discussion topic",
           description="Creates a new discussion topic in the specified course.",
           request=OpenApiRequest(request=TopicSerializer),
           responses={
               201: OpenApiResponse(
                   response=TopicSerializer,
                   description="Discussion topic created successfully"
               ),
               400: OpenApiResponse(
                   description="Bad request - invalid data"
               ),
               403: OpenApiResponse(
                   description="Permission denied"
               ),
           }
       )
       def post(self, request):
           """Create a new discussion topic."""
           return Response({"detail": "Topic created"}, status=201)

Consequences
------------

Positive
~~~~~~~~

* Provides machine-readable schemas for external systems integrations.
* Improves developer experience through standardized OpenAPI documentation.
* Enables automatic client SDK generation.
* Reduces duplicate endpoints through better discoverability.
* Facilitates API testing and validation.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires initial effort to document existing endpoints.
* Ongoing maintenance to keep documentation in sync with code changes.
* Learning curve for teams unfamiliar with drf-spectacular decorators.
* **api-doc-tools and drf-yasg will be deprecated and archived.** Existing
  usages across edx-platform and downstream services must be migrated. See
  the migration guide below.

Alternatives Considered
-----------------------

* **Keep minimal documentation**: rejected due to poor discoverability and integration challenges.
* **Use separate documentation files**: rejected because inline decorators provide better maintainability.
* **Update api-doc-tools to use drf-spectacular internally**: investigated and
  found infeasible — the decorator contracts of the two libraries diverge
  significantly, making a compatibility shim as costly as a direct migration.

Migration: api-doc-tools to drf-spectacular
--------------------------------------------

**What is api-doc-tools?**
``api-doc-tools`` is the current Open edX documentation library, built on top of
``drf-yasg``. It provides the ``@schema`` / ``@schema_for`` decorators and the
``make_docs_urls`` URL helper used across edx-platform today.

**Decorator mapping:**

+----------------------------------------------+-------------------------------------------------------+
| api-doc-tools (old)                          | drf-spectacular (new)                                 |
+==============================================+=======================================================+
| ``@schema(parameters=[...], responses={...})``| ``@extend_schema(parameters=[...], responses={...})`` |
+----------------------------------------------+-------------------------------------------------------+
| ``@schema_for("list", "docstring", ...)``    | ``@extend_schema_view(list=extend_schema(...))``      |
+----------------------------------------------+-------------------------------------------------------+
| ``parameter(name, type, desc)``              | ``OpenApiParameter(name, type, desc)``                |
+----------------------------------------------+-------------------------------------------------------+
| ``query_parameter(name, type, desc)``        | ``OpenApiParameter(name, type, desc,``                |
|                                              | ``location=OpenApiParameter.QUERY)``                  |
+----------------------------------------------+-------------------------------------------------------+
| ``path_parameter(name, type, desc)``         | ``OpenApiParameter(name, type, desc,``                |
|                                              | ``location=OpenApiParameter.PATH)``                   |
+----------------------------------------------+-------------------------------------------------------+
| ``make_docs_urls(make_api_info(...))``        | ``SpectacularAPIView`` + ``SpectacularSwaggerView``   |
+----------------------------------------------+-------------------------------------------------------+

Endpoints should be migrated incrementally. New endpoints must use
drf-spectacular directly. Once all usages are migrated, ``edx-api-doc-tools``
and ``drf-yasg`` will be removed from requirements and the ``api-doc-tools``
repository will be archived.

Rollout Plan
------------

1. Configure drf-spectacular across all Open edX services.
2. Create documentation templates and guidelines for common endpoint patterns.
3. Audit existing endpoints and prioritize high-impact APIs for documentation.
4. Implement automated testing to ensure schema completeness.
5. Set up continuous integration to validate documentation quality.
6. Publish and maintain OpenAPI specifications for all services.
7. Once migration is complete, archive ``api-doc-tools`` and remove ``drf-yasg``
   from service requirements.

References
----------

* Open edX REST API Standards: "API Documentation & Schema Coverage" recommendations for discoverability.
* `api-doc-tools repository <https://github.com/openedx/api-doc-tools/>`_
* `drf-spectacular documentation <https://drf-spectacular.readthedocs.io/>`_