Preparing static assets for edx-platform
########################################

To run a production or development edx-platform site, you will need to `build
assets`_ assets using ``npm run ...`` commands. Furthermore, for a production
site, you will also need to `collect assets`_.

*Please note that developing new frontend pages for edx-platform is highly
discouraged. New frontend pages should be built as micro-frontends (MFEs),
which communicate with edx-platform over AJAX, but are built and deployed
independently. Eventually, we expect that MFEs will replace all edx-platform
frontend pages, except perhaps XBlock views.*

Configuration
*************

To customize the static assets build, set some or all of these variable in your
shell environment before building or collecting static assets. As noted below,
some of these values will automatically become available as Django settings in
LMS or CMS (unless you separately override them in a private Django settings
file or ``LMS_CFG``/``CMS_CFG`` yaml file).

.. list-table::
   :header-rows: 1

   * - Environment Variable
     - Default
     - Description
     - LMS Django Setting
     - CMS Django Setting

   * - ``COMPREHENSIVE_THEME_DIRS``
     - (empty)
     - Directories that will be searched when compiling themes.
       Separate multiple paths with colons (``:``).
     - ``COMPREHENSIVE_THEME_DIRS``
     - ``COMPREHENSIVE_THEME_DIRS``

   * - ``WEBPACK_CONFIG_PATH``
     - ``webpack.prod.config.js``
     - Path to Webpack config file
     - N/A
     - N/A

   * - ``STATIC_ROOT_LMS``
     - ``test_root/staticfiles``
     - Path to which LMS's static assets will be collected
     - ``STATIC_ROOT``
     - N/A

   * - ``STATIC_ROOT_CMS``
     - ``$STATIC_ROOT_LMS/studio``.
     - Path to which CMS's static assets will be collected
     - N/A
     - ``STATIC_ROOT``

   * - ``JS_ENV_EXTRA_CONFIG``
     - ``{}``
     - Global configuration object available to edx-platform JS modules. Specified as a JSON string.
       Known keys:

        * ``TINYMCE_ADDITIONAL_PLUGINS``
        * ``TINYMCE_CONFIG_OVERRIDES``

     - N/A
     - N/A

Build assets
************

Building frontend assets requires an active Node and Python environment with
dependencies installed::

  npm clean-install
  uv sync --no-default-groups --group assets


Once your environment variables are set and build dependencies are installed,
the one-sized-fits-all command to build assets is ``npm run build``. If
your needs are more advanced, though, you can use some combination of the
commands below:

.. list-table::
   :header-rows: 1

   * - Command
     - Meaning
     - Options
   * - ``npm run build``
     - Combines ``npm run webpack`` and ``npm run compile-sass``
     - None
   * - ``npm run build-dev``
     - Combines ``npm run webpack-dev`` and ``npm run compile-sass-dev``
     - None
   * - ``npm run webpack``
     - Build JS bundles with Webpack
     - Options are passed through to the `webpack CLI`_
   * - ``npm run webpack-dev``
     - Build JS bundles with Webpack for a development environment
     - Options are passed through to the `webpack CLI`_
   * - ``npm run compile-sass``
     - Compile default and/or themed Sass
     - Use ``--help`` to see available options
   * - ``npm run compile-sass-dev``
     - Compile default and/or themed Sass, uncompressed with source comments
     - Use ``--help`` to see available options
   * - ``npm run watch``
     - Dev-only. Combine ``npm run watch-webpack`` and ``npm run watch-sass``
     - None.
   * - ``npm run watch-webpack``
     - Dev-only. Wait for JS changes and re-run Webpack
     - Options are passed through to the `webpack CLI`_
   * - ``npm run watch-sass``
     - Dev-only. Wait for Sass changes and re-compile
     - None.

When supplying options to these commands, separate the command from the options
with a double-hyphen (``--``), like this::

    npm run compile-sass -- --themes-dir /my/custom/themes/dir

Omitting the double-hyphen will pass the option to ``npm run`` itself, which
probably isn't what you want to do.

If you would like to understand these more deeply, they are defined in
`package.json`_. Please note: the ``npm run`` command interfaces are stable and
supported, but their underlying implementations may change without notice.

.. _webpack CLI: https://webpack.js.org/api/cli/
.. _package.json: ../../package.json

Collect assets
**************

Once assets are built, they can be *collected* into another directory for
efficient serving. This is only necessary on production sites; developers can
skip this section.

First, ensure you have a Python enironment with all edx-platform dependencies
installed::

  uv sync --no-default-groups --group bundled

Next, download localized versions of edx-platform assets. Under the hood, this
command uses the `Open edX Atlas`_ tool, which manages aggregated translations
from edx-platform and its various plugins::

  make pull_translations

Finally, invoke `Django's collectstatic command`_, once for the Learning
Management System, and once for the Content Management Studio::

  ./manage.py lms collectstatic --noinput
  ./manage.py cms collectstatic --noinput

The ``--noinput`` option lets you avoid having to type "yes" when overwriting
existing collected assets.

.. _Open edX Atlas: https://github.com/openedx/openedx-atlas
.. _Django's collectstatic command: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#collectstatic
