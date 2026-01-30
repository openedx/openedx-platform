Enrollment API v2 Specification
--------------------------------

Status
======

**Draft**

This ADR will move to **Provisional** status once the OpenAPI specification is approved and implementation begins. It will move to **Accepted** status once the API is fully implemented and deployed.

Context
=======

The existing enrollment API (v1) has several limitations that make it difficult to use in modern applications. A new v2 enrollment API is needed to support the instructor dashboard MFE migration and other enrollment management use cases across the platform. The current implementation provides enrollment operations (enroll, unenroll, list enrollments) through legacy endpoints in ``lms/djangoapps/instructor/enrollment.py`` and the v1 enrollment API at ``/api/enrollment/v1/``.

Decisions
=========

#. **RESTful Resource-Oriented Design**

   Use resource-oriented URLs: ``/api/enrollment/v2/courses/{course_key}/enrollments``

   Use appropriate HTTP methods per Open edX REST API Conventions:

   * ``GET`` for read operations (list enrollments, get enrollment details)
   * ``POST`` for enrollments (enroll one or more learners)
   * ``DELETE`` for unenrollments (unenroll a single learner)

#. **Synchronous vs Asynchronous Execution**

   * Operations targeting a single learner execute synchronously and return ``200 OK``
     with immediate results (< 5s typical, typically 100-500ms)
   * Operations targeting multiple learners queue a background task and return
     ``202 Accepted`` with task tracking information
   * Task monitoring uses shared Task API endpoint:
     ``GET /api/enrollment/v2/courses/{course_key}/tasks/{task_id}``
     (defined in separate Task API specification)

#. **Enrollment State Model**

   * Support both active enrollments (``CourseEnrollment``) and pre-enrollments
     (``CourseEnrollmentAllowed``)
   * Track enrollment state transitions with before/after snapshots
   * Handle cases where user doesn't exist yet (creates CourseEnrollmentAllowed)
   * Support auto-enrollment upon user registration
   * Support multiple enrollment modes (audit, honor, verified, professional, etc.)

#. **Pagination and Performance**

   * Use DRF standard pagination format with ``next``, ``previous``, ``count``,
     ``num_pages``, and ``results`` fields (not nested pagination)
   * Default page size of 25, maximum of 100 per page
   * 1-indexed page numbers for consistency with DRF defaults
   * Return basic enrollment data by default to optimize performance

#. **Optional Fields via requested_fields Parameter**

   * Support ``requested_fields`` query parameter per Open edX conventions
   * Available optional fields: ``beta_tester``, ``profile_image``
   * Comma-delimited list format: ``?requested_fields=beta_tester,profile_image``
   * Reduces database queries and improves performance when optional data not needed

#. **Authentication and Authorization**

   * Support both OAuth2 (for mobile clients and micro-services) and
     Session-based authentication (for mobile webviews and browser clients)
   * Require appropriate permissions based on operation scope:

     * Course staff or instructor: Can manage enrollments within their courses
     * Global staff: Can manage enrollments across all courses
     * Self-enrollment: Learners can enroll/unenroll themselves (future consideration)

   * Follow separation of filtering and authorization (explicit filtering in URLs)

#. **Error Handling**

   * Follow Open edX REST API Conventions error format
   * Include ``error_code`` (machine-readable), ``developer_message``,
     ``user_message`` (internationalized), and ``status_code``
   * Support ``field_errors`` object for field-specific validation errors
   * Use appropriate HTTP status codes: 200, 202, 400, 401, 403, 404

#. **Date/Time Serialization**

   * Serialize all dates and timestamps to ISO 8601 format with explicit timezone offsets
   * Prefer UTC timestamps
   * Example format: ``2024-01-15T10:30:00Z``

#. **Email Notifications**

   * Support optional email notifications via ``email_students`` parameter
   * Use different message types based on user state:

     * ``enrolled_enroll``: User already registered, being enrolled
     * ``allowed_enroll``: User not yet registered, pre-enrollment created
     * ``enrolled_unenroll``: User being unenrolled
     * ``allowed_unenroll``: Pre-enrollment being removed

   * Support optional ``reason`` parameter included in notification emails

#. **OpenAPI Specification**

   Maintain an OpenAPI specification at ``../references/enrollment-v2-api-spec.yaml``
   to guide implementation. This static specification serves as a reference during development,
   but ``/api-docs/`` is the source of truth for what is actually deployed. Once implementation
   is complete and the endpoints are live in ``/api-docs/``, the static spec file will be
   deleted to avoid maintaining outdated documentation.

Consequences
============

Positive
~~~~~~~~

* Consistent URL patterns following Open edX conventions make the API predictable
* Explicit sync/async behavior based on operation scope allows proper UI feedback
* Pagination support efficiently handles courses with thousands of enrollments
* Optional fields optimize performance by avoiding unnecessary database queries
* OpenAPI specification enables automated validation, testing, and type-safe client generation
* Resource-oriented design makes it easy to add new operations
* Support for both enrollments and pre-enrollments handles all use cases
* Before/after state tracking provides clear audit trail of changes
* Email notification support maintains current functionality for learner communication

Negative
~~~~~~~~

* Existing clients using legacy enrollment endpoints need to be updated
* Dual maintenance during transition period
* Developers familiar with legacy endpoints need to learn new patterns
* Optional fields via ``requested_fields`` add complexity to serialization logic
* Async operations require additional task monitoring implementation

Alternatives Considered
=======================

#. **Separate Endpoints for Enroll/Unenroll**

   Considered ``POST /enrollments`` for enroll and ``POST /unenrollments`` for unenroll,
   but using ``DELETE /enrollments/{id}`` is more RESTful and follows HTTP verb semantics.

#. **Nested Pagination Format**

   Considered nesting pagination metadata under a ``pagination`` key (per Cliff Dyer's
   proposal), but chose DRF standard flat format (``next``, ``previous``, ``count``,
   ``num_pages``, ``results`` at top level) as it's the established convention
   documented in Open edX REST API Conventions.

#. **Expand Parameter Instead of requested_fields**

   Considered using ``expand`` parameter for related objects, but ``requested_fields``
   is more appropriate for optional fields that are not separate resources. Using
   ``expand`` would imply these are related resources with their own endpoints,
   which is not the case for beta tester status or profile images in this context.

References
==========

* OpenAPI Specification: ``../references/enrollment-v2-api-spec.yaml``
* Live API Documentation: ``/api-docs/``
* Existing v1 Enrollment API: ``https://master.openedx.io/api-docs/#/enrollment``
* Legacy Implementation: ``lms/djangoapps/instructor/enrollment.py``
* `Open edX REST API Conventions <https://openedx.atlassian.net/wiki/spaces/AC/pages/18350757/Open+edX+REST+API+Conventions>`
* `Optional Fields and API Versioning: https://openedx.atlassian.net/wiki/spaces/AC/pages/40862782/Optional+Fields+and+API+Versioning`
