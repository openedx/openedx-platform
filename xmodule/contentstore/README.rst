contentstore ("Files & Uploads")
################################

.. contents:: Contents
   :local:
   :depth: 2

Overview
********

``xmodule/contentstore`` is the storage backend for **course-level static assets** — the files a
course team uploads under Studio's *Content* → *Files & Uploads* page.
It is the "V1" companion to ModuleStore: where ModuleStore stores the *structure and fields* of
course content, contentstore stores the *binary blobs* that content refers to.

In practice it holds things like:

* images, PDFs, and other documents referenced from HTML blocks (``/static/diagram.png``),
* the course card image (``course_image``) and its generated thumbnails,
* video transcript ``.srt``/``.sjson`` files that predate (or fall back from) edx-val,
* ``python_lib.zip``, the code library used by custom-Python-graded CAPA problems,
* JavaScript/CSS/HTML used by JSInput problems.

It does **not** store video files (that's edx-val), XBlock-bundled static assets shipped in
Python packages, or V2 content library assets (those live in ``openedx_content``; see
`Replacing contentstore`_).

.. note::

   This directory is ``xmodule/contentstore``, the storage backend.
   It is unrelated to ``cms/djangoapps/contentstore``, which is Studio's general-purpose
   "authoring" Django app and merely happens to share the name.
   The Studio app *is* the primary consumer of this backend
   (see `cms/djangoapps/contentstore/asset_storage_handlers.py`_).

.. _cms/djangoapps/contentstore/asset_storage_handlers.py: ../../cms/djangoapps/contentstore/asset_storage_handlers.py

Module layout
*************

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Module
     - Contents
   * - `content.py <./content.py>`_
     - ``StaticContent`` / ``StaticContentStream`` (the in-memory representation of one asset,
       plus a large pile of static helpers for asset paths and URLs) and ``ContentStore``,
       the abstract backend interface.
   * - `mongo.py <./mongo.py>`_
     - ``MongoContentStore``, the only real implementation. MongoDB + GridFS.
   * - `django.py <./django.py>`_
     - ``contentstore(name="default")`` — the process-global, lazily constructed, cached
       singleton factory driven by the ``CONTENTSTORE`` Django setting.
   * - `utils.py <./utils.py>`_
     - ``empty_asset_trashcan()`` / ``restore_asset_from_trashcan()``, used only by two Studio
       management commands.

Key concepts
************

AssetKey
========

Every asset is identified by an ``opaque_keys.edx.keys.AssetKey`` (concretely, an
``AssetLocator``), scoped to a course run::

    asset-v1:OpenCraft+DemoX+2026+type@asset+block@diagram.png
    asset-v1:OpenCraft+DemoX+2026+type@thumbnail+block@diagram-jpg.jpg

The ``block_type`` is either ``asset`` or ``thumbnail``.
The very old ``c4x/org/course/asset/name`` serialization is still supported for pre-split
courses, and ``XASSET_LOCATION_TAG = 'c4x'`` is still baked into the Mongo documents of *all*
assets, new and old. (In course exports, ``assets.json`` still references ``"tag": "c4x"``
each asset and uses a ``c4x``-style reference to thumnbnail locations.)

The asset namespace is **flat** (no subfolders): ``StaticContent.compute_location()``
replaces ``/`` with ``_``, so ``images/fig1.png`` and ``images_fig1.png`` collide.
In part, this is because ``AssetKey`` does not allow the filename to contain ``/``,
so it would be impossible to reference an asset in a subfolder. However, course
import/export tarballs **can** organize assets using subfolders, and the "full"
hierarchical path is stored in the ``import_path`` field (see ``assets.json``).

So: in a course export tarball, assets can be in subfolders; they get flattened
into one namespace on import, but put back into subfolders again on export.
Making the situation a little more confusing, assets imported from a subfolder
will have their ``displayname`` value set to just their filename. So: a course
tarball may have ``usa/flag.png`` and ``mexico/flag.png`` but since each has
``"displayname": "flag.png"``, they will both be listed as just ``flag.png`` in
the "Files & Uploads" UI.

.. _cms/djangoapps/contentstore/helpers.py: ../../cms/djangoapps/contentstore/helpers.py

StaticContent and StaticContentStream
=====================================

``StaticContent`` is a plain object bundling the asset's key, display name, MIME type, bytes,
length, upload date, ``locked`` flag, optional ``thumbnail_location``, optional ``import_path``
(where the file lived in the exported OLX tarball), and ``content_digest`` (an MD5 of the data,
stored in Mongo as ``custom_md5``).

``StaticContentStream`` is the same thing but wrapping an open GridFS file handle instead of
bytes, with ``stream_data()`` / ``stream_data_in_range(first, last)`` generators. This is what
makes HTTP ``Range`` requests work in the contentserver, and what avoids buffering large files
in memory.

Three URL forms
===============

The same asset appears in three different shapes, and much of ``content.py`` exists to convert
between them:

1. **Portable / "durable"** — ``/static/diagram.png``. This is what is stored in OLX and in
   XBlock fields. It survives course reruns and copy/paste because it contains no course ID.
   However, even though it looks like a URL, it is not a valid URL and must be "expanded" to
   the full URL (next form, below) before it can be used. A lot of complexity in the XBlock
   editing/import/export/rendering code paths in our platform comes from this mismatch.
2. **Serialized asset key** — ``/asset-v1:OpenCraft+DemoX+2026+type@asset+block@diagram.png``.
   This is what the browser actually requests. This form is also sometimes used in OLX,
   despite being "non-portable" as it encodes the course ID (including run). (The editing
   frontend tries to aggressively rewrite these to portable form when editing any HTML
   component, and the `rewrite_nonportable_content_links`_ function does so on import.)
3. **Versioned** — ``/assets/courseware/v1/<md5>/asset-v1:...``. A CDN-friendly, immutable path
   built from the asset's content digest; a mismatched digest 301-redirects to the current one.

Conversion from (1) to (2)/(3) happens at render time in
`common/djangoapps/static_replace <../../common/djangoapps/static_replace/__init__.py>`_, which
calls ``StaticContent.get_canonicalized_asset_path()``.
That function does a contentstore ``find()`` per URL in order to decide whether the asset is
``locked`` (limited to enrolled users; never CDN-served) and to pick up its digest.

.. _rewrite_nonportable_content_links: https://github.com/openedx/openedx-platform/blob/feb3e3fd95978b5ccf7ca760516bd68cd2e5e331/xmodule/modulestore/store_utilities.py#L24-L78

Locking
=======

An asset can be marked ``locked``, which means "only enrolled learners may download it".
Unlocked assets are world-readable by anyone who knows the URL. This single boolean is the
only authorization consideration for contentstore. Access is enforced at serve time in
`openedx/core/djangoapps/contentserver/views.py <../../openedx/core/djangoapps/contentserver/views.py>`_,
with one hard-coded special case: ``python_lib.zip`` is restricted to users with Studio read
access (gated by the ``course_assets.allow_download_code_library`` waffle flag). (In other
words, only instructors and not students should be able to view that file, as it may contain
grading code.)

Thumbnails
==========

``ContentStore.generate_thumbnail()`` (implemented in the *base* class, not the Mongo one) uses
Pillow to produce a 128×128 JPEG for any ``image/*`` upload, saves it as a second asset with
``block_type='thumbnail'``, and returns its key so the caller can set
``content.thumbnail_location``. SVGs are stored verbatim. Failures are swallowed and logged —
thumbnails are best-effort.

Thumbnails are **not** included in a course's export tarball, however the ``assets.json``
does include a ``thumbnail_location`` key and value that are related to the thumbnails.
This seems to be vestigial, as ``thumbnail_location`` is ignored on import.

The trashcan
============

``contentstore('trashcan')`` returns a second ``MongoContentStore`` instance pointed at a
different GridFS bucket (``trash_fs``), configured via
``CONTENTSTORE['ADDITIONAL_OPTIONS']['trashcan']``. Deleting an asset in Studio copies it there
first, so ``restore_asset_from_trashcan`` can undo it. Nothing empties it automatically; the
``empty_asset_trashcan`` management command exists for that.

Relationship to modulestore
***************************

``contentstore`` and ``modulestore`` are **separate stores that are wired together but do not share
data**.

Wiring
======

``xmodule/modulestore/django.py`` constructs the global modulestore and passes
``contentstore()`` into it as the ``contentstore`` constructor kwarg::

    _MIXED_MODULESTORE = create_modulestore_instance(
        settings.MODULESTORE['default']['ENGINE'],
        contentstore(),                       # <-- here
        ...
    )

``ModuleStoreRead.__init__`` stores it as ``self.contentstore``, ``MixedModuleStore`` forwards
it down to each backing store, and ``ModuleStoreWriteBase`` uses it in exactly three places
(`xmodule/modulestore/__init__.py <../modulestore/__init__.py>`_):

* ``clone_course()`` → ``self.contentstore.copy_all_course_assets(source, dest)`` (course reruns)
* ``delete_course()`` → ``self.contentstore.delete_all_course_assets(course_key)``
* ``close_connections()`` / ``_drop_database()`` → connection and test teardown

So the *only* structural coupling is course-lifecycle bookkeeping: create a rerun, delete a
course, tear down a test database.

Import/export
=============

Course OLX import and export are the other joint operation. Both take a contentstore
explicitly rather than reading it off the modulestore:

* ``export_course_to_xml(modulestore, contentstore, ...)`` in
  `xml_exporter.py <../modulestore/xml_exporter.py>`_ calls
  ``contentstore.export_all_for_course()``, which writes every asset into ``static/`` and
  every asset's metadata (display name, locked, import_path, content type) into
  ``policies/assets.json``.
* ``import_course_from_xml(..., static_content_store=contentstore(), ...)`` in
  `xml_importer.py <../modulestore/xml_importer.py>`_ walks ``static/``, reads
  ``policies/assets.json``, generates thumbnails, and ``save()``\s each file.

Independent lifecycles
======================

Beyond the three hooks above, the two stores know nothing about each other. There is no
referential integrity: deleting an HTML block does not delete the images it referenced, and
deleting an asset does not invalidate blocks that point at it. Studio's "usage locations" panel
computes references by scanning block data on demand, not by maintaining a link table — see
`Asset usage tracking`_.

MongoDB and GridFS
******************

``MongoContentStore`` talks to GridFS directly via ``gridfs.GridFS(mongo_db, bucket)``, using
the connection helper in `xmodule/mongo_utils.py <../mongo_utils.py>`_. It deliberately does
**not** wrap the database in the ``MongoProxy`` retry wrapper that ModuleStore uses, because
GridFS breaks on the proxy.

It keeps three handles:

* ``self.fs`` — the ``GridFS`` object (default bucket ``fs``, or ``trash_fs`` for the trashcan)
* ``self.fs_files`` — the raw ``fs.files`` collection, used for all querying and metadata updates
* ``self.chunks`` — the raw ``fs.chunks`` collection, used only for dropping in tests

Document shape
==============

Each asset is one GridFS file whose ``_id`` is derived by ``asset_db_key()``:

* **Split (modern) courses** — ``_id`` is the *string* form of the asset key
  (``asset-v1:org+course+run+type@asset+block@name``), and a parallel ``content_son`` field holds
  an ordered BSON ``SON`` document ``{category, name, course, tag, org, revision, run}`` that all
  the queries actually filter on.
* **Deprecated (Old Mongo) courses** — ``_id`` *is* that ``SON`` document, with no ``run``.

Key ordering in the ``SON`` is load-bearing: MongoDB compares embedded documents by exact key
order, so ``ordered_key_fields`` in ``mongo.py`` must never be reordered or the assets become
unfindable. ``make_id_son()`` exists purely to re-impose that ordering on documents coming back
out of pymongo.

Additional per-file fields stored alongside GridFS's built-ins (``length``, ``chunkSize``,
``uploadDate``, ``md5``, ``filename``): ``displayname``, ``contentType``, ``content_son``,
``thumbnail_location`` (stored as a deprecated *list* repr, hence the ``thumbnail_location[4]``
indexing you'll see when reading it back), ``import_path``, ``locked``, and ``custom_md5``.

``custom_md5`` exists because GridFS's own ``md5`` field was deprecated and removed in newer
server/driver versions; ``save()`` computes the digest itself while streaming chunks.

Operations
==========

* **save** — GridFS has no replace, and this code uses the asset key as the ``_id`` rather than
  as a version-able ``filename``, so ``save()`` does a delete-then-insert. There is no
  versioning of assets, ever.
* **find** — ``fs.get(content_id)``, wrapped into a ``StaticContent`` (reads all bytes) or a
  ``StaticContentStream`` (keeps the handle open). Raises ``xmodule.exceptions.NotFoundError``
  unless ``throw_on_not_found=False``.
* **listing** — ``_get_all_content_for_course()`` queries ``fs_files`` with the SON prefix built
  by ``query_for_course()``, and supports skip/limit pagination, sorting by ``uploadDate`` or by
  ``displayname`` (using a locale-aware, case-insensitive Mongo ``collation``), and arbitrary
  extra ``filter_params`` (Studio passes regex filters for text search and content type).
  Returns ``(assets, total_count)``.
* **copy_all_course_assets** — reads every source file into memory and re-``put``\s it under a
  rewritten key. Explicitly noted in the code as "fairly expensive"; this is what makes course
  reruns slow for asset-heavy courses.
* **ensure_indexes** — creates six sparse background compound indexes over ``_id.*``/
  ``content_son.*`` plus ``uploadDate``/``displayname``. Invoked by the
  ``ensure_indexes`` management command, not automatically.

Public API surface, and who uses it
***********************************

The whole API is reached through ``contentstore()``, ``StaticContent``, or
``AssetManager.find()`` (a one-line wrapper over ``contentstore().find()``).
Counting non-test call sites in this repo:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Method
     - Non-test consumers
   * - ``find(key, throw_on_not_found=True, as_stream=False)``
     - By far the most-used. ``AssetManager.find`` →
       `contentserver/views.py`_ (serving),
       `xblock_serializer/utils.py`_ (OLX export / copy-paste),
       `openedx/core/lib/courses.py`_ (course images),
       ``StaticContent.get_canonicalized_asset_path`` (URL rewriting, i.e. *every rendered page*).
       Direct calls from `asset_storage_handlers.py`_, `content_staging/api.py`_,
       `video_config/transcripts_utils.py`_, `contentstore/views/transcripts_ajax.py`_,
       `xmodule/util/sandboxing.py`_ (``python_lib.zip``), `contentstore/helpers.py`_.
   * - ``save(content)``
     - `asset_storage_handlers.py`_ (upload), `xml_importer.py`_ (course import),
       `contentstore/helpers.py`_ (paste), `video_config/transcripts_utils.py`_,
       `contentstore/utils.py <./utils.py>`_ (trashcan restore).
   * - ``delete(location_or_id)``
     - `asset_storage_handlers.py`_, `video_config/transcripts_utils.py`_,
       `contentstore/utils.py <./utils.py>`_.
   * - ``get_all_content_for_course(key, start, maxresults, sort, filter_params)``
     - `asset_storage_handlers.py`_ — this *is* the Files & Uploads listing endpoint.
       Also `contentstore/utils.py <./utils.py>`_.
   * - ``generate_thumbnail(content, tempfile_path, dimensions)``
     - `asset_storage_handlers.py`_, `xml_importer.py`_, `contentstore/helpers.py`_,
       `openedx/core/lib/courses.py`_.
   * - ``set_attr(key, attr, value)``
     - One call site: the lock/unlock toggle in `asset_storage_handlers.py`_.
   * - ``get_attr`` / ``get_attrs`` / ``set_attrs``
     - Tests only.
   * - ``export_all_for_course`` / ``export``
     - `xml_exporter.py`_ only.
   * - ``get_all_content_thumbnails_for_course``
     - `contentstore/utils.py <./utils.py>`_ (trashcan emptying) and tests.
   * - ``copy_all_course_assets`` / ``delete_all_course_assets``
     - ModuleStore ``clone_course`` / ``delete_course`` only.
   * - ``remove_redundant_content_for_courses``
     - The ``cleanup_assets`` management command only. Mongo-specific (deletes ``.DS_Store``
       and ``._*`` files left over from imports).
   * - ``ensure_indexes``
     - The ``ensure_indexes`` management command only. Mongo-specific.
   * - ``close_connections`` / ``_drop_database``
     - ModuleStore teardown and test fixtures. Mongo-specific.

.. _contentserver/views.py: ../../openedx/core/djangoapps/contentserver/views.py
.. _xblock_serializer/utils.py: ../../openedx/core/lib/xblock_serializer/utils.py
.. _openedx/core/lib/courses.py: ../../openedx/core/lib/courses.py
.. _asset_storage_handlers.py: ../../cms/djangoapps/contentstore/asset_storage_handlers.py
.. _content_staging/api.py: ../../openedx/core/djangoapps/content_staging/api.py
.. _video_config/transcripts_utils.py: ../../openedx/core/djangoapps/video_config/transcripts_utils.py
.. _contentstore/views/transcripts_ajax.py: ../../cms/djangoapps/contentstore/views/transcripts_ajax.py
.. _xmodule/util/sandboxing.py: ../util/sandboxing.py
.. _contentstore/helpers.py: ../../cms/djangoapps/contentstore/helpers.py
.. _xml_importer.py: ../modulestore/xml_importer.py
.. _xml_exporter.py: ../modulestore/xml_exporter.py

``StaticContent`` static helpers (used independently of any store)
==================================================================

These are pure functions and are used far more widely than the store itself. Any replacement
must either keep them working or migrate every caller:

``compute_location``, ``get_asset_key_from_path``, ``get_location_from_path``,
``serialize_asset_key_with_slash``, ``get_static_path_from_location``,
``get_base_url_path_for_course_assets``, ``get_canonicalized_asset_path``,
``is_versioned_asset_path`` / ``parse_versioned_asset_path`` / ``add_version_to_asset_path``,
``is_c4x_path``, ``generate_thumbnail_name``.

Consumers include `static_replace <../../common/djangoapps/static_replace/__init__.py>`_,
`contentserver <../../openedx/core/djangoapps/contentserver/views.py>`_,
`html_block.py <../html_block.py>`_ (the ``base_asset_url`` passed to the TinyMCE editor),
`video_block.py <../video_block/video_block.py>`_ (rewriting the ``handout`` field on import),
`content_staging <../../openedx/core/djangoapps/content_staging/api.py>`_, and the
`xblock_serializer <../../openedx/core/lib/xblock_serializer/utils.py>`_.

Serving
=======

Reads at runtime almost all funnel through
`openedx/core/djangoapps/contentserver <../../openedx/core/djangoapps/contentserver/>`_, which
owns the ``/c4x/``, ``/asset-v1:``, and ``/assets/courseware/`` URL patterns. It adds a
memcached layer (``caching.py``, capped at 1 MB per asset, keyed by asset key and versioned by
``STATIC_CONTENT_VERSION``), ``Range`` support, conditional requests, cache-control headers
driven by the ``locked`` flag, and the enrollment check.

Configuration
=============

::

    CONTENTSTORE = {
        'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
        'DOC_STORE_CONFIG': {...},         # passed as **kwargs to the engine
        'ADDITIONAL_OPTIONS': {            # per-named-instance overrides
            'trashcan': {'bucket': 'trash_fs'},
        },
    }

``ENGINE`` is a swappable dotted path, so an alternative ``ContentStore`` subclass is a
supported (if never-exercised) extension point.

Asset usage tracking
********************

Nothing records which blocks reference which assets. "Which blocks use this file?" is answered
by brute-force scanning the course on demand, every time it is asked.

The single implementation is
`_get_asset_usage_path() <../../cms/djangoapps/contentstore/asset_storage_handlers.py>`_ in
Studio's asset handler. It is called from two places:

* ``_assets_json()`` — the Files & Uploads listing endpoint, once per request, for the whole
  page of assets being listed. Its result is folded into each asset's JSON as
  ``usage_locations`` by ``get_asset_json()``.
* ``get_asset_usage_path_json()`` — a per-asset endpoint at
  ``/assets/{course_key}/{asset_key}/usage``, which the file-detail sidebar calls.

What the UI shows
=================

There is no ``active`` field in the API. The **Active** column in the Files & Uploads MFE is
rendered from ``usage_locations`` being non-empty: a checkmark means "something in this course
appears to reference this file". The same list populates the **Usage locations** links in the
file-detail sidebar, each entry being
``{'display_location': '<subsection> - <unit> / <block>', 'url': '/container/<unit>#<block>'}``.

How it is computed
==================

#. ``modulestore().get_items(course_key, qualifiers={'category': 'vertical'})`` — load every
   vertical in the course.
#. Flatten ``vertical.get_children()`` into a list of blocks.
#. For each block × each asset:

   * **video blocks** — test whether the serialized asset key appears in the block's ``handout``
     field.
   * **every other block** — test whether either ``/static/<name>`` or the full ``asset-v1:...``
     string appears as a substring of ``block.data``.

#. On a hit, walk ``block.get_parent()`` twice to build the unit/subsection display names.
   Any ``AttributeError`` during that walk is swallowed and the block skipped.

Caveats
=======

The scan is much narrower than "is this file used", so **an empty Active cell is not grounds for
deleting a file**:

* **Only direct children of verticals are scanned.** Anything nested deeper is missed, as are
  the verticals themselves.
* **Only the ``data`` field is consulted** (plus ``handout`` for videos). XBlocks that keep
  asset references in other fields — most third-party blocks, e.g. drag-and-drop-v2's JSON
  config — never match.
* **Course-level references do not count.** The course card image, static tabs/custom pages, and
  course updates/handouts are not scanned, so ``course_image`` typically shows as inactive.
* **Video transcripts do not count** — only ``handout`` is checked on video blocks.
* **Indirect references are invisible.** A JSInput HTML file that internally references
  ``fig1.png``, or anything ``python_lib.zip`` opens at runtime, will not mark its target active.
* **Substring matching gives false positives on prefixes** — an asset named ``fig.png`` matches
  a reference to ``/static/fig.png2``.
* **Cost scales with course size, not page size.** Every load of the Files & Uploads page walks
  all verticals and their children, regardless of the 50-asset page being displayed.

This is a direct consequence of `Independent lifecycles`_: because there is no link table
between assets and the blocks referencing them, usage can only ever be recomputed, and only
ever approximated. A replacement backend that recorded references at authoring time could make
this exact, cheap, and safe to delete against — worth treating as a feature to gain rather than
behaviour to port.

The ``assetstore`` dead end
***************************

``xmodule/assetstore`` looks like a second asset backend but is not one. It defines
``AssetMetadata``, a richer metadata record than the GridFS document — it adds ``curr_version``/
``prev_version``, full created-by/edited-by provenance, an ``internal_name`` "handle for the
storage system", and an open-ended ``fields`` dict. It was an unfinished attempt to give course
assets versioning, an audit trail, and a blob store that need not be GridFS.

ModuleStore, not contentstore, would have persisted it: Split keeps it inside the versioned
course *structure* document under ``structure['assets']``
(`split.py <../modulestore/split_mongo/split.py>`_, ``_find_course_assets``), while Old Mongo
uses a separate ``assetstore`` collection. The exporter writes it to ``assets/assets.xml`` and
the importer reads it back.

**The feature is dead, and dead circularly.** Excluding tests, ``save_asset_metadata``\ (``_list``)
has only two production callers: ``xml_importer.py``, parsing ``assets.xml`` on import, and
``mixed.py``'s ``copy_all_asset_metadata`` on a cross-store clone (copying from a source that is
itself empty). Studio's upload path never calls either — ``update_course_run_asset()`` goes
straight to ``contentstore().save()``. So ``assets.xml`` is written only from data that is only
ever populated by reading ``assets.xml``; in practice it is ``<assets/>`` in every real course
export, and importing an empty one is a no-op.

The read side was severed separately: ``AssetManager.find()``
(`xmodule/assetstore/assetmgr.py <../assetstore/assetmgr.py>`_) used to consult
``find_asset_metadata()`` first and now goes straight to the contentstore, because — per its
docstring — every lookup had to unpickle and decompress the whole cached course structure. That
is the cost of putting per-asset metadata inside the versioned structure document, and it is
worth remembering when designing a replacement.

Assume contentstore is the sole source of truth for asset data *and* metadata. A replacement can
ignore ``AssetMetadata`` entirely — there is no production data to migrate — but should keep
emitting an (empty) ``assets/assets.xml`` and keep tolerating one on import, since the export
format is a compatibility surface.

Replacing contentstore
**********************

As documented in `openedx-core ADR 0005, "Serving Course Team Authored Static Assets"
<https://github.com/openedx/openedx-core/blob/main/docs/openedx_content/decisions/0005-serving-static-assets.rst>`__, we
are considering replacing ``contentstore`` with a new system based on ``openedx-content``,
which would ultimately store files on S3-like storage instead of GridFS, moving us closer
to removing MongoDB as a dependency.

What the new API already gives you
==================================

``openedx_content`` already stores V2 content library assets this way:

* ``openedx_content.applets.media`` — ``Media`` (a content-addressed blob: ``hash_digest``,
  ``size``, ``media_type``, either an inline ``text`` field or a file in a configurable
  ``Storage``) and ``MediaType``. Files are named by hash and stored in a bucket that is
  **not** publicly readable.
* ``openedx_content.applets.components`` — ``ComponentVersionMedia`` maps a human path
  (``static/images/fig1.png``) onto a ``Media``, scoped to a specific ``ComponentVersion``.
* ``get_redirect_response_for_component_asset()`` — produces an ``X-Accel-Redirect`` response
  with correct ``Content-Type``/``Content-Disposition``/caching headers, intended to be handled
  by a Caddy/Nginx reverse proxy so Django never streams the bytes.
* Storage backend is configured by ``settings.OPENEDX_LEARNING['MEDIA']``.

The design is laid out in `openedx-core ADR 0005, "Serving Course Team Authored Static Assets"
<https://github.com/openedx/openedx-core/blob/main/docs/openedx_content/decisions/0005-serving-static-assets.rst>`__,
which explicitly anticipates courses migrating onto it: "we'll want to allow courses
to port their existing files and uploads into this system … probably by creating a non-XBlock,
filesystem Component type that can treat the entire course's uploads as a Component."

edx-platform already has a working, if unoptimised, precedent in
``get_component_version_asset`` in
`content_libraries/rest_api/blocks.py <../../openedx/core/djangoapps/content_libraries/rest_api/blocks.py>`_,
which calls the redirect API and then strips the ``X-Accel-Redirect`` header and streams the
content itself.

Model mismatches to resolve
===========================

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - contentstore
     - openedx_content media
   * - Scope
     - Course run
     - LearningPackage + ComponentVersion
   * - Namespace
     - Flat; ``/`` → ``_``
     - Real paths, ``static/a/b.png``
   * - Versioning
     - None. Save overwrites.
     - Every asset belongs to a ComponentVersion
   * - Publishing
     - None. Uploads are live instantly.
     - Draft/published distinction
   * - Identity
     - ``AssetKey`` (opaque key, appears in URLs and in the DB ``_id``)
     - Media hash + path + component version UUID
   * - Dedup
     - None
     - Content-addressed, shared across references
   * - Permissions
     - One ``locked`` boolean, course-level
     - ???
   * - Size cap
     - ``MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB`` setting
     - ``Media.MAX_FILE_SIZE`` = 50 MB
   * - Serving
     - Django streams from GridFS, memcached in front
     - ``X-Accel-Redirect`` to a reverse proxy, separate domain

The scope mismatch is the big one. Course "Files & Uploads" is deliberately *not* attached to
any block — it is a course-wide bucket that any block may reference by ``/static/`` path, and
Studio's UI lists and manages it as such. Mapping it onto ``ComponentVersionMedia`` requires
inventing a course-level pseudo-component (as ADR 0005 suggests), or adding a course-level
media association to ``openedx_content`` alongside the component-level one.

Specific considerations
=======================

**1. URL compatibility is the hardest constraint.**
Asset keys are not an internal detail: they are baked into published course content, into
learners' browser caches and bookmarks, into CDN caches, into third-party course exports, and
into every OLX tarball ever produced. Any replacement needs
``/asset-v1:...+type@asset+block@foo.png`` (and maybe ``/c4x/...``?) to keep resolving — most likely by
retaining the contentserver URL patterns and having the view resolve an ``AssetKey`` to a new
backend record. Note that ADR 0005 also mandates serving assets from a *different domain*,
which is incompatible with the current same-origin URLs; both need to be served during any
transition.

**2. Read paths are hot and latency-sensitive.**
``StaticContent.get_canonicalized_asset_path()`` performs a ``find()`` per ``/static/`` URL
during rendering, just to read ``locked`` and ``content_digest``. On a page with many images
that is many round trips. Today they hit Mongo (which stores metadata and bytes together); with
S3 they must hit MySQL for metadata *only* and never touch S3 during rendering. The existing
memcached layer in ``contentserver/caching.py`` caches whole ``StaticContent`` objects including
their bytes — a hash-addressed backend suggests caching metadata separately from data.

**3. ``as_stream`` and ``Range`` must survive.**
The contentserver supports byte-range requests via ``StaticContentStream.stream_data_in_range``.
Under the ``X-Accel-Redirect`` model the proxy handles ranges natively, which is strictly better
— but any interim "Django streams it" implementation (like the library one today) loses this
unless it is written back in.

**4. Thumbnails are a contentstore concept, not a media concept.**
Thumbnails are stored as ordinary sibling assets with ``type@thumbnail`` keys, generated
eagerly at upload/import time, and surfaced in the Files & Uploads UI. ``openedx_content`` has
no equivalent. Options: keep generating them as ordinary media at a derived path, generate them
on demand, or add a proper thumbnail/derivative concept. Whatever is chosen must keep
``asset['thumbnail_location']`` working in the Studio listing JSON, including its odd
list-repr-index-4 encoding.

**5. Listing, sorting, filtering and pagination move from Mongo to the ORM.**
The Files & Uploads endpoint asks for skip/limit pagination, sort by upload date or by display
name with a *locale-aware case-insensitive collation*, plus regex text search and content-type
filters. All of that currently rides on Mongo queries and indexes built in ``ensure_indexes``.
Reproducing the collation behaviour on MySQL needs deliberate column collation choices (note
``openedx_django_lib`` already provides ``MultiCollationTextField`` and
``case_insensitive_char_field`` for exactly this reason).

**6. Import/export needs a compatibility story.**
``export_all_for_course`` writes ``static/*`` plus ``policies/assets.json`` with
``displayname``/``locked``/``contentType``/``import_path``. That format is consumed by other
tools and by every existing course export. A new backend must produce and consume the identical
tarball layout, including the ``import_path`` round-trip that restores the original directory
structure on export even though storage is flat.

**7. The permissions model needs to grow, carefully.**
Today: a single ``locked`` bool, checked against ``CourseEnrollment``, plus the ``python_lib.zip``
special case. The new system could support richer, per-component checks, but the *upgrade* must
preserve current semantics exactly — silently making previously-public assets private (or
vice versa) on migration would be a serious regression. Note also that unlocked assets are
currently CDN-cacheable and locked ones are explicitly ``no-store``; that distinction must
survive.

**8. Course lifecycle hooks are the modulestore contract.**
``copy_all_course_assets`` (rerun) and ``delete_all_course_assets`` (course delete) are called
from ``ModuleStoreWriteBase``. Content-addressed storage makes rerun dramatically cheaper — a
rerun becomes a metadata copy rather than a byte-for-byte re-upload — but deletion becomes
harder, because blobs may be shared. ``openedx_content`` scopes ``Media`` to a
``LearningPackage`` partly to make that garbage-collection tractable; a course-level design has
to make the same call.

**9. The trashcan.**
Soft-delete-to-a-second-bucket has no analogue in ``openedx_content``. Given that assets there
are immutable and versioned, the natural replacement is "don't delete, unpublish" — but the two
management commands (``empty_asset_trashcan``, ``restore_asset_from_trashcan``) and the Studio
delete flow all need a decision.

**10. Assorted consumers that will each need attention.**

* **Video transcripts** — ``video_config/transcripts_utils.py`` and ``transcripts_ajax.py``
  read/write ``.srt``/``.sjson`` through the contentstore as a fallback behind edx-val. Several
  call sites assume synchronous ``find().data``.
* **``python_lib.zip``** — ``SandboxService.get_python_lib_zip`` is called on every codejail
  execution; it needs a fast, cached path. Its access restriction is currently special-cased in
  the contentserver view by *filename*.
* **Copy/paste and library sync** — ``content_staging`` copies asset bytes into
  ``StagedContentFile`` rows (with a 10 MB cutoff) and back out again via
  ``contentstore().save()``. With content-addressed storage this could become a reference copy
  instead of a byte copy, which is a meaningful simplification worth designing for rather than
  porting verbatim.
* **JSInput dependency discovery** — ``get_js_input_files_if_using`` reads an uploaded HTML
  file's *content* and regexes out its relative asset references. Relative links between assets
  are exactly the case ADR 0005 calls out as the reason hash-named blobs cannot be served
  directly.
* **``StaticContent`` as a data type** — several consumers pass ``StaticContent`` objects around
  (the memcache layer pickles them; ``content_staging`` and the serializer read ``.data``). A
  replacement will probably want to keep a compatible façade object for a while.

**11. Migration mechanics.**
Existing GridFS data for every course on an installation has to move. Consider: a dual-read
period (new store first, GridFS fallback) behind a per-course waffle flag; a management command
to migrate a course at a time; how to migrate courses that are actively being edited; and how to
verify a migrated course (the ``custom_md5`` digest already stored on every file makes
byte-level verification straightforward, where present — it is ``None`` on older records).

**12. Things safe to drop.**
``get_attr``/``get_attrs``/``set_attrs`` are test-only. ``remove_redundant_content_for_courses``
and ``ensure_indexes`` are Mongo-specific chores with no analogue. ``_drop_database`` is test
scaffolding. The deprecated Old-Mongo ``c4x`` ``_id``-as-SON code path only matters for courses
that have not been migrated to split; check whether any still exist before carrying that
complexity forward.
