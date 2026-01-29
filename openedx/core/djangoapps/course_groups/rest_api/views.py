"""
REST API views for content group configurations.
"""
import edx_api_doc_tools as apidocs
from common.djangoapps.util.db import MYSQL_MAX_INT, generate_int_id
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID, UserPartition

from lms.djangoapps.instructor import permissions
from openedx.core.djangoapps.course_groups.constants import (
    COHORT_SCHEME,
    CONTENT_GROUP_CONFIGURATION_DESCRIPTION,
    CONTENT_GROUP_CONFIGURATION_NAME,
)
from openedx.core.djangoapps.course_groups.partition_scheme import get_cohorted_user_partition
from openedx.core.djangoapps.course_groups.rest_api.serializers import (
    ContentGroupConfigurationSerializer,
    ContentGroupsListResponseSerializer,
)
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id


class GroupConfigurationsListView(DeveloperErrorViewMixin, APIView):
    """
    API view for listing content group configurations.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id",
                apidocs.ParameterLocation.PATH,
                description="The course key (e.g., course-v1:org+course+run)",
            ),
        ],
        responses={
            200: "Successfully retrieved content groups",
            400: "Invalid course key",
            401: "Authentication required",
            403: "User does not have permission to access this course",
            404: "Course not found",
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

        if content_group_partition is None:
            used_ids = {p.id for p in course.user_partitions}
            content_group_partition = UserPartition(
                id=generate_int_id(MINIMUM_UNUSED_PARTITION_ID, MYSQL_MAX_INT, used_ids),
                name=str(CONTENT_GROUP_CONFIGURATION_NAME),
                description=str(CONTENT_GROUP_CONFIGURATION_DESCRIPTION),
                groups=[],
                scheme_id=COHORT_SCHEME
            )

        context = {
            "all_group_configurations": [content_group_partition.to_json()],
            "should_show_enrollment_track": False,
            "should_show_experiment_groups": True,
            "context_course": None,
            "group_configuration_url": f"/api/cohorts/v2/courses/{course_id}/group_configurations",
            "course_outline_url": f"/api/contentstore/v1/courses/{course_id}",
        }

        serializer = ContentGroupsListResponseSerializer(context)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupConfigurationDetailView(DeveloperErrorViewMixin, APIView):
    """
    API view for retrieving a specific content group configuration.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id",
                apidocs.ParameterLocation.PATH,
                description="The course key",
            ),
            apidocs.path_parameter(
                "configuration_id",
                int,
                description="The ID of the content group configuration",
            ),
        ],
        responses={
            200: "Content group configuration details",
            400: "Invalid course key",
            401: "Authentication required",
            403: "User does not have permission to access this course",
            404: "Content group configuration not found",
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
