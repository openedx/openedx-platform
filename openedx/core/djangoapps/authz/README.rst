AuthZ Django Integration
########################

Overview
********

The ``openedx.core.djangoapps.authz`` app provides Django integrations for the
`openedx-authz` authorization framework within ``edx-platform``.

The `openedx-authz` library implements a centralized authorization system based
on explicit permissions and policy evaluation. This Django app acts as a thin
integration layer between ``edx-platform`` and the external library, providing
utilities that make it easier to enforce authorization checks in Django views.

Currently, the app provides a decorator used to enforce AuthZ permissions in
views. The app may also host additional Django-specific helpers and utilities
as the integration with the AuthZ framework evolves.

Purpose
*******

This app exists to:

- Provide Django-specific integrations for the ``openedx-authz`` framework
- Offer reusable decorators for enforcing authorization checks in views
- Centralize AuthZ-related utilities used across LMS and Studio

Keeping these integrations in a dedicated app avoids coupling authorization
logic with unrelated apps and provides a clear location for future extensions.

Location in the Platform
************************

The app lives in ``openedx/core/djangoapps`` because the functionality it
provides is a **platform-level concern shared across LMS and Studio**, rather
than something specific to either service.

Usage
*****

The primary utility currently provided by this app is a decorator that enforces
authorization checks using the AuthZ framework.

Example usage::

    from openedx.core.djangoapps.authz.decorators import authz_permission_required


    @authz_permission_required("course.read")
    def my_view(request, course_key):
        ...

The decorator ensures that the requesting user has the required permission
before allowing the view to execute.

Additional parameters may allow compatibility with legacy permission checks
during the transition to the new authorization framework.

Contents
********

The app currently includes:

- **Decorators** for enforcing AuthZ permissions in Django views
- **Constants** used by the AuthZ integration
- **Tests** validating decorator behavior

Relationship with ``openedx-authz``
***********************************

This app does not implement the authorization framework itself. Instead, it
provides Django-specific integrations that connect ``edx-platform`` with the
external ``openedx-authz`` library.

Keeping these integrations in ``edx-platform`` ensures that the external
library remains framework-agnostic.

References
**********

- `openedx-authz repository <https://github.com/openedx/openedx-authz>`_
- `openedx-authz documentation <https://openedx-authz.readthedocs.io/>`_
