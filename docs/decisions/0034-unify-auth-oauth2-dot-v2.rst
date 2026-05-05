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
   authentication mechanism for all API(external and internal) access**, per `OEP-0042`_
2. **Session authentication MAY be supported alongside** ``JwtAuthentication``
   on any endpoint — this is the platform default and is acceptable.
3. **``BearerAuthentication`` and ``BearerAuthenticationAllowInactiveUser`` are
   deprecated and MUST NOT be used in new code**
4. **``OAuth2Authentication`` and ``OAuth2AuthenticationAllowInactiveUser`` are
   deprecated aliases for** ``BearerAuthentication`` **and MUST NOT be used in new code**
5. **All new APIs MUST follow these authentication patterns based on use case**
6. **Existing APIs MUST be audited and updated to remove** ``BearerAuthentication``

Implementation requirements:

* All APIs: ``JwtAuthentication`` (+ ``SessionAuthentication`` where appropriate)
* ``BearerAuthentication`` / ``BearerAuthenticationAllowInactiveUser``: remove from all endpoints
* ``OAuth2Authentication`` / ``OAuth2AuthenticationAllowInactiveUser``: remove once external repos migrate

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

* **Example APIs (Keep supporting OAuth2 JWT token & session authentication and deprecate Bearer token)**

.. code-block:: python

   # lms/djangoapps/course_home_api/dates/views.py — target state (BearerAuth removed as per decision #3)
   from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
   from openedx.core.lib.api.authentication import SessionAuthenticationAllowInactiveUser
   from rest_framework.permissions import IsAuthenticated

   class DatesTabView(RetrieveAPIView):
       """Request details for the Dates Tab."""
       authentication_classes = (
           JwtAuthentication,
           SessionAuthenticationAllowInactiveUser,
       )
       permission_classes = (IsAuthenticated,)


* **Browser-first API (Session primary, JWT added & deprecate Bearer Auth):**


.. code-block:: python

   # lms/djangoapps/teams/views.py — target state (BearerAuth removed & add JWT authentication support)
   from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
   from openedx.core.lib.api.authentication import SessionAuthenticationAllowInactiveUser
   from rest_framework.permissions import IsAuthenticated

   class TeamsDashboardView(GenericAPIView):
       """View methods related to the teams dashboard."""
       authentication_classes = (
           SessionAuthenticationAllowInactiveUser,
           JwtAuthentication,
       )
       permission_classes = (IsAuthenticated,)

Implementation Notes
====================

* Supporting both ``JwtAuthentication`` and ``SessionAuthentication`` on the same
  endpoint is acceptable — this is already the platform default in
  ``openedx/envs/common.py`` (``DEFAULT_AUTHENTICATION_CLASSES``)
* The primary migration target is the ``view_auth_classes`` decorator — one change
  removes ``BearerAuthentication`` from 49+ endpoints
* Verify no active external clients are still sending Bearer tokens before
  removing ``BearerAuthentication`` from any endpoint
* ``JWT_AUTH_ADD_KID_HEADER`` toggle in ``openedx/core/djangoapps/oauth_dispatch/jwt.py``
  is past its removal date (target: 2024-04-20) — KID header should be made always-on
  and the toggle removed
* ``OAuth2Authentication`` / ``OAuth2AuthenticationAllowInactiveUser`` in
  ``openedx/core/lib/api/authentication.py`` are deprecated aliases that exist only
  to avoid breaking external repos — remove once those repos migrate to ``JwtAuthentication``

Rollout Plan
------------

1. Audit existing APIs and categorize — flag any using ``BearerAuthentication`` variants
2. Check client metrics for active Bearer token usage
3. Update ``view_auth_classes`` decorator to remove ``BearerAuthenticationAllowInactiveUser``
4. Mark ``BearerAuthentication`` / ``BearerAuthenticationAllowInactiveUser`` deprecated in source
5. Remove overdue ``JWT_AUTH_ADD_KID_HEADER`` toggle — make KID header always-on
6. Migrate external clients from Bearer tokens to JWT token flow
7. Remove ``BearerAuthentication`` and its ``OAuth2Authentication`` aliases once migration is complete

References
==========

* `OEP-0042`_ — Open edX Authentication Best Practices (primary reference)
* Django REST Framework - Authentication and permissions
* Django OAuth Toolkit documentation
* Open edX Authentication Patterns Guide

.. _OEP-0042: https://docs.openedx.org/projects/openedx-proposals/en/latest/best-practices/oep-0042-bp-authentication.html
