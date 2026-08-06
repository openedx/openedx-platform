Using the ``development.py`` settings (experimental)
####################################################
.. contents::

Overview
========

This guide describes an **experimental** way to run the LMS and CMS for local
development. Instead of the legacy ``devstack.py`` settings (which build on top
of ``production.py``), it uses a dedicated ``development.py`` settings module for
each service that builds directly on top of the ``common.py`` defaults.

Two things differ from the older bare-metal / devstack instructions:

* **Settings module:** you pass ``--settings=development`` to ``manage.py``
  instead of relying on ``devstack.py``.
* **Domains:** services are addressed through ``local.openedx.io`` subdomains
  (which resolve to ``127.0.0.1``) rather than ``localhost:<port>``. This gives
  nicer, production-like hostnames and lets cookie, CORS, and CSRF behavior be
  exercised more realistically across services and MFEs.

.. warning::

   This workflow is under active development and is **not** yet the recommended
   default. Some steps may change. If you want a supported development
   environment today, use `Tutor's development mode`_.

Prerequisites
=============

Follow the **System Dependencies** and initial **Build Steps** from the
``Bare Metal (Advanced)`` section of the `openedx-platform README`_ (Python
3.12, Node, MySQL, Mongo, Memcached, a virtualenv, ``npm clean-install``, and
``pip install -r requirements/edx/development.txt``). The steps below replace
only the "Run the Platform" portion of those instructions.

Domain names
============

``local.openedx.io`` and its subdomains resolve to the loopback address
(``127.0.0.1``), so no web server or proxy is required. This guide uses:

* ``local.openedx.io`` — LMS
* ``studio.local.openedx.io`` — CMS / Studio
* ``apps.local.openedx.io`` — Micro-frontends (MFEs)

Database and migrations
=======================

Create the databases and run migrations exactly as in the bare-metal
instructions, but pass ``--settings=development``::

  python manage.py lms --settings=development migrate
  python manage.py lms --settings=development migrate --database=student_module_history
  python manage.py cms --settings=development migrate

Build frontend assets
======================

Build the webpack bundles once (or run the watcher for a live edit/rebuild
loop)::

  npm run build-dev      # one-time build
  # or, for an auto-rebuilding dev loop:
  npm run watch

You do **not** need to run ``collectstatic``. With ``DEBUG = True`` the
staticfiles finders serve assets directly from their source directories. The
``development.py`` settings point ``STATIC_ROOT`` at ``test_root/staticfiles``
only so that the webpack stats manifest (``webpack-stats.json``) can be located.

Run the LMS and CMS
===================

First, ensure MySQL, Mongo, and Memcached are running. Then start each service
with the ``development`` settings, bound to its ``local.openedx.io`` host:

Start the LMS::

  python manage.py lms --settings=development runserver local.openedx.io:8000

Start the CMS::

  python manage.py cms --settings=development runserver studio.local.openedx.io:8001

Set up CMS SSO
==============

Studio authenticates against the LMS via OAuth. Create the worker user and
OAuth application (as in the bare-metal instructions), using the Studio
``local.openedx.io`` redirect URI::

  python manage.py lms --settings=development manage_user studio_worker studio_worker@example.com --unusable-password
  # DO NOT DO THIS IN PRODUCTION. It will make your auth insecure.
  python manage.py lms --settings=development create_dot_application studio-sso-id studio_worker \
      --grant-type authorization-code \
      --skip-authorization \
      --redirect-uris 'http://studio.local.openedx.io:8001/complete/edx-oauth2/' \
      --scopes user_id \
      --client-id 'studio-sso-key' \
      --client-secret 'studio-sso-secret'

Run the MFEs
============

Most of the UI now lives in Micro-frontends, which run separately. Each MFE is
served under ``apps.local.openedx.io`` on its own port. Clone the MFE repo(s)
you need next to ``openedx-platform``, install dependencies (``npm
clean-install``), and start each one with its ``dev`` script. That script points
``MFE_CONFIG_API_URL`` at the LMS MFE Config API
(``http://local.openedx.io:8000/api/mfe_config/v1``), so the MFE fetches its
runtime configuration (LMS/Studio URLs, etc.) from the running LMS::

  npm run dev

At a minimum you will want the Authoring, Learning, and Learner Home MFEs. The
``development.py`` settings already configure URLs for the full default set:

.. list-table::
   :header-rows: 1

   * - MFE
     - Location
     - Setting
   * - frontend-app-learning
     - apps.local.openedx.io:2000/learning
     - ``LEARNING_MICROFRONTEND_URL``
   * - frontend-app-authoring
     - apps.local.openedx.io:2001/authoring
     - ``COURSE_AUTHORING_MICROFRONTEND_URL``
   * - frontend-app-learner-dashboard
     - apps.local.openedx.io:1996/learner-dashboard
     - ``LEARNER_HOME_MICROFRONTEND_URL``
   * - frontend-app-account
     - apps.local.openedx.io:1997/account
     - ``ACCOUNT_MICROFRONTEND_URL``
   * - frontend-app-profile
     - apps.local.openedx.io:1995/profile
     - ``PROFILE_MICROFRONTEND_URL``

The remaining default MFE URLs (authn, discussions, communications,
ora-grading, gradebook, catalog, admin-console) are also set in
``lms/envs/development.py``; see that file for the complete list and ports.

Notes and differences from devstack
===================================

* **No broker required.** ``CELERY_ALWAYS_EAGER = True`` runs Celery tasks
  in-process, so you do not need to run a message broker or worker.
* **MFE configuration is served by the LMS.** The MFE Config API
  (``/api/mfe_config/v1``) is enabled and populated so MFEs pick up the
  ``local.openedx.io`` URLs instead of their built-in ``localhost`` defaults.
* **CORS / CSRF / login redirects** for the default MFE origins are pre-declared
  in the ``development.py`` files.

.. _Tutor's development mode: https://docs.tutor.edly.io/dev.html
.. _openedx-platform README: https://github.com/openedx/edx-platform/blob/master/README.rst
