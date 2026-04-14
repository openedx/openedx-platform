SAML Admin Views Migrated to edx-enterprise Plugin
###################################################

**Status**: Accepted
**Date**: 2026-03-30

-----

Context
*******

The ``third_party_auth`` Django app in openedx-platform previously contained two
REST API viewsets for managing SAML identity provider configurations:

* ``auth/saml/v0/provider_config/`` -> ``SAMLProviderConfigViewSet``: CRUD for
  ``SAMLProviderConfig`` records scoped to a specific enterprise customer.
* ``auth/saml/v0/provider_data/`` -> ``SAMLProviderDataViewSet``: CRUD and
  metadata sync for ``SAMLProviderData`` records scoped to a specific enterprise customer.

**These viewsets existed solely to serve the enterprise admin portal.** Every
operation required an ``enterprise_customer_uuid`` to scope the editable SAML
providers to just the ones specifically belonging to that enterprise, according
to the ``EnterpriseCustomerIdentityProvider`` mapping model.

Note: The URL routes for the views make them seem general-purpose for
administering any SAML providers in the openedx platform.  The routes are
misleading---the views are specifically designed to be used only by the
enterprise admin portal.

As part of the effort to decouple edx-enterprise from openedx-platform
(ENT-11567), these viewsets were identified as enterprise-specific code that
should not live in the platform core.

Decision
********

The two SAML admin viewsets will be moved from openedx-platform to the
edx-enterprise repository:

* ``enterprise/api/v1/views/saml_provider_config.py``
* ``enterprise/api/v1/views/saml_provider_data.py``

They will be registered in ``enterprise/api/v1/urls.py`` under the same
``auth/saml/v0/`` prefix, so the API contract is preserved for existing clients.

The underlying ``SAMLProviderConfig``, ``SAMLProviderData``, and
``SAMLConfiguration`` models remain in
``common/djangoapps/third_party_auth/models.py`` because they are
general-purpose SAML models used by the platform's authentication layer
regardless of whether the enterprise plugin is installed.

Consequences
************

* **URL routes collision risk**: Contributors adding new routes under
  ``auth/saml/v0/`` in openedx-platform must check for conflicts with the
  enterprise plugin to avoid URL collisions.

* **Dependency direction**: edx-enterprise now imports ``SAMLProviderConfig``,
  ``SAMLProviderData``, and several utility functions from
  ``common.djangoapps.third_party_auth``. This is the correct dependency
  direction (plugin depends on platform, not the reverse).

* **Deployments without edx-enterprise**: The SAML admin API endpoints will not
  be available in deployments that do not install edx-enterprise. This is
  acceptable because the endpoints ONLY serve frontend-app-admin-portal
  which doesn't work without edx-enterprise installed anyway.
