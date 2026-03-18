"""
XBlock runtime services for LegacyLibraryContentBlock
"""
from __future__ import annotations

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist
from opaque_keys.edx.locator import LibraryLocator
from user_tasks.models import UserTaskStatus

from openedx.core.lib import ensure_cms
from openedx.core.djangoapps.content_libraries import tasks as library_tasks
from xmodule.library_content_block import LegacyLibraryContentBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.exceptions import ItemNotFoundError


def normalize_key_for_search(library_key):
    """ Normalizes library key for use with search indexing """
    return library_key.replace(version_guid=None, branch=None)


class LegacyLibraryToolsService:
    """
    Service for LegacyLibraryContentBlock.

    Allows to interact with libraries in the modulestore and openedx_content.

    Should only be used in the CMS.
    """
    def __init__(self, modulestore, user_id):
        self.store = modulestore
        self.user_id = user_id

    def get_latest_library_version(self, library_id: str | LibraryLocator) -> str | None:
        """
        Get the version of the given library as string.

        The return value (library version) could be:
            str(<ObjectID>) - for V1 library;
            None            - if the library does not exist.
        """
        library_key: LibraryLocator
        if isinstance(library_id, str):
            library_key = LibraryLocator.from_string(library_id)
        else:
            library_key = library_id
        library_key = library_key.for_branch(ModuleStoreEnum.BranchName.library).for_version(None)
        try:
            library = self.store.get_library(
                library_key, remove_version=False, remove_branch=False, head_validation=False
            )
        except ItemNotFoundError:
            return None
        if not library:
            return None
        # We need to know the library's version so ensure it's set in library.location.library_key.version_guid
        assert library.location.library_key.version_guid is not None
        return str(library.location.library_key.version_guid)

    def can_use_library_content(self, block):
        """
        Determines whether a modulestore holding a course_id supports libraries.
        """
        return self.store.check_supports(block.location.course_key, 'copy_from_template')

    def trigger_library_sync(self, dest_block: LegacyLibraryContentBlock, library_version: str | None) -> None:
        """
        Queue task to synchronize the children of `dest_block` with it source library (at `library_version` or latest).

        Raises ObjectDoesNotExist if library/version cannot be found.

        The task will:
        * Load that library at `dest_block.source_library_id` and `library_version`.
          * If `library_version` is None, load the latest.
          * Update `dest_block.source_library_version` based on what is loaded.
        * Ensure that `dest_block` has children corresponding to all matching source library blocks.
          * Considered fields of `dest_block` include: `source_library_id`, `source_library_version`, `capa_type`.
            library version, and upate `dest_block.source_library_version` to match.
        * Derive each child block id as a function of `dest_block`'s id and the library block's definition id.
        * Follow these important create/update/delete semantics for children:
          * When a matching library child DOES NOT EXIT in `dest_block.children`: import it in as a new block.
          * When a matching library child ALREADY EXISTS in `dest_block.children`: re-import its definition, clobbering
            any content updates in this existing child, but preserving any settings overrides in the existing child.
          * When a block in `dest_block.children` DOES NOT MATCH any library children: delete it from
            `dest_block.children`.
        """
        ensure_cms("library_content block children may only be synced in a CMS context")
        if not isinstance(dest_block, LegacyLibraryContentBlock):
            raise ValueError(f"Can only sync children for library_content blocks, not {dest_block.tag} blocks.")
        if not dest_block.source_library_id:
            dest_block.source_library_version = ""
            return
        library_key = dest_block.source_library_key.for_branch(
            ModuleStoreEnum.BranchName.library
        ).for_version(library_version)
        try:
            self.store.get_library(library_key, remove_version=False, remove_branch=False, head_validation=False)
        except ItemNotFoundError as exc:
            if library_version:
                raise ObjectDoesNotExist(f"Version {library_version} of library {library_key} not found.") from exc
            raise ObjectDoesNotExist(f"Library {library_key} not found.") from exc

        # TODO: This task is synchronous until we can figure out race conditions with import.
        # These race conditions lead to failed imports of library content from course import.
        # See: TNL-11339, https://github.com/openedx/edx-platform/issues/34029 for more info.
        library_tasks.sync_from_library.apply(
            kwargs=dict(
                user_id=self.user_id,
                dest_block_id=str(dest_block.scope_ids.usage_id),
                library_version=library_version,
            ),
        )

    def trigger_duplication(
        self, source_block: LegacyLibraryContentBlock, dest_block: LegacyLibraryContentBlock
    ) -> None:
        """
        Queue a task to duplicate the children of `source_block` to `dest_block`.
        """
        ensure_cms("library_content block children may only be duplicated in a CMS context")
        if not isinstance(dest_block, LegacyLibraryContentBlock):
            raise ValueError(f"Can only duplicate children for library_content blocks, not {dest_block.tag} blocks.")
        if source_block.scope_ids.usage_id.context_key != source_block.scope_ids.usage_id.context_key:
            raise ValueError(
                "Cannot duplicate_children across different learning contexts "
                f"(source={source_block.scope_ids.usage_id}, dest={dest_block.scope_ids.usage_id})"
            )
        if source_block.source_library_key != dest_block.source_library_key:
            raise ValueError(
                "Cannot duplicate_children across different source libraries or versions thereof "
                f"({source_block.source_library_key=}, {dest_block.source_library_key=})."
            )
        library_tasks.duplicate_children.delay(
            user_id=self.user_id,
            source_block_id=str(source_block.scope_ids.usage_id),
            dest_block_id=str(dest_block.scope_ids.usage_id),
        )

    def are_children_syncing(self, library_content_block: LegacyLibraryContentBlock) -> bool:
        """
        Is a task currently running to sync the children of `library_content_block`?

        Only checks the latest task (so that this block's state can't get permanently messed up by
        some older task that's stuck in PENDING).
        """
        args = {'dest_block_id': library_content_block.scope_ids.usage_id}
        name = library_tasks.LibrarySyncChildrenTask.generate_name(args)
        status = UserTaskStatus.objects.filter(name=name).order_by('-created').first()
        return status and status.state in [
            UserTaskStatus.IN_PROGRESS, UserTaskStatus.PENDING, UserTaskStatus.RETRYING
        ]

    def list_available_libraries(self):
        """
        List all known legacy libraries.

        Returns tuples of (library key, display_name).
        """
        user = User.objects.get(id=self.user_id)
        return [
            (lib.location.library_key.replace(version_guid=None, branch=None), lib.display_name)
            for lib in self.store.get_library_summaries()
        ]
