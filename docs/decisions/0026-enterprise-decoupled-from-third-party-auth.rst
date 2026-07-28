Enterprise Decoupled from Third-Party Auth
##########################################

**Status**: Accepted
**Date**: 2026-04-16

-----

Context
*******

The ``third_party_auth`` Django app in openedx-platform has several coupling
points with enterprise-specific logic:

* Enterprise-specific TPA pipeline steps are injected into the social auth
  pipeline, including:

  - ``associate_by_email_if_saml``: resolves which existing LMS account should
    own the incoming SSO identity, matching by email only when the existing
    user is an ``EnterpriseCustomerUser`` for the current provider's
    enterprise.  Runs during the user-association phase, before the
    ``create_user`` step.

    - Note: this step name was misleading because the logic was always
      enterprise-specific but the name lacked "enterprise". It was a no-op when
      the enterprise integration was disabled.

  - ``handle_enterprise_logistration``: establishes and manages the
    enterprise relationship for the now-authenticated user, creating or
    updating the ``EnterpriseCustomerUser`` record among other things.  Runs
    during the post-authentication phase (after user creation and social-auth
    linking).

    - Note: this step was already migrated to enterprise, but it was still
      imported/injected into the pipeline via ``third_party_auth`` app code.

* ``is_enterprise_customer_user`` utility function which queries enterprise models.

* SAML account disconnection triggers enterprise-specific cleanup to remove the
  identity provider link.

* The ``disable_for_enterprise_sso`` boolean field on ``OAuth2ProviderConfig``
  which exists solely to exclude social auth providers (e.g. Facebook, Google)
  from the ``EnterpriseCustomer`` Django admin IDP dropdown.

All these unconditional enterprise imports contribute to the reason
``edx-enterprise`` is a mandatory pip dependency for any openedx deployment.
As part of the broader effort to make ``edx-enterprise`` optional, all coupling
points must be removed from the ``third_party_auth`` Django app.

Decision
********

The enterprise-specific logic is removed from ``third_party_auth`` and migrated
to the ``enterprise`` plugin:

**Pipeline injection migrated**

Enterprise pipeline steps are no longer injected at TPA app startup by
importing enterprise code.  Instead, the ``enterprise`` plugin registers the
enterprise-specific steps itself (via its own ``plugin_settings()``).

**associate_by_email pipeline step migrated**

The platform-side ``common.djangoapps.third_party_auth.pipeline.associate_by_email_if_saml``
pipeline step becomes a no-op for backwards compatibility with custom
pipeline configs.  The actual step is migrated to the ``enterprise``
plugin under a more accurate name (``enterprise.tpa_pipeline.enterprise_associate_by_email``).

**SAML disconnect via Django signal**

A new ``SAMLAccountDisconnected`` Django signal replaces the direct call
to enterprise unlink logic during SAML account disconnection.  The
``enterprise`` plugin now handles the signal to perform the enterprise-specific
cleanup.

Consequences
************

* **New signal contract**: The ``SAMLAccountDisconnected`` signal is a new
  public contract between the platform and plugins.  Its keyword arguments must
  be treated as stable once released.

  - It's generally understood by the community that signal signatures are
    treated as stable, and changes must be made with caution.

* **Pipeline injection is sensitive to platform changes**: Enterprise pipeline
  steps are injected relative to existing platform-defined steps in
  ``SOCIAL_AUTH_PIPELINE``.  If the platform renames, reorders, or removes steps
  that the plugin keys off of, the injection will break. Platform maintainers
  should continue to treat the pipeline step names as a stable interface.

  - To mitigate silent failures, the plugin raises a fatal exception blocking
    django startup if step injection fails.

* **``disable_for_enterprise_sso`` field left in platform**: The
  ``disable_for_enterprise_sso`` field on ``OAuth2ProviderConfig`` remains in
  the Third-Party Auth app for now.  Leaving it does not block the immediate goal
  of making ``edx-enterprise`` an optional pip dependency.  It should ideally be
  migrated into the enterprise plugin in the future, but its presence has
  minimal impact on installations that do not use enterprise functionality —
  it simply appears as an unused boolean on the provider config.

References
**********

* ADR 0025 (``0025-saml-admin-views-in-enterprise-plugin.rst``) — Related migration of SAML admin viewsets to edx-enterprise.
* OpenEdX forum thread announcing the pluginification of enterprise: https://discuss.openedx.org/t/rfc-pluginifying-edx-enterprise-looking-for-community-interest-collaborators/18316
* ENT-11418 — 2U JIRA initiative for the overall pluginification of enterprise.
* ENT-11566 — 2U JIRA ticket for this specific migration.
