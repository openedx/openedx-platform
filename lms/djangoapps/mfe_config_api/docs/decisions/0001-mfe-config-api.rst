0001 MFE CONFIG API
####################

Status
******

Accepted

Context
*******

Currently, MFE settings are set via command line environment variables or an .env file that is read during the build process, causing the operators to rebuild mfes each time when any variables are changed. The creation of the ``mfe_config_api`` allows configuration at runtime and avoids rebuilds.
`MFE Configuration during Runtime`_.

Decision
********

- A lightweight API will be created that returns the mfe configuration variables from the site configuration or django settings. `PR Discussion about django settings`_
- The API will always be available.  It was originally gated behind an
  ``ENABLE_MFE_CONFIG_API`` toggle that defaulted to ``False``; that toggle was
  removed by the `DEPR ticket for ENABLE_MFE_CONFIG_API`_ because the API only exposes
  non-sensitive frontend configuration and returns nothing that an operator has
  not explicitly configured.
- The API will take the mfe configuration in the ``MFE_CONFIG`` keyset in the site configuration (admin > site configuration > your domain) or in django settings.
- This API allows to consult the configurations by specific MFE. Making a request like ``/api/mfe_config/v1?mfe=mymfe`` will return the configuration defined in ``MFE_CONFIG_OVERRIDES["mymfe"]`` merged with the ``MFE_CONFIG`` configuration.
- The API will have a mechanism to cache the response with ``MFE_CONFIG_API_CACHE_TIMEOUT`` variable.
- The API will live in lms/djangoapps because this is not something Studio needs to serve and it is a lightweight API. `PR Discussion`_
- The API will not require authentication or authorization.
- The API request and response will be like:

Request::

    GET http://lms.base.com/api/mfe_config/v1?mfe=learning

Response::

    {
                "BASE_URL": "https://name_of_mfe.example.com",
                "LANGUAGE_PREFERENCE_COOKIE_NAME": "example-language-preference",
                "CREDENTIALS_BASE_URL": "https://credentials.example.com",
                "DISCOVERY_API_BASE_URL": "https://discovery.example.com",
                "LMS_BASE_URL": "https://courses.example.com",
                "LOGIN_URL": "https://courses.example.com/login",
                "LOGOUT_URL": "https://courses.example.com/logout",
                "STUDIO_BASE_URL": "https://studio.example.com",
                "LOGO_URL": "https://courses.example.com/logo.png"

    }

Consequences
************

- We have to change all the mfes so that they take the information from the API. `Issue MFE runtime configuration in frontend-wg`_
- Initialize the MFE could have a delay due to the HTTP method.
- `Site configuration is going to be deprecated`_ so later we have to clean the code that uses site configuration.
- The operator is responsible for configuring the settings in site configuration or django settings.
- We can have duplicate keys in site configuration (example: we can have a logo definition for each mfe).
- If the request is made from a domain that does not have a site configuration, it returns django settings.

Rejected Alternatives
**********************

- It was not made as a plugin or IDA because it is a lightweight implementation `PR Discussion`_

References
**********

.. _MFE Configuration during Runtime: https://docs.google.com/document/d/1-FHIQmyeQZu3311x8eYUNMru4JX7Yb3UlqjmJxvM8do/edit?usp=sharing

.. _PR Discussion: https://github.com/openedx/edx-platform/pull/30473#issuecomment-1146176151

.. _Site configuration is going to be deprecated: https://github.com/openedx/platform-roadmap/issues/21

.. _Issue MFE runtime configuration in frontend-wg: https://github.com/openedx/frontend-wg/issues/103

.. _PR Discussion about django settings: https://github.com/openedx/edx-platform/pull/30473#discussion_r916263245

.. _DEPR ticket for ENABLE_MFE_CONFIG_API: https://github.com/openedx/openedx-platform/issues/38959
