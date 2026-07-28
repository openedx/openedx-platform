0001 AUTHZ DJANGO INTEGRATION APP
####################

Status
******

Accepted

Context
*******

This ADR defines where Django integrations for the openedx-authz framework should live within edx-platform.
The openedx-authz library introduces a new authorization framework for Open edX based on explicit permissions and a centralized policy engine. Integrating this framework into edx-platform requires several Django-specific utilities.
One of the first integrations is a view decorator used to enforce authorization checks using the new AuthZ framework. This decorator is expected to be reused across multiple views in LMS and Studio.
During implementation, the question arose of where these Django integrations should live.

Some options considered were:

- common/djangoapps/student/auth.py
- openedx/core/authz.py (a new python module)
- openedx/core/djangoapps/authz (a new Django app)

The student app contains legacy authentication and authorization logic tied to student functionality. Adding new platform-level authorization integrations there would introduce cross-cutting concerns into an unrelated app.

Another option was creating a single module such as openedx/core/authz.py. However, the integration already includes multiple components (decorators, constants, tests) and is expected to grow over time.

Because of this, a dedicated Django app provides a clearer and more scalable structure for these integrations.

Decision
********

edx-platform will introduce a new lightweight Django app openedx.core.djangoapps.authz to host Django integrations for the openedx-authz framework.

- The app will contain reusable decorators enforcing AuthZ permissions in Django views.
- Supporting modules such as constants and helper utilities will live in this app.
- The app will include tests validating these integrations.
- The app acts as a thin integration layer between edx-platform and the external openedx-authz library.
- The app will live in openedx/core/djangoapps because this functionality is a platform-level concern shared by LMS and Studio.

Initial contents include:

- An authorization decorator for Django views.
- A constants.py module for AuthZ-related constants.
- Tests validating the decorator behavior.

Consequences
************

- Django integrations for the AuthZ framework have a centralized and discoverable location.
- Future integrations can be added without expanding unrelated modules.
- The separation clarifies the distinction between authentication (authn) and authorization (authz) responsibilities.

However:

- Introducing a new Django app slightly increases project structure complexity.
- Some authorization logic may remain elsewhere until future refactoring occurs.

Rejected Alternatives
**********************

- Add the decorator to common/djangoapps/student/auth.py
Rejected because the module belongs to the student app and already mixes authentication and authorization responsibilities.

- Create a single module openedx/core/authz.py
Rejected because the integration already includes multiple components and is expected to grow.

- Implement the decorator in the openedx-authz library
Rejected because the decorator is Django-specific and tied to how edx-platform integrates authorization checks into views.

References
**********

.. _openedx-authz repository: https://github.com/openedx/openedx-authz
.. _openedx-authz documentation: https://openedx-authz.readthedocs.io/
