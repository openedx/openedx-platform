"""
Public permissions that are part of the content libraries API.

Deprecated. This module re-exports legacy content library permissions.
See https://github.com/openedx/openedx-platform/issues/37409.
"""
# pylint: disable=unused-import

from ..permissions import (
    CAN_CREATE_CONTENT_LIBRARY,
    CAN_DELETE_THIS_CONTENT_LIBRARY,
    CAN_EDIT_THIS_CONTENT_LIBRARY,
    CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM,
    CAN_LEARN_FROM_THIS_CONTENT_LIBRARY,
    CAN_VIEW_THIS_CONTENT_LIBRARY,
    CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM,
)

LEGACY_LIB_PERMISSIONS = frozenset({
    CAN_CREATE_CONTENT_LIBRARY,
    CAN_DELETE_THIS_CONTENT_LIBRARY,
    CAN_EDIT_THIS_CONTENT_LIBRARY,
    CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM,
    CAN_LEARN_FROM_THIS_CONTENT_LIBRARY,
    CAN_VIEW_THIS_CONTENT_LIBRARY,
    CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM,
})
