"""
Services for learning content
"""
from __future__ import annotations

from content_libraries.api import has_permission_for_library_key, permissions
from opaque_keys.edx.locator import LibraryLocatorV2

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access


class StudioPermissionsService:
    """
    Service that can provide information about a user's permissions.
    """

    def __init__(self, user):
        self._user = user

    def can_read(self, context_key):
        """ Does the user have read access to the given course/library? """
        if isinstance(context_key, LibraryLocatorV2):
            return has_permission_for_library_key(
                context_key,
                self._user,
                permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
            )
        return has_studio_read_access(self._user, context_key)

    def can_write(self, context_key):
        """ Does the user have read access to the given course/library? """
        if isinstance(context_key, LibraryLocatorV2):
            return has_permission_for_library_key(
                context_key,
                self._user,
                permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
            )
        return has_studio_write_access(self._user, context_key)
