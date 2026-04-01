Allow fetching configuration for inactive providers
===================================================


Status
------

Proposal


Context
-------

In [ADR 003](./0003-configuration-rest-api.rst) an API was proposed for
discussion configuration. This proposed a single API that returned
information about the active provider and listed other available providers.

There are a few issues with this API that we'd like to rectify such as:

- It includes a lot of information that is mostly read-only.
- It doesn't provide any way to fetch setting for inactive provider.

By moving the bits of the API that are read-only to a separate API the consumer
of this API can cache the response better since it doesn't change that often so
the data doesn't need to be processed and fetched as often.

The inability to fetch information for inactive provider mainly causes issues
when switching providers between the in-built/legacy providers and LTI
providers.

The current mechanism for configuring providers in the course authoring MFE is
through two steps. In the first step, you select a provider, and in the second
step you change its configuration.

However when changing providers, the only information you have from the API
is about the current provider, meaning that you cannot have configuration
pre-filled in the configuration page in the second step. The settings page will
only have default information, which means that on saving the configuration in
the second step, the original saved configuration will be overridden, and
potentially might be in an inconsistent state.

Requirements
------------

Split the configuration API into two parts where one part returns static
data about the available providers, and the other can be the configuration
data for the discussions provider.

The configuration API should provide a way to fetch settings for a provider
without needing to switch to it first.

Alternatives Considered
-----------------------

Another way around the second issue above is to save the provider change
first, fetch the configuration data and then proceed with the second
configuration step.

However this can be very disruptive since changing the provider first will
trigger a course republish and a lot of background machinery to build
discussion topics, and will change the live experience using old settings that
the course author might have wanted to change before publishing.

Decision
--------

We can split the existing configuration API into two parts. The first part will
be a read-only API for listing provider details. No data in this API can be
modified by the user.

The other API will return all the writable configuration for the providers.
It will support updating the settings of the current provider as it does
now, but will also support returning the configuration/settings of other
providers by taking a ``provider_id`` query parameter.


Provider Endpoint
~~~~~~~~~~~~~~~~~

``/api/discussions/v0/course/{course_id}/providers``

This will return the list of available providers and their data. This will
return the following pieces of information:

- ``active``: the active provider
- ``features``: feature support levels
- ``available``: provider listing

**Sample response:**

.. code-block::

    {
        "active": "legacy",
        "features": [
            {
                "id": "primary-discussion-app-experience",
                "feature_support_type": "basic"
            },
            …
        ],
        "available": {
            "legacy": {
                "features": [ … ],
                "supports_lti": false,
                "external_links": { … },
                "messages": [ … ],
                "has_full_support": true
            },
            …
        }
    }


Settings Endpoint
~~~~~~~~~~~~~~~~~

``/api/discussions/v0/course/{course_id}/settings``

This is the read-write settings API that can take an optional `provider_id`
argument in which case returns data for that provider.

This will return the following information which is common for all providers:

- enabled
- provider_type
- enable_in_context
- enable_graded_units
- unit_level_visibility

The following information will vary based on provider specified:

- lti_configuration: only returned for the active provider.
- plugin_configuration: plugin configuration for the specified provider.

Changing settings for an inactive provider is out of scope here.

**Sample response:**

.. code-block::

    {
      "enabled": true,
      "provider_type": "legacy",
      "enable_in_context": true,
      "enable_graded_units": true,
      "unit_level_visibility": false,
      "lti_configuration": {},
      "plugin_configuration": { … }
    }


Conclusion
----------

By splitting the data across two endpoints we make sure that only the relevant
data can be fetched as needed.

By making it possible to fetch settings for a discussion provider that is not
currently active we make the process of switching providers much smoother,
since the frontend can fetch the configuration for a provider before switching
to it, allowing the user to get a clearer, and more consistent view of the
settings before committing to them.
