"""
REST API views for content group configurations.
"""
from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.instructor import permissions
from openedx.core.djangoapps.course_groups.constants import COHORT_SCHEME
from openedx.core.djangoapps.course_groups.partition_scheme import get_cohorted_user_partition
from openedx.core.djangoapps.course_groups.rest_api.serializers import (
    ContentGroupConfigurationSerializer,
    ContentGroupsListResponseSerializer,
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.exceptions import ItemNotFoundError


class GroupConfigurationsListView(DeveloperErrorViewMixin, APIView):
    """
    API view for listing content group configurations.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "course_id",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                description="The course key (e.g., course-v1:org+course+run)",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Successfully retrieved content groups"),
            400: OpenApiResponse(description="Invalid course key"),
            401: OpenApiResponse(description="Authentication required"),
            403: OpenApiResponse(description="User does not have permission to access this course"),
            404: OpenApiResponse(description="Course not found"),
        },
    )
    def get(self, request, course_id):
        """
        List all content groups for a course.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {"error": f"Invalid course key: {course_id}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            course = get_course_by_id(course_key)
        except ItemNotFoundError:
            return Response(
                {"error": f"Course not found: {course_id}"},
                status=status.HTTP_404_NOT_FOUND
            )

        content_group_partition = get_cohorted_user_partition(course)

        # Extract partition ID and groups, or None/empty list if no partition exists
        if content_group_partition is not None:
            partition_id = content_group_partition.id
            groups = [group.to_json() for group in content_group_partition.groups]
        else:
            partition_id = None
            groups = []

        # Build full Studio URL for content group configuration
        mfe_config = configuration_helpers.get_value("MFE_CONFIG", settings.MFE_CONFIG)
        studio_base_url = mfe_config.get("STUDIO_BASE_URL", "")
        studio_content_groups_link = f"{studio_base_url}/course/{course_id}/group_configurations"

        response_data = {
            "id": partition_id,
            "groups": groups,
            "studio_content_groups_link": studio_content_groups_link,
        }

        serializer = ContentGroupsListResponseSerializer(response_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupConfigurationDetailView(DeveloperErrorViewMixin, APIView):
    """
    API view for retrieving a specific content group configuration.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "course_id",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                description="The course key",
            ),
            OpenApiParameter(
                "configuration_id",
                OpenApiTypes.INT,
                OpenApiParameter.PATH,
                description="The ID of the content group configuration",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Content group configuration details"),
            400: OpenApiResponse(description="Invalid course key"),
            401: OpenApiResponse(description="Authentication required"),
            403: OpenApiResponse(description="User does not have permission to access this course"),
            404: OpenApiResponse(description="Content group configuration not found"),
        },
    )
    def get(self, request, course_id, configuration_id):
        """
        Retrieve a specific content group configuration.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {"error": f"Invalid course key: {course_id}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            course = get_course_by_id(course_key)
        except ItemNotFoundError:
            return Response(
                {"error": f"Course not found: {course_id}"},
                status=status.HTTP_404_NOT_FOUND
            )

        partition = None
        for p in course.user_partitions:
            if p.id == int(configuration_id) and p.scheme.name == COHORT_SCHEME:
                partition = p
                break

        if not partition:
            return Response(
                {"error": f"Content group configuration {configuration_id} not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        response_data = partition.to_json()
        serializer = ContentGroupConfigurationSerializer(response_data)
        return Response(serializer.data, status=status.HTTP_200_OK)
