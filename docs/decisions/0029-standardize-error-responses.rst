Standardize Error Responses
============================

:Status: Accepted
:Date: 2026-03-31
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards – Error response interoperability

Context
-------

Open edX APIs currently return errors in multiple incompatible shapes (e.g., ``{"error": ...}``,
``{"detail": ...}``, nested field errors, and even HTTP 200 responses containing ``"success": false``). This
inconsistency makes it difficult for external clients and AI systems to reliably detect and map error
states across services.

Objectives
----------

We want error responses that:

* Use **correct HTTP status codes** (4xx/5xx) for failures, and avoid masking errors behind HTTP 200.
* Provide a **single, predictable JSON shape** so clients can implement one parsing path across services.
* Include **machine-readable identifiers** (e.g. a URI for the error class) so tools and integrations can
  classify failures without scraping free-form text.
* Carry a **short human-readable summary** plus a **specific explanation** for this request when helpful.
* Tie errors to the **request** when useful (e.g. request path or URL) for support and logging.
* Represent **validation failures** in a consistent way (e.g. field/path to messages) instead of ad-hoc nesting.
* Are **documented and enforced** in DRF (central exception handling + schema generation).

Decision
--------

We will standardize all Open edX REST APIs to return errors using a **structured JSON error object** for
non-2xx responses that meets the objectives above.

Implementation requirements:

* Use appropriate HTTP status codes (4xx/5xx). Avoid returning HTTP 200 for error conditions.
* Return a consistent payload with these core fields:

  * ``type`` (URI identifying the problem type)
  * ``title`` (short, developer/operator-facing summary of the error class; not intended for display to end users)
  * ``status`` (HTTP status code)
  * ``detail`` (stable, developer-facing explanation specific to this occurrence; safe for log
    aggregators and APM tools — see *Note on RFC 9457 deviation* below)
  * ``instance`` (the URI of the request that produced this error, e.g. the request path; see
    *Note on ``instance``* below)
  * ``user_message`` *(optional)* — a human-readable, translatable string intended for
    display in MFEs or end-user UIs. MFE clients should prefer mapping the ``type`` URI to a
    locally-translated string; use ``user_message`` when the server must supply context that cannot
    be expressed by ``type`` alone.

* For validation errors, include a predictable extension member ``errors``: a dict mapping each
  invalid field/path to a list of error message strings. This maps directly onto DRF's native
  ``ValidationError.detail`` dict, so the central exception handler can populate it without
  per-view changes. Example::

    "errors": {
        "course_id": ["This field is required."],
        "display_name": ["Ensure this field has no more than 255 characters."]
    }

* Define a small catalog of common ``type`` URIs for shared errors. Initial entries:

  .. list-table::
     :header-rows: 1
     :widths: 50 10 40

     * - URI
       - Status
       - When to use
     * - ``https://docs.openedx.org/errors/not-found``
       - 404
       - Resource does not exist
     * - ``https://docs.openedx.org/errors/authz``
       - 403
       - Authenticated but not authorized
     * - ``https://docs.openedx.org/errors/authn``
       - 401
       - Not authenticated
     * - ``https://docs.openedx.org/errors/validation``
       - 400
       - Request body / query-param validation failure
     * - ``https://docs.openedx.org/errors/rate-limited``
       - 429
       - Rate limit exceeded
     * - ``https://docs.openedx.org/errors/internal``
       - 500
       - Unexpected server error

  App-specific types may extend this catalog; they must still be absolute URIs.

  While many catalog entries map 1-to-1 with an HTTP status code, ``type`` provides
  sub-category granularity that HTTP status alone cannot express (e.g. ``authn`` vs
  ``authz`` vs ``validation`` vs ``not-found`` are all 4xx but represent distinct failure
  classes). App-specific ``type`` extensions add even finer-grained identifiers (e.g.
  ``https://docs.openedx.org/errors/enrollment/already-enrolled``). The ``status`` field is
  a convenience duplicate for clients that triage responses by status code without
  inspecting the body further.

  These URIs serve as **opaque, stable identifiers** first. They *should* eventually resolve to
  human-readable documentation pages on ``docs.openedx.org`` describing the error class, its
  causes, and remediation steps — but dereference-ability is not a requirement for the initial
  rollout. Clients must treat ``type`` as an opaque string and never rely on HTTP-fetching it at
  runtime.
* Error responses must respect the content type signalled by the request. The platform must not
  produce HTML error pages when the request used JSON (i.e. when ``Content-Type: application/json``
  or ``Accept: application/json`` was sent). The platform-level DRF exception handler must catch
  exceptions that would otherwise produce Django's default HTML error page and return a JSON body
  in the standardized format instead. Endpoints not using DRF's ``APIView`` must be identified and
  wrapped accordingly.
* For **5xx / unhandled exceptions** in **production** (``DEBUG=False``), the handler must return
  a **generic error body** — no stack traces, no internal exception messages, and no sensitive
  system details must be included in the response. Only the ``https://docs.openedx.org/errors/internal``
  ``type`` and a fixed ``"Internal Server Error"`` title are safe to return. Detailed diagnostics
  belong in server-side logs and APM tooling, not in API responses.

  In **development** (``DEBUG=True``), the handler MAY include additional diagnostic information
  (e.g. the exception class and message) in an extension field (e.g. ``debug_detail``) to ease
  local debugging. Stack traces should still be written to the server log regardless of mode.
* Preserve **CORS headers** on error responses. When the exception handler short-circuits the
  normal response cycle, ``Access-Control-*`` headers set by ``django-cors-headers`` can be
  dropped, causing browsers to surface a misleading CORS error rather than the actual error
  body. The platform-level exception handler must ensure CORS headers are not stripped from
  error responses.
* Ensure the schema is **documented in drf-spectacular** by registering the standardized error
  shape as a reusable component (``#/components/schemas/ErrorResponse``), so all API endpoint
  docs automatically reference it for 4xx/5xx response types.

Note on RFC 9457 deviation
~~~~~~~~~~~~~~~~~~~~~~~~~~

`RFC 9457 <https://www.rfc-editor.org/rfc/rfc9457>`_ (Problem Details for HTTP APIs) defines
``detail`` as a "human-readable explanation" intended for the client/end-user. This ADR
intentionally deviates from that definition: we use ``detail`` for a **stable, developer-facing,
English-language** string that is safe to forward to APM systems and log aggregators. User-facing
copy is carried in the separate ``user_message`` field instead. This separation keeps localizable,
UI-bound strings out of the machine-readable layer while still providing a meaningful explanation
for developers and on-call engineers.

Note on ``instance``
~~~~~~~~~~~~~~~~~~~~

The ``instance`` field in this ADR is the **path of the request that produced the error** (e.g.
``request.path``, yielding ``/api/courses/v1/``). A path-only value is preferred over a full
absolute URL (``request.build_absolute_uri()``) because it is useful for correlation and support
without embedding the server hostname or protocol, which can vary across environments. RFC 9457
permits ``instance`` to be either relative or absolute and does not require it to be
dereferenceable; using the request path is a valid application of the field.

Relevance in edx-platform
-------------------------

Current error shapes in the codebase are inconsistent:

* **DeveloperErrorViewMixin** (``openedx/core/lib/api/view_utils.py``) returns
  ``{"developer_message": "...", "error_code": "..."}`` and for validation
  ``{"developer_message": "...", "field_errors": {field: {"developer_message": "..."}}}``.
* **Instructor API** (``lms/djangoapps/instructor/views/api.py``) uses
  ``JsonResponse({"error": msg}, 400)``.
* **Registration** (``openedx/core/djangoapps/user_authn/views/register.py``) returns
  HTTP 200 with ``success: true/false`` and ``error_code`` for some failures.
* **ORA Staff Grader** (``lms/djangoapps/ora_staff_grader/errors.py``) uses a custom
  ``ErrorSerializer`` with an ``error`` field.
* **Enrollment API** (``openedx/core/djangoapps/enrollments/``) returns
  ``{"message": "..."}`` or ``{"message": "...", "localizedMessage": "..."}`` for errors.

Code example (target shape)
---------------------------

**Example structured error response (4xx):**

.. code-block:: json

   {
     "type": "https://docs.openedx.org/errors/validation",
     "title": "Validation Error",
     "status": 400,
     "detail": "The request body failed validation.",
     "user_message": "Some required fields are missing or invalid.",
     "instance": "/api/courses/v1/",
     "errors": {
       "course_id": ["This field is required."],
       "display_name": ["Ensure this field has no more than 255 characters."]
     }
   }

**Attaching a** ``user_message`` **to an exception:**

Because ``user_message`` is detected via ``hasattr``, it can be set on any ``APIException``
instance before raising — no subclass required:

.. code-block:: python

   from django.utils.translation import gettext_lazy as _
   from rest_framework.exceptions import APIException

   exc = APIException("Enrollment limit reached for course-v1:edX+DemoX+Demo_Course.")
   exc.user_message = _("This course is currently full. Please try again later.")
   raise exc

The central exception handler's ``hasattr(exc, 'user_message')`` check picks this up
automatically, requiring no per-view changes.

**Example DRF exception handler emitting the standard shape:**

.. code-block:: python

   # Central exception handler (e.g. in openedx/core/lib/api/exceptions.py)
   def standardized_error_exception_handler(exc, context):
       from rest_framework.views import exception_handler
       response = exception_handler(exc, context)
       if response is None:
           # DRF returned None — unhandled exception (e.g. IntegrityError, unexpected 5xx).
           # Always return a generic body; never include stack traces or exception details.
           return Response(
               {
                   "type": "https://docs.openedx.org/errors/internal",
                   "title": "Internal Server Error",
                   "status": 500,
                   "detail": "An unexpected error occurred. Please try again later.",
               },
               status=500,
           )
       request = context.get("request")
       body = {
           "type": f"https://docs.openedx.org/errors/{_error_type(exc)}",
           "title": _error_title(exc),
           "status": response.status_code,
           "detail": _flatten_detail(response.data),
       }
       if request:
           body["instance"] = request.path
       if hasattr(exc, "user_message") and exc.user_message:
           body["user_message"] = exc.user_message
       if isinstance(exc, ValidationError) and hasattr(exc, "detail"):
           body["errors"] = _normalize_validation_errors(exc.detail)
       response.data = body
       response["Content-Type"] = "application/json"
       return response

Consequences
------------

Positive
~~~~~~~~

* Clients can implement a single error-handling path across services.
* AI agents and external integrations can programmatically detect and classify error states.
* Removes "hidden failures" caused by HTTP 200 + ``success: false`` patterns.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires refactoring of existing endpoints and tests that currently depend on ad-hoc error shapes.
* Some clients may need a migration period if they parse legacy error formats.

Alternatives Considered
-----------------------

* **Keep per-app formats**: rejected due to interoperability and client complexity.
* **Use DRF defaults only**: rejected because DRF defaults still vary across validation/auth exceptions
  unless centrally handled and documented.
* **`drf-standardized-errors <https://github.com/ghazi-git/drf-standardized-errors>`_**: a well-maintained
  third-party library that implements RFC 9457-style responses for DRF. Considered but not adopted
  because: (a) it would add a new dependency to platform core, (b) we need custom behavior for CORS
  header preservation and the non-``APIView`` 500 path that would require overriding most of the
  library anyway, and (c) the contract defined here is lightweight enough to implement directly in
  the platform exception handler without a library.

Rollout Plan
------------

Error response format changes are considered backwards-compatible: well-behaved clients should
handle unexpected JSON fields gracefully (robustness principle). The default migration path is
therefore **in-place** — update the exception handler and, where needed, individual views without
bumping the URL version. Teams with clients that are tightly coupled to a legacy error shape MAY
version their endpoint following ADR-0037 (API Versioning Strategy) and maintain both shapes
during a deprecation window.

1. Introduce a shared DRF exception handler (platform-level) that emits the standardized error shape,
   including catching unhandled exceptions that would otherwise produce Django's HTML 500 page.
2. Verify CORS headers (``Access-Control-*``) are preserved on all error responses; update the
   exception handler if ``django-cors-headers`` does not run before it.
3. Update existing endpoint unit tests to assert the standardized error shape. Contract tests
   across services are optional but encouraged for endpoints consumed by external clients.
4. Audit and fix endpoints that still return HTML errors on 500 (e.g. non-``APIView`` entry points).
5. Migrate apps module-by-module; keep a short deprecation window for legacy shapes where feasible.
6. Update API documentation to specify the standard error schema.

References
----------

* Open edX REST API Standards: "Inconsistent Error Response Structure" and alignment with structured,
  interoperable error payloads across services.
* `RFC 9457 – Problem Details for HTTP APIs <https://www.rfc-editor.org/rfc/rfc9457>`_
* `drf-standardized-errors <https://github.com/ghazi-git/drf-standardized-errors>`_
