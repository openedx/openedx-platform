"""
Methods related to syncing a downstream XBlock with an upstream Container.

See upstream_sync.py for general upstream sync code that applies even when the
upstream is a container, not an XBlock.
"""
from __future__ import annotations

import typing as t

from django.utils.translation import gettext_lazy as _  # noqa: F401
from opaque_keys.edx.locator import LibraryContainerLocator
from xblock.core import XBlock

from openedx.core.djangoapps.content_libraries import api as lib_api

from .upstream_sync import UpstreamLink

if t.TYPE_CHECKING:
    from django.contrib.auth.models import User  # pylint: disable=imported-auth-user


def sync_from_upstream_container(
    downstream: XBlock,
    user: User,
) -> list[lib_api.LibraryXBlockMetadata | lib_api.ContainerMetadata]:
    """
    Update `downstream` with content+settings from the latest available version of its linked upstream content.

    Preserves overrides to customizable fields; overwrites overrides to other fields.
    Does not save `downstream` to the store. That is left up to the caller.

    If `downstream` lacks a valid+supported upstream link, this raises an UpstreamLinkException.

    ⭐️ Does not directly sync static assets (containers don't have them) nor
    children. Returns a list of the upstream children so the caller can do that.

    Should children be handled in here? Maybe if sync_from_upstream_block
    were updated to handle static assets and also save changes to modulestore.
    """
    link = UpstreamLink.get_for_block(downstream)  # can raise UpstreamLinkException
    if not isinstance(link.upstream_key, LibraryContainerLocator):
        raise TypeError("sync_from_upstream_container() only supports Container upstreams, not containers")
    lib_api.require_permission_for_library_key(  # TODO: should permissions be checked at this low level?
        link.upstream_key.lib_key,
        user,
        permission=lib_api.permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
    )
    upstream_meta = lib_api.get_container(link.upstream_key)
    upstream_children = lib_api.get_container_children(link.upstream_key, published=True)
    _update_customizable_fields(upstream=upstream_meta, downstream=downstream, only_fetch=False)
    _update_non_customizable_fields(upstream=upstream_meta, downstream=downstream)
    _update_tags(upstream=upstream_meta, downstream=downstream)
    downstream.upstream_version = link.version_available
    return upstream_children


def fetch_customizable_fields_from_container(*, downstream: XBlock) -> None:
    """
    Fetch upstream-defined value of customizable fields and save them on the downstream.

    The container version only retrieves values from *published* containers.

    Basically, this sets the value of "upstream_display_name" on the downstream block.
    """
    upstream = lib_api.get_container(LibraryContainerLocator.from_string(downstream.upstream))
    _update_customizable_fields(upstream=upstream, downstream=downstream, only_fetch=True)


def _update_customizable_fields(*, upstream: lib_api.ContainerMetadata, downstream: XBlock, only_fetch: bool) -> None:
    """
    For each customizable field:
    * Save the upstream value to a hidden field on the downstream ("FETCH").
    * If `not only_fetch`, and if the field *isn't* customized on the downstream, then:
      * Update it the downstream field's value from the upstream field ("SYNC").

    Concrete example: Imagine `lib_problem` is our upstream and `course_problem` is our downstream.

     * Say that the customizable fields are [display_name, max_attempts].

     * Set `course_problem.upstream_display_name = lib_problem.display_name` ("fetch").
     * If `not only_fetch`, and `course_problem.display_name` wasn't customized, then:
       * Set `course_problem.display_name = lib_problem.display_name` ("sync").
    """
    # For now, the only supported container "field" is display_name
    syncable_field_names = ["display_name"]

    for field_name, fetch_field_name in downstream.get_customizable_fields().items():

        if field_name not in syncable_field_names:
            continue

        # Downstream-only fields don't have an upstream fetch field
        if fetch_field_name is None:
            continue

        # FETCH the upstream's value and save it on the downstream (ie, `downstream.upstream_$FIELD`).
        old_upstream_value = getattr(downstream, fetch_field_name)
        new_upstream_value = getattr(upstream, f"published_{field_name}")
        setattr(downstream, fetch_field_name, new_upstream_value)

        if only_fetch:
            continue

        # Okay, now for the nuanced part...
        # We need to update the downstream field *iff it has not been customized**.
        # Determining whether a field has been customized will differ in Beta vs Future release.
        # (See "PRESERVING DOWNSTREAM CUSTOMIZATIONS" comment below for details.)

        ## FUTURE BEHAVIOR: field is "customized" iff we have noticed that the user edited it.
        #  if field_name in downstream.downstream_customized:
        #      continue

        ## BETA BEHAVIOR: field is "customized" iff we have the prev upstream value, but field doesn't match it.
        downstream_value = getattr(downstream, field_name)
        if old_upstream_value and downstream_value != old_upstream_value:
            continue  # Field has been customized. Don't touch it. Move on.

        # Field isn't customized -- SYNC it!
        setattr(downstream, field_name, new_upstream_value)


def _update_non_customizable_fields(*, upstream: lib_api.ContainerMetadata, downstream: XBlock) -> None:
    """
    For each field `downstream.blah` that isn't customizable: set it to `upstream.blah`.
    """
    # For now, there's nothing to do here - containers don't have any non-customizable fields.


def _update_tags(*, upstream: lib_api.ContainerMetadata, downstream: XBlock) -> None:
    """
    Update tags from `upstream` to `downstream`
    """
    from openedx.core.djangoapps.content_tagging.api import copy_tags_as_read_only

    # For any block synced with an upstream, copy the tags as read_only
    # This keeps tags added locally.
    copy_tags_as_read_only(
        str(upstream.container_key),
        str(downstream.usage_key),
    )
