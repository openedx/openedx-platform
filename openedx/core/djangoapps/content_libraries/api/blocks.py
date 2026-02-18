"""
Content libraries API methods related to XBlocks/Components.

These methods don't enforce permissions (only the REST APIs do).
"""
from __future__ import annotations
import logging
import mimetypes
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_unicode_slug
from django.db import transaction
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from lxml import etree
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from opaque_keys.edx.keys import LearningContextKey, UsageKeyV2
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
    LibraryBlockData,
    LibraryCollectionData,
    LibraryContainerData
)
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_BLOCK_CREATED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_BLOCK_UPDATED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_UPDATED
)
from openedx_content import api as content_api
from openedx_content.models_api import (
    Component, ComponentVersion, LearningPackage, MediaType,
    Container, Collection
)
from xblock.core import XBlock

from openedx.core.djangoapps.xblock.api import (
    get_component_from_usage_key,
    get_xblock_app_config,
    xblock_type_display_name
)
from openedx.core.types import User as UserType

from ..models import ContentLibrary
from .exceptions import (
    BlockLimitReachedError,
    ContentLibraryBlockNotFound,
    IncompatibleTypesError,
    InvalidNameError,
    LibraryBlockAlreadyExists,
)
from .block_metadata import LibraryXBlockMetadata, LibraryXBlockStaticFile
from .containers import (
    create_container,
    get_container,
    get_containers_contains_item,
    update_container_children,
    ContainerMetadata,
    ContainerType,
)
from .collections import library_collection_locator
from .libraries import PublishableItem
from .. import tasks

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
        associated_collections = content_api.get_entity_collections(
            component.learning_package_id,
            component.key,
        ).values('key', 'title')
    else:
        associated_collections = None
    xblock_metadata = LibraryXBlockMetadata.from_component(
        library_key=usage_key.context_key,
        component=component,
        associated_collections=associated_collections,
    )
    return xblock_metadata


def set_library_block_olx(usage_key: LibraryUsageLocatorV2, new_olx_str: str) -> ComponentVersion:
    """
    Replace the OLX source of the given XBlock.

    This is only meant for use by developers or API client applications, as
    very little validation is done and this can easily result in a broken XBlock
    that won't load.

    Returns the version number of the newly created ComponentVersion.
    """
    assert isinstance(usage_key, LibraryUsageLocatorV2)

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

    now = datetime.now(tz=timezone.utc)

    with transaction.atomic():
        new_content = content_api.get_or_create_text_content(
            component.learning_package_id,
            get_or_create_olx_media_type(usage_key.block_type).id,
            text=new_olx_str,
            created=now,
        )
        new_component_version = content_api.create_next_component_version(
            component.pk,
            title=new_title,
            content_to_replace={
                'block.xml': new_content.pk,
            },
            created=now,
        )

    # .. event_implemented_name: LIBRARY_BLOCK_UPDATED
    # .. event_type: org.openedx.content_authoring.library_block.updated.v1
    LIBRARY_BLOCK_UPDATED.send_event(
        library_block=LibraryBlockData(
            library_key=usage_key.context_key,
            usage_key=usage_key
        )
    )

    # For each container, trigger LIBRARY_CONTAINER_UPDATED signal and set background=True to trigger
    # container indexing asynchronously.
    affected_containers = get_containers_contains_item(usage_key)
    for container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=container.container_key,
                background=True,
            )
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

    # .. event_implemented_name: LIBRARY_BLOCK_CREATED
    # .. event_type: org.openedx.content_authoring.library_block.created.v1
    LIBRARY_BLOCK_CREATED.send_event(
        library_block=LibraryBlockData(
            library_key=content_library.library_key,
            usage_key=usage_key
        )
    )

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
    staged_content_id: int,
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
        component = content_api.create_component(
            learning_package.id,
            component_type=component_type,
            local_key=usage_key.block_id,
            created=now,
            created_by=user.id,
        )

        # This will create the first component version and set the OLX/title
        # appropriately. It will not publish. Once we get the newly created
        # ComponentVersion back from this, we can attach all our files to it.
        component_version = set_library_block_olx(usage_key, olx_str)

        for staged_content_file_data in staged_content_files:
            # The ``data`` attribute is going to be None because the clipboard
            # is optimized to not do redundant file copying when copying/pasting
            # within the same course (where all the Files and Uploads are
            # shared). Learning Core backed content Components will always store
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
            # Learning Core backed data model (v2 Libraries), then we want to
            # prepend "static/" to the filename. This will need to get updated
            # when we start moving courses over to Learning Core, or if we start
            # storing course component assets in sub-directories of Files and
            # Uploads.
            #
            # The reason we don't just search for a "static/" prefix is that
            # Learning Core components can store other kinds of files if they
            # wish (though none currently do).
            source_assumes_global_assets = not isinstance(
                source_context_key, LibraryLocatorV2
            )
            if source_assumes_global_assets:
                filename = f"static/{filename}"

            # Now construct the Learning Core data models for it...
            # TODO: more of this logic should be pushed down to openedx-learning
            media_type_str, _encoding = mimetypes.guess_type(filename)
            if not media_type_str:
                media_type_str = "application/octet-stream"

            media_type = content_api.get_or_create_media_type(media_type_str)
            content = content_api.get_or_create_file_content(
                learning_package.id,
                media_type.id,
                data=file_data,
                created=now,
            )
            content_api.create_component_version_content(
                component_version.pk,
                content.id,
                key=filename,
            )

    # Emit library block created event
    # .. event_implemented_name: LIBRARY_BLOCK_CREATED
    # .. event_type: org.openedx.content_authoring.library_block.created.v1
    LIBRARY_BLOCK_CREATED.send_event(
        library_block=LibraryBlockData(
            library_key=content_library.library_key,
            usage_key=usage_key
        )
    )

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
    staged_content_id: int,
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
        container_type=ContainerType.from_source_olx_tag(olx_node.tag),
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

    now = datetime.now(tz=timezone.utc)

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

    Learning Core stores all Content with a Media Type (a.k.a. MIME type). For
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
    component = get_component_from_usage_key(usage_key)
    library_key = usage_key.context_key
    affected_collections = content_api.get_entity_collections(component.learning_package_id, component.key)
    affected_containers = get_containers_contains_item(usage_key)

    content_api.soft_delete_draft(component.pk, deleted_by=user_id)

    # .. event_implemented_name: LIBRARY_BLOCK_DELETED
    # .. event_type: org.openedx.content_authoring.library_block.deleted.v1
    LIBRARY_BLOCK_DELETED.send_event(
        library_block=LibraryBlockData(
            library_key=library_key,
            usage_key=usage_key
        )
    )

    # For each collection, trigger LIBRARY_COLLECTION_UPDATED signal and set background=True to trigger
    # collection indexing asynchronously.
    #
    # To delete the component on collections
    for collection in affected_collections:
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library_key,
                    collection_key=collection.key,
                ),
                background=True,
            )
        )

    # For each container, trigger LIBRARY_CONTAINER_UPDATED signal and set background=True to trigger
    # container indexing asynchronously.
    #
    # To update the components count in containers
    for container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=container.container_key,
                background=True,
            )
        )


def restore_library_block(usage_key: LibraryUsageLocatorV2, user_id: int | None = None) -> None:
    """
    Restore the specified library block.
    """
    component = get_component_from_usage_key(usage_key)
    library_key = usage_key.context_key
    affected_collections = content_api.get_entity_collections(component.learning_package_id, component.key)

    # Set draft version back to the latest available component version id.
    content_api.set_draft_version(
        component.pk,
        component.versioning.latest.pk,
        set_by=user_id,
    )

    # .. event_implemented_name: LIBRARY_BLOCK_CREATED
    # .. event_type: org.openedx.content_authoring.library_block.created.v1
    LIBRARY_BLOCK_CREATED.send_event(
        library_block=LibraryBlockData(
            library_key=library_key,
            usage_key=usage_key
        )
    )

    # Add tags and collections back to index
    # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
    # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
        content_object=ContentObjectChangedData(
            object_id=str(usage_key),
            changes=["collections", "tags", "units"],
        ),
    )

    # For each collection, trigger LIBRARY_COLLECTION_UPDATED signal and set background=True to trigger
    # collection indexing asynchronously.
    #
    # To restore the component in the collections
    for collection in affected_collections:
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library_key,
                    collection_key=collection.key,
                ),
                background=True,
            )
        )

    # For each container, trigger LIBRARY_CONTAINER_UPDATED signal and set background=True to trigger
    # container indexing asynchronously.
    #
    # To update the components count in containers
    affected_containers = get_containers_contains_item(usage_key)
    for container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=container.container_key,
                background=True,
            )
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

    # cvc = the ComponentVersionContent through table
    cvc_set = (
        component_version
        .componentversioncontent_set
        .filter(content__has_file=True)
        .order_by('key')
        .select_related('content')
    )

    site_root_url = get_xblock_app_config().get_site_root_url()

    return [
        LibraryXBlockStaticFile(
            path=cvc.key,
            size=cvc.content.size,
            url=site_root_url + reverse(
                'content_libraries:library-assets',
                kwargs={
                    'component_version_uuid': component_version.uuid,
                    'asset_path': cvc.key,
                }
            ),
        )
        for cvc in cvc_set
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
            component.pk,
            content_to_replace={file_path: file_content},
            created=datetime.now(tz=timezone.utc),
            created_by=user.id if user else None,
        )
        transaction.on_commit(
            # .. event_implemented_name: LIBRARY_BLOCK_UPDATED
            # .. event_type: org.openedx.content_authoring.library_block.updated.v1
            lambda: LIBRARY_BLOCK_UPDATED.send_event(
                library_block=LibraryBlockData(
                    library_key=usage_key.context_key,
                    usage_key=usage_key,
                )
            )
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
    now = datetime.now(tz=timezone.utc)

    with transaction.atomic():
        component_version = content_api.create_next_component_version(
            component.pk,
            content_to_replace={file_path: None},
            created=now,
            created_by=user.id if user else None,
        )
        transaction.on_commit(
            # .. event_implemented_name: LIBRARY_BLOCK_UPDATED
            # .. event_type: org.openedx.content_authoring.library_block.updated.v1
            lambda: LIBRARY_BLOCK_UPDATED.send_event(
                library_block=LibraryBlockData(
                    library_key=usage_key.context_key,
                    usage_key=usage_key,
                )
            )
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
    drafts_to_publish = content_api.get_all_drafts(learning_package.id).filter(entity__key=component.key)
    # Publish the component and update anything that needs to be updated (e.g. search index):
    publish_log = content_api.publish_from_drafts(
        learning_package.id, draft_qset=drafts_to_publish, published_by=user_id,
    )
    # Since this is a single component, it should be safe to process synchronously and in-process:
    tasks.send_events_after_publish(publish_log.pk, str(library_key))
    # IF this is found to be a performance issue, we could instead make it async where necessary:
    # tasks.wait_for_post_publish_events(publish_log, library_key=library_key)


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
    now = datetime.now(tz=timezone.utc)
    xml_text = f'<{usage_key.block_type} />'

    learning_package = content_lib.learning_package
    assert learning_package is not None  # mostly for type checker

    with transaction.atomic():
        component_type = content_api.get_or_create_component_type(
            "xblock.v1", usage_key.block_type
        )
        component, component_version = content_api.create_component_and_version(
            learning_package.id,
            component_type=component_type,
            local_key=usage_key.block_id,
            title=display_name,
            created=now,
            created_by=user_id,
            can_stand_alone=can_stand_alone,
        )
        content = content_api.get_or_create_text_content(
            learning_package.id,
            get_or_create_olx_media_type(usage_key.block_type).id,
            text=xml_text,
            created=now,
        )
        content_api.create_component_version_content(
            component_version.pk,
            content.id,
            key="block.xml",
        )

        return component_version
