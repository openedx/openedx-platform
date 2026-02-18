"""
Definition of "Library" as a learning context.
"""
import logging

from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import NotFound

from openedx_events.content_authoring.data import LibraryBlockData, LibraryContainerData
from openedx_events.content_authoring.signals import LIBRARY_BLOCK_UPDATED, LIBRARY_CONTAINER_UPDATED
from opaque_keys.edx.keys import UsageKeyV2
from opaque_keys.edx.locator import LibraryUsageLocatorV2, LibraryLocatorV2
from openedx_content import api as content_api

from openedx.core.djangoapps.content_libraries import api, permissions
from openedx.core.djangoapps.content_libraries.models import ContentLibrary
from openedx.core.djangoapps.xblock.api import LearningContext
from openedx.core.types import User as UserType

log = logging.getLogger(__name__)


class LibraryContextImpl(LearningContext):
    """
    Implements content libraries as a learning context.

    This is the *new* content libraries based on Learning Core, not the old content
    libraries based on modulestore.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.use_draft = kwargs.get('use_draft', None)

    def can_edit_block(self, user: UserType, usage_key: UsageKeyV2) -> bool:
        """
        Assuming a block with the specified ID (usage_key) exists, does the
        specified user have permission to edit it (make changes to the
        fields / authored data store)?

        May raise ContentLibraryNotFound if the library does not exist.
        """
        assert isinstance(usage_key, LibraryUsageLocatorV2)
        return self._check_perm(user, usage_key.lib_key, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)

    def can_view_block_for_editing(self, user: UserType, usage_key: UsageKeyV2) -> bool:
        """
        Assuming a block with the specified ID (usage_key) exists, does the
        specified user have permission to view its fields and OLX details (but
        not necessarily to make changes to it)?

        May raise ContentLibraryNotFound if the library does not exist.
        """
        assert isinstance(usage_key, LibraryUsageLocatorV2)
        return self._check_perm(user, usage_key.lib_key, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)

    def can_view_block(self, user: UserType, usage_key: UsageKeyV2) -> bool:
        """
        Does the specified usage key exist in its context, and if so, does the
        specified user have permission to view it and interact with it (call
        handlers, save user state, etc.)?

        May raise ContentLibraryNotFound if the library does not exist.
        """
        assert isinstance(usage_key, LibraryUsageLocatorV2)
        return self._check_perm(user, usage_key.lib_key, permissions.CAN_LEARN_FROM_THIS_CONTENT_LIBRARY)

    def _check_perm(self, user: UserType, lib_key: LibraryLocatorV2, perm) -> bool:
        """ Helper method to check a permission for the various can_ methods"""
        try:
            api.require_permission_for_library_key(lib_key, user, perm)
            return True
        except PermissionDenied:
            return False
        except api.ContentLibraryNotFound as exc:
            # A 404 is probably what you want in this case, not a 500 error, so do that by default.
            raise NotFound(f"Content Library '{lib_key}' does not exist") from exc

    def block_exists(self, usage_key: LibraryUsageLocatorV2):
        """
        Does the block for this usage_key exist in this Library?

        Note that this applies to all versions, i.e. you can put a usage key for
        a piece of content that has been soft-deleted (removed from Drafts), and
        it will still return True here. That's because for the purposes of
        permission checking, we just want to know whether that block has ever
        existed in this Library, because we could be looking at any older
        version of it.
        """
        try:
            content_lib = ContentLibrary.objects.get_by_key(usage_key.context_key)  # type: ignore[attr-defined]
        except ContentLibrary.DoesNotExist:
            return False

        learning_package = content_lib.learning_package
        if learning_package is None:
            return False

        return content_api.component_exists_by_key(
            learning_package.id,
            namespace='xblock.v1',
            type_name=usage_key.block_type,
            local_key=usage_key.block_id,
        )

    def send_block_updated_event(self, usage_key: UsageKeyV2):
        """
        Send a "block updated" event for the library block with the given usage_key.
        """
        assert isinstance(usage_key, LibraryUsageLocatorV2)
        # .. event_implemented_name: LIBRARY_BLOCK_UPDATED
        # .. event_type: org.openedx.content_authoring.library_block.updated.v1
        LIBRARY_BLOCK_UPDATED.send_event(
            library_block=LibraryBlockData(
                library_key=usage_key.lib_key,
                usage_key=usage_key,
            )
        )

    def send_container_updated_events(self, usage_key: UsageKeyV2):
        """
        Send "container updated" events for containers that contains the library block
        with the given usage_key.
        """
        assert isinstance(usage_key, LibraryUsageLocatorV2)
        affected_containers = api.get_containers_contains_item(usage_key)
        for container in affected_containers:
            # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
            # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
            LIBRARY_CONTAINER_UPDATED.send_event(
                library_container=LibraryContainerData(
                    container_key=container.container_key,
                    background=True,
                )
            )
