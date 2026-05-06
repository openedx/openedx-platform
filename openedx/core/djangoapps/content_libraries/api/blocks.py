"""
Content libraries API methods related to XBlocks/Components.

These methods don't enforce permissions (only the REST APIs do).
"""
from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timezone
from functools import cache
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_unicode_slug
from django.db import transaction
from django.db.models import F, QuerySet
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from lxml import etree
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import LearningContextKey, UsageKeyV2
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_content import api as content_api
from openedx_content.models_api import Collection, Component, ComponentVersion, Container, LearningPackage, MediaType
from openedx_events.content_authoring.data import LibraryBlockData
from openedx_events.content_authoring.signals import LIBRARY_BLOCK_DELETED
from xblock.core import XBlock

from openedx.core.djangoapps.content_staging.data import StagedContentID
from openedx.core.djangoapps.xblock.api import (
    get_component_from_usage_key,
    get_xblock_app_config,
    xblock_type_display_name,
)
from openedx.core.types import User as UserType

from ..models import ContentLibrary
from .block_metadata import (
    DirectPublishedEntity,
    LibraryHistoryContributor,
    LibraryHistoryEntry,
    LibraryPublishHistoryGroup,
    LibraryXBlockMetadata,
    LibraryXBlockStaticFile,
    direct_published_entity_from_record,
    make_contributor,
    resolve_change_action,
)
from .container_metadata import container_subclass_for_olx_tag
from .containers import (
    ContainerMetadata,
    create_container,
    get_container,
    update_container_children,
)
from .exceptions import (
    BlockLimitReachedError,
    ContentLibraryBlockNotFound,
    IncompatibleTypesError,
    InvalidNameError,
    LibraryBlockAlreadyExists,
)
from .libraries import PublishableItem

# This content_libraries API is sometimes imported in the LMS (should we prevent that?), but the content_staging app
# cannot be. For now we only need this one type import at module scope, so only import it during type checks.
# To use the content_staging API or other CMS-only code, we import it within the functions below.
if TYPE_CHECKING:
    from openedx.core.djangoapps.content_staging.api import StagedContentFileData

log = logging.getLogger(__name__)


# The public API is only the following symbols:
__all__ = [
    # API methods
    "get_library_components",
    "get_library_containers",
    "get_library_collections",
    "get_library_block",
    "set_library_block_olx",
    "get_component_from_usage_key",
    "validate_can_add_block_to_library",
    "create_library_block",
    "import_staged_content_from_user_clipboard",
    "get_or_create_olx_media_type",
    "delete_library_block",
    "restore_library_block",
    "get_library_block_static_asset_files",
    "add_library_block_static_asset_file",
    "delete_library_block_static_asset_file",
    "publish_component_changes",
    "get_library_component_draft_history",
    "get_library_component_publish_history",
    "get_library_component_publish_history_entries",
    "get_library_component_creation_entry",
]


def get_library_components(
    library_key: LibraryLocatorV2,
    text_search: str | None = None,
    block_types: list[str] | None = None,
) -> QuerySet[Component]:
    """
    Get the library components and filter.

    TODO: Full text search needs to be implemented as a custom lookup for MySQL,
    but it should have a fallback to still work in SQLite.
    """
    lib = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    learning_package = lib.learning_package
    assert learning_package is not None
    components = content_api.get_components(
        learning_package.id,
        draft=True,
        namespace='xblock.v1',
        type_names=block_types,
        draft_title=text_search,
    )

    return components


def get_library_containers(library_key: LibraryLocatorV2) -> QuerySet[Container]:
    """
    Get all containers in the given content library.
    """
    lib = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    learning_package = lib.learning_package
    assert learning_package is not None
    containers: QuerySet[Container] = content_api.get_containers(
        learning_package.id
    )

    return containers


def get_library_collections(library_key: LibraryLocatorV2) -> QuerySet[Collection]:
    """
    Get all collections in the given content library.
    """
    lib = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    learning_package = lib.learning_package
    assert learning_package is not None
    collections = content_api.get_collections(
        learning_package.id
    )
    return collections


def get_library_block(usage_key: LibraryUsageLocatorV2, include_collections=False) -> LibraryXBlockMetadata:
    """
    Get metadata about (the draft version of) one specific XBlock in a library.

    This will raise ContentLibraryBlockNotFound if there is no draft version of
    this block (i.e. it's been soft-deleted from Studio), even if there is a
    live published version of it in the LMS.
    """
    try:
        component = get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist as exc:
        raise ContentLibraryBlockNotFound(usage_key) from exc

    # The component might have existed at one point, but no longer does because
    # the draft was soft-deleted. This is actually a weird edge case and I'm not
    # clear on what the proper behavior should be, since (a) the published
    # version still exists; and (b) we might want to make some queries on the
    # block even after it's been removed, since there might be versioned
    # references to it.
    draft_version = component.versioning.draft
    if not draft_version:
        raise ContentLibraryBlockNotFound(usage_key)

    if include_collections:
        # Temporarily alias collection_code to "key" so downstream consumers
        # (search indexer, REST API) keep the same field name.  We will update
        # downstream consumers later: https://github.com/openedx/openedx-platform/issues/38406
        associated_collections = content_api.get_entity_collections(
            component.learning_package_id,
            component.entity_ref,
        ).values("title", key=F('collection_code'))
    else:
        associated_collections = None
    xblock_metadata = LibraryXBlockMetadata.from_component(
        library_key=usage_key.context_key,
        component=component,
        associated_collections=associated_collections,
    )
    return xblock_metadata


def get_library_component_draft_history(
    usage_key: LibraryUsageLocatorV2,
    request=None,
) -> list[LibraryHistoryEntry]:
    """
    Return the draft change history for a library component since its last publication,
    ordered from most recent to oldest.

    Raises ContentLibraryBlockNotFound if the component does not exist.
    """
    try:
        component = get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist as exc:
        raise ContentLibraryBlockNotFound(usage_key) from exc

    @cache
    def _contributor(user):
        return make_contributor(user, request)

    draft_change_records = (
        content_api.get_entity_draft_history(component.publishable_entity)
        .select_related("entity__component__component_type", "draft_change_log__changed_by__profile")
    )
    entries = []
    for record in draft_change_records:
        version = record.new_version if record.new_version is not None else record.old_version
        # old_version is None only for the very first publish (entity had no prior published version)
        old_version_num = record.old_version.version_num if record.old_version else 0
        # new_version is None for soft-delete publishes (component deleted without a new draft version)
        new_version_num = record.new_version.version_num if record.new_version else None
        entries.append(LibraryHistoryEntry(
            contributor=_contributor(record.draft_change_log.changed_by),
            changed_at=record.draft_change_log.changed_at,
            title=version.title if version is not None else "",
            item_type=record.entity.component.component_type.name,
            action=resolve_change_action(record.old_version, record.new_version),
            old_version=old_version_num,
            new_version=new_version_num,
        ))
    return entries


def get_library_component_publish_history(
    usage_key: LibraryUsageLocatorV2,
    request=None,
) -> list[LibraryPublishHistoryGroup]:
    """
    Return the publish history of a library component as a list of groups.

    Each group corresponds to one publish event (PublishLogRecord) and includes:
    - who published and when
    - the distinct set of contributors: users who authored draft changes between
      the previous publish and this one (via DraftChangeLogRecord version bounds)

    direct_published_entities per era:
    - Pre-Verawood (direct=None): single entry for the component itself.
    - Post-Verawood, direct=True: single entry for the component (directly published).
    - Post-Verawood, direct=False: all direct=True records from the same PublishLog
      (e.g. a parent container that was directly published).

    Groups are ordered most-recent-first. Returns [] if the component has never
    been published.
    """
    try:
        component = get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist as exc:
        raise ContentLibraryBlockNotFound(usage_key) from exc

    entity = component.publishable_entity
    publish_records = (
        content_api.get_entity_publish_history(entity)
        .select_related("entity__component__component_type")
    )

    groups = []
    for pub_record in publish_records:
        # old_version is None only for the very first publish (entity had no prior published version)
        old_version_num = pub_record.old_version.version_num if pub_record.old_version else 0
        # new_version is None for soft-delete publishes (component deleted without a new draft version)
        new_version_num = pub_record.new_version.version_num if pub_record.new_version else None

        contributing_users = content_api.get_entity_version_contributors(
            entity,
            old_version_num=old_version_num,
            new_version_num=new_version_num,
        ).select_related('profile')
        contributors = [
            LibraryHistoryContributor.from_user(user, request)
            for user in contributing_users
        ]

        if pub_record.direct is None or pub_record.direct is True:
            # Pre-Verawood or component was directly published: single entry for itself.
            # Use new_version title normally; fall back to old_version for soft-delete publishes
            # (new_version=None means the component was deleted).
            version = pub_record.new_version or pub_record.old_version
            direct_published_entities = [DirectPublishedEntity(
                entity_key=usage_key,
                title=version.title if version else "",
                entity_type=pub_record.entity.component.component_type.name,
            )]
        else:
            # Post-Verawood, direct=False: component published as a dependency.
            # Find all direct=True records in the same PublishLog.
            direct_records = (
                pub_record.publish_log.records
                .filter(direct=True)
                .select_related(
                    'entity__component__component_type',
                    'entity__container__container_type',
                    'new_version',
                    'old_version',
                )
            )
            direct_published_entities = [
                direct_published_entity_from_record(r, usage_key.lib_key)
                for r in direct_records
            ]

        groups.append(LibraryPublishHistoryGroup(
            publish_log_uuid=pub_record.publish_log.uuid,
            published_by=pub_record.publish_log.published_by,
            published_at=pub_record.publish_log.published_at,
            contributors=contributors,
            direct_published_entities=direct_published_entities,
            scope_entity_key=usage_key,
        ))

    return groups


def get_library_component_publish_history_entries(
    usage_key: LibraryUsageLocatorV2,
    publish_log_uuid: UUID,
    request=None,
) -> list[LibraryHistoryEntry]:
    """
    Return the individual draft change entries for a specific publish event.

    Called lazily when the user expands a publish event in the UI. Entries are
    the DraftChangeLogRecords that fall between the previous publish event and
    this one, ordered most-recent-first.
    """
    try:
        component = get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist as exc:
        raise ContentLibraryBlockNotFound(usage_key) from exc

    @cache
    def _contributor(user):
        return make_contributor(user, request)

    records = (
        content_api.get_entity_publish_history_entries(
            component.publishable_entity, str(publish_log_uuid)
        )
        .select_related("entity__component__component_type", "draft_change_log__changed_by__profile")
    )
    entries = []
    for record in records:
        # Deleted components can't reach this endpoint, so new_version is always set.
        # (Unlike containers — see get_library_container_publish_history_entries.)
        assert record.new_version is not None  # for satisfy the type check
        # old_version is None only for the very first publish (entity had no prior published version)
        old_version_num = record.old_version.version_num if record.old_version else 0
        # new_version is None for soft-delete publishes (component deleted without a new draft version)
        new_version_num = record.new_version.version_num if record.new_version else None
        entries.append(LibraryHistoryEntry(
            contributor=_contributor(record.draft_change_log.changed_by),
            changed_at=record.draft_change_log.changed_at,
            title=record.new_version.title,
            item_type=record.entity.component.component_type.name,
            action=resolve_change_action(record.old_version, record.new_version),
            old_version=old_version_num,
            new_version=new_version_num,
        ))
    return entries


def get_library_component_creation_entry(
    usage_key: LibraryUsageLocatorV2,
    request=None,
) -> LibraryHistoryEntry | None:
    """
    Return the creation entry for a library component.

    This is a single LibraryHistoryEntry representing the moment the
    component was first created. Returns None if the component
    has no versions yet.

    Raises ContentLibraryBlockNotFound if the component does not exist.
    """
    try:
        component = get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist as exc:
        raise ContentLibraryBlockNotFound(usage_key) from exc

    # TODO: replace with component.versioning.earliest once VersioningHelper exposes that helper.
    first_version = (
        component.publishable_entity.versions
        .order_by('version_num')
        .select_related("created_by__profile")
        .first()
    )
    if first_version is None:
        return None

    user = first_version.created_by
    return LibraryHistoryEntry(
        contributor=make_contributor(user, request),
        changed_at=first_version.created,
        title=first_version.title,
        item_type=component.component_type.name,
        action="created",
        old_version=0,
        new_version=first_version.version_num,
    )


def set_library_block_olx(
    usage_key: LibraryUsageLocatorV2,
    new_olx_str: str,
    paths_to_media: dict | None = None,
) -> ComponentVersion:
    """
    Replace the OLX source of the given XBlock.

    This is only meant for use by developers or API client applications, as
    very little validation is done and this can easily result in a broken XBlock
    that won't load.

    The optional ``paths_to_media`` parameter can be used to attach
    openedx_content Media to this XBlock. A common use case for this would be to
    add images or other static assets to a text block::

      figure_a_media = content_api.get_or_create_file_media(...)
      paths_to_media={
          'static/figure_a.png': figure_a_media,
      }

    Returns the version number of the newly created ComponentVersion.
    """
    assert isinstance(usage_key, LibraryUsageLocatorV2)
    paths_to_media = paths_to_media or {}

    # HTMLBlock uses CDATA to preserve HTML inside the XML, so make sure we
    # don't strip that out.
    parser = etree.XMLParser(strip_cdata=False)

    # Verify that the OLX parses, at least as generic XML, and the root tag is correct:
    node = etree.fromstring(new_olx_str, parser=parser)
    if node.tag != usage_key.block_type:
        raise ValueError(
            f"Tried to set the OLX of a {usage_key.block_type} block to a <{node.tag}> node. "
            f"{usage_key=!s}, {new_olx_str=}"
        )

    # We're intentionally NOT checking if the XBlock type is installed, since
    # this is one of the only tools you can reach for to edit content for an
    # XBlock that's broken or missing.
    component = get_component_from_usage_key(usage_key)

    # Get the title from the new OLX (or default to the default specified on the
    # XBlock's display_name field.
    new_title = node.attrib.get(
        "display_name",
        xblock_type_display_name(usage_key.block_type),
    )

    # Libraries don't use the url_name attribute, because they encode that into
    # the Component key. Normally this is stripped out by the XBlockSerializer,
    # but we're not actually creating the XBlock when it's coming from the
    # clipboard right now.
    if "url_name" in node.attrib:
        del node.attrib["url_name"]
        new_olx_str = etree.tostring(node, encoding='unicode')

    now = datetime.now(tz=timezone.utc)  # noqa: UP017

    with transaction.atomic():
        new_olx_media = content_api.get_or_create_text_media(
            component.learning_package_id,
            get_or_create_olx_media_type(usage_key.block_type).id,
            text=new_olx_str,
            created=now,
        )
        new_component_version = content_api.create_next_component_version(
            component.id,
            title=new_title,
            media_to_replace={
                **paths_to_media,
                'block.xml': new_olx_media.pk,
            },
            created=now,
        )

    return new_component_version


def validate_can_add_block_to_library(
    library_key: LibraryLocatorV2,
    block_type: str,
    block_id: str,
) -> tuple[ContentLibrary, LibraryUsageLocatorV2]:
    """
    Perform checks to validate whether a new block with `block_id` and type `block_type` can be added to
    the library with key `library_key`.

    Returns the ContentLibrary that has the passed in `library_key` and  newly created LibraryUsageLocatorV2 if
    validation successful, otherwise raises errors.
    """
    assert isinstance(library_key, LibraryLocatorV2)
    content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]

    # If adding a component would take us over our max, return an error.
    assert content_library.learning_package_id is not None
    component_count = content_api.get_all_drafts(content_library.learning_package_id).count()
    if component_count + 1 > settings.MAX_BLOCKS_PER_CONTENT_LIBRARY:
        raise BlockLimitReachedError(
            _("Library cannot have more than {} Components.").format(
                settings.MAX_BLOCKS_PER_CONTENT_LIBRARY
            )
        )

    # Make sure the proposed ID will be valid:
    validate_unicode_slug(block_id)
    # Ensure the XBlock type is valid and installed:
    block_class = XBlock.load_class(block_type)  # Will raise an exception if invalid
    if block_class.has_children:
        raise IncompatibleTypesError(
            _(
                'The "{block_type}" XBlock (ID: "{block_id}") has children,'
                ' so it is not supported in content libraries.'
            ).format(block_type=block_type, block_id=block_id)
        )
    # Make sure the new ID is not taken already:
    usage_key = LibraryUsageLocatorV2(  # type: ignore[abstract]
        lib_key=library_key,
        block_type=block_type,
        usage_id=block_id,
    )

    if _component_exists(usage_key):
        raise LibraryBlockAlreadyExists(
            _("An XBlock with ID '{usage_key}' already exists.").format(usage_key=usage_key)
        )

    return content_library, usage_key


def create_library_block(
    library_key: LibraryLocatorV2,
    block_type: str,
    definition_id: str,
    user_id: int | None = None,
    can_stand_alone: bool = True,
):
    """
    Create a new XBlock in this library of the specified type (e.g. "html").

    Set can_stand_alone = False when a component is created under a container, like unit.
    """
    # It's in the serializer as ``definition_id``, but for our purposes, it's
    # the block_id. See the comments in ``LibraryXBlockCreationSerializer`` for
    # more details. TODO: Change the param name once we change the serializer.
    block_id = definition_id

    content_library, usage_key = validate_can_add_block_to_library(library_key, block_type, block_id)

    _create_component_for_block(content_library, usage_key, user_id, can_stand_alone)

    # Now return the metadata about the new block:
    return get_library_block(usage_key)


def _title_from_olx_node(olx_node) -> str:
    """
    Given an OLX XML node (etree node), find an appropriate title for that
    XBlock.
    """
    title = olx_node.attrib.get("display_name")
    if not title:
        # Find a localized default title if none was set:
        from cms.djangoapps.contentstore import helpers as studio_helpers
        title = studio_helpers.xblock_type_display_name(olx_node.tag)
    return title


def _import_staged_block(
    block_type: str,
    olx_str: str,
    library_key: LibraryLocatorV2,
    source_context_key: LearningContextKey,
    user,
    staged_content_id: StagedContentID,
    staged_content_files: list[StagedContentFileData],
    now: datetime,
) -> LibraryXBlockMetadata:
    """
    Create a new library block and populate it with staged content from clipboard

    Returns the newly created library block
    """
    from openedx.core.djangoapps.content_staging import api as content_staging_api

    # Generate a block_id:
    try:
        olx_node = etree.fromstring(olx_str)
        title = _title_from_olx_node(olx_node)
        # Slugify the title and append some random numbers to make a unique slug
        block_id = slugify(title, allow_unicode=True) + '-' + uuid4().hex[-6:]
    except Exception:   # pylint: disable=broad-except
        # Just generate a random block_id if we can't make a nice slug.
        block_id = uuid4().hex[-12:]

    content_library, usage_key = validate_can_add_block_to_library(
        library_key,
        block_type,
        block_id
    )

    # content_library.learning_package is technically a nullable field because
    # it was added in a later migration, but we can't actually make a Library
    # without one at the moment. TODO: fix this at the model level.
    learning_package: LearningPackage = content_library.learning_package  # type: ignore

    # Create component for block then populate it with clipboard data
    with transaction.atomic(savepoint=False):
        # First create the Component, but do not initialize it to anything (i.e.
        # no ComponentVersion).
        component_type = content_api.get_or_create_component_type(
            "xblock.v1", usage_key.block_type
        )
        component = content_api.create_component(  # noqa: F841
            learning_package.id,
            component_type=component_type,
            component_code=usage_key.block_id,
            created=now,
            created_by=user.id,
        )

        paths_to_media = {}
        for staged_content_file_data in staged_content_files:
            # The ``data`` attribute is going to be None because the clipboard
            # is optimized to not do redundant file copying when copying/pasting
            # within the same course (where all the Files and Uploads are
            # shared). openedx_content backed content Components will always store
            # a Component-local "copy" of the data, and rely on lower-level
            # deduplication to happen in the ``contents`` app.
            filename = staged_content_file_data.filename

            # Grab our byte data for the file...
            file_data = content_staging_api.get_staged_content_static_file_data(
                staged_content_id,
                filename,
            )
            if not file_data:
                log.error(
                    f"Staged content {staged_content_id} included referenced "
                    f"file {filename}, but no file data was found."
                )
                continue

            # Courses don't support having assets that are local to a specific
            # component, and instead store all their content together in a
            # shared Files and Uploads namespace. If we're pasting that into a
            # openedx_content backed data model (v2 Libraries), then we want to
            # prepend "static/" to the filename. This will need to get updated
            # when we start moving courses over to openedx_content, or if we start
            # storing course component assets in sub-directories of Files and
            # Uploads.
            #
            # The reason we don't just search for a "static/" prefix is that
            # openedx_content components can store other kinds of files if they
            # wish (though none currently do).
            source_assumes_global_assets = not isinstance(
                source_context_key, LibraryLocatorV2
            )
            if source_assumes_global_assets:
                filename = f"static/{filename}"

            # Now construct the Core data models for it...
            # TODO: more of this logic should be pushed down to openedx_content
            media_type_str, _encoding = mimetypes.guess_type(filename)
            if not media_type_str:
                media_type_str = "application/octet-stream"

            media_type = content_api.get_or_create_media_type(media_type_str)
            media = content_api.get_or_create_file_media(
                learning_package.id,
                media_type.id,
                data=file_data,
                created=now,
            )
            paths_to_media[filename] = media.id

        # This will create the first component version and set the OLX/title
        # appropriately. It will not publish. Once we get the newly created
        # ComponentVersion back from this, we can attach all our files to it.
        set_library_block_olx(usage_key, olx_str, paths_to_media)

    # Now return the metadata about the new block
    return get_library_block(usage_key)


def _is_container(block_type: str) -> bool:
    """
    Return True if the block type is a container.
    """
    return block_type in ["vertical", "sequential", "chapter"]


def _import_staged_block_as_container(
    library_key: LibraryLocatorV2,
    source_context_key: LearningContextKey,
    user,
    staged_content_id: StagedContentID,
    staged_content_files: list[StagedContentFileData],
    now: datetime,
    *,
    olx_str: str | None = None,
    olx_node: etree.Element | None = None,
    copied_from_map: dict[str, LibraryUsageLocatorV2 | LibraryContainerLocator] | None = None,
) -> ContainerMetadata:
    """
    Convert the given XBlock (e.g. "vertical") to a Container (e.g. Unit) and
    import it into the library, along with all its child XBlocks.
    """
    if olx_node is None:
        if olx_str is None:
            raise ValueError("Either olx_str or olx_node must be provided")
        olx_node = etree.fromstring(olx_str)

    assert olx_node is not None  # This assert to make sure olx_node has the correct type

    # The olx_str looks like this:
    # <vertical><block1>...[XML]...</block1><block2>...[XML]...</block2>...</vertical>
    # Ideally we could split it up and preserve the strings, but that is difficult to do correctly, so we'll split
    # it up using the XML nodes. This will unfortunately remove any custom comments or formatting in the XML, but that's
    # OK since Studio-edited blocks won't have that anyways (hand-edited and library blocks can and do).

    title = _title_from_olx_node(olx_node)

    container = create_container(
        library_key=library_key,
        container_cls=container_subclass_for_olx_tag(olx_node.tag),
        slug=None,  # auto-generate slug from title
        title=title,
        user_id=user.id,
    )

    # Keep track of which blocks were copied from the library, so we don't duplicate them
    if copied_from_map is None:
        copied_from_map = {}

    # Handle children
    new_child_keys: list[LibraryUsageLocatorV2 | LibraryContainerLocator] = []
    for child_node in olx_node:
        child_is_container = _is_container(child_node.tag)
        copied_from_block = child_node.attrib.get('copied_from_block', None)
        if copied_from_block:
            # Get the key of the child block
            try:
                child_key: LibraryContainerLocator | LibraryUsageLocatorV2
                if child_is_container:
                    child_key = LibraryContainerLocator.from_string(copied_from_block)
                else:
                    child_key = LibraryUsageLocatorV2.from_string(copied_from_block)

                if child_key.context_key == library_key:
                    # This is a block that was copied from the library, so we just link it to the container
                    new_child_keys.append(child_key)
                    continue

            except InvalidKeyError:
                # This is a XBlock copied from a course, so we need to create a new copy of it.
                pass

        # This block is not copied from a course, or it was copied from a different library.
        # We need to create a new copy of it.
        if child_is_container:
            if copied_from_block in copied_from_map:
                # This container was already copied from the library, so we just link it to the container
                new_child_keys.append(copied_from_map[copied_from_block])
                continue

            child_container = _import_staged_block_as_container(
                library_key=library_key,
                source_context_key=source_context_key,
                user=user,
                staged_content_id=staged_content_id,
                staged_content_files=staged_content_files,
                now=now,
                olx_node=child_node,
                copied_from_map=copied_from_map,
            )
            if copied_from_block:
                copied_from_map[copied_from_block] = child_container.container_key
            new_child_keys.append(child_container.container_key)
            continue

        # This is not a container, so we import it as a standalone block
        try:
            if copied_from_block in copied_from_map:
                # This block was already copied from the library, so we just link it to the container
                new_child_keys.append(copied_from_map[copied_from_block])
                continue

            child_metadata = _import_staged_block(
                block_type=child_node.tag,
                olx_str=etree.tostring(child_node, encoding='unicode'),
                library_key=library_key,
                source_context_key=source_context_key,
                user=user,
                staged_content_id=staged_content_id,
                staged_content_files=staged_content_files,
                now=now,
            )
            if copied_from_block:
                copied_from_map[copied_from_block] = child_metadata.usage_key
            new_child_keys.append(child_metadata.usage_key)
        except IncompatibleTypesError:
            continue  # Skip blocks that won't work in libraries

    update_container_children(container.container_key, new_child_keys, user_id=user.id)  # type: ignore[arg-type]
    # Re-fetch the container because the 'last_draft_created' will have changed when we added children
    container = get_container(container.container_key)

    return container


def import_staged_content_from_user_clipboard(library_key: LibraryLocatorV2, user) -> PublishableItem:
    """
    Create a new library item from the staged content from clipboard.
    Can create containers (e.g. units) or XBlocks.

    Returns the newly created item metadata
    """
    from openedx.core.djangoapps.content_staging import api as content_staging_api

    user_clipboard = content_staging_api.get_user_clipboard(user)
    if not user_clipboard:
        raise ValidationError("The user's clipboard is empty")

    staged_content_id = user_clipboard.content.id
    source_context_key = user_clipboard.source_context_key

    staged_content_files = content_staging_api.get_staged_content_static_files(staged_content_id)

    olx_str = content_staging_api.get_staged_content_olx(staged_content_id)
    if olx_str is None:
        raise RuntimeError("olx_str missing")  # Shouldn't happen - mostly here for type checker

    now = datetime.now(tz=timezone.utc)  # noqa: UP017

    if _is_container(user_clipboard.content.block_type):
        # This is a container and we can import it as such.
        # Start an atomic section so the whole paste succeeds or fails together:
        with transaction.atomic():
            return _import_staged_block_as_container(
                library_key,
                source_context_key,
                user,
                staged_content_id,
                staged_content_files,
                now,
                olx_str=olx_str,
            )
    else:
        return _import_staged_block(
            user_clipboard.content.block_type,
            olx_str,
            library_key,
            source_context_key,
            user,
            staged_content_id,
            staged_content_files,
            now,
        )

def get_or_create_olx_media_type(block_type: str) -> MediaType:
    """
    Get or create a MediaType for the block type.

    openedx_content stores all Content with a Media Type (a.k.a. MIME type). For
    OLX, we use the "application/vnd.*" convention, per RFC 6838.
    """
    return content_api.get_or_create_media_type(
        f"application/vnd.openedx.xblock.v1.{block_type}+xml"
    )


def delete_library_block(
    usage_key: LibraryUsageLocatorV2,
    user_id: int | None = None,
) -> None:
    """
    Delete the specified block from this library (soft delete).
    """
    library_key = usage_key.context_key

    try:
        component = get_component_from_usage_key(usage_key)
    except Component.DoesNotExist:
        # There may be cases where entries are created in the
        # search index, but the component is not created
        # (an intermediate error occurred).
        # In that case, we keep the index updated by removing the entry,
        # but still raise the error so the caller knows the component did not exist.

        # .. event_implemented_name: LIBRARY_BLOCK_DELETED
        # .. event_type: org.openedx.content_authoring.library_block.deleted.v1
        LIBRARY_BLOCK_DELETED.send_event(
            library_block=LibraryBlockData(library_key=library_key, usage_key=usage_key)
        )
        raise

    content_api.soft_delete_draft(component.id, deleted_by=user_id)


def restore_library_block(usage_key: LibraryUsageLocatorV2, user_id: int | None = None) -> None:
    """
    Restore the specified library block.
    """
    component = get_component_from_usage_key(usage_key)
    # Set draft version back to the latest available component version id.
    content_api.set_draft_version(
        component.id,
        component.versioning.latest.pk,
        set_by=user_id,
    )


def get_library_block_static_asset_files(usage_key: LibraryUsageLocatorV2) -> list[LibraryXBlockStaticFile]:
    """
    Given an XBlock in a content library, list all the static asset files
    associated with that XBlock.

    Returns a list of LibraryXBlockStaticFile objects, sorted by path.

    TODO: Should this be in the general XBlock API rather than the libraries API?
    """
    component = get_component_from_usage_key(usage_key)
    component_version = component.versioning.draft

    # If there is no Draft version, then this was soft-deleted
    if component_version is None:
        return []

    # cvm = the ComponentVersionMedia through table
    cvm_set = (
        component_version
        .componentversionmedia_set
        .filter(media__has_file=True)
        .order_by('path')
        .select_related('media')
    )

    site_root_url = get_xblock_app_config().get_site_root_url()

    return [
        LibraryXBlockStaticFile(
            path=cvm.path,
            size=cvm.media.size,
            url=site_root_url + reverse(
                'content_libraries:library-assets',
                kwargs={
                    'component_version_uuid': component_version.uuid,
                    'asset_path': cvm.path,
                }
            ),
        )
        for cvm in cvm_set
    ]


def add_library_block_static_asset_file(
    usage_key: LibraryUsageLocatorV2,
    file_path: str,
    file_content: bytes,
    user: UserType | None = None,
) -> LibraryXBlockStaticFile:
    """
    Upload a static asset file into the library, to be associated with the
    specified XBlock. Will silently overwrite an existing file of the same name.

    file_path should be a name like "doc.pdf". It may optionally contain slashes
        like 'en/doc.pdf'
    file_content should be a binary string.

    Returns a LibraryXBlockStaticFile object.

    Sends a LIBRARY_BLOCK_UPDATED event.

    Example:
        video_block = UsageKey.from_string("lb:VideoTeam:python-intro:video:1")
        add_library_block_static_asset_file(video_block, "subtitles-en.srt", subtitles.encode('utf-8'))
    """
    # File path validations copied over from v1 library logic. This can't really
    # hurt us inside our system because we never use these paths in an actual
    # file system–they're just string keys that point to hash-named data files
    # in a common library (learning package) level directory. But it might
    # become a security issue during import/export serialization.
    if file_path != file_path.strip().strip('/'):
        raise InvalidNameError("file_path cannot start/end with / or whitespace.")
    if '//' in file_path or '..' in file_path:
        raise InvalidNameError("Invalid sequence (// or ..) in file_path.")

    component = get_component_from_usage_key(usage_key)

    with transaction.atomic():
        component_version = content_api.create_next_component_version(
            component.id,
            media_to_replace={file_path: file_content},
            created=datetime.now(tz=timezone.utc),  # noqa: UP017
            created_by=user.id if user else None,
        )

    # Now figure out the URL for the newly created asset...
    site_root_url = get_xblock_app_config().get_site_root_url()
    local_path = reverse(
        'content_libraries:library-assets',
        kwargs={
            'component_version_uuid': component_version.uuid,
            'asset_path': file_path,
        }
    )

    return LibraryXBlockStaticFile(
        path=file_path,
        url=site_root_url + local_path,
        size=len(file_content),
    )


def delete_library_block_static_asset_file(usage_key, file_path, user=None):
    """
    Delete a static asset file from the library.

    Sends a LIBRARY_BLOCK_UPDATED event.

    Example:
        video_block = UsageKey.from_string("lb:VideoTeam:python-intro:video:1")
        delete_library_block_static_asset_file(video_block, "subtitles-en.srt")
    """
    component = get_component_from_usage_key(usage_key)
    now = datetime.now(tz=timezone.utc)  # noqa: UP017

    with transaction.atomic():
        content_api.create_next_component_version(
            component.id,
            media_to_replace={file_path: None},
            created=now,
            created_by=user.id if user else None,
        )


def publish_component_changes(usage_key: LibraryUsageLocatorV2, user_id: int):
    """
    Publish all pending changes in a single component.
    """
    component = get_component_from_usage_key(usage_key)
    library_key = usage_key.context_key
    content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    learning_package = content_library.learning_package
    assert learning_package
    # The core publishing API is based on draft objects, so find the draft that corresponds to this component:
    drafts_to_publish = content_api.get_all_drafts(learning_package.id).filter(entity__entity_ref=component.entity_ref)
    # Publish the component and update anything that needs to be updated (e.g. search index):
    content_api.publish_from_drafts(learning_package.id, draft_qset=drafts_to_publish, published_by=user_id)


def _component_exists(usage_key: UsageKeyV2) -> bool:
    """
    Does a Component exist for this usage key?

    This is a lower-level function that will return True if a Component object
    exists, even if it was soft-deleted, and there is no active draft version.
    """
    try:
        get_component_from_usage_key(usage_key)
    except ObjectDoesNotExist:
        return False
    return True


def _create_component_for_block(
    content_lib: ContentLibrary,
    usage_key: LibraryUsageLocatorV2,
    user_id: int | None = None,
    can_stand_alone: bool = True,
):
    """
    Create a Component for an XBlock type, initialize it, and return the ComponentVersion.

    This will create a Component, along with its first ComponentVersion. The tag
    in the OLX will have no attributes, e.g. `<problem />`. This first version
    will be set as the current draft. This function does not publish the
    Component.

    Set can_stand_alone = False when a component is created under a container, like unit.

    TODO: We should probably shift this to openedx.core.djangoapps.xblock.api
    (along with its caller) since it gives runtime storage specifics. The
    Library-specific logic stays in this module, so "create a block for my lib"
    should stay here, but "making a block means creating a component with
    text data like X" goes in xblock.api.
    """
    display_name = xblock_type_display_name(usage_key.block_type)
    now = datetime.now(tz=timezone.utc)  # noqa: UP017
    xml_text = f'<{usage_key.block_type} />'

    learning_package = content_lib.learning_package
    assert learning_package is not None  # mostly for type checker

    with transaction.atomic():
        component_type = content_api.get_or_create_component_type(
            "xblock.v1", usage_key.block_type
        )
        block_olx_media = content_api.get_or_create_text_media(
            learning_package.id,
            get_or_create_olx_media_type(usage_key.block_type).id,
            text=xml_text,
            created=now,
        )
        _component, component_version = content_api.create_component_and_version(
            learning_package.id,
            component_type=component_type,
            component_code=usage_key.block_id,
            title=display_name,
            created=now,
            created_by=user_id,
            can_stand_alone=can_stand_alone,
            media={
                'block.xml': block_olx_media
            }
        )

        return component_version
