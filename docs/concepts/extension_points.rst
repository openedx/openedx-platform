Options for Extending the Open edX Platform
--------------------------------------------

Open edX platform development follows the `Open-Closed Principle`_: we want the Open edX platform to be an extensible platform that allows developers to build extensions that integrate with the core of the platform. This allows the core to remain small, while volatile extensions remain in the periphery.

As you can see in this document, there are many different ways to integrate with the Open edX software. However, we know that there are still some features/integrations that are not possible today without modifying the core. If you have such a need, please consider proposing a new extension point in the core that would make possible the functionality you have in mind. When you submit a pull request for a new extension point, be sure to include a change to this file to document your new extension point. (Doing so will also notify reviewers that want to help with making the platform more extensible.)

Throughout this document, we will refer to the **Status** (**Adoption** and **Completion**) of each specific integration point. The Completion refers to how complete and stable an integration point is: either "Limited" (incomplete, or unstable) or "Stable" (complete and stable enough for general use in some or all cases). Adoption shows how the integration point is currently being used, and whether or not it should be used in the future:

* **Adopt**: Technologies we have high confidence in to serve our purpose, also in large scale. Technologies with a usage culture in our production environment, low risk and recommended to be widely used.
* **Trial**: Technologies that we have seen work with success in project work to solve a real problem; first serious usage experience that confirm benefits and can uncover limitations. Trial technologies are slightly more risky.
* **Assess**: Technologies that we are considering using; to be listed on this page, they must exist as a prototype in the codebase.
* **Hold**: Technologies not recommended to be used for new projects. Technologies that we think are not (yet) worth to (further) invest in. They should not be used for new projects, but usually can be continued for existing projects.

.. _Open-Closed Principle: https://en.wikipedia.org/wiki/Open%E2%80%93closed_principle

.. contents:: **Integration Methods Overview**


REST API Integration with External Applications
===============================================

*Status: Adopt, Limited*

The Open edX platform provides a suite of REST APIs that any type of external application can use. Auto-generated API documentation for the main LMS and Studio APIs is available at (LMS URL)/api-docs/ and (Studio URL)/api-docs/ .

Things that you can develop as an external application using REST APIs include:

* Marketing sites / course catalogs
* Custom learning frontends
* Native mobile applications
* E-commerce and subscription management portals
* Administration tools
* Custom course authoring tools

You can write your external application using any language and framework you'd like. The API specifications are available in OpenAPI format (e.g. at /api-docs/?format=openapi), and you can use the `OpenAPI Generator`_ to generate an API client library in the language of your choice.

Be aware that most existing REST APIs are not considered stable, and some platform features lack a REST API. We welcome help as we work to standardize our API practices and version them to create API stability.

.. _OpenAPI Generator: https://github.com/OpenAPITools/openapi-generator

Content Integrations
====================

If you want to provide learners with new content experiences within courses, options include:

.. list-table::
   :header-rows: 1
   :widths: 15 10 75

   * - Type
     - Status
     - Details
   * - **XBlocks**
     - Adopt, Stable
     - XBlocks are python plugins specific to Open edX that get installed into edx-platform and can be used to build courses. An XBlock defines a new *type* of interactive component, and authors can then create many instances of that content type in their courses (each with different settings and content). If you only need to support Open edX, XBlocks provide the best user experience. Open edX operators must install an XBlock into their Open edX instance before it can be used. See the `XBlock tutorial`_ to learn more about XBlocks.
   * - **LTI**
     - Adopt, Stable
     - Learning Tools Interoperability is a standard that allows an individual piece of learnable/interactive content (the "tool") to be embedded via an IFrame in a host Learning Platform (the "consumer") such as Open edX. Open edX supports LTI content in both directions: `as a consumer`_ (external content appearing in an Open edX course) and `as a provider`_ (Open edX course content appearing in an external Learning Platform). If you need to support multiple Learning Platforms, and not just Open edX, LTI is usually the best way to integrate your content. Note that not all LTI versions/features are supported, however.
   * - **Custom JavaScript Applications**
     - Adopt, Stable
     - If you have a single piece of content, such as a single interactive HTML5 animation or problem, and you want to use it in an Open edX course, you can create it as a `custom JavaScript application`_. Unlike XBlocks, these applications can be implemented without intervention by the Open edX operator.
   * - **External Graders**
     - Hold, Stable
     - An external grader is a service that receives learner responses to a problem, processes those responses, and returns feedback and a problem grade to the edX platform. You build and deploy an external grader separately from the edX platform. An external grader is particularly useful for software programming courses where learners are asked to submit complex code. See the `external grader documentation`_ for details.
   * - **TinyMCE (Visual Text/HTML Editor) Plugins**
     - Trial, Limited
     - TinyMCE's functionality can be extended with so-called Plugins. Custom TinyMCE plugins can be particularly useful for serving certain content in courses that isn't available yet; they can also be used to facilitate the educator's work. `You can follow this guide to install and enable custom TinyMCE plugins`_.

For a more detailed comparison of content integration options, see `Options for Extending the edX Platform`_ in the *Open edX Developer's Guide*.

.. _XBlock tutorial: https://docs.openedx.org/projects/xblock/en/latest/xblock-tutorial/index.html
.. _as a consumer: https://docs.openedx.org/en/latest/educators/navigation/components_activities.html#lti-component
.. _as a provider: https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/lti/index.html
.. _Options for Extending the edX Platform: https://docs.openedx.org/en/latest/developers/references/developer_guide/extending_platform/extending.html
.. _custom JavaScript application: https://docs.openedx.org/en/latest/educators/references/course_development/exercise_tools/custom_javascript.html
.. _external grader documentation: https://docs.openedx.org/en/latest/educators/concepts/exercise_tools/about_external_graders.html
.. _You can follow this guide to install and enable custom TinyMCE plugins: ../extensions/tinymce_plugins.html




Platform Features (Integrating Python Code)
===========================================

If you wish to customize aspects of the learner or educator experiences, you'll most likely need to create a Python plugin that can be installed into the platform and which uses Python APIs to extend the platform's functionality.

Most python plugins are enabled using one of two methods:

1. A Python Entry point: the core Open edX platform provides a standard plugin loading mechanism in |edx_django_utils.plugins|_ which uses `stevedore`_ to find all installed python packages that declare a specific "entry point" in their package configuration (typically ``pyproject.toml`` or ``setup.py``). See the ``entry_points`` defined in edx-platform's own ``pyproject.toml`` for examples.
2. A Django setting: Some plugins require modification of Django settings, which is typically done by editing ``/edx/etc/lms.yml`` (in Production) or ``edx-platform/lms/envs/private.py`` (on Devstack).

.. |edx_django_utils.plugins| replace:: ``edx_django_utils.plugins``
.. _edx_django_utils.plugins: https://github.com/openedx/edx-django-utils/blob/master/edx_django_utils/plugins
.. _stevedore: https://pypi.org/project/stevedore/

Here are the different integration points that python plugins can use:

.. list-table::
   :header-rows: 1
   :widths: 15 10 75

   * - Plugin Type
       (and entry point or setting)
     - Status
     - Details
   * - Django App Plugin (``lms.djangoapp`` and ``cms.djangoapp``)
     - Adopt, Stable
     - A "Django app plugin" is a self-contained Django `Application`_ that can define models (MySQL tables), new REST APIs, signal listeners, asynchronous tasks, and more. Even some parts of the core platform are implemented as Django app plugins, for better separation of concerns (``announcements``, ``credentials``, ``grades``, etc.) Read the `Django app plugin documentation`_ to learn more.

       Plugins can also inject custom data into django template contexts, to affect standard pages delivered by the core platform. See `Plugin Contexts`_ to learn more.
   * - Course tab (``openedx.course_tab``)
     - Hold, Stable
     - A course tab plugin adds a new tab shown to learners within a course. ``courseware``, ``course_info``, and ``discussion`` are examples of built-in tab plugins. Read the `course tabs documentation`_ to learn more.

       This API may be changing soon with the new Courseware microfrontend implementation.
   * - Course tool (``openedx.course_tool``)
     - Hold, Stable
     - The course home page (the landing page for the course) includes a "Course Tools" section that provides links to "tools" associated with the course. Examples of course tool plugins included in the core are reviews, updates, and bookmarks. See |course_tools.py|_ to learn more.

       This API may be changing soon with the new Courseware microfrontend implementation.
   * - Custom profile extension form app (``PROFILE_EXTENSION_FORM`` Django setting in the LMS)
     - Trial, Stable
     - By default, the registration page for each instance of Open edX has fields that ask for information such as a user’s name, country, and highest level of education completed. You can add custom fields to the registration page and user profile for your own Open edX instance. These fields can be different types, including text entry fields and drop-down lists. See `Adding Custom Fields to the Registration Page`_.

       **Important Migration Note:**

       - ``REGISTRATION_EXTENSION_FORM`` (deprecated) continues to work with old behavior: custom fields only for registration, data stored in UserProfile.meta
       - ``PROFILE_EXTENSION_FORM`` (new) enables new capabilities: custom fields in registration and account settings, data stored in dedicated model

       Sites using the deprecated setting will maintain backward compatibility. To get the new capabilities, migrate to ``PROFILE_EXTENSION_FORM``.
   * - Learning Context (``openedx.learning_context``)
     - Trial, Limited
     - A "Learning Context" is a course, a library, a program, a blog, an external site, or some other collection of content where learning happens. If you are trying to build a totally new learning experience that's not a type of course, you may need to implement a new learning context. Learning contexts are a new abstraction and are only supported in the nascent openedx_content-based XBlock runtime. Since existing courses use modulestore instead of openedx_content, they are not yet implemented as learning contexts. However, openedx_content-based content libraries are. See |learning_context.py|_ to learn more.
   * - User partition scheme (``openedx.user_partition_scheme`` and ``openedx.dynamic_partition_generator``)
     - Unknown, Stable
     - A user partition scheme is a named way for dividing users in a course into groups, usually to show different content to different users or to run experiments. Partitions may be added to a course manually, or automatically added by a "dynamic partition generator." The core platform includes partition scheme plugins like ``random``, ``cohort``, and ``enrollment_track``. See the |UserPartition docstring|_ to learn more.
   * - XBlock (``xblock.v1``)
     - Adopt, Stable
     - An XBlock provides a new type of learnable content that can be used in courses, content libraries, etc. See `Content Integrations`_.
   * - XBlock unit tests (``xblock.test.v0``)
     - Assess, Limited
     - XBlocks can also install test code that will then be run alongside the platform's usual python unit tests. It's unclear how well-supported this is at the moment.
   * - Pluggable override (``edx_django_utils.plugins.pluggable_override.pluggable_override``)
     - Trial, Stable
     - This decorator allows overriding any function or method by pointing to an alternative implementation in settings. Read the |pluggable_override docstring|_ to learn more.
   * - Open edX Events
     - Adopt, Stable
     - Events are part of the greater Hooks Extension Framework for open extension of edx-platform. Events are a stable way for plugin developers to react to learner or author events. They are defined by a `separate events library`_ that developers can include in their requirements to develop and test the code without creating a dependency on this large repo. For more information see the `Hooks Extension Framework docs`_ or for more detailed documentation about Open edX Events, see the `Open edX Events documentation`_.
   * - Open edX Filters
     - Adopt, Stable
     - Filters are also part of Hooks Extension Framework for open extension of edx-platform. Filters are a flexible way for plugin developers to modify learner or author application flows. They are defined by a `separate filters library`_ that developers can include in their requirements to develop and test the code without creating a dependency on this large repo. For more information see the `Hooks Extension Framework docs`_ or for more detailed documentation about Open edX Filters, see the `Open edX Filters documentation`_.

.. _Application: https://docs.djangoproject.com/en/3.0/ref/applications/
.. _Django app plugin documentation: https://github.com/openedx/edx-platform/blob/master/openedx/core/djangoapps/plugins/README.rst
.. _Plugin Contexts: https://github.com/openedx/edx-platform/blob/master/openedx/core/djangoapps/plugins/docs/decisions/0003-plugin-contexts.rst
.. _course tabs documentation: https://openedx.atlassian.net/wiki/spaces/AC/pages/30965919/Adding+a+new+course+tab
.. |course_tools.py| replace:: ``course_tools.py``
.. _course_tools.py: https://github.com/openedx/edx-platform/blob/master/openedx/features/course_experience/course_tools.py
.. _Adding Custom Fields to the Registration Page: https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/customize_registration_page.html
.. |learning_context.py| replace:: ``learning_context.py``
.. _learning_context.py: https://github.com/openedx/edx-platform/blob/master/openedx/core/djangoapps/xblock/learning_context/learning_context.py
.. |UserPartition docstring| replace:: ``UserPartition`` docstring
.. _UserPartition docstring: https://github.com/openedx/edx-platform/blob/f8cc58618a39c9f7b8e9e1001eb2d7a10395797e/common/lib/xmodule/xmodule/partitions/partitions.py#L105-L120
.. |pluggable_override docstring| replace:: ``pluggable_override`` docstring
.. _pluggable_override docstring: https://github.com/openedx/edx-django-utils/blob/master/edx_django_utils/plugins/pluggable_override.py
.. _separate events library: https://github.com/eduNEXT/openedx-events/
.. _separate filters library: https://github.com/eduNEXT/openedx-filters/
.. _Hooks Extension Framework docs: https://docs.openedx.org/en/latest/developers/concepts/hooks_extension_framework.html
.. _Open edX Events documentation: https://docs.openedx.org/projects/openedx-events/en/latest/
.. _Open edX Filters documentation: https://docs.openedx.org/projects/openedx-filters/en/latest/

Platform Look & Feel
====================

Frontend Plugin Framework Slots
*********************************

*Status: Adopt, Stable*

Utilizing *frontend plugin framework slots*, site operators now have the ability to customize various portions of Open edX MFEs. A “frontend plugin framework slot” refers to an area of a web page - comprising one or more visual elements - that can be “swapped out” with other visual elements using custom code defined in an ``env.config.jsx file``. Note: In some cases a slot may default to being empty, existing solely to be a placeholder for optional elements.

See the `FPF Slots how-to <https://docs.openedx.org/en/latest/site_ops/how-tos/use-frontend-plugin-slots.html#use-a-frontend-plugin-framework-slot>`_ for more detail.

Design Tokens (Theming)
********************************

*Status: Adopt, Stable*

Open edX frontend applications are built using the `Paragon design system <https://paragon-openedx.netlify.app/>`_, which supports runtime theming. Paragon uses design tokens (`see the Design Tokens concept page <https://docs.openedx.org/en/latest/developers/concepts/design_tokens.html#design-token>`_) to define its built-in styles, compiling them into CSS custom properties (variables) for visual properties like colors, spacing, and typography. A theme can override those defaults by compiling its own design tokens into a stylesheet that sets new values for those variables. A theme can also include custom SCSS that compiles into additional CSS, allowing for additional customization past the scope of the defined tokens. These themes can be loaded at runtime, meaning this customization can be done without rebuilding any frontend applications


Custom MFEs
****************

*Status: Trial, Limited*

Forking an MFE is generally not a recommended way of customizing an MFE, as it causes a lot of headache and rework to upgrade to a new release. Utilize Frontend Plugin Framework Slots instead to customize an existing MFE.

Occasionally an operator may have a need for an entirely new MFE. This requires substantial development resources, but can be done. To do so, fork the `frontend-template-application <https://github.com/openedx/frontend-template-application>`_ and customize according to your requirements. See also `this helpful tutorial <https://opencraft.com/building-your-own-micro-front-end-for-open-edx/>`_.
