##########################
edx-platform Documentation
##########################

Developer documentation for `edx-platform` can be found in the following
locations.

* The `edx-platform docs directory`_ contains some local developer
  documentation.

* The `Developer Documentation Index`_ in Confluence provides additional links
  to developer documentation for this and other projects. The rest of the `Open
  edX Development space`_ in Confluence provides additional documentation.

* User documentation and a more general Developer's Guide can be read on `Open
  edX ReadTheDocs`_.  The source for these guides can be found in the
  `docs.openedx.org`_ repository.

.. _edx-platform docs directory: https://github.com/openedx/edx-platform/tree/master/docs
.. _Developer Documentation Index: https://openedx.atlassian.net/wiki/spaces/DOC/overview
.. _Open edX Development space: https://openedx.atlassian.net/wiki/spaces/COMM/overview
.. _Open edX ReadTheDocs: http://docs.openedx.org/
.. _Hooks Extensions Framework: https://docs.openedx.org/en/latest/developers/concepts/hooks_extension_framework.html

.. toctree::
    :maxdepth: 1

    docstrings/docstrings

.. toctree::
    :hidden:

    how-tos/index
    references/index
    concepts/index
    extensions/tinymce_plugins
    decisions/index

.. grid:: 1 2 2 2
   :gutter: 3
   :padding: 0

   .. grid-item-card:: How-tos
      :class-card: sd-shadow-md sd-p-2
      :class-footer: sd-border-0

      * :doc:`how-tos/celery`
      +++
      .. button-ref:: how-tos/index
         :color: primary
         :outline:
         :expand:

   .. grid-item-card:: References
      :class-card: sd-shadow-md sd-p-2
      :class-footer: sd-border-0

      * :doc:`references/lms_apis`
      * :doc:`references/settings`
      * :doc:`references/featuretoggles`
      +++
      .. button-ref:: references/index
         :color: primary
         :outline:
         :expand:

   .. grid-item-card:: Concepts
      :class-card: sd-shadow-md sd-p-2
      :class-footer: sd-border-0

      * :doc:`concepts/extension_points`
      * :doc:`concepts/testing/testing`
      * :doc:`concepts/frontend/javascript`
      +++
      .. button-ref:: concepts/index
         :color: primary
         :outline:
         :expand:

   .. grid-item-card:: Hooks and Extensions
      :class-card: sd-shadow-md sd-p-2
      :class-footer: sd-border-0

      * `Hooks Extensions Framework`_
      * :doc:`extensions/tinymce_plugins`
      +++
      .. button-link:: https://docs.openedx.org/en/latest/developers/concepts/hooks_extension_framework.html
         :color: primary
         :outline:
         :expand:

         Hooks Extensions Framework


Change History
**************

* April 2025: Documentation moved to docs.openedx.org

* Jun 30, 2023

  * Added API, Feature Toggle and Settings docs.
  * Re-organized how the docs are laid out.

* December, 2020: Added documentation about new protocols for writing celery tasks.

* April, 2019: API and repository-specific documentation builds resumed.

* May, 2017: The local docs directory was cleared out to start fresh.

* January 13, 2015: The "edX Developer's Guide" was moved to
  `edx-documentation`_.

* November 3, 2014: The documentation for several sub-projects were moved into
  `edx-documentation`_.

.. _docs.openedx.org: https://github.com/openedx/docs.openedx.org
.. _edx-documentation: https://github.com/openedx/edx-documentation
