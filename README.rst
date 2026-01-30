Open edX Platform
#################
| |License: AGPL v3| |Status| |Python CI|

.. |License: AGPL v3| image:: https://img.shields.io/badge/License-AGPL_v3-blue.svg
  :target: https://www.gnu.org/licenses/agpl-3.0

.. |Python CI| image:: https://github.com/openedx/openedx-platform/actions/workflows/unit-tests.yml/badge.svg
  :target: https://github.com/openedx/openedx-platform/actions/workflows/unit-tests.yml

.. |Status| image:: https://img.shields.io/badge/status-maintained-31c653

*Formerly known as "edx-platform"*

Purpose
*******
The `Open edX Platform <https://openedx.org>`_ enables the authoring and
delivery of online learning at any scale.  The platform is written in
Python and JavaScript and makes extensive use of the Django
framework. At the highest level, the platform is composed of a
modular monolith, some independently deployable applications (IDAs), and
micro-frontends (MFEs) based on the ReactJS.

This repository hosts the monolith at the center of the Open edX
platform.  Functionally, the openedx-platform repository provides two services:

* CMS (Content Management Service), which powers Open edX Studio, the platform's learning content authoring environment; and
* LMS (Learning Management Service), which delivers learning content.

Documentation
*************

Documentation can be found at https://docs.openedx.org/projects/edx-platform.

Getting Started
***************

For Production
==============

Installing and running an Open edX instance is not simple.  We strongly
recommend that you use a service provider to run the software for you.  They
have free trials that make it easy to get started:
https://openedx.org/get-started/

However, if you have the time and expertise, then it is is possible to
self-manage a production Open edX instance. To help you build, customize,
upgrade, and scale your instance, we recommend using `Tutor`_, the
community-supported, Docker-based Open edX distribution.

You can read more about getting up and running with a Tutor deployment
at the `Site Ops home on docs.openedx.org`_.

For Development
===============

Tutor also features a `development mode`_ which will also help you modify,
test, and extend openedx-platform. We recommend this method for all Open edX
developers.

Bare Metal (Advanced)
=====================

It is also possible to spin up an Open edX platform directly on a Linux host.
This method is less common and mostly undocumented. The Open edX community will
only be able to provided limited support for it.

Running "bare metal" is only advisable for (a) developers seeking an
adventure and (b) experienced system administrators who are willing to take the
complexity of Open edX configuration and deployment into their own hands.

System Dependencies
-------------------

OS:

* Ubuntu 24.04

Interpreters/Tools:

* Python 3.11 or 3.12

* Node: See the ``.nvmrc`` file in this repository.

Services:

* MySQL 8.0

* Mongo 7.x

* Memcached

Language Packages:

* Frontend:

  - ``npm clean-install`` (production)
  - ``npm clean-install --dev`` (development)

* Backend build:

  - ``pip install -r requirements/edx/assets.txt``

* Backend application:

  - ``pip install -r requirements/edx/base.txt`` (production)
  - ``pip install -r requirements/edx/development.txt`` (development)

  Some Python packages have system dependencies. For example, installing these packages on Debian or Ubuntu will require first running ``sudo apt install python3-dev default-libmysqlclient-dev build-essential pkg-config`` to satisfy the requirements of the ``mysqlclient`` Python package.

Codejail Setup
--------------

As a part of the baremetal setup, you will need to configure your system to
work properly with codejail.  See the `codejail installation steps`_ for more
details.

.. _codejail installation steps: https://github.com/openedx/codejail?tab=readme-ov-file#installation

Build Steps
-----------

Create two MySQL databases and a MySQL user with write permissions to both, and configure
Django to use them by updating the ``DATABASES`` setting.

Then, run migrations::

  ./manage.py lms migrate
  ./manage.py lms migrate --database=student_module_history
  ./manage.py cms migrate

Build static assets (for more details, see `building static
assets`_)::

  npm run build  # or, 'build-dev'

Download locales and collect static assets (can be skipped for development
sites)::

  make pull_translations
  ./manage.py lms collectstatic
  ./manage.py cms collectstatic

Set up CMS SSO (for Development)::

  ./manage.py lms manage_user studio_worker example@example.com --unusable-password
  # DO NOT DO THIS IN PRODUCTION. It will make your auth insecure.
  ./manage.py lms create_dot_application studio-sso-id studio_worker \
      --grant-type authorization-code \
      --skip-authorization \
      --redirect-uris 'http://localhost:18010/complete/edx-oauth2/' \
      --scopes user_id  \
      --client-id 'studio-sso-id' \
      --client-secret 'studio-sso-secret'

Set up CMS SSO (for Production):

* Create the CMS user and the OAuth application::

    ./manage.py lms manage_user studio_worker <email@yourcompany.com> --unusable-password
    ./manage.py lms create_dot_application studio-sso-id studio_worker \
        --grant-type authorization-code \
        --skip-authorization \
        --redirect-uris 'http://localhost:18010/complete/edx-oauth2/' \
        --scopes user_id

* Log into Django admin (eg. http://localhost:18000/admin/oauth2_provider/application/),
  click into the application you created above (``studio-sso-id``), and copy its "Client secret".
* In your private LMS_CFG yaml file or your private Django settings module:

 * Set ``SOCIAL_AUTH_EDX_OAUTH2_KEY`` to the client ID (``studio-sso-id``).
 * Set ``SOCIAL_AUTH_EDX_OAUTH2_SECRET`` to the client secret (which you copied).

Run the Platform
----------------

Startup the Platform
====================

First, ensure MySQL, Mongo, and Memcached are running.

Start the LMS::

  ./manage.py lms runserver 18000

Start the CMS::

  ./manage.py cms runserver 18010

This will give you a mostly-headless Open edX platform. Most frontends have
been migrated to "Micro-Frontends (MFEs)" which need to be installed and run
separately. At a bare minimum, you will need to run the `Authoring MFE`_,
`Learner Home MFE`_, and `Learning MFE`_ in order meaningfully navigate the UI.
A full list of the MFEs expected to run by default are listed below.

.. _Tutor: https://github.com/overhangio/tutor
.. _Site Ops home on docs.openedx.org: https://docs.openedx.org/en/latest/site_ops/index.html
.. _development mode: https://docs.tutor.edly.io/dev.html
.. _building static assets: ./docs/references/static-assets.rst
.. _Learner Home MFE: https://github.com/openedx/frontend-app-learner-dashboard
.. _Learning MFE: https://github.com/openedx/frontend-app-learning/
.. _Authoring MFE: https://github.com/openedx/frontend-app-authoring/

Expected MFEs and Default Ports
-------------------------------

Currently the following MFEs are enabled by default, and are expected to be
running at the given ports.

.. list-table::
   :header-rows: 1


   * - Service Name
     - Expected Location
     - Override Setting Name
   * - frontend-app-authoring
     - localhost:2001
     - COURSE_AUTHORING_MICROFRONTEND_URL
   * - frontend-app-learning
     - localhost:2000
     - LEARNING_MICROFRONTEND_URL
   * - frontend-app-learner-dashboard
     - localhost:1996
     - LEARNER_HOME_MICROFRONTEND_URL
   * - frontend-app-profile
     - localhost:1995
     - PROFILE_MICROFRONTEND_URL
   * - frontend-app-account
     - localhost:1997
     - ACCOUNT_MICROFRONTEND_URL

License
*******

The code in this repository is licensed under version 3 of the AGPL
unless otherwise noted. Please see the `LICENSE`_ file for details.

.. _LICENSE: https://github.com/openedx/openedx-platform/blob/master/LICENSE


More about Open edX
*******************

See the `Open edX site`_ to learn more about the Open edX world. You can find
information about hosting, extending, and contributing to Open edX software. In
addition, the Open edX site provides product announcements, the Open edX blog,
and other rich community resources.

.. _Open edX site: https://openedx.org


Getting Help
************

If you're having trouble, we have discussion forums at
https://discuss.openedx.org where you can connect with others in the community.

Our real-time conversations are on Slack. You can request a `Slack
invitation`_, then join our `community Slack team`_.

For more information about these options, see the `Getting Help`_ page.

.. _Slack invitation: https://openedx.org/slack
.. _community Slack team: http://openedx.slack.com/
.. _Getting Help: https://openedx.org/getting-help


Issue Tracker
*************

We use Github Issues for our issue tracker. You can search
`previously reported issues`_.  If you need to report a bug, or want to discuss
a new feature before you implement it, please `create a new issue`_.

.. _previously reported issues: https://github.com/openedx/openedx-platform/issues
.. _create a new issue: https://github.com/openedx/openedx-platform/issues/new/choose


How to Contribute
*****************

Contributions are welcome! The first step is to submit a signed
`individual contributor agreement`_.  See our `CONTRIBUTING`_ file for more
information – it also contains guidelines for how to maintain high code
quality, which will make your contribution more likely to be accepted.

New features are accepted. Discussing your new ideas with the maintainers
before you write code will also increase the chances that your work is accepted.

Code of Conduct
***************

Please read the `Community Code of Conduct`_ for interacting with this repository.

Reporting Security Issues
*************************

Please do not report security issues in public. Please email
security@openedx.org.

.. _individual contributor agreement: https://openedx.org/cla
.. _CONTRIBUTING: https://github.com/openedx/.github/blob/master/CONTRIBUTING.md
.. _Community Code of Conduct: https://openedx.org/code-of-conduct/

People
******

The current maintainers of this repository can be found on `Backstage`_.

.. _Backstage: https://backstage.openedx.org/catalog/default/component/openedx-platform
