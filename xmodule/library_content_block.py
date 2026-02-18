"""
LegacyLibraryContent: The XBlock used to randomly select a subset of blocks from a "v1" (modulestore-backed) library.

In Studio, it's called the "Randomized Content Module".

In the long-term, this block is deprecated in favor of "v2" (openedx_content-backed) library references:
https://github.com/openedx/edx-platform/issues/32457

We need to retain backwards-compatibility, but please do not build any new features into this.
"""
from __future__ import annotations

import json
import logging
import typing as t
from gettext import gettext, ngettext

import nh3
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from opaque_keys.edx.locator import LibraryLocator, LibraryUsageLocatorV2
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import XBlock
from xblock.fields import Boolean, Scope, String

from xmodule.capa.responsetypes import registry
from xmodule.item_bank_block import ItemBankMixin
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.validation import StudioValidation, StudioValidationMessage
from xmodule.x_module import XModuleToXBlockMixin

if t.TYPE_CHECKING:
    from xmodule.library_tools import LegacyLibraryToolsService


_ = lambda text: text

logger = logging.getLogger(__name__)

ANY_CAPA_TYPE_VALUE = 'any'


def _get_human_name(problem_class):
    """
    Get the human-friendly name for a problem type.
    """
    return getattr(problem_class, 'human_name', problem_class.__name__)


def _get_capa_types():
    """
    Gets capa types tags and labels
    """
    capa_types = {tag: _get_human_name(registry.get_class_for_tag(tag)) for tag in registry.registered_tags()}

    return [{'value': ANY_CAPA_TYPE_VALUE, 'display_name': _('Any Type')}] + sorted([
        {'value': capa_type, 'display_name': caption}
        for capa_type, caption in capa_types.items()
    ], key=lambda item: item.get('display_name'))


class LibraryToolsUnavailable(ValueError):
    """
    Raised when the library_tools service is requested in a runtime that doesn't provide it.
    """
    def __init__(self):
        super().__init__("Needed 'library_tools' features which were not available in the current runtime")


@XBlock.wants('library_tools')
@XBlock.wants('studio_user_permissions')  # Only available in CMS.
class LegacyLibraryContentBlock(ItemBankMixin, XModuleToXBlockMixin, XBlock):
    """
    An XBlock whose children are chosen dynamically from a legacy (v1) content library.
    Can be used to create randomized assessments among other things.

    Note: technically, all matching blocks from the content library are added
    as children of this block, but only a subset of those children are shown to
    any particular student.
    """
    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        default="Randomized Content Block",
        scope=Scope.settings,
    )
    source_library_id = String(
        display_name=_("Library"),
        help=_("Select the library from which you want to draw content."),
        scope=Scope.settings,
        values_provider=lambda instance: instance.source_library_values(),
    )
    source_library_version = String(
        # This is a hidden field that stores the version of source_library when we last pulled content from it
        display_name=_("Library Version"),
        scope=Scope.settings,
    )
    capa_type = String(
        display_name=_("Problem Type"),
        help=_('Choose a problem type to fetch from the library. If "Any Type" is selected no filtering is applied.'),
        default=ANY_CAPA_TYPE_VALUE,
        values=_get_capa_types(),
        scope=Scope.settings,
    )
    # This is a hidden field that stores whether child blocks are migrated to v2, i.e., whether they have an upstream.
    # We can never completely remove the legacy library_content block; otherwise, we'd lose student data,
    # (such as selected fields), which tracks the children selected for each user.
    # However, once all legacy libraries are migrated to v2 and removed, this block can be converted into a very thin
    # compatibility wrapper around ItemBankBlock. All other aspects of LegacyLibraryContentBlock (the editor, the child
    # viewer, the block picker, the legacy syncing mechanism, etc.) can then be removed.
    is_migrated_to_v2 = Boolean(
        display_name=_("Is Migrated to library v2"),
        scope=Scope.settings,
        default=False,
    )

    @property
    def source_library_key(self):
        """
        Convenience method to get the library ID as a LibraryLocator and not just a string.
        """
        return LibraryLocator.from_string(self.source_library_id)

    @property
    def is_source_lib_migrated_to_v2(self) -> bool:
        """
        Determines whether the source library has been migrated to v2.
        """
        from cms.djangoapps.modulestore_migrator.api import is_forwarded

        return (
            self.source_library_id
            and self.source_library_version
            and is_forwarded(self.source_library_key)
        )

    @property
    def is_ready_to_migrate_to_v2(self) -> bool:
        """
        Returns whether the block can be migrated to v2.
        """
        return self.is_source_lib_migrated_to_v2 and not self.is_migrated_to_v2

    def author_view(self, context):
        """
        Renders the Studio views.
        Normal studio view: If block is properly configured, displays library status summary
        Studio container view: displays a preview of all possible children.
        """
        if self.is_migrated_to_v2:
            # Show ItemBank UI in this case
            return super().author_view(context)
        fragment = Fragment()
        root_xblock = context.get('root_xblock')
        is_root = root_xblock and root_xblock.location == self.location
        try:
            is_updating = self.get_tools().are_children_syncing(self)
        except LibraryToolsUnavailable:
            is_updating = False
        if is_root and not is_updating:
            # User has clicked the "View" link. Show a preview of all possible children:
            if self.children:  # pylint: disable=no-member
                max_count = self.max_count
                if max_count < 0:
                    max_count = len(self.children)

                fragment.add_content(self.runtime.service(self, 'mako').render_cms_template(
                    "library-block-author-preview-header.html", {
                        'max_count': max_count,
                        'display_name': self.display_name or self.url_name,
                    }))
                context['can_edit_visibility'] = False
                context['can_move'] = False
                context['can_collapse'] = True
                self.render_children(context, fragment, can_reorder=False, can_add=False)
        # else: When shown on a unit page, don't show any sort of preview -
        # just the status of this block in the validation area.
        context['is_loading'] = is_updating

        # The following JS is used to make the "Update now" button work on the unit page and the container view:
        if root_xblock and 'library' in root_xblock.category:
            if root_xblock.source_library_id and len(root_xblock.children) > 0:
                fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/library_content_edit.js'))
        else:
            fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/library_content_edit.js'))

        fragment.initialize_js('LibraryContentAuthorView', {"is_root": is_root})
        return fragment

    @property
    def non_editable_metadata_fields(self):
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.extend([
            LegacyLibraryContentBlock.source_library_version,
            LegacyLibraryContentBlock.is_migrated_to_v2,
        ])
        if self.is_migrated_to_v2:
            # If the block is migrated, hide legacy settings to make it similar to the new ItemBankBlock.
            non_editable_fields.extend([
                LegacyLibraryContentBlock.capa_type,
                LegacyLibraryContentBlock.source_library_id,
            ])
        return non_editable_fields

    def get_tools(self, to_read_library_content: bool = False) -> LegacyLibraryToolsService:
        """
        Grab the library tools service and confirm that it'll work for us. Else, raise LibraryToolsUnavailable.
        """
        if tools := self.runtime.service(self, 'library_tools'):
            if (not to_read_library_content) or tools.can_use_library_content(self):
                return tools
        raise LibraryToolsUnavailable()

    def _validate_sync_permissions(self):
        """
        Raises PermissionDenied() if we can't confirm that user has write on this block and read on source library.

        If source library isn't set, then that's OK.
        """
        if not (user_perms := self.runtime.service(self, 'studio_user_permissions')):
            raise PermissionDenied("Access cannot be validated in the current runtime.")
        if not user_perms.can_write(self.scope_ids.usage_id.context_key):
            raise PermissionDenied(f"Cannot write to block at {self.scope_ids.usage_id}")
        if self.source_library_key:
            if not user_perms.can_read(self.source_library_key):
                raise PermissionDenied(f"Cannot read library at {self.source_library_key}")

    @XBlock.handler
    def upgrade_and_sync(self, request=None, suffix=None):  # pylint: disable=unused-argument
        """
        HTTP handler allowing Studio users to update to latest version of source library and synchronize children.

        This is a thin wrapper around `sync_from_library(upgrade_to_latest=True)`, plus permission checks.

        Returns 400 if libraray tools or user permission services are not available.
        Returns 403/404 if user lacks read access on source library or write access on this block.
        """
        if self.is_migrated_to_v2:
            # If the block is already migrated to behave like ItemBankBlock
            return Response(
                _("This block is already migrated to use library v2. You can sync individual blocks now"),
                status=400
            )
        self._validate_sync_permissions()
        if not self.source_library_id:
            return Response(_("Source content library has not been specified."), status=400)
        try:
            self.sync_from_library(upgrade_to_latest=True)
        except LibraryToolsUnavailable:
            return Response(_("Content libraries are not available in the current runtime."), status=400)
        except ObjectDoesNotExist:
            return Response(
                _("Source content library does not exist: {source_library_id}").format(
                    source_library_id=self.source_library_id
                ),
                status=400,
            )
        return Response()

    def sync_from_library(self, upgrade_to_latest: bool = False) -> None:
        """
        Synchronize children with source library.

        If `upgrade_to_latest==True` or if source library version is unset, update library version to latest.
        Otherwise, use current source library version.

        Raises ObjectDoesNotExist if library or version is missing.
        """
        self.get_tools(to_read_library_content=True).trigger_library_sync(
            dest_block=self,
            library_version=(None if upgrade_to_latest else self.source_library_version),
        )

    @XBlock.handler
    def children_are_syncing(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Returns whether this block is currently having its children updated from the source library.
        """
        try:
            is_updating = self.get_tools().are_children_syncing(self)
        except LibraryToolsUnavailable:
            is_updating = False
        return Response(json.dumps(is_updating))

    def studio_post_duplicate(self, store, source_block):
        """
        Used by the studio after basic duplication of a source block. We handle the children
        ourselves, because we have to properly reference the library upstream and set the overrides.

        Otherwise we'll end up losing data on the next refresh.
        """
        if hasattr(super(), 'studio_post_duplicate'):
            super().studio_post_duplicate(store, source_block)

        if self.is_migrated_to_v2:
            # If the block is already migrated to behave like ItemBankBlock
            return False  # Children have not been handled

        self._validate_sync_permissions()
        self.get_tools(to_read_library_content=True).trigger_duplication(source_block=source_block, dest_block=self)
        return True  # Children have been handled.

    def studio_post_paste(self, store, source_node) -> bool:
        """
        Pull the children from the library and let library_tools assign their IDs.
        """
        if hasattr(super(), 'studio_post_paste'):
            super().studio_post_paste(store, source_node)

        if self.is_migrated_to_v2:
            # If the block is already migrated to behave like ItemBankBlock
            return False  # Children have not been handled

        self.sync_from_library(upgrade_to_latest=False)
        return True  # Children have been handled

    def v2_update_children_upstream_version(self, user_id=None):
        """
        Update the upstream and upstream version fields of all children to point to library v2 version of the legacy
        library blocks. This essentially converts this legacy block to new ItemBankBlock.
        """
        from cms.djangoapps.modulestore_migrator import api as migrator_api
        store = modulestore()
        with store.bulk_operations(self.course_id):
            children = self.get_children()
            # These are the v1 library item upstream UsageKeys
            child_old_upstream_keys = [
                self.runtime.modulestore.get_block_original_usage(child.usage_key)[0]
                for child in children
            ]
            child_migrations = migrator_api.get_forwarding_for_blocks(child_old_upstream_keys)
            for child, old_upstream_key in zip(children, child_old_upstream_keys):
                upstream_migration = child_migrations.get(old_upstream_key)
                if upstream_migration and isinstance(upstream_migration.target_key, LibraryUsageLocatorV2):
                    child.upstream = str(upstream_migration.target_key)
                    if upstream_migration.target_version_num:
                        child.upstream_version = upstream_migration.target_version_num
                else:
                    child.upstream = ""
                # Use `modulestore()` instead of `self.runtime.modulestore` to make sure that the XBLOCK_UPDATED signal
                # is triggered
                store.update_item(child, user_id)
            self.is_migrated_to_v2 = True
            self.save()
            store.update_item(self, user_id)

    def _validate_library_version(self, validation, lib_tools, version, library_key):
        """
        Validates library version
        """
        latest_version = lib_tools.get_latest_library_version(library_key)
        if self.is_ready_to_migrate_to_v2:
            validation.set_summary(
                StudioValidationMessage(
                    StudioValidationMessage.WARNING,
                    _(
                        'This legacy library reference is no longer supported, and'
                        ' needs to be updated to receive future changes'
                    ),
                    # TODO: change this to action_runtime_event='...' once the unit page supports that feature.
                    # See https://openedx.atlassian.net/browse/TNL-993
                    action_class='library-block-migrate-btn',
                    # Translators: {refresh_icon} placeholder is substituted to "↻" (without double quotes)
                    action_label=_('{refresh_icon} Update reference').format(refresh_icon='↻'),
                )
            )
            return False
        if latest_version is not None:
            if version is None or version != latest_version:
                validation.set_summary(
                    StudioValidationMessage(
                        StudioValidationMessage.WARNING,
                        _('This component is out of date. The library has new content.'),
                        # TODO: change this to action_runtime_event='...' once the unit page supports that feature.
                        # See https://openedx.atlassian.net/browse/TNL-993
                        action_class='library-update-btn',
                        # Translators: {refresh_icon} placeholder is substituted to "↻" (without double quotes)
                        action_label=_("{refresh_icon} Update now.").format(refresh_icon="↻")
                    )
                )
                return False
        else:
            validation.set_summary(
                StudioValidationMessage(
                    StudioValidationMessage.ERROR,
                    _('Library is invalid, corrupt, or has been deleted.'),
                    action_class='edit-button',
                    action_label=_("Edit Library List.")
                )
            )
            return False
        return True

    def _set_validation_error_if_empty(self, validation, summary):
        """  Helper method to only set validation summary if it's empty """
        if validation.empty:
            validation.set_summary(summary)

    @XBlock.handler
    def upgrade_to_v2_library(self, request=None, suffix=None):
        """
        Upgrate this legacy block to a mode where it behaves like the new ItemBankBlock which uses library v2 blocks as
        children.
        """
        if not self.is_source_lib_migrated_to_v2:
            return Response(_("The source library has not been migrated to version 2"), status=400)
        if self.is_migrated_to_v2:
            return Response(_("The block has already been upgraded to version 2"), status=400)
        # If the source library is migrated but this block still depends on legacy library
        # Migrate the block by setting upstream field to all children blocks
        self.v2_update_children_upstream_version()
        return Response()

    def validate(self):
        """
        Validates the state of this Library Content Block Instance.
        """
        if self.is_migrated_to_v2:
            # If the block is already migrated to v2 i.e. ItemBankBlock
            # super() will call ItemBankMixin.validate() as it is first in inheritance order
            return super().validate()

        # We cannot use `super()` here because we do not want to invoke `ItemBankMixin.validate()`.
        # Instead, we want to use `XBlock.validate`.
        validation = XBlock.validate(self)
        if not isinstance(validation, StudioValidation):
            validation = StudioValidation.copy(validation)
        try:
            lib_tools = self.get_tools(to_read_library_content=True)
        except LibraryToolsUnavailable:
            validation.set_summary(
                StudioValidationMessage(
                    StudioValidationMessage.ERROR,
                    _(
                        "This course does not support content libraries. "
                        "Contact your system administrator for more information."
                    )
                )
            )
            return validation
        if not self.source_library_id:
            validation.set_summary(
                StudioValidationMessage(
                    StudioValidationMessage.NOT_CONFIGURED,
                    _("A library has not yet been selected."),
                    action_class='edit-button',
                    action_label=_("Select a Library.")
                )
            )
            return validation
        self._validate_library_version(
            validation,
            lib_tools,
            self.source_library_version,
            self.source_library_key
        )

        # Note: we assume children have been synced
        # since the last time fields like source_library_id or capa_types were changed.
        matching_children_count = len(self.children)  # pylint: disable=no-member
        if matching_children_count == 0:
            self._set_validation_error_if_empty(
                validation,
                StudioValidationMessage(
                    StudioValidationMessage.WARNING,
                    (gettext('There are no problems in the specified library of type {capa_type}.'))
                    .format(capa_type=self.capa_type),
                    action_class='edit-button',
                    action_label=_("Select another problem type.")
                )
            )

        if matching_children_count < self.max_count:
            self._set_validation_error_if_empty(
                validation,
                StudioValidationMessage(
                    StudioValidationMessage.WARNING,
                    (
                        ngettext(
                            'The specified library is configured to fetch {count} problem, ',
                            'The specified library is configured to fetch {count} problems, ',
                            self.max_count
                        ) +
                        ngettext(
                            'but there is only {actual} matching problem.',
                            'but there are only {actual} matching problems.',
                            matching_children_count
                        )
                    ).format(count=self.max_count, actual=matching_children_count),
                    action_class='edit-button',
                    action_label=_("Edit the library configuration.")
                )
            )

        return validation

    def source_library_values(self):
        """
        Return a list of possible values for self.source_library_id
        """
        lib_tools = self.get_tools()
        user_perms = self.runtime.service(self, 'studio_user_permissions')
        all_libraries = [
            (key, nh3.clean(name)) for key, name in lib_tools.list_available_libraries()
            if user_perms.can_read(key) or self.source_library_id == str(key)
        ]
        all_libraries.sort(key=lambda entry: entry[1])  # Sort by name
        if self.source_library_id and self.source_library_key not in [entry[0] for entry in all_libraries]:
            all_libraries.append((self.source_library_id, _("Invalid Library")))
        all_libraries = [("", _("No Library Selected"))] + all_libraries
        values = [{"display_name": name, "value": str(key)} for key, name in all_libraries]
        return values

    def post_editor_saved(self, user, old_metadata, old_content):  # pylint: disable=unused-argument
        """
        If source library or capa_type have been edited, upgrade library & sync automatically.
        """
        if self.is_migrated_to_v2:
            # If the block is already migrated to v2 i.e. ItemBankBlock, Do nothing
            return True
        source_lib_changed = (self.source_library_id != old_metadata.get("source_library_id", ""))
        capa_filter_changed = (self.capa_type != old_metadata.get("capa_type", ANY_CAPA_TYPE_VALUE))
        if source_lib_changed or capa_filter_changed:
            try:
                self.sync_from_library(upgrade_to_latest=True)
            except (ObjectDoesNotExist, LibraryToolsUnavailable):
                # The validation area will display an error message, no need to do anything now.
                pass

    def format_block_keys_for_analytics(self, block_keys: list[tuple[str, str]]) -> list[dict]:
        """
        Implement format_block_keys_for_analytics using the modulestore-specific legacy library original-usage system.
        """
        if self.is_migrated_to_v2:
            return super().format_block_keys_for_analytics(block_keys)

        def summarize_block(usage_key):
            """ Basic information about the given block """
            orig_key, orig_version = self.runtime.modulestore.get_block_original_usage(usage_key)
            return {
                "usage_key": str(usage_key),
                "original_usage_key": str(orig_key.replace(version=None, branch=None)) if orig_key else None,
                "original_usage_version": str(orig_version) if orig_version else None,
            }

        result_json = []
        for block_key in block_keys:
            key = self.context_key.make_usage_key(*block_key)
            info = summarize_block(key)
            info['descendants'] = []
            try:
                block = self.runtime.modulestore.get_item(key, depth=None)  # Load the item and all descendants
                children = list(getattr(block, "children", []))
                while children:
                    child_key = children.pop().replace(version=None, branch=None)
                    child = self.runtime.modulestore.get_item(child_key)
                    info['descendants'].append(summarize_block(child_key))
                    children.extend(getattr(child, "children", []))
            except ItemNotFoundError:
                pass  # The block has been deleted
            result_json.append(info)
        return result_json

    @classmethod
    def get_selected_event_prefix(cls) -> str:
        """
        Prefix for events on `self.selected`.

        We use librarycontent rather than legacylibrarycontent for backwards compatibility (this wasn't always the
        "legacy" library content block :)
        """
        return "edx.librarycontentblock.content"


class LegacyLibrarySummary:
    """
    A library summary object which contains the fields required for library listing on studio.
    """

    def __init__(self, library_locator, display_name):
        """
        Initialize LegacyLibrarySummary

        Arguments:
        library_locator (LibraryLocator):  LibraryLocator object of the library.

        display_name (unicode): display name of the library.
        """
        self.display_name = display_name if display_name else _("Empty")

        self.id = library_locator  # pylint: disable=invalid-name
        self.location = library_locator.make_usage_key('library', 'library')

    @property
    def display_org_with_default(self):
        """
        Org display names are not implemented. This just provides API compatibility with CourseBlock.
        Always returns the raw 'org' field from the key.
        """
        return self.location.library_key.org

    @property
    def display_number_with_default(self):
        """
        Display numbers are not implemented. This just provides API compatibility with CourseBlock.
        Always returns the raw 'library' field from the key.
        """
        return self.location.library_key.library
