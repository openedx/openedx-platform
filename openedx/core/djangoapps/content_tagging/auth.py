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
