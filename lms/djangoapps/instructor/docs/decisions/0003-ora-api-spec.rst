Instructor Dashboard – Open Response Assessment (ORA) API Specification
======================================================================

Status
======
Accepted

Context
=======

The Instructor Dashboard is being migrated to a Micro-Frontend (MFE) architecture,
which requires stable, well-defined, and RESTful API endpoints.

The existing Open Response Assessment (ORA) functionality exposes summary and
detailed assessment data through legacy endpoints that are tightly coupled to
server-rendered views. These endpoints do not meet the requirements for MFE
consumption, including consistent URL patterns, centralized permission handling,
and standardized API documentation.

Additionally, ORA functionality is implemented in the edx-ora2 library, which
is maintained as a separate Django app from the main platform. Historically,
Instructor Dashboard APIs have been implemented directly in the platform and
coupled to ORA data sources, rather than being owned by or exposed directly from
edx-ora2. While edx-ora2 is expected to be installed in the platform, this
cross-app coupling is not an ideal long-term pattern.

As part of this work, the new versioned ORA APIs will continue to rely on the
existing OraAggregateData interface to retrieve assessment data. This ensures
consistency with current ORA behavior and avoids introducing tighter coupling or
duplicate business logic as part of this migration.

Refactoring or relocating these REST endpoints into the edx-ora2 library
itself—so that the Instructor Dashboard consumes them as an external dependency—
is acknowledged as a potential future improvement. However, this architectural
change is explicitly out of scope for the current effort.

To support the MFE migration in the short term, this work introduces a new
versioned ORA API that follows RESTful principles and aligns with existing
Instructor v2 APIs, while preserving current data access patterns and interfaces.

Decisions
=========

1. RESTful Resource-Oriented Design
----------------------------------

Introduce a versioned API under ``/api/instructor/v2/`` using resource-oriented
URLs and clear HTTP semantics.

**Summary endpoint**

.. code-block:: http

   GET /api/instructor/v2/courses/{course_key}/ora/summary/

Returns aggregated ORA statistics for a course, including:

- Total units
- Total assessments
- Total responses
- Counts by assessment type (training, peer, self, waiting, staff)
- Number of final grades received

**Assessments list endpoint**

.. code-block:: http

   GET /api/instructor/v2/courses/{course_key}/ora/

Returns a collection of Open Response Assessments (ORAs) for the specified course.
Each item in the response represents a single assessment and includes
per-assessment metrics such as response counts, grading progress, and assessment
state.

2. Permissions Aligned with Instructor Dashboard
------------------------------------

The permission checks for these new API endpoints align with existing
Instructor Dashboard permissions:
- Only users with instructor or staff roles for the course can access these endpoints.

3. Serializer-Based Business Logic
---------------------------------

Use Django REST Framework serializers (``ORASummarySerializer`` and
``ORASerializer``) to encapsulate business logic for data retrieval and formatting.

Views remain thin and focused on request handling.

4. OpenAPI Specification
------------------------

Maintain an OpenAPI specification at ``../references/instructor-v2-ora-api-spec.yaml`` to guide implementation. 
This static specification serves as a reference during development, but ``/api-docs/`` is the source of truth for what is actually deployed. 
Once implementation is complete and the endpoints are live in ``/api-docs/``, the static spec file will be deleted to avoid maintaining outdated documentation.

Consequences
============

- Reduced the complexity of client-side code by providing structured data
  tailored for MFE consumption
- Centralized business logic ensures consistent permission checks and data
  formatting
- Simplified client-side logic for the Instructor Dashboard MFE
- OpenAPI documentation enables type-safe client generation and easier
  integration

References
==========

- OpenAPI Specification: ``../references/instructor-v2-ora-api-spec.yaml``
- Live API Documentation: ``/api-docs/``
