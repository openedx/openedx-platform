"""
Services for learning content
"""
from __future__ import annotations

import warnings
from typing import Literal

from opaque_keys.edx.keys import LearningContextKey
from opaque_keys.edx.locator import CourseLocator, LibraryLocatorV2
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import (
    COURSES_CREATE_FILES,
    COURSES_EDIT_COURSE_CONTENT,
    COURSES_EDIT_FILES,
    COURSES_MANAGE_COURSE_UPDATES,
    COURSES_VIEW_COURSE,
    COURSES_VIEW_FILES,
    EDIT_LIBRARY_CONTENT,
    REUSE_LIBRARY_CONTENT,
    VIEW_LIBRARY,
)
from openedx_authz.data import PermissionData

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access

COMMON_PERM_KEYS = Literal[
    "read_content",
    "edit_content",
    "read_files",
    "edit_files",
    "create_files",
    "reuse_content",
    "manage_updates",
]
CONTEXT_TYPES = Literal["course", "library"]


# Maps conceptual permissions to how they're applied to a particular learning context.
# Note, for instance, that courses need a special file permission that libraries don't need,
# since library files are constrained to the blocks whereas course files are course-wide.
#
# This can be expanded with more permissions from the AuthZ constants if they're needed later
# on following the patterns in this file.
PERMISSION_MAP: dict[COMMON_PERM_KEYS, dict[CONTEXT_TYPES, PermissionData]] = {
    "read_content": {
        "course": COURSES_VIEW_COURSE,
        "library": VIEW_LIBRARY,
    },
    "edit_content": {
        "course": COURSES_EDIT_COURSE_CONTENT,
        "library": EDIT_LIBRARY_CONTENT,
    },
    "read_files": {
        "course": COURSES_VIEW_FILES,
        "library": VIEW_LIBRARY,
    },
    "create_files": {
        "course": COURSES_CREATE_FILES,
        "library": EDIT_LIBRARY_CONTENT,
    },
    "edit_files": {
        "course": COURSES_EDIT_FILES,
        "library": EDIT_LIBRARY_CONTENT,
    },
    "reuse_content": {
        "library": REUSE_LIBRARY_CONTENT,
    },
    "manage_updates": {
        "course": COURSES_MANAGE_COURSE_UPDATES,
    }
}


def perm_for_key(perm_label: COMMON_PERM_KEYS, context_key: LearningContextKey) -> PermissionData:
    """Returns the appropriate permission data for a context key."""
    if perm_label not in PERMISSION_MAP:
        raise ValueError(f"Unsupported permission category, {perm_label}.")
    match context_key:
        case LibraryLocatorV2():
            try:
                return PERMISSION_MAP[perm_label]["library"]
            except KeyError as err:
                raise TypeError(f"Permission category not applicable to libraries: {perm_label}") from err
        case CourseLocator():
            try:
                return PERMISSION_MAP[perm_label]["course"]
            except KeyError as err:
                raise TypeError(f"Permission category not applicable to courses: {perm_label}") from err
    raise TypeError(f"{context_key} is not a recognized LearningContextKey.")


class StudioPermissionsService:
    """
    Service that can provide information about a user's permissions.
    """

    def __init__(self, user) -> None:
        self._user = user

    def _check_permission(self, perm_label: COMMON_PERM_KEYS, context_key: LearningContextKey) -> bool:
        """ Verify that a user has a specific permission for this context. """
        return self._user.is_active and authz_api.is_user_allowed(
            self._user,
            perm_for_key(perm_label, context_key).identifier,
            str(context_key),
        )

    def can_read_content(self, context_key: LearningContextKey) -> bool:
        """ Can the user read the content of this course/library? """
        return self._check_permission("read_content", context_key)

    def can_edit_content(self, context_key: LearningContextKey) -> bool:
        """ Can the user edit the content of this course/library? """
        return self._check_permission("edit_content", context_key)

    def can_read_files(self, context_key: LearningContextKey) -> bool:
        """ Can the user read files for this course/library? """
        return self._check_permission("read_files", context_key)

    def can_create_files(self, context_key: LearningContextKey) -> bool:
        """ Can the user create files for this course/library? """
        return self._check_permission("create_files", context_key)

    def can_edit_files(self, context_key: LearningContextKey) -> bool:
        """ Can the user write files for this course/library? """
        return self._check_permission("edit_files", context_key)

    def can_read(self, context_key: LearningContextKey) -> bool:
        """ Does the user have read access to the given course/library? """
        warnings.warn(
            "can_read is deprecated. "
            "Use a more specific permission, like can_read_content or can_read_files instead. "
            "See https://github.com/openedx/openedx-platform/issues/37409.",
            DeprecationWarning,
            stacklevel=2,
        )
        return has_studio_read_access(self._user, context_key)

    def can_write(self, context_key: LearningContextKey) -> bool:
        """ Does the user have write access to the given course/library? """
        warnings.warn(
            "can_write is deprecated. "
            "Use a more specific permission, like can_write_content, can_create_files, "
            "or can_edit_files instead. "
            "See https://github.com/openedx/openedx-platform/issues/37409.",
            DeprecationWarning,
            stacklevel=2,
        )
        return has_studio_write_access(self._user, context_key)

    def can_reuse_content(self, context_key: LibraryLocatorV2) -> bool:
        """ Does the user have the ability to reuse content from this library? """
        return self._check_permission("reuse_content", context_key)

    def can_manage_updates(self, context_key: CourseLocator) -> bool:
        """ Does the user have the ability to manage updates for this course? """
        return self._check_permission("manage_updates", context_key)
