Course Apps API
###############

Status
******

Proposal

Context
*******

The new `Course Authoring MFE`_ includes a new UX called "Pages and Resources"
for configuring different aspects of the course experience such as progress,
wiki, teams, discussions, etc. all from one place.

Currently, the code for these aspects of the course experience is intermingled
with the core platform, however, it would be useful to have a way to
automatically discover the data for driving this page instead of hardcoding it
such that in the future existing functionality can be split out into an external
plugin and so that future third-party plugins can also show up here.

This ADR describes how we can use the existing plugin architecture of Open edX
to automatically discover such apps and expose them via an API. This API will
be used to list the installed apps that are available for a course and to
enable/disable these apps using the API.

.. _Course Authoring MFE: https://github.com/openedx/frontend-app-course-authoring/


Decision
########

We propose to call such individual course features "Course Apps". They can be
introduced as a new type of Open edX plugin. Any functionality that can be
enabled or disabled at a course-level and can be configured by instructors is
a candidate for being bundled as a Course App.

To be able to show these Course Apps to course admins, they will need to provide
some bits of metadata, such as a name, a description etc. Additionally we will
need a common interface for such apps so they can be enabled/disabled using
a standard common interface.

To do this we can follow the example of existing plugins, `such as Course Tabs`_,
which provide a specific Python class that the plugin can inherit from, or
implement. The required metadata and features, can be implemented as class
attributes, and methods on this class.

We can then discover the installed apps using the existing tooling for plugins
using a subclass of ``PluginManager`` designed for this purpose.
Here is an example for `CourseTabs`_:

.. code-block:: python

     class CourseTabPluginManager(PluginManager):
     """
     Manager for all of the course tabs that have been made available.

     All course tabs should implement `CourseTab`.
     """
     NAMESPACE = COURSE_TAB_NAMESPACE

     @classmethod
     def get_tab_types(cls):
         """
         Returns the list of available course tabs in their canonical order. 

It might not always make sense for an app installed in this way to be
automatically show up for use on all courses. So each app will expose a method
to check if it should be available for a particular course.

Once an app has been marked as available for a course, it will show up in the
API, where the next step is to mark it as enabled.

In the context of these apps, we need to distinguish between *installed*,
*available* and *enabled*.

Let's look at an existing feature to explain those terms. The `edxnotes` app
has [code that is part of the
platform](https://github.com/openedx/edx-platform/tree/636b2ca4c5add531cfce755fdb8965599acd79e0/lms/djangoapps/edxnotes).
This code comes preinstalled since it's part of the platform. So it is already
*installed*, however no one can use it just yet, since it it needs to first be
enabled globally. In the case of an external plugin, you consider it installed
if it is `pip install`ed in the same environment in which edx-platform is
running.

To make the feature *available* for use, you need to now [enable a feature
flag](https://github.com/openedx/edx-platform/blob/636b2ca4c5add531cfce755fdb8965599acd79e0/lms/envs/common.py#L531-L543).
Until this is set course authors/admins will [not even see the option of
enabling this for their
course](https://github.com/openedx/edx-platform/blob/636b2ca4c5add531cfce755fdb8965599acd79e0/cms/djangoapps/models/settings/course_metadata.py#L91-L93).
For course apps this is where the availability check comes in.

In the case of `edxnotes`, after setting the above feature flag, an option will
show up in the advanced settings page of studio that allows you to *enable*
the `edxnotes` for a particular course. If this value is true, then edxnotes
will be enabled for the course.

In the case of Course Apps, the standard plugin API will automatically discover
all installed apps. Inactive apps will be filtered out during the availability
check.

.. _such as Course Tabs: https://github.com/openedx/edx-platform/blob/636b2ca4c5add531cfce755fdb8965599acd79e0/common/lib/xmodule/xmodule/tabs.py#L24-L243
.. _CourseTabs: https://github.com/openedx/edx-platform/blob/636b2ca4c5add531cfce755fdb8965599acd79e0/openedx/core/lib/course_tabs.py#L13-L47

Course App Plugin Class
=======================

To be loaded as a Course App, you need to provide an entrypoint in ``pyproject.toml``
with the namespace ``openedx.course_app``. The entry should point to a Python
class with the following basic structure:

.. code-block:: python

    class CourseApp:
        # The app id should match what is specified in the pyproject.toml entrypoint
        app_id: str = 'wiki'
        name: str = 'Wiki'
        description: str = 'A short description of what the Wiki does.'
        # Specify if this app is enabled by default. If the app is made available for a course
        # should it also automatically be considered enabled.
        default_enabled: bool = False

        # This method will not be in the sample/base class, but will be added to
        # existing Course Apps.
        if LEGACY_APP:
            @classmethod
            def legacy_link(cls, course_key):
                return f'some/link/to/{course_key}'

        @classmethod
        def is_available(cls, course_key):
            # Some mechanism, ideally a waffle flag in the Course Apps namespace
            # to see if this app can be enabled/configured for this course.
            return True

        @classmethod
        def is_enabled(cls, course_key):
            # Some logic to check if the app is enabled for this course
            # This will not vary from user-to-user in studio.
            return True

        @classmethod
        def set_enabled(cls, course_key, user, enabled):
            # Some logic to enable the app for this course.
            # The user here isn't passed on for permission checking, but just
            # for logging/auditing.
            return enabled

        @classmethod
        def get_allowed_operations(cls, course_key, user):
            # This should return a dictionary with at least the `enable` and `configure` keys.
            return {
                'enable': can_user_enable(course_key, user),
                'configure': can_user_configure(course_key, user),
            }


For existing features that need to be exposed as Course Apps, we can create
such a class and have these class methods call back to the existing code for
availability checks and enabled checks.

Course Apps API
===============

Each app has some associated metadata:

- **id**: (string) A unique identifier for the app.
- **name**: (string) A friendly name for the app that can be shown in the UI.
- **description**: (string) A friendly description of what the app does, to be shown to
  users in the UI.
- **enabled**: (boolean) Is this app enabled for the current course.
- **allowed_operations**: (dictionary) Apps can potentially enable/disable certain
  operations. The following operations should be specified for all apps:

    - **enable**: (boolean) Can the current user enable/disable this app.
    - **configure**: (boolean) Can the current user can configure this app.

  If an app doesn't have any configuration it can set the ``configure`` to false
  and the UI will simply not show a configuration option for that app.
- **legacy_link**: (string) If available, this will point to the legacy link for
  configuring the app. This can be provided as a fallback while the new UX is
  still in development.

The is also the structure that will form the basis of the API's response:

.. code-block:: python

    {
        'id': 'courseapp',
        'enabled': False,
        'name': 'Course App',
        'description': 'A sample Course App for use as documentation.',
        'allowed_operations': {
            'enable': True,
            'configure': True,
            'edit_lti_config': False,
        },
        'legacy_link': 'https://studio.example.com/course_id/app-page',
    }


This API can be hosted at: ``/course_apps/v1/apps/{course_id}/``

    GET ``/course_apps/v1/apps/{course_id}/``

A ``GET`` request to this API will return an array of objects with the above
structure.

    PATCH ``/course_apps/v1/apps/{course_id}/`` {
        "id": "wiki",
        "enabled": true
    }

A ``PATCH`` request to the same endpoint with just the ``id`` of the application
and the ``enabled`` attribute can be used to enable/disable the app if it's
possible to do so.

Note that it may not always be possible to enable/disable an app. Similar to
disabling an XBlock from a course that's in use, some apps might break the
course if you remove them while they are in use. In other cases the app may not
support enabling/disabling without changing a setting/django config. Or an app
might need to be configured first before it can be enabled.

This data is provided by the a special configuration class that is part of the app.
It's structure is detailed in the previous section.

To enumerate the list of available apps, we will list the installed plugins,
check which plugins are available for the current course using `is_available`
and get the static metadata from the config class. If the plugin marks itself
as unavailable, it won't be listed in the API response.

Only legacy apps will support the ``legacy_link`` method, it will not be a
publicised part of the API so new/external plugin apps shouldn't use it. This
link should only be provided for Course Apps that don't have a UI in the course
authoring MFE yet. If a partial UI exists, the MFE settings view can always link
back to the old studio view from there.

Consequences
************

- A new Course Apps API that consistently uses a standard mechanism (a plugin
  class) for discovering Course Apps, determining their availability and
  enabling/disabling them.
- We still leave the more complex considerations of configuration to each
  individual app to implement in its own best way. i.e. the aim here isn't to
  have a uniform API to configure all Course Apps.
