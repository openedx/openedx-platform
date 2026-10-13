"""Permissions for the v3 contentstore API."""

from openedx_authz.constants.permissions import COURSES_EDIT_GRADING_SETTINGS, COURSES_VIEW_GRADING_SETTINGS
from rest_framework.permissions import BasePermission

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission


class _GradingSettingsPermission(BasePermission):
    """
    Allow callers holding ``authz_permission`` on the course in the path.

    The course comes from the view's ``get_course_key()``, which refuses an
    unparsable or unknown course with a 404 before permission is checked. On
    courses using policy-based authoring permissions that policy decides;
    elsewhere Studio read access to the course is required.
    """

    authz_permission = None

    def has_permission(self, request, view):
        return user_has_course_permission(
            request.user,
            self.authz_permission,
            view.get_course_key(),
            LegacyAuthoringPermission.READ,
        )


class CanViewGradingSettings(_GradingSettingsPermission):
    """Allow callers who may view the grading settings of the course in the path."""

    authz_permission = COURSES_VIEW_GRADING_SETTINGS.identifier


class CanEditGradingSettings(_GradingSettingsPermission):
    """
    Allow callers who may edit the grading settings of the course in the path.

    Where policy-based permissions are off, Studio read access is enough to
    edit, as for every other grading endpoint in Studio.
    """

    authz_permission = COURSES_EDIT_GRADING_SETTINGS.identifier
