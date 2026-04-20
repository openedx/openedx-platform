Standardize Authentication Patterns and Security Schemes
========================================================

:Status: Proposed
:Date: 2026-04-07
:Deciders: Open edX Platform / API Working Group
:Technical Story: Open edX REST API Standards - Consistent authentication patterns and security scheme usage

Context
=======

Open edX APIs have inconsistent authentication patterns and security scheme implementations:

* Multiple authentication mechanisms are enabled globally but not consistently applied
* OAuth2 and JWT are not separate mechanisms DOT issues JWTs as OAuth2 tokens,
  validated by ``JwtAuthentication``. The deprecated ``BearerAuthentication``
  handles old Bearer tokens and must not be confused with this.
* Security scheme declarations don't match actual authentication behavior
* External integrators cannot reliably predict which authentication method to use
* Internal APIs mix authentication mechanisms without clear patterns

This inconsistency creates confusion for:
- External developers determining which auth method to implement
- Internal teams maintaining consistent authentication patterns
- Security reviews and compliance assessments
- Automated tools expecting predictable authentication

The codebase has two JWT issuance paths, both validated by ``JwtAuthentication``:

* ``create_jwt_token_dict()`` — wraps a DOT OAuth2 access token into a JWT (DB-backed, revocable, for external clients)
* ``create_jwt_for_user()`` — issues a JWT directly with no OAuth2 flow and no DB row (non-revocable, for internal service communication)

Decision
========

1. **JWT authentication via** ``JwtAuthentication`` **MUST be the standard
   authentication mechanism for all API access** (external and internal), per `OEP-0042`_
2. **``BearerAuthentication`` and ``BearerAuthenticationAllowInactiveUser`` are
   deprecated and MUST NOT be used in new code**
3. **Session authentication MUST be used only for browser-based UI interactions**
4. **All new APIs MUST follow these authentication patterns based on use case**
5. **Existing APIs MUST be audited and updated to follow consistent patterns**

Implementation requirements:

* External APIs (public, partner integrations): ``JwtAuthentication`` only
* Internal APIs (service-to-service): ``JwtAuthentication`` only
* Browser-based APIs (UI interactions): Session only
* DRF authentication classes must match the intended use case
* No mixing of authentication mechanisms in single endpoints

Consequences
============

* Pros

  * Clear, predictable authentication patterns for different API use cases
  * Improved security through proper separation of auth mechanisms
  * Aligns with OEP-0042 — removes deprecated ``BearerAuthentication`` from active use
  * Easier integration for external developers (single standard: JWT)
  * Simplified internal service communication (same ``JwtAuthentication`` class)
  * Better browser experience (session-based auth)

* Cons / Costs

  * Existing APIs need audit and potential refactoring to match patterns
  * Teams need to understand and implement proper authentication choices(where to use JWT or session)
  * External clients still using Bearer tokens must migrate to JWT
  * Migration effort for services currently using mixed authentication

Relevance in edx-platform
=========================

* **OAuth2/DOT**: LMS uses Django OAuth Toolkit at ``/oauth2/``
  (``lms/urls.py``, ``openedx/core/djangoapps/oauth_dispatch``). Settings include
  ``OAUTH2_PROVIDER_APPLICATION_MODEL``, ``OAUTH2_VALIDATOR_CLASS`` (e.g.
  ``EdxOAuth2Validator``). DOT issues JWTs as access tokens via ``create_jwt_token_dict()``.
* **Current API auth**: ``openedx/core/lib/api/view_utils.view_auth_classes``
  configures both **JWT** and **Bearer** (deprecated) and session across 49+ files:

  .. code-block:: python

     # openedx/core/lib/api/view_utils.py (current — violates OEP-0042)
     func_or_class.authentication_classes = (
         JwtAuthentication,
         BearerAuthenticationAllowInactiveUser,  # deprecated per OEP-0042
         SessionAuthenticationAllowInactiveUser
     )

* **Bearer auth**: ``openedx/core/lib/api/authentication.py`` implements
  ``BearerAuthentication`` / ``BearerAuthenticationAllowInactiveUser`` using
  ``oauth2_provider`` (DOT) for access token validation. This is the deprecated path.

Code examples (authentication patterns by use case)
===================================================

* **External API (OAuth2 JWT — clients obtain token via OAuth2 flow at** ``/oauth2/`` **):**

.. code-block:: python

   from rest_framework import viewsets
   from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
   from rest_framework.permissions import IsAuthenticated

   class ExternalCourseViewSet(viewsets.ViewSet):
       """External API — OAuth2 JWT authentication. Send as: Authorization: JWT <token>"""
       authentication_classes = [JwtAuthentication]
       permission_classes = [IsAuthenticated]

* **Internal Service API (JWT issued via** ``create_jwt_for_user()`` **):**

.. code-block:: python

   from rest_framework import viewsets
   from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
   from rest_framework.permissions import IsAuthenticated

   class InternalServiceViewSet(viewsets.ViewSet):
       """Internal service-to-service API — same JwtAuthentication, different token issuance."""
       authentication_classes = [JwtAuthentication]
       permission_classes = [IsAuthenticated]

* **Browser-based API (Session only):**

.. code-block:: python

   from rest_framework import viewsets
   from openedx.core.lib.api.authentication import SessionAuthenticationAllowInactiveUser
   from rest_framework.permissions import IsAuthenticated

   class BrowserUIViewSet(viewsets.ViewSet):
       """Browser UI API - Session authentication only."""
       authentication_classes = [SessionAuthenticationAllowInactiveUser]
       permission_classes = [IsAuthenticated]

Implementation Notes
====================

* Audit existing APIs to identify authentication pattern violations
* The primary migration target is the ``view_auth_classes`` decorator — one change
  removes ``BearerAuthentication`` from 49+ endpoints
* Verify no active external clients are still sending Bearer tokens before
  removing ``BearerAuthentication`` from any endpoint
* Provide migration guidance for APIs currently using mixed authentication
* Document authentication patterns for development teams

Rollout Plan
------------

1. Audit existing APIs and categorize by intended use case (external/internal/browser)
2. Check client metrics for active Bearer token usage
3. Update ``view_auth_classes`` decorator to remove ``BearerAuthenticationAllowInactiveUser``
4. Mark ``BearerAuthentication`` / ``BearerAuthenticationAllowInactiveUser`` deprecated in source
5. Refactor high-priority APIs to follow single-authentication patterns
6. Migrate external clients from Bearer tokens to JWT token flow
7. Remove ``BearerAuthentication`` classes once client migration is confirmed complete

References
==========

* `OEP-0042`_ — Open edX Authentication Best Practices (primary reference)
* Django REST Framework - Authentication and permissions
* Django OAuth Toolkit documentation
* Open edX Authentication Patterns Guide

.. _OEP-0042: https://docs.openedx.org/projects/openedx-proposals/en/latest/best-practices/oep-0042-bp-authentication.html
