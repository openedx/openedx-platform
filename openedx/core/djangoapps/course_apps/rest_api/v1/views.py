# pylint: disable=missing-module-docstring
import logging
from typing import Dict

from django.contrib.auth import get_user_model
from edx_api_doc_tools import path_parameter, schema
from edx_django_utils.plugins import PluginError
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response

from common.djangoapps.student.auth import has_studio_write_access
from openedx.core.djangoapps.course_apps.models import CourseAppStatus
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, validate_course_key, verify_course_exists

from ...api import is_course_app_enabled, set_course_app_enabled
from ...plugins import CourseApp, CourseAppsPluginManager

User = get_user_model()
log = logging.getLogger(__name__)


class HasStudioWriteAccess(BasePermission):
    """
    Check if the user has write access to studio.
    """

    def has_permission(self, request, view):
        """
        Check if the user has write access to studio.
        """
        user = request.user
        course_key_string = view.kwargs.get("course_id")
        course_key = validate_course_key(course_key_string)
        return has_studio_write_access(user, course_key)


class CourseAppSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for course app data.
    """

    id = serializers.CharField(read_only=True, help_text="Unique ID for course app.")
    enabled = serializers.BooleanField(
        required=True,
        help_text="Whether the course app is enabled for the specified course.",
    )
    name = serializers.CharField(read_only=True, help_text="Friendly name of the course app.")
    description = serializers.CharField(read_only=True, help_text="A friendly description of what the course app does.")
    legacy_link = serializers.URLField(required=False, help_text="A link to the course app in the legacy studio view.")
    documentation_links = serializers.JSONField(required=True)
    allowed_operations = serializers.DictField(
        read_only=True,
        help_text="What all operations are supported by the app.",
    )

    def to_representation(self, instance: CourseApp) -> Dict:
        course_key = self.context.get("course_key")
        request = self.context.get("request")
        app_status = self.context.get("app_status")
        data = {
            "id": instance.app_id,
            "enabled": app_status.get(instance.app_id, is_course_app_enabled(course_key, instance.app_id)),
            "name": instance.name,
            "description": instance.description,
            "allowed_operations": instance.get_allowed_operations(course_key, request.user),
            "documentation_links": instance.documentation_links,
        }
        if hasattr(instance, "legacy_link"):
            course_legacy_link = instance.legacy_link(course_key)
            data["legacy_link"] = request.build_absolute_uri(course_legacy_link) if course_legacy_link else ''
        return data


class CourseAppsView(DeveloperErrorViewMixin, views.APIView):
    """
    A view for getting a list of all apps available for a course.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (HasStudioWriteAccess,)

    @schema(
        parameters=[
            path_parameter("course_id", str, description="Course Key"),
        ],
        responses={
            200: CourseAppSerializer,
            401: "The requester is not authenticated.",
            403: "The requester does not have staff access access to the specified course",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists("Requested apps for unknown course {course}")
    def get(self, request: Request, course_id: str):
        """
        Get a list of all the course apps available for a course.

        **Example Response**

            GET /api/course_apps/v1/apps/{course_id}

        ```json
        [
            {
                "allowed_operations": {
                    "configure": false,
                    "enable": true
                },
                "description": "Provide an in-browser calculator that supports simple and complex calculations.",
                "enabled": false,
                "id": "calculator",
                "name": "Calculator"
            },
            {
                "allowed_operations": {
                    "configure": true,
                    "enable": true
                },
                "description": "Encourage participation and engagement in your course with discussion forums.",
                "enabled": false,
                "id": "discussion",
                "name": "Discussion"
            },
            ...
        ]
        ```
        """
        course_key = CourseKey.from_string(course_id)
        course_apps = CourseAppsPluginManager.get_apps_available_for_course(course_key)
        course_apps_status = CourseAppStatus.get_all_app_status_data_for_course(course_key)
        serializer = CourseAppSerializer(
            course_apps,
            many=True,
            context={
                "course_key": course_key,
                "app_status": course_apps_status,
                "request": request,
            }
        )
        return Response(serializer.data)

    @schema(
        parameters=[
            path_parameter("course_id", str, description="Course Key"),
        ],
        responses={
            200: CourseAppSerializer,
            401: "The requester is not authenticated.",
            403: "The requester does not have staff access access to the specified course",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists("Requested apps for unknown course {course}")
    def patch(self, request: Request, course_id: str):
        """
        Enable/disable a course app.

        **Example Response**

            PATCH /api/course_apps/v1/apps/{course_id} {
                "id": "wiki",
                "enabled": true
            }

        ```json
        {
            "allowed_operations": {
                "configure": false,
                "enable": false
            },
            "description": "Enable learners to access, and collaborate on information about your course.",
            "enabled": true,
            "id": "wiki",
            "name": "Wiki"
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        app_id = request.data.get("id")
        enabled = request.data.get("enabled")
        if app_id is None:
            raise ValidationError({"id": "App id is missing"})
        if enabled is None:
            raise ValidationError({"enabled": "Must provide value for `enabled` field."})
        try:
            course_app = CourseAppsPluginManager.get_plugin(app_id)
        except PluginError:
            course_app = None
        if not course_app or not course_app.is_available(course_key):
            raise ValidationError({"id": "Invalid app ID"})
        is_enabled = set_course_app_enabled(course_key=course_key, app_id=app_id, enabled=enabled, user=request.user)
        serializer = CourseAppSerializer(
            course_app,
            context={
                "course_key": course_key,
                "request": request,
                "app_status": {app_id: is_enabled},
            },
        )
        return Response(serializer.data)
