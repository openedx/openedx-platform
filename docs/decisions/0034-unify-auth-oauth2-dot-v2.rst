Standardize Authentication Patterns and Security Schemes
========================================================

:Status: Proposed
:Date: 2026-04-07
:Deciders: Open edX Platform / API Working Group
:Technical Story: Open edX REST API Standards - Consistent authentication patterns and security scheme usage

Context
=======

Open edX APIs have inconsistent authentication patterns and security scheme implementations:

* Some APIs use OAuth2, others use JWT, some use session authentication
* Multiple authentication mechanisms are enabled globally but not consistently applied
* Security scheme declarations don't match actual authentication behavior
* External integrators cannot reliably predict which authentication method to use
* Internal APIs mix authentication mechanisms without clear patterns

This inconsistency creates confusion for:
- External developers determining which auth method to implement
- Internal teams maintaining consistent authentication patterns
- Security reviews and compliance assessments
- Automated tools expecting predictable authentication

Decision
========

1. **OAuth2 via Django OAuth Toolkit (DOT) MUST be the standard authentication
   mechanism for external API access**
2. **JWT authentication MUST be used only for internal service-to-service communication**
3. **Session authentication MUST be used only for browser-based UI interactions**
4. **All new APIs MUST follow these authentication patterns based on use case**
5. **Existing APIs MUST be audited and updated to follow consistent patterns**

Implementation requirements:

* External APIs (public, partner integrations): OAuth2 only
* Internal APIs (service-to-service): JWT only  
* Browser-based APIs (UI interactions): Session only
* DRF authentication classes must match the intended use case
* No mixing of authentication mechanisms in single endpoints

Consequences
============

* Pros

  * Clear, predictable authentication patterns for different API use cases
  * Improved security through proper separation of auth mechanisms
  * Easier integration for external developers (OAuth2 standard)
  * Simplified internal service communication (JWT)
  * Better browser experience (session-based auth)

* Cons / Costs

  * Existing APIs need audit and potential refactoring to match patterns
  * Teams need to understand and implement proper authentication choices
  * Some APIs may need to be split or redesigned for single auth mechanism
  * Migration effort for services currently using mixed authentication

Relevance in edx-platform
=========================

* **OAuth2/DOT**: LMS uses Django OAuth Toolkit at ``/oauth2/``
  (``lms/urls.py``, ``openedx/core/djangoapps/oauth_dispatch``). Settings include
  ``OAUTH2_PROVIDER_APPLICATION_MODEL``, ``OAUTH2_VALIDATOR_CLASS`` (e.g.
  ``EdxOAuth2Validator``).
* **Current API auth**: ``openedx/core/lib/api/view_utils.view_auth_classes``
  configures both **JWT** and **OAuth2** (Bearer) and session:

  .. code-block:: python

     # openedx/core/lib/api/view_utils.py (current)
     func_or_class.authentication_classes = (
         JwtAuthentication,
         BearerAuthenticationAllowInactiveUser,  # OAuth2 Bearer via DOT
         SessionAuthenticationAllowInactiveUser
     )

* **Bearer auth**: ``openedx/core/lib/api/authentication.py`` implements
  ``BearerAuthentication`` / ``BearerAuthenticationAllowInactiveUser`` using
  ``oauth2_provider`` (DOT) for access token validation.

Code examples (authentication patterns by use case)
===================================================

* **External API (OAuth2 only):**

.. code-block:: python

   from rest_framework import viewsets
   from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
   from rest_framework.permissions import IsAuthenticated

   class ExternalCourseViewSet(viewsets.ViewSet):
       """External API for course data - OAuth2 authentication only."""
       authentication_classes = [BearerAuthenticationAllowInactiveUser]
       permission_classes = [IsAuthenticated]

* **Internal Service API (JWT only):**

.. code-block:: python

   from rest_framework import viewsets
   from openedx.core.lib.api.authentication import JwtAuthentication
   from rest_framework.permissions import IsAuthenticated

   class InternalServiceViewSet(viewsets.ViewSet):
       """Internal service-to-service API - JWT authentication only."""
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
* Create guidelines for determining appropriate authentication mechanism
* Update global authentication configurations to enforce patterns
* Provide migration guidance for APIs currently using mixed authentication
* Document authentication patterns for development teams

Rollout Plan
------------

1. Audit existing APIs and categorize by intended use case (external/internal/browser)
2. Update global authentication configurations to prevent mixed authentication
3. Refactor high-priority APIs to follow single-authentication patterns
4. Create development guidelines and documentation for authentication choices
5. Implement automated testing to validate authentication pattern compliance
6. Monitor and enforce authentication patterns in code reviews

References
==========

* Django REST Framework - Authentication and permissions
* Django OAuth Toolkit documentation
* Open edX Authentication Patterns Guide
