3. Offline content generation architecture
==========================================

Status
------

Proposed

Supersedes the implementation decisions of `ADR-001`_; its product decisions stand: a user-agnostic
``offline_view`` that XBlocks opt into, archives generated on publish, an ``offline_download`` field in the
mobile API, and a JavaScript bridge for offline submissions. Narrows `ADR-002`_: the shared-resources archive
(its decision 1) is deferred until the mobile applications can consume one; decisions 2–4 are unaffected.
ADR-001 and ADR-002 gain a status line pointing here.

Context
-------

ADR-001 decided the product shape of offline content: a user-agnostic ``offline_view`` that XBlocks opt into,
archives generated when a course is published, an ``offline_download`` field in the mobile API, and a
JavaScript bridge through which a page hands offline submissions to the application. The mobile applications
implement their side of it and fix the wire contract the platform must honour: ``offline_download {file_url,
last_modified, file_size}`` on ``/api/mobile/v4/course_info/blocks/``, a ZIP containing ``index.html`` and
``assets/``, and ``GET /api/mobile/v1/download_courses/{username}`` for the application-level downloads page.
The bridge the applications ship today is not part of that contract; decision 7 replaces it.

The platform side remains to be built: generation on the CMS, ``offline_view`` implementations, offline access to
internal files and uploads, the downloads page, and the course content that is not an XBlock. This ADR proposes
the architecture for that work. Its scope is the views for HTML, problems (read-only), survey and poll
(interactive) and drag and drop (read-only), and the course handouts and updates; word cloud is outside it, and
interactive problems are the next scope's (decision 11).

Two proposals accompany this ADR and hold the detail of decisions 3, 7 and 8: `offline_view for xblock-poll`_,
which also defines the runtime interface every ``offline_view`` uses, and `Read-only offline_view for
problem`_.

Decision
--------

1. One application, two roles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The feature is one Django application, ``openedx.features.offline_content``, installed in both the LMS and the
CMS with one ``AppConfig``. The CMS generates: its ``course_published`` and ``course_deleted`` receivers are
connected in both services, like the platform's other receivers of these signals, which only the CMS sends; its
tasks, renderer, collector, packager, storage writes and management command run on the CMS workers. The LMS
serves: it reads the manifest through one read-only module. One application keeps one migration history and
one settings namespace. The generation path depends on the LMS courseware only for the field-data binding that
Studio's preview also uses; it builds LMS course URLs from the LMS root of the course's site, following the
LMS URL patterns, which it must track. It builds no handler URLs (decision 3).

2. Trigger: the CMS publish signal, gated by a course flag
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generation is triggered by the existing ``SignalHandler.course_published`` signal in the CMS, the hook that
already fans out search indexing, course outlines and special-exam registration, when the course waffle flag
``offline_content.enable_offline_content`` (annotated per OEP-17) is on.

*Enqueueing.* On transaction commit, the receiver enqueues one course task, ``OFFLINE_CONTENT_PUBLISH_DELAY``
seconds later. It does not enqueue when a task for the course is already queued and has not started, which a
shared-cache key tracks and the task deletes as it starts; when the cache is unavailable, every publish
enqueues.

*The course task.* It walks the published structure once, with the course's handouts and updates blocks
(decision 10), and reconciles the manifest: rows for new blocks; removal of the rows, and the archives, of
blocks no longer published; and the list of rows to render, which is the new rows, the failed rows and the rows
whose block's ``published_on`` moved since their archive was built. Block tasks render that list in chunks,
declining the audience cases of decision 3 before they render.

*What a publish covers.* A unit publish renders that unit's components; a save of the handouts or of an update
is a publish of that block (decision 10). Deleting a section, subsection or unit publishes, so their archives go
at the next pass; a component deleted from a unit keeps its archive until the unit is published. Changes that
happen outside a publish reach archives only through the management command ``generate_offline_content``:
platform or block-package upgrades, a change of the LMS root, replaced course files, inherited course settings.
The command also back-fills one course or all courses, reports coverage and prunes.

*Overlaps and deletion.* Passes may overlap; the write rule of decision 5 keeps them correct. A receiver of the
existing ``course_deleted`` signal removes a deleted course's rows and archives. There is no ``item_deleted``
receiver, no new modulestore signal, no coupling to other publish-time tasks, and nothing crosses from the CMS
to the LMS through a queue or an HTTP call.

3. ``offline_view`` is the only view that is rendered
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``offline_view`` is a named XBlock view that a block class opts into, implemented in the block's own package:
``xblocks-contrib`` for the extracted HTML and problem blocks, ``xblock-poll`` for the survey and poll,
``xblock-drag-and-drop-v2`` for drag and drop, and ``openedx-platform`` itself for the course handouts and
updates (decision 10); ``openedx-platform`` picks the others up through its requirement pins.

``offline_view`` is the one rendering of a block's content, not a second frontend beside ``student_view``. It
renders content and settings only; the learner's state is fetched by the block's JavaScript after load, and
interactions go through the ``runtime`` object the JavaScript is given (decision 7). A block builds its
``student_view`` on it, and its ``public_view`` when its author judges the content fit for visitors who are not
enrolled. `offline_view for xblock-poll`_ sets out this split and the runtime changes it needs.

The CMS renders it with no user and no request, in the course language, through a runtime prepared like
Studio's preview runtime minus everything that comes from a request: no user, no session, no Studio wrappers,
no asides. During the render, reading a user-scoped field raises and ``handler_url`` raises, so a view that
depends on the learner or on a handler URL fails when it is generated and not silently. There is no
``student_view`` fallback: a block without ``offline_view`` is recorded as unsupported and never announced in
the API.

The block instance decides **invariance**. It declines, by raising a dedicated exception that blocks can
import without depending on ``openedx-platform``, when its content differs per learner or needs a live handler
to display: for example randomization other than "Never", or learner substitutions such as ``%%USER_ID%%`` and
``%%USER_EMAIL%%``. The block is recorded ``unsupported`` with the exception's message; any other exception is
a failure. Each block's conditions are in its proposal.

The platform decides **audience**, before it renders. A leaf that is staff-only or carries group access (both
inherited from its containers), or sits in a timed or proctored subsection, is ``unsupported`` with that reason.

4. Packaging from the fragment, not from the page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The archive is assembled from the ``Fragment`` (body and resources) and a DOM pass over the body.

*Platform files.* The page scripts (jQuery, the XBlock JavaScript core and runtime, the archive runtime) and
MathJax are read through the staticfiles finders, never from paths on disk; if one is missing, the row fails.
The page also supplies the LMS page globals that block scripts expect; the list is kept with the archive
runtime and tested.

*Block resources.* Blocks declare the same JavaScript and CSS in the fragment as for their other views. A
resource given as text is packaged as it is; a URL resource is collected when it is a platform static URL or a
``runtime.local_resource_url`` resource, which is read from the block's package. ``url()`` references inside
collected CSS are rewritten like ``src`` and ``href`` attributes.

*Course assets.* Assets referenced as ``/static/``, ``/asset-v1:`` or ``/c4x/`` are read from the contentstore
only when they belong to the course being generated, under per-file and per-archive size caps; what is skipped
is recorded so the application can say "not available offline". The course's code library
(``python_lib.zip``) is never collected. The course's locked assets are collected: an archive is announced
only to learners who may open the block (decision 6), and an archive holder has what a learner had.

*MathJax.* MathJax 4 as one file that loads nothing at run time, built from ``@mathjax/src`` when the
platform's npm dependencies are installed, from a component source kept with the application: TeX input with
the packages MathJax 3's ``all-packages`` had, SVG output with the TeX font embedded (the one MathJax 4 font
without dynamic ranges) and the mhchem font extension, the hidden assistive MathML; no contextual menu and no
speech, which load components and data on demand. It is added to a page whose content contains a platform
delimiter pair (opener and closer, as the platform's own math detection checks), collected before course
assets and outside the per-file cap; about 0.73 MB to download and 2.0 MB on the device per such archive. The
page sets ``displayOverflow: linebreak``, so displayed math breaks at the screen's width; MathML is rendered by
the WebView itself and AsciiMath is not typeset offline.

*Other hosts.* Nothing on another host is fetched. Images, frames and stylesheet ``@import`` references on
other hosts stay as they are and are recorded ``external``; blocks must not ship external ``@import``
references. Root-relative LMS references (``/jump_to_id/``, ``/course/``, ``/courses/``, other LMS paths)
become absolute URLs on the LMS root of the course's site, since the page is not served by the LMS; links to
pages are not recorded.

*Names.* Every collected path is normalised; ``..`` and absolute paths are refused; archive member names derive
from hashes, never from author-controlled strings. The layout is ``index.html`` plus ``assets/`` with relative
references, because the applications load the file directly. Archives are self-contained in this scope: the
shared-resources archive of ADR-002 needs the applications to learn a second download, and waits for that
change.

5. State: a manifest model and one storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every leaf block of a published course with the flag on, and its handouts and updates blocks (decision 10), has
a row in ``OfflineArchive``: course key, usage key, block type, a status (pending, generated, unsupported,
failed) and, when a block is unsupported or failed for a reason of its own, that reason; a generated row adds the
content hash, size, generation time, the ``published_on`` of the version it was built from, and the list of
skipped assets. Rows of block types without ``offline_view`` are created once and never rewritten. Unsupported
blocks keep their rows on purpose: the row says why a block cannot be downloaded, a missing row never has to be
interpreted, and the offline coverage of a course or a catalogue is one query by status and block type. The row
is what the API reads and what the task compares against; storage is never listed to answer a request.

*Storage.* Archives are written and read through ``STORAGES["offline_content"]`` when it is defined, else
through ``default_storage``. The storage must return URLs the applications can fetch without credentials for
as long as the row exists; for S3 that means unsigned URLs, on a custom domain or CDN. Paths derive from the
usage key and the content hash, so nobody can compute them without the content.

*Write rule.* A file is written completely, and checked by size, before a row says ``generated``. A row is
updated only if it is still the row the writer read (a conditional update on its path and ``published_on``); a
writer whose update matches nothing deletes the file it wrote. Block tasks never insert rows. An old file is
deleted only after no row points at it. A failed re-render of a generated row keeps the row and its archive,
and logs.

*Orphans.* ``generate_offline_content --prune`` may list a course's prefix in the storage to collect orphaned
files; storage is still never listed to answer a request.

6. The mobile API reads the manifest
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``/api/mobile/v4/course_info/blocks/``, in dict form, adds ``offline_download {file_url, last_modified,
file_size}`` to a block when the course flag is on, the block has a generated row, and the requesting user may
open the block now: no authorization denial reason, and courseware access to the course. ``file_size`` is in
bytes; ``last_modified`` identifies the archive version and the applications compare it as an opaque string
to decide whether to download again. The same response announces the handouts and updates archives of decision
10 at the course level. Each request makes one manifest query, and URLs are built only for the blocks in the
response.

``GET /api/mobile/v1/download_courses/{username}`` serves the user in the URL, like the other user-scoped
mobile endpoints. It lists the learner's mobile-available enrollments in the current site's organisations, the
500 most recent first, each with ``total_size``: the bytes of the course's generated archives, counted for the
course as a whole, not per learner, and without video. It makes one manifest query, then checks the flag only
for the courses that have archives. The applications present ``total_size`` as the size of the whole download;
the video they download themselves is theirs to add.

7. Blocks talk to the runtime; the bridge is internal to the archive runtime
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A block's JavaScript reaches its state and its handlers only through the ``runtime`` object: it loads the
learner's state, calls a handler by name, and asks whether handlers are live, queued or unavailable. The LMS
runtime, the Studio preview runtime, the newer runtime's iframe and the archive runtime each implement these
methods; `offline_view for xblock-poll`_ and `Read-only offline_view for problem`_ define them. The same
script therefore runs online and in an archive.

In an archive, the archive runtime implements them over the bridge to the application
(``window.AndroidBridge`` and ``window.webkit.messageHandlers.IOSBridge``): it obtains the state the
application downloaded and the calls not yet synchronised, and hands each new call to the application to
store. Blocks never use the bridge, and nothing in a block names it. This replaces ``markCompleted`` in
ADR-001 and the ``markProblemCompleted`` global and ``{url, type, data, content_type}`` payload the
applications ship today.

A stored call is a record ``{usage key, handler name, body, sequence, id}``. No URL and no header of the
learner's is sent to the device. When the device is back online the application sends its records to a batch
replay endpoint, authenticating as the learner. The endpoint runs them in order through the handler dispatch of
the LMS's XBlock handler route, so every block's own rules apply, and answers per record with the handler's
result and the fresh state; a record id table makes retries idempotent. The applications surface a rejected
replay to the learner. The runtime methods, the bridge and the replay endpoint are this scope's; the
interactive survey of decision 8 ships with them.

8. Problems and drag and drop are read-only; the survey and poll are interactive
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The problem view renders the block for reading with the templates and the script of its ``student_view``:
what a fresh learner sees, with no saved answers, hints, solutions or feedback. The script makes the page
read-only, with a note that answers are accepted online, when the runtime reports that handlers are not live.
`Read-only offline_view for problem`_ describes it. Drag and drop is read-only on the same principle. Offline
submission for problems is the next scope's (decision 11) and changes the script, not the rendering.

The survey and poll (``xblock-poll``) are this scope's interactive pilot, described in `offline_view for
xblock-poll`_: the vote is a handler call through the runtime, shown as pending until it is replayed. They
land together with the application releases that implement the bridge and the replay of decision 7.

9. The flag gates generation and announcement
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With the course flag off nothing is generated and nothing is announced: no task is enqueued on publish,
``offline_download`` is omitted and ``download_courses`` counts nothing for the course. Existing archives stay
fetchable at their URLs until ``generate_offline_content --prune`` removes them, which also removes the
archives of blocks that are no longer published. Turning the flag on generates nothing until the next publish
or the command.

10. The course handouts and updates are archives of their ``course_info`` blocks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The proposal's non-XBlock stage asks for the course handouts and updates offline, with the files they link to.
Both are ``course_info`` blocks of the course, ``handouts`` and ``updates``, detached from the structure; Studio
publishes them as it saves them, and the mobile API serves them online as HTML whose course file links point at
the LMS (``course_info/{course_id}/handouts`` and ``updates``).

They are archives like any other. The course-info block, which belongs to ``openedx-platform``, implements
``offline_view``: the handouts as published; the updates that are not deleted, newest first, each date above its
content, without the folding controls of the LMS page. The view declines when there is nothing to show and, for
the handouts, on the learner substitutions. The course task reconciles the two blocks before the leaves of the
structure, so each has a row under its usage key and is rendered, packaged and stored by the same code: the files
they link to are collected like images, within the caps, and their archives count in ``total_size``.

The applications learn of them from the ``v4`` course blocks response, next to the URLs of their online form:
``course_handouts_offline_download`` and ``course_updates_offline_download`` carry the three fields of
``offline_download`` under the same conditions (the flag, courseware access) and are absent while there is no
archive. The applications render the archive's ``index.html`` where they render the online HTML. Course
details stay online, as the proposal says; static tabs and the about page are not shown by the applications.

11. Interactive blocks: the next scope builds on these decisions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This scope ships problems and drag and drop for reading and the survey and poll interactive; the next one
brings every block that can interact offline, with its interactions synchronised when the device is back
online. That scope adds to this design and changes nothing in it, because the archive is content and
interaction is state:

*Content stays learner-free.* The archive is rendered as decided here, with no user, once per block version.
A render with the user would carry the learner's state as it was at generation, which is stale by the time the
block is opened, so the application would fetch the current state anyway; a user-bound render adds a stale copy
at the cost of one archive per learner and generation on request.

*State is the learner's and travels beside the archive.* A block returns the user-scoped state its JavaScript
needs through its state handler (decision 7), the LMS serves it per course with the user bound, with the
learner's substitutions for ``%%USER_ID%%`` and ``%%USER_EMAIL%%``; the application stores it, refreshes it
when online, and the archive runtime hands it to the block when the block loads its state. The page becomes
the learner's at load time, not at render time.

*Interactions are recorded and replayed as the learner.* The runtime methods, the record and the replay
endpoint are those of decision 7, in place since this scope. A block's script learns to show a queued
interaction as pending. A block that cannot complete an interaction offline (grading, answers, hints are
server-side) records it and says so; the result arrives with the replay. For problems, `Read-only
offline_view for problem`_ lists what remains.

*Content that depends on the learner is the bounded exception.* Per-student randomisation and per-learner
selections cannot share an archive; the next scope decides, with data, between selection through state and a
learner-bound archive rendered on request by the LMS for blocks that opt in. Steps that need the server or other
people at the moment of interaction (peer assessment, LTI, discussions, timed exams) stay online.

Consequences
------------

* No learner state reaches an archive: content that varies per learner is declined by the block, and content a
  learner may not open is not announced to them; no service user, session or permission takes part in
  generation.
* Generation runs on the CMS Celery workers at publish time; a deployment without a CMS worker generates nothing.
  Python-scripted problems need codejail on those workers.
* A block supports offline content by implementing one view; the generator does not change when a block is
  added. Blocks without the view are simply not available offline, and the applications say so.
  ``USE_EXTRACTED_*_BLOCK = False`` switches to the built-in blocks, which have no ``offline_view``.
* A block has one rendering and one script for its online and offline forms; ``student_view`` and
  ``public_view`` are built on ``offline_view`` as blocks adopt it.
* The JavaScript runtime gains methods for loading state, calling a handler and reporting whether handlers are
  live. They are implemented in the LMS runtime, the Studio preview runtime, the newer runtime's iframe and the
  archive runtime; a block that must run on older platforms falls back to ``handlerUrl`` when they are missing.
* Interactivity for further blocks is the next scope's and needs no change to generation, to the runtime
  methods or to the replay endpoint: each block's script learns to show a queued interaction as pending
  (decision 11).
* Every request makes one manifest query; ``download_courses`` adds one flag lookup per course that has
  archives; every announced URL costs one ``storage.url()`` call, which on S3 without a custom domain is a
  presign.
* Self-contained archives repeat shared JavaScript, CSS and MathJax per block until the shared-resources
  archive and the matching application change land; MathJax is the largest item, about 0.73 MB downloaded and
  2.0 MB on the device per archive with math.
* Installing the platform's npm dependencies builds the MathJax file: three more packages at build time (about
  100 MB with their dependencies), nothing at run time; the component source lists the TeX packages by name and
  is the place to add one.
* A platform or block-package upgrade reaches archives only through the command, and then every learner
  downloads everything again.
* Anyone who holds an archive URL can fetch it without credentials, including a learner who has since
  unenrolled; to withdraw content, prune it.
* Every ``offline_view`` of this scope lives in another repository and follows that repository's release
  cadence: the block packages release their views before ``openedx-platform`` raises its pins, and the
  platform's tests do not depend on those packages.
* The handouts and updates archives reach learners once the applications read the two course-level fields; until
  then they are generated and counted in ``total_size`` (the page scripts, tens of kilobytes, plus linked files)
  but never downloaded.
* Before the interactive survey, the applications must implement the bridge and the replay of decision 7 in
  place of the ``{url, type, data, content_type}`` payload they ship today, surface replay rejections, and
  count video in ``total_size``.

Rejected Alternatives
---------------------

* **LMS-side generation on behalf of a service user.** The service user's state, seed and language leak into
  every archive, the account must be provisioned by hand, and the publish event happens in the CMS.
* **A new modulestore signal sent from a publish-time task.** It couples generation to that task, fires on
  every rebuild of what the task maintains, and depends on how the CMS routes the task's queue.
* **``student_view`` as the default ``offline_view``.** ``student_view`` is the learner's live page and
  assumes the LMS behind it: it links the LMS handler URLs, leaves ``%%USER_ID%%`` in the text, calls handlers
  when the page loads, and fails for blocks that read the user. Making it work offline means changing every
  block's view, and that is ``offline_view`` without the opt-in and the decline. The dependency runs the other
  way: ``student_view`` is built on ``offline_view`` (decision 3).
* **A separate offline rendering and script per block.** Two frontends per block, which drift; a read-only
  template for problems would be a third rendering, discarded when problems become interactive.
* **Blocks posting to the mobile bridge, and replaying stored HTTP requests.** A script that names the bridge
  runs only in the applications. A stored ``{url, type, data, content_type}`` makes every handler's URL and
  request body a format kept on devices for months; a record of handler name and body, replayed through the
  handler dispatch, does not.
* **Studio's preview rendering as it is.** It needs a request, a session and a user; the runtime is prepared
  the same way without them.
* **The platform's newer XBlock runtime (``openedx.core.djangoapps.xblock``).** It loads Learning Core content
  through learning contexts, of which only content libraries are registered, so it cannot load a course usage
  key; it refuses every view but ``public_view`` when it has no user. Decision 3 does not depend on it: when
  courses move to Learning Core, the binding changes and ``offline_view`` does not.
* **Rewriting rendered HTML with regular expressions.** Fragile against markup changes, blind to CSS
  ``url()`` and to resources the fragment declares; the fragment and the DOM are the sources of truth.
* **The rendered content hash as the change detector.** It costs a render of every block on every publish;
  ``published_on`` moves only for what a publish changed.
* **Storage as the only state.** It cannot answer what was skipped or why, costs several storage calls per
  block per request, and spreads the path layout across readers.
* **Fetching MathJax (or any external asset) at generation time.** A build-time network dependency and an
  SSRF surface inside the worker; vendoring replaces the one, recording replaces the other.
* **Typesetting math on the server.** It needs Node on the CMS workers or a lossy converter, and it diverges
  from the LMS's rendering.
* **The MathJax 2.7 tree the platform's pages load.** They load ``mathjax@2.7.5`` from a CDN as a loader that
  fetches extensions, font ranges and locales on demand from a 3 362-file tree; which files a page needs is
  known only after it is typeset. The 2.x line ended with 2.7.9 (2020).
* **MathJax 3's ``tex-svg-full.js``.** One file with everything, but the last 3.x release is from 2022 and it
  cannot break lines, so displayed math scrolls on a phone; the MathJax 4 build is smaller and wraps.
* **A stock MathJax 4 file.** Version 4 dropped the ``-full`` builds: its combined files autoload extensions,
  its default font loads glyph ranges from a CDN and ``mhchem`` loads a font extension, so no shipped file
  typesets offline on its own (verified in a browser with no network). The blocks' JavaScript already uses
  the API that MathJax 3.2 and 4 share.
* **Caching the handouts and updates responses in the applications.** The responses link course files by absolute
  LMS URLs, so the files the proposal asks for would still need a download of their own; an archive carries them
  and reuses the collector.
* **An HTTP endpoint that triggers generation.** It would need its own authentication, rate limit and audit; the
  publish signal and the management command are the only triggers.
* **A separate plugin repository for the generator.** The mobile API and the publish signal are in
  ``openedx-platform``, the views are in the blocks' own repositories, and ADR-001 already placed the feature in
  ``openedx/features/offline_content``.

References
----------

* `ADR-001`_ and `ADR-002`_ in this directory
* `offline_view for xblock-poll`_ and `Read-only offline_view for problem`_, the proposals for decisions 3, 7
  and 8
* `wg-mobile#55`_, the mobile working group's offline mode roadmap
* `OEP-17`_ feature toggles, `OEP-19`_ developer documentation

.. _ADR-001: 001-mobile-offline-content-support.rst
.. _ADR-002: 002-mobile-offline-content-support.rst
.. _offline_view for xblock-poll: 003-offline-content-support/offline-view-for-xblock-poll.rst
.. _Read-only offline_view for problem: 003-offline-content-support/read-only-offline-view-for-problem.rst
.. _wg-mobile#55: https://github.com/openedx/wg-mobile/issues/55
.. _OEP-17: https://open-edx-proposals.readthedocs.io/en/latest/best-practices/oep-0017-bp-feature-toggles.html
.. _OEP-19: https://open-edx-proposals.readthedocs.io/en/latest/best-practices/oep-0019-bp-developer-documentation.html
