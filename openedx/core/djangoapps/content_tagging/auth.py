"""
Functions to validate the access in content tagging actions
"""
import logging

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import COURSES_EXPORT_TAGS
from openedx_tagging import rules as oel_tagging_rules

from openedx.core import toggles as core_toggles

from .utils import get_context_key_from_key_string

log = logging.getLogger(__name__)


def has_view_object_tags_access(user, object_id):
    """
    Check if the user has access to view object tags for the given object.
    """
    # If authz is enabled, check for the export tags authz permission
    course_key = None
    # Try to parse the object_id as a CourseKey, if it fails,
    # it means object_id is not a course, so we don't validate against authz
    # and fallback to the legacy check.
    try:
        course_key = CourseKey.from_string(object_id)
    except InvalidKeyError:
        pass

    if course_key and core_toggles.enable_authz_course_authoring(course_key):
        return authz_api.is_user_allowed(
            user.username, COURSES_EXPORT_TAGS.identifier, str(course_key)
        )

    # Always check for tagging permissions
    return user.has_perm(
        "oel_tagging.view_objecttag",
        # The obj arg expects a model, but we are passing an object
        oel_tagging_rules.ObjectTagPermissionItem(taxonomy=None, object_id=object_id),  # type: ignore[arg-type]
    )


def should_use_course_authz_for_object(object_id) -> tuple[bool, CourseKey | None]:
    """
    Check if openedx-authz should be used for the given object based on the context key and toggle.

    This intentionally scopes to course objects only. For non-course objects
    (e.g. content libraries), this returns (False, None), and callers are
    expected to fall back to legacy django-rules permissions, which handle
    library authz checks separately (see can_change_object_tag_objectid
    in rules.py).

    Returns (should_use_authz, course_key) where:
    - should_use_authz: True if authz should be used, False otherwise
    - course_key: The CourseKey if object is a course and the toggle is active, None otherwise
    """
    # Extract context_key and ensure it is a CourseKey (not a LibraryLocatorV2, etc.)
    try:
        context_key = get_context_key_from_key_string(object_id)
        if not isinstance(context_key, CourseKey):
            return False, None
    except (ValueError, AttributeError):
        return False, None

    # Check if toggle is active
    if not core_toggles.enable_authz_course_authoring(context_key):
        return False, None

    # Authz should be used for this course object
    return True, context_key
