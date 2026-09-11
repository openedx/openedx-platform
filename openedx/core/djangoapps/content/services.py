"""
Services for learning content
"""
from __future__ import annotations

from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import EDIT_LIBRARY_CONTENT, VIEW_LIBRARY

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
            return self._user.is_active and authz_api.is_user_allowed(
                self._user,
                VIEW_LIBRARY.identifier,
                str(context_key),
            )
        return has_studio_read_access(self._user, context_key)

    def can_write(self, context_key):
        """ Does the user have write access to the given course/library? """
        if isinstance(context_key, LibraryLocatorV2):
            return self._user.is_active and authz_api.is_user_allowed(
                self._user,
                EDIT_LIBRARY_CONTENT.identifier,
                str(context_key),
            )
        return has_studio_write_access(self._user, context_key)
