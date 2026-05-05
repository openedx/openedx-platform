========================================
Standardize Filtering/Sorting Parameters
========================================

:Status: Accepted
:Date: 2026-04-08
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Filtering/Sorting parameters standardization for consistency

Context
=======

Filtering and sorting syntax varies across Open edX APIs (e.g., inconsistent parameter names such as
``course_key`` vs ``course``). This forces clients to hardcode endpoint-specific logic and prevents
tooling/agents from reliably inferring query patterns.

Decision
========

1. Adopt ``django-filter`` for list endpoints requiring filtering.
2. Standardize parameter naming conventions (e.g., use ``course_key`` consistently) and document them.
3. Provide consistent sorting conventions:

   * Use a standard ``ordering`` parameter (DRF convention), with documented allowed fields.

4. Update schemas so filters and ordering are discoverable via OpenAPI.

Relevance in edx-platform
=========================

* **Existing usage**: ``django_filters`` is already used in several places:
  ``openedx/core/djangoapps/user_api/views.py`` (``DjangoFilterBackend``),
  ``lms/djangoapps/experiments/views.py`` and ``experiments/filters.py``
  (``ExperimentDataFilter``, ``ExperimentKeyValueFilter``),
  ``common/djangoapps/entitlements/rest_api/v1/views.py`` and
  ``entitlements/rest_api/v1/filters.py`` (``CourseEntitlementFilter`` with
  ``uuid``, ``user__username``, ``course_uuid``, ``expired_at__isnull``).
* **Inconsistency**: Parameter names and filter semantics vary across APIs
  (e.g. ``course_key`` vs ``course``); standardizing on ``course_key`` for course identifier strings (per `OEP-68`_) and
  a single ``ordering`` parameter aligns with this ADR.

Code examples
=============

**FilterSet (entitlements pattern in edx-platform):**

.. code-block:: python

   # common/djangoapps/entitlements/rest_api/v1/filters.py
   from django_filters import rest_framework as filters

   class CourseEntitlementFilter(filters.FilterSet):
       user = filters.CharFilter(field_name='user__username')
       course_uuid = UUIDListFilter(field_name='course_uuid')
       expired_at__isnull = filters.BooleanFilter(field_name='expired_at', lookup_expr='isnull')

       class Meta:
           model = CourseEntitlement
           fields = ('uuid', 'user', 'course_uuid')

**ViewSet with filter and ordering:**

.. code-block:: python

   from django_filters.rest_framework import DjangoFilterBackend
   from rest_framework.filters import OrderingFilter

   class CourseEntitlementViewSet(viewsets.ReadOnlyModelViewSet):
       queryset = CourseEntitlement.objects.all()
       filter_backends = (DjangoFilterBackend, OrderingFilter)
       filterset_class = CourseEntitlementFilter
       ordering_fields = ["created", "modified", "expired_at"]
       ordering = ["-created"]   # default

   # GET /api/entitlements/v1/?user=john&course_uuid=...&ordering=-modified

Consequences
============

* Pros

  * Predictable client implementation; easier SDK generation and AI discovery.
  * Reduced duplication across apps.

* Cons / Costs

  * Requires coordinated changes and backward-compatible aliases (temporary) for existing params.

Implementation Notes
====================

* Add filtersets per endpoint; expose allowed fields via schema generation.
* Provide deprecation warnings for old parameter names and remove after a defined window.
* Create migration guide for teams updating existing API clients.

Backward Compatibility Strategy
===============================

To ensure smooth transition for existing API consumers:

1. **Parameter Aliases**: Support old parameter names alongside new standardized names:

.. code-block:: python

   class CourseEntitlementFilter(filters.FilterSet):
       course = filters.CharFilter(field_name='course_uuid')  # old param
       course_key = filters.CharFilter(field_name='course_uuid')  # new param

2. **Deprecation Warnings**: Return HTTP headers warning about deprecated parameters:

  * Deprecation: Parameter 'course' is deprecated. Use 'course_key' instead.
  * Support will be removed in release '<release_name>'.

3. **Gradual Migration**: 

  * Phase 1: Support both old and new parameters with warnings
  * Phase 2: Remove old parameters after 2 release cycles
  * Phase 3: Enforce new parameter names only

4. **Documentation Updates**: Clearly mark deprecated parameters in OpenAPI schemas with `deprecated: true`.

**Why django-filter was chosen:**

  * **Mature ecosystem**: Well-maintained with extensive documentation
  * **DRF integration**: Seamless integration with Django REST Framework
  * **OpenAPI support**: Automatic schema generation for filters
  * **Feature completeness**: Supports complex filtering scenarios (lookups, OR conditions, etc.)
  * **Community adoption**: Widely used in Django ecosystem with good community support

References
==========

* `Open edX REST API Standardization Notes`_ — “Missing Filter/Sort Consistency” recommendation
* `OEP-68`_ — Content Identifiers (defines ``course_key`` naming convention)
* `django-filter documentation`_

.. _Open edX REST API Standardization Notes: https://openedx.atlassian.net/wiki/spaces/AC/pages/18350757/Open+edX+REST+API+Conventions#Conventions
.. _OEP-68: https://docs.openedx.org/projects/openedx-proposals/en/latest/best-practices/oep-0068-bp-content-identifiers.html#summary
.. _django-filter documentation: https://django-filter.readthedocs.io/
