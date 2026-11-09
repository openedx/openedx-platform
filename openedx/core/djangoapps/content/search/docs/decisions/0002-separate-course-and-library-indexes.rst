Separate Meilisearch Indexes for Course and Library Content
############################################################

Status
******

Accepted


Context
*******

`0001-meilisearch.rst`_ put all Studio content into one Meilisearch index,
``studio_content``: course blocks (``type = course_block``) plus Libraries V2
components, containers and collections.

Library authoring updates that index synchronously so the frontend sees the
change on its next query. Meilisearch's per-document indexing cost grows with
index size, and course blocks dominate the index on large instances. One
instance reported 1.3 million documents (11.9 GiB) of which 99.93% were course
blocks, and saw Studio time out when creating library components
(`openedx-platform#38993`_).

Course content is indexed asynchronously by Celery tasks, so it does not need to
share an index with library content.

.. _0001-meilisearch.rst: ./0001-meilisearch.rst
.. _openedx-platform#38993: https://github.com/openedx/openedx-platform/issues/38993


Decision
********

Studio content is stored in two indexes:

* ``<MEILISEARCH_INDEX_PREFIX>studio_content``: course blocks
  (``DocType.course_block``). This is the original index name, so upgrading does
  not require reindexing course content.
* ``<MEILISEARCH_INDEX_PREFIX>studio_library_content``: library blocks, library
  containers and collections.

Both indexes use the same settings. Every write is routed by document type or by
the learning context of its key. Rebuild locks and temporary ``_new`` indexes
are per index.

Every modulestore block is indexed as a ``course_block`` document, including
course blocks that link to upstream library content, so those stay in the
course index.

``GET /api/content_search/v2/studio/`` returns ``course_index_name`` and
``library_index_name``, and one tenant token whose search rules cover both
indexes with the same access filter. Each Studio search surface already
searches either course content or library content, never both, so each one
picks the matching index. ``index_name`` is still returned (equal to
``course_index_name``) for one release, for frontends that predate the split.


Upgrading
*********

On an instance that already has a populated ``studio_content`` index:

#. ``./manage.py cms migrate``. The ``post_migrate`` reconciliation creates and
   configures the empty library index.
#. ``./manage.py cms reindex_studio --libraries-only``. This enqueues a Celery
   task that rebuilds the library index, then deletes every document with
   ``type != "course_block"`` from the course index. Courses are not reindexed.

Deploy a frontend that reads ``library_index_name`` at the same time. A frontend
that only knows ``index_name`` searches the course index, so it stops finding
library content once step 2 has run.

A full ``./manage.py cms reindex_studio`` also populates both indexes and runs
the same cleanup, but it reindexes every course.


Consequences
************

* Library writes no longer pay for the size of the course index.
* A future search across courses and libraries together would need a
  multi-index search request.
* The Meilisearch API key used by Studio must be allowed to manage both index
  names (and their ``_new`` temporary indexes).
