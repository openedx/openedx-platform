4. Upstream and downstream content
##################################

Status
******

Accepted.

Implementation in progress as of 2024-09-03.

Context
*******

We are replacing the existing Legacy ("V1") Content Libraries system, based on
ModuleStore, with a Relaunched ("V2") Content Libraries  system, based on
Learning Core. V1 and V2 libraries will coexist for at least one release to
allow for migration; eventually, V1 libraries will be removed entirely.

Content from V1 libraries can only be included into courses using the
LibraryContentBlock (called "Randomized Content Module" in Studio), which works
like this:

* Course authors add a LibraryContentBlock to a Unit and configure it with a
  library key and a count of N library blocks to select (or `-1` for "all
  blocks").

* For each block in the chosen library, its *content definition* is copied into
  the course as a child of the LibraryContentBlock, whereas its *settings* are
  copied into a special "default" settings dictionary in the course's structure
  document--this distinction will matter later. The usage key of each copied
  block is derived from a hash of the original library block's usage key plus
  the LibraryContentBlock's own usage key--this will also matter
  later.

* The course author is free to override the content and settings of the
  course-local copies of each library block.

* When any update is made to the library, the course author is prompted to
  update the LibraryContentBlock. This involves re-copying the library blocks'
  content definitions and default settings, which clobbers any overrides they
  have made to content, but preserves any overrides they have made to settings.
  Furthermore, any blocks that were added to the library are newly copied into
  the course, and any blocks that were removed from the library are deleted
  from the course. For all blocks, usage keys are recalculated using the same
  hash derivation described above; for existing blocks, it is important that
  this recalculation yields the same usage key so that student state is not
  lost.

* Over in the LMS, when a learner loads LibraryContentBlock, they are shown a
  list of N randomly-picked blocks from the library. Subsequent visits show
  them the same list, *unless* children were added, children were removed, or N
  changed. In those cases, the LibraryContentBlock tries to make the smallest
  possible adjustment to their personal list of blocks while respecting N and
  the updated list of children.

This system has several issues:

#. **Missing defaults after import:** When a course with a LibraryContentBlock
   is imported into an Open edX instance *without* the referenced library, the
   blocks' *content* will remain intact as will course-local *settings
   overrides*. However, any *default settings* defined in the library will be
   missing. This can result in content that is completely broken, especially
   since critical fields like video URLs and LTI URLs are considered
   "settings". For a detailed scenario, see `LibraryContentBlock Curveball 1`_.

#. **Strange behavior when duplicating content:** Typically, when a
   block is duplicated or copy-pasted, the new block's usage key and its
   children's usage keys are randomly generated. However, recall that when a
   LibraryContentBlock is updated, its children's usage keys are rederived
   using a hash function. That would cause the children's usage keys to change,
   thus destroying any student state. So, we must work around this with a hack:
   upon duplicating or pasting a LibraryContentBlock, we immediately update the
   LibraryContentBlock, thus discarding the problematic randomly-generated keys
   in favor of hash-derived keys. This works, but:

   * it involves weird code hacks,
   * it unexpectedly discards any content overrides the course author made to
     the copied LibraryContentBlock's children,
   * it unexpectedly uses the latest version of library content, regardless of
     which version the copied LibraryContentBlock was using, and
   * it fails if the library does not exist on the Open edX instance, which
     can happen if the course was imported from another instance.

#. **Conflation of reference and randomization:** The LibraryContentBlock does
   two things: it connects courses to library content, and it shows users a
   random subset of content. There is no reason that those two features need to
   be coupled together. A course author may want to randomize course-defined
   content, or they may want to randomize content from multiple different
   libraries. Or, they may want to use content from libraries without
   randomizing it at all. While it is feasible to support all these things in a
   single XBlock, trying to do so led to a `very complicated XBlock concept`_
   which difficult to explain to product managers and other engineers.

#. **Unpredictable preservation of overrides:** Recall that *content
   definitions* and *settings* are handled differently. This distinction is
   defined in the code: every authorable XBlock field is either defined with
   `Scope.content` or `Scope.settings`. In theory, XBlock developers would use
   the content scope for fields that are core to the meaning of piece of
   content, and they would only use the settings scope for fields that would be
   reasonable to configure in a local copy of the piece of content. In
   practice, though, XBlock developers almost always use `Scope.settings`. The
   result of this is that customizations to blocks *almost always* survive
   through library updates, except when they don't. Course authors have no way
   to know (or even guess) when their customizations they will and won't
   survive updates.

#. **General pain and suffering:** The relationship between courses and V1
   libraries is confusing to content authors, site admins, and developers
   alike. The behaviors above toe the line between "quirks" and "known bugs",
   and they are not all documented. Past attempts to improve the system have
   `triggered series of bugs`_, some of which led to permanent loss of learner
   state. In other cases, past Content Libraries improvement efforts have
   slowed or completely stalled out in code review due to the overwhelming
   amount of context and edge cases that must be understood to safely make any
   changes.

.. _LibraryContentBlock Curveball 1: https://openedx.atlassian.net/wiki/spaces/COMM/pages/3966795804/Fun+with+LibraryContentBlock+export+import+and+duplication#Curveball-1%3A-Import%2FExport
.. _LibraryContentBlock Curveball 2: https://openedx.atlassian.net/wiki/spaces/COMM/pages/3966795804/Fun+with+LibraryContentBlock+export+import+and+duplication#Curveball-2:-Duplication
.. _very complicated XBlock concept: https://github.com/openedx/edx-platform/blob/master/xmodule/docs/decisions/0003-library-content-block-schema.rst
.. _triggered series of bugs: https://openedx.atlassian.net/wiki/spaces/COMM/pages/3858661405/Bugs+from+Content+Libraries+V1

We are keen to use the Library Relaunch project to address all of these
problems. So, V2 libraries will interop with courses using a completely
different data model.


Decision
********

We will create a framework where a *downstream* piece of content (e.g. a course
block) can be *linked* to an *upstream* piece of content (e.g., a library
block) with the following properties:

* **Portable:** Links can refer to certain content on the current Open edX
  instance, and in the future they may be able to refer to content on other
  Open edX instances or sites. Links will never include information that is
  internal to a particular Open edX instance, such as foreign keys.

* **Flat:** The *link* is a not a wrapper (like the LibraryContentBlock),
  but simply a piece of metadata directly on the downstream content which
  points to the upstream content. We will no longer rely on precarious
  hash-derived usage keys to establish connection to upstream blocks;
  like any other block, an upstream-linked blocks can be granted whatever block
  ID that the authoring environment assigns it, whether random or
  human-readable.

* **Forwards-compatible:** If downstream content is created in a course on
  an Open edX site that supports upstream and downstreams (e.g., a Teak
  instance), and then it is exported and imported into a site that doesn't
  (e.g., a Quince instance), the downstream content will simply act like
  regular course content.

* **Independent:** Upstream content and downstream content exist separately
  from one another:

  * Modifying upstream content does not affect any downstream content (unless a
    sync happens, more on that later).
  * Deleting upstream content does not impact its downstream content. By
    corollary, pieces of downstream content can completely and correctly render
    on Open edX instances that are missing their linked upstream content.
  * (Preserving a positive feature of the V1 LibraryContentBlock) The link
    persists through export-import and copy-paste, regardless of whether the
    upstream content actually exists. A "broken" link to upstream content is
    seamlessly "repaired" if the upstream content becomes available again.

* **Customizable:** On an OLX level, authors can still override the value
  of any field for a piece of downstream content. However, we will empower
  Studio to be more prescriptive about what authors *can* override versus what
  they *should* override:

  * We define a set of *customizable* fields, with platform-level defaults
    like display_name and a max_attempts, plus the ability for external
    XBlocks to opt their own fields into customizability.
  * Studio may use this list to provide an interface for customizing
    downstream blocks, separate from the usual "Edit" interface that would
    permit them to make unsafe overrides.
  * Furthermore, downstream content will record which fields the user has
    customized...

    * even if the customization is to simply clear the value of the fields...
    * and even if the customization is made redundant in a future version of
      the upstream content. For example, if max_attempts is customized from 3
      to 5 in the downstream content, but the next version of the upstream
      content also changes max_attempts to 5, the downstream would still
      consider max_attempts to be customized. If the following version of the
      upstream content again changed max_attempts to 6, the downstream would
      retain max_attempts to be 5.

  * Finally, the downstream content will locally save the upstream value of
    customizable fields, allowing the author to *revert* back to them
    regardless of whether the upstream content is actually available.

* **Synchronizable, without surprises:** Downstream content can be *synced*
  with updates that have been made to its linked upstream. This means that the
  latest available upstream content field values will entirely replace all of
  the downstream field values, *except* those which were customized, as
  described in the previous item.

* **Concrete, but flexible:** The internal implementation of upstream-downstream
  syncing will assume that:

  * upstream content belongs to a V2 content library,
  * downstream content belongs to a course on the same instance, and
  * the link is the stringified usage key of the upstream library content.

  This will allow us to keep the implementation straightforward. However, we
  will *not* expose these assumptions in the Python APIs, the HTTP APIs, or in
  the persisted fields, allowing us in the future to generalize to other
  upstreams (such as externally-hosted libraries) and other downstreams (such
  as a standalone enrollable sequence without a course).

  If any of these assumptions are violated, we will raise an exception or log a
  warning, as appropriate. Particularly, if these assumptions are violated at
  the OLX level via a course import, then we will probably show a warning at
  import time and refuse to sync from the unsupported upstream; however, we
  will *not* fail the entire import or mangle the value of upstream link, since
  we want to remain forwards-compatible with potential future forms of syncing.
  As a concrete example: if a course block has *another course block's usage
  key* as an upstream, then we will faithfully keep that value through the
  import and export process, but we will not prompt the user to sync updates
  for that block.

* **Decoupled:** Upstream-downstream linking is not tied up with any other
  courseware feature; in particular, it is unrelated to content randomization.
  Randomized library content will be supported, but it will be a *synthesis* of
  two features: (1) a RandomizationBlock that randomly selects a subset of its
  children, where (2) some or all of those children are linked to upstream
  blocks.

Consequences
************

To support the Libraries Relaunch in Sumac:

* For every XBlock in CMS, we will use XBlock fields to persist the upstream
  link, its versions, its customizable fields, and its set of downstream
  overrides.

  * We will avoid exposing these fields to LMS code.

  * We will define an initial set of customizable fields for Problem, Text, and
    Video blocks.

* We will define method(s) for syncing update on the XBlock runtime so that
  they are available in the SplitModuleStoreRuntime.

  * Either in the initial implementation or in a later implementation, it may
    make sense to declare abstract versions of the syncing method(s) higher up
    in XBlock Runtime inheritance hierarchy.

* We will expose a CMS HTTP API for syncing updates to blocks from their
  upstreams.

  * We will avoid exposing this API from the LMS.

For reference, here are some excerpts of a potential implementation. This may
change through development and code review.

(UPDATE: When implementing, we ended up factoring this code differently.
Particularly, we opted to use regular functions rather than add new
XBlock Runtime methods, allowing us to avoid mucking with the complicated
inheritance hierarchy of CachingDescriptorSystem and SplitModuleStoreRuntime.)

.. code-block:: python

    ###########################################################################
    # cms/lib/xblock/upstream_sync.py
    ###########################################################################

    class UpstreamSyncMixin(XBlockMixin):
        """
        Allows an XBlock in the CMS to be associated & synced with an upstream.
        Mixed into CMS's XBLOCK_MIXINS, but not LMS's.
        """

        # Metadata related to upstream synchronization
        upstream = String(
            help=("""
                The usage key of a block (generally within a content library)
                which serves as a source of upstream updates for this block,
                or None if there is no such upstream. Please note: It is valid
                for this field to hold a usage key for an upstream block
                that does not exist (or does not *yet* exist) on this instance,
                particularly if this downstream block was imported from a
                different instance.
            """),
            default=None, scope=Scope.settings, hidden=True, enforce_type=True
        )
        upstream_version = Integer(
            help=("""
                Record of the upstream block's version number at the time this
                block was created from it. If upstream_version is smaller
                than the upstream block's latest version, then the user will be
                able to sync updates into this downstream block.
            """),
            default=None, scope=Scope.settings, hidden=True, enforce_type=True,
        )
        downstream_customized = Set(
            help=("""
                Names of the fields which have values set on the upstream
                block yet have been explicitly overridden on this downstream
                block. Unless explicitly cleared by the user, these
                customizations will persist even when updates are synced from
                the upstream.
            """),
            default=[], scope=Scope.settings, hidden=True, enforce_type=True,
        )

        # Store upstream defaults for customizable fields.
        upstream_display_name = String(...)
        upstream_max_attempts = List(...)
        ...  # We will probably want to pre-define several more of these.

        def get_upstream_field_names(cls) -> dict[str, str]:
            """
            Mapping from each customizable field to field which stores its upstream default.
            XBlocks outside of edx-platform can override this in order to set
            up their own customizable fields.
            """
            return {
                "display_name": "upstream_display_name",
                "max_attempts": "upstream_max_attempts",
            }

        def save(self, *args, **kwargs):
            """
            Update `downstream_customized` when a customizable field is modified.
            Uses `get_upstream_field_names` keys as the list of fields that are
            customizable.
            """
            ...

    @dataclass(frozen=True)
    class UpstreamInfo:
        """
        Metadata about a block's relationship with an upstream.
        """
        usage_key: UsageKey
        current_version: int
        latest_version: int | None
        sync_url: str
        error: str | None

        @property
        def sync_available(self) -> bool:
            """
            Should the user be prompted to sync this block with upstream?
            """
            return (
                self.latest_version
                and self.current_version < self.latest_version
                and not self.error
            )


    ###########################################################################
    # xmodule/modulestore/split_mongo/runtime.py
    ###########################################################################

    class SplitModuleStoreRuntime(...):

        def validate_upstream_key(self, usage_key: UsageKey | str) -> UsageKey:
            """
            Raise an error if the provided key is not a valid upstream reference.
            Instead of explicitly checking whether a key is a LibraryLocatorV2,
            callers should validate using this function, and use an `except` clause
            to handle the case where the key is not a valid upstream.
            Raises: InvalidKeyError, UnsupportedUpstreamKeyType
            """
            ...

        def sync_from_upstream(self, *, downstream_key: UsageKey, apply_updates: bool) -> None:
            """
            Python API for loading updates from upstream block.
            Can choose whether or not to actually apply those updates...
                apply_updates=False: Think "get fetch".
                                     Use case: course import.
                apply_updates=True:  Think "git pull".
                                     Use case: sync_updates handler.
            Raises: InvalidKeyError, UnsupportedUpstreamKeyType, XBlockNotFoundError
            """
            ...

        def get_upstream_info(self, downstream_key: UsageKey) -> UpstreamInfo | None:
            """
            Python API for upstream metadata, or None.
            Raises: InvalidKeyError, XBlockNotFoundError
            """
            ...

Finally, here is what the OLX for a library-sourced Problem XBlock in a course
might look like:

.. code-block:: xml

   <problem
     display_name="A title that has been customized in the course"
     max_attempts="2"
     upstream="lb:myorg:mylib:problem:p1"
     upstream_version="12"
     downstream_customized="[&quot;display_name&quot;,&quot;max_attempts&quot;]"
     upstream_display_name="The title that was defined in the library block"
     upstream_max_attempts="3"
   >
     <!-- problem content would go here -->
   </problem>
