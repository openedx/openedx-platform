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
auto-discover endpoints, creates integration challenges for external
developers, and leads to the emergence of duplicate or overlapping endpoints.

Today, the documentation that does exist in the platform is largely produced
through `api-doc-tools <https://github.com/openedx/api-doc-tools>`_, an Open
edX-maintained shim over
`drf-yasg <https://github.com/axnsan12/drf-yasg>`_. ``api-doc-tools`` provides
simplified decorators (``@schema``, ``@schema_for``) that wrap
``@swagger_auto_schema``, helper functions such as ``parameter()`` for
declaring query/path parameters, and a configured schema view (typically
served at ``/api-docs.yaml`` and ``/api-docs``). Internally it emits OpenAPI
2.0 (Swagger 2.0).

``drf-yasg`` itself is in light maintenance mode and explicitly will not gain
OpenAPI 3.x support (per its upstream README). As a result, neither
``api-doc-tools`` nor ``drf-yasg`` is a viable long-term home for the
platform's API documentation.

Decision
--------

We will standardize all Open edX REST APIs to use **drf-spectacular** with
**@extend_schema decorators** for complete machine-readable documentation, and
we will deprecate ``api-doc-tools`` (and the platform's transitive dependency
on ``drf-yasg``) as part of the same effort.

Implementation requirements:

* Use drf-spectacular for all API endpoints with @extend_schema decorators.
* Document request/response schemas, status codes, and error conditions.
* Include comprehensive descriptions and examples for complex endpoints.
* Ensure all endpoints have machine-readable OpenAPI coverage.
* Maintain consistent documentation patterns across services.
* Migrate endpoints currently documented via ``api-doc-tools`` directly to
  ``@extend_schema`` rather than through a wrapper.

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
* Removes the platform's dependency on an unmaintained OpenAPI 2.0 library (``drf-yasg``).

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires initial effort to document existing endpoints.
* Ongoing maintenance to keep documentation in sync with code changes.
* Learning curve for teams unfamiliar with drf-spectacular decorators.
* Existing consumers of ``api-doc-tools`` decorators (``@schema``, ``@schema_for``, ``parameter()``) will need to be migrated to ``@extend_schema``.

Deprecation of api-doc-tools and drf-yasg
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As a direct consequence of this decision:

* ``api-doc-tools`` will be deprecated and, once it has no remaining consumers in the Open edX platform or community, archived.
* ``drf-yasg`` will be removed from the platform's dependency set as part of the same migration.
* During the transition window, ``api-doc-tools`` will be kept functional so that already-documented endpoints continue to render. New endpoints, however, must use ``drf-spectacular`` directly.

Alternatives Considered
-----------------------

* **Keep minimal documentation**: rejected due to poor discoverability and integration challenges.
* **Use separate documentation files**: rejected because inline decorators provide better maintainability.
* **Replace drf-yasg with drf-spectacular inside api-doc-tools**: rejected. The two libraries are not drop-in compatible at any meaningful layer; the wrapper would effectively need to be rewritten, and existing consumers would still require migration. See "Why we are not replacing drf-yasg inside api-doc-tools" below for the detailed analysis.

Why we are not replacing drf-yasg inside api-doc-tools
------------------------------------------------------

The most attractive alternative on the surface was to update
``api-doc-tools`` to use ``drf-spectacular`` under the hood instead of
``drf-yasg``, leaving existing ``@schema`` / ``@schema_for`` / ``parameter()``
call sites untouched. After investigation this path is not viable. The
specific incompatibilities, recorded here for future reference, are:

1. **Different OpenAPI specification versions.** ``drf-yasg`` only emits
   OpenAPI 2.0 (Swagger 2.0); ``drf-spectacular`` emits OpenAPI 3.0/3.1. The
   two output documents are structurally different, so any downstream consumer
   of the generated ``/api-docs.yaml`` (codegen tools, AI tooling, external
   integrators) would need to be updated regardless of whether the wrapper
   survives. ``drf-yasg``'s upstream documentation explicitly states that
   OpenAPI 3.x support will not be added.

2. **Different decorator APIs.** ``api-doc-tools``'s ``@schema`` and
   ``@schema_for`` wrap ``@swagger_auto_schema``. The ``drf-spectacular``
   equivalent is ``@extend_schema``, with different argument names (e.g.
   ``operation_description`` → ``description``), different ``summary`` vs.
   ``description`` semantics, and different supported keyword arguments
   (``exclude``, ``versions``, ``examples``, etc.). This is not a 1:1 rename —
   every wrapper call site would need to be re-translated.

3. **Different type and parameter primitives.** ``parameter()`` in
   ``api-doc-tools`` relies on ``drf_yasg.openapi.TYPE_*`` / ``FORMAT_*``
   constants and the ``drf_yasg.openapi.Schema`` class. ``drf-spectacular``
   uses the ``OpenApiTypes`` enum and plain Python types/``dict`` s, and its
   ``OpenApiParameter`` has a different shape (``format`` is folded into
   ``type``, ``many=True`` replaces drf-yasg's ``Items`` class, and
   ``IN_BODY`` / ``IN_FORM`` location constants have no direct equivalent —
   request bodies are handled via
   ``@extend_schema(request={"<media-type>": ...})``).

4. **Different docstring conventions.** ``drf-yasg`` treats the first line of
   a docstring as the operation ``summary`` and the remainder as the
   ``description``. ``drf-spectacular`` uses the entire docstring as the
   ``description`` and requires ``summary`` to be passed explicitly. Every
   docstring-documented endpoint in the platform would render differently
   after a silent backend swap.

5. **Different schema view, settings, and UI integration.** ``drf-yasg``
   exposes the schema via ``drf_yasg.views.get_schema_view`` and is
   configured through ``SWAGGER_SETTINGS`` / ``REDOC_SETTINGS``.
   ``drf-spectacular`` uses ``SpectacularAPIView`` / ``SpectacularSwaggerView``
   / ``SpectacularRedocView`` configured through ``SPECTACULAR_SETTINGS``. UI
   assets are also handled differently: ``drf-yasg`` ships Swagger UI and
   Redoc internally, whereas ``drf-spectacular`` serves them from a CDN or
   via the optional ``drf-spectacular-sidecar`` package.

6. **Different authentication scheme handling.** ``drf-yasg`` requires manual
   security scheme definitions. ``drf-spectacular`` auto-generates security
   definitions for built-in DRF authenticators and popular third-party
   packages, with ``OpenApiAuthenticationExtension`` as the hook for custom
   classes. Auth-related schema configuration in ``api-doc-tools`` consumers
   would need to be re-expressed.

7. **Different extension and customization architectures.** Custom schema
   generation in ``drf-yasg`` is done by subclassing
   ``OpenAPISchemaGenerator`` and ``SwaggerAutoSchema``. In
   ``drf-spectacular`` it is done via ``OpenApiSerializerExtension``,
   ``OpenApiSerializerFieldExtension``, ``OpenApiAuthenticationExtension``,
   etc. — different inheritance hierarchies and different hook signatures, so
   no custom generator code can be carried over unchanged.

8. **AutoSchema generation differs even on identical inputs.** Given the
   same DRF serializers and viewsets, the two libraries produce materially
   different schemas in practice — common discrepancies include handling of
   ``read_only`` / ``write_only`` fields, nullable fields, nested
   serializers, and custom ``@action`` endpoints. A drop-in engine swap would
   silently change generated documentation for every endpoint without any
   corresponding code change, which is unacceptable as an "invisible"
   upgrade.

Because of the above, swapping the engine inside ``api-doc-tools`` would not
save existing endpoints from migration, every consumer of ``@schema``,
``@schema_for``, ``parameter()``, and ``get_schema_view`` would still need to
change, while also locking the project into indefinitely maintaining a
wrapper aligned with ``drf-spectacular``'s evolving API. A direct migration
to ``drf-spectacular`` is therefore preferred.

Migration Plan for api-doc-tools consumers
------------------------------------------

Existing usages of ``api-doc-tools`` will be migrated to ``drf-spectacular``
directly, using the following mapping as a starting point:

* ``@schema(...)`` and ``@schema_for(...)`` → ``@extend_schema(...)``
* ``parameter(name, type, description)`` → ``OpenApiParameter(name=..., type=..., description=..., location=OpenApiParameter.QUERY|PATH|HEADER|COOKIE)``
* ``responses={status: SerializerOrString}`` → ``responses={status: OpenApiResponse(response=Serializer, description=...)}``
* ``get_schema_view`` from ``drf_yasg.views`` → ``SpectacularAPIView`` / ``SpectacularSwaggerView`` / ``SpectacularRedocView`` from ``drf_spectacular.views``
* ``SWAGGER_SETTINGS`` / ``REDOC_SETTINGS`` → ``SPECTACULAR_SETTINGS``

Approach:

1. Stand up ``drf-spectacular`` alongside the existing ``api-doc-tools``
   setup so both can co-exist during the transition.
2. Migrate endpoints incrementally, prioritizing high-traffic and
   externally-consumed APIs.
3. Once a service has no remaining ``api-doc-tools`` imports, remove the
   dependency from that service.
4. When no service in the platform depends on ``api-doc-tools``, deprecate
   the package on its own repository and schedule archival.

Rollout Plan
------------

1. Configure drf-spectacular across all Open edX services.
2. Create documentation templates and guidelines for common endpoint patterns.
3. Audit existing endpoints and prioritize high-impact APIs for documentation.
4. Implement automated testing to ensure schema completeness.
5. Set up continuous integration to validate documentation quality.
6. Publish and maintain OpenAPI specifications for all services.
7. Track and complete migration of ``api-doc-tools`` consumers per the
   migration plan above; remove ``api-doc-tools`` and ``drf-yasg`` from the
   dependency set once migration is complete.

References
----------

* Open edX REST API Standards: "API Documentation & Schema Coverage" recommendations for discoverability.
* drf-spectacular migration guide: https://drf-spectacular.readthedocs.io/en/stable/drf_yasg.html
* drf-yasg OpenAPI 3.0 status: https://drf-yasg.readthedocs.io/en/stable/readme.html
* api-doc-tools repository: https://github.com/openedx/api-doc-tools
