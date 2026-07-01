17. Standardize Authentication Patterns and Security Schemes
------------------------------------------------------------

This decision has been documented in the platform-level ADR:

:doc:`docs/decisions/0034-unify-auth-oauth2-dot-v2`

See that document for:

* The decision to standardize on ``JwtAuthentication`` for all DRF user-authenticated APIs
* Deprecation of ``BearerAuthentication`` and ``BearerAuthenticationAllowInactiveUser``
* Code examples showing current and target states for existing views
* Rollout plan and reference to the `DEPR: BearerAuthentication <https://github.com/openedx/edx-drf-extensions/issues/284>`_ ticket
