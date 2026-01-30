"""
Instructor API v2 views.

This module contains the v2 API endpoints for instructor functionality.
These APIs are designed to be consumed by MFEs and other API clients.
"""

import logging

from dataclasses import dataclass
from typing import Optional, Tuple
import edx_api_doc_tools as apidocs
from edx_when import api as edx_when_api
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import GenericAPIView
from rest_framework.exceptions import NotFound
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from common.djangoapps.util.json_request import JsonResponseBadRequest

from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.views.api import _display_unit, get_student_from_identifier
from lms.djangoapps.instructor.views.instructor_task_helpers import extract_task_features
from lms.djangoapps.instructor_task import api as task_api
from lms.djangoapps.instructor.ora import get_open_response_assessment_list, get_ora_summary
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id
from .serializers_v2 import (
    InstructorTaskListSerializer,
    CourseInformationSerializerV2,
    BlockDueDateSerializerV2,
    UnitExtensionSerializer,
    ORASerializer,
    ORASummarySerializer,
)
from .tools import (
    find_unit,
    get_units_with_due_date,
    set_due_date_extension,
    title_or_url,
)

log = logging.getLogger(__name__)


class CourseMetadataView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Retrieve comprehensive course metadata including enrollment counts, dashboard configuration,
        permissions, and navigation sections.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
        ],
        responses={
            200: CourseInformationSerializerV2,
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request, course_id):
        """
        Retrieve comprehensive course information including metadata, enrollment statistics,
        dashboard configuration, and user permissions.

        **Use Cases**

            Retrieve comprehensive course metadata including enrollment counts, dashboard configuration,
            permissions, and navigation sections.

        **Example Requests**

            GET /api/instructor/v2/courses/{course_id}

        **Response Values**

            {
                "course_id": "course-v1:edX+DemoX+Demo_Course",
                "display_name": "Demonstration Course",
                "org": "edX",
                "course_number": "DemoX",
                "enrollment_start": "2013-02-05T00:00:00Z",
                "enrollment_end": null,
                "start": "2013-02-05T05:00:00Z",
                "end": "2024-12-31T23:59:59Z",
                "pacing": "instructor",
                "has_started": true,
                "has_ended": false,
                "total_enrollment": 150,
                "enrollment_counts": {
                    "total": 150,
                    "audit": 100,
                    "verified": 40,
                    "honor": 10
                },
                "num_sections": 12,
                "grade_cutoffs": "A is 0.9, B is 0.8, C is 0.7, D is 0.6",
                "course_errors": [],
                "studio_url": "https://studio.example.com/course/course-v1:edX+DemoX+2024",
                "permissions": {
                    "admin": false,
                    "instructor": true,
                    "finance_admin": false,
                    "sales_admin": false,
                    "staff": true,
                    "forum_admin": true,
                    "data_researcher": false
                },
                "tabs": [
                    {
                      "tab_id": "courseware",
                      "title": "Course",
                      "url": "INSTRUCTOR_MICROFRONTEND_URL/courses/course-v1:edX+DemoX+2024/courseware"
                    },
                    {
                      "tab_id": "progress",
                      "title": "Progress",
                      "url": "INSTRUCTOR_MICROFRONTEND_URL/courses/course-v1:edX+DemoX+2024/progress"
                    },
                ],
                "disable_buttons": false,
                "analytics_dashboard_message": "To gain insights into student enrollment and participation..."
            }

        **Parameters**

            course_key: Course key for the course.

        **Returns**

            * 200: OK - Returns course metadata
            * 401: Unauthorized - User is not authenticated
            * 403: Forbidden - User lacks instructor permissions
            * 404: Not Found - Course does not exist
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        tabs = get_course_tab_list(request.user, course)
        context = {
            'tabs': tabs,
            'course': course,
            'user': request.user,
            'request': request
        }
        serializer = CourseInformationSerializerV2(context)

        return Response(serializer.data, status=status.HTTP_200_OK)


class InstructorTaskListView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        List instructor tasks for a course.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_key}/instructor_tasks
        GET /api/instructor/v2/courses/{course_key}/instructor_tasks?problem_location_str=block-v1:...
        GET /api/instructor/v2/courses/{course_key}/instructor_tasks?
        problem_location_str=block-v1:...&unique_student_identifier=student@example.com

    **Response Values**

        {
            "tasks": [
                {
                    "task_id": "2519ff31-22d9-4a62-91e2-55495895b355",
                    "task_type": "grade_problems",
                    "task_state": "PROGRESS",
                    "status": "Incomplete",
                    "created": "2019-01-15T18:00:15.902470+00:00",
                    "task_input": "{}",
                    "task_output": null,
                    "duration_sec": "unknown",
                    "task_message": "No status information available",
                    "requester": "staff"
                }
            ]
        }

    **Parameters**

        course_key: Course key for the course.
        problem_location_str (optional): Filter tasks to a specific problem location.
        unique_student_identifier (optional): Filter tasks to specific student (must be used with problem_location_str).

    **Returns**

        * 200: OK - Returns list of instructor tasks
        * 400: Bad Request - Invalid parameters
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.SHOW_TASKS

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
            apidocs.string_parameter(
                'problem_location_str',
                apidocs.ParameterLocation.QUERY,
                description="Optional: Filter tasks to a specific problem location.",
            ),
            apidocs.string_parameter(
                'unique_student_identifier',
                apidocs.ParameterLocation.QUERY,
                description="Optional: Filter tasks to a specific student (requires problem_location_str).",
            ),
        ],
        responses={
            200: InstructorTaskListSerializer,
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request, course_id):
        """
        List instructor tasks for a course.
        """

        course_key = CourseKey.from_string(course_id)

        # Get query parameters
        problem_location_str = request.query_params.get('problem_location_str', None)
        unique_student_identifier = request.query_params.get('unique_student_identifier', None)

        student = None
        if unique_student_identifier:
            try:
                student = get_student_from_identifier(unique_student_identifier)
            except Exception:  # pylint: disable=broad-except
                return Response(
                    {'error': 'Invalid student identifier'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate parameters
        if student and not problem_location_str:
            return Response(
                {'error': 'unique_student_identifier must be used with problem_location_str'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get tasks based on filters
        if problem_location_str:
            try:
                module_state_key = UsageKey.from_string(problem_location_str).map_into_course(course_key)
            except InvalidKeyError:
                return Response(
                    {'error': 'Invalid problem location'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if student:
                # Tasks for specific problem and student
                tasks = task_api.get_instructor_task_history(course_key, module_state_key, student)
            else:
                # Tasks for specific problem
                tasks = task_api.get_instructor_task_history(course_key, module_state_key)
        else:
            # All running tasks
            tasks = task_api.get_running_instructor_tasks(course_key)

        # Extract task features and serialize
        tasks_data = [extract_task_features(task) for task in tasks]
        serializer = InstructorTaskListSerializer({'tasks': tasks_data})
        return Response(serializer.data, status=status.HTTP_200_OK)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ChangeDueDateView(APIView):
    """
    Grants a due date extension to a student for a particular unit.
    this version works with a new payload that is JSON and more up to date.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GIVE_STUDENT_EXTENSION
    serializer_class = BlockDueDateSerializerV2

    def post(self, request, course_id):
        """
        Grants a due date extension to a learner for a particular unit.

        params:
            blockId (str): The URL related to the block that needs the due date update.
            due_datetime (str): The new due date and time for the block.
            email_or_username (str): The email or username of the learner whose access is being modified.
        """
        serializer_data = self.serializer_class(data=request.data)
        if not serializer_data.is_valid():
            return JsonResponseBadRequest({'error': serializer_data.errors})

        learner = serializer_data.validated_data.get('email_or_username')
        due_date = serializer_data.validated_data.get('due_datetime')
        course = get_course_by_id(CourseKey.from_string(course_id))
        unit = find_unit(course, serializer_data.validated_data.get('block_id'))
        reason = strip_tags(serializer_data.validated_data.get('reason', ''))
        try:
            set_due_date_extension(course, unit, learner, due_date, request.user, reason=reason)
        except Exception as error:  # pylint: disable=broad-except
            return JsonResponseBadRequest({'error': str(error)})

        return Response(
            {
                'message': _(
                    'Successfully changed due date for learner {0} for {1} '
                    'to {2}').
                format(learner.profile.name, _display_unit(unit), due_date.strftime('%Y-%m-%d %H:%M')
                       )})


class GradedSubsectionsView(APIView):
    """View to retrieve graded subsections with due dates"""
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD

    def get(self, request, course_id):
        """
        Retrieves a list of graded subsections (units with due dates) within a specified course.
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)
        graded_subsections = get_units_with_due_date(course)
        formated_subsections = {"items": [
            {
                "display_name": title_or_url(unit),
                "subsection_id": str(unit.location)
            } for unit in graded_subsections]}

        return Response(formated_subsections, status=status.HTTP_200_OK)


@dataclass(frozen=True)
class UnitDueDateExtension:
    """Dataclass representing a unit due date extension for a student."""

    username: str
    full_name: str
    email: str
    unit_title: str
    unit_location: str
    extended_due_date: Optional[str]

    @classmethod
    def from_block_tuple(cls, row: Tuple, unit):
        username, full_name, due_date, email, location = row
        unit_title = title_or_url(unit)
        return cls(
            username=username,
            full_name=full_name,
            email=email,
            unit_title=unit_title,
            unit_location=location,
            extended_due_date=due_date,
        )

    @classmethod
    def from_course_tuple(cls, row: Tuple, units_dict: dict):
        username, full_name, email, location, due_date = row
        unit_title = title_or_url(units_dict[str(location)])
        return cls(
            username=username,
            full_name=full_name,
            email=email,
            unit_title=unit_title,
            unit_location=location,
            extended_due_date=due_date,
        )


class UnitExtensionsView(ListAPIView):
    """
    Retrieve a paginated list of due date extensions for units in a course.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_id}/unit_extensions
        GET /api/instructor/v2/courses/{course_id}/unit_extensions?page=2
        GET /api/instructor/v2/courses/{course_id}/unit_extensions?page_size=50
        GET /api/instructor/v2/courses/{course_id}/unit_extensions?email_or_username=john
        GET /api/instructor/v2/courses/{course_id}/unit_extensions?block_id=block-v1:org@problem+block@unit1

    **Response Values**

        {
            "count": 150,
            "next": "http://example.com/api/instructor/v2/courses/course-v1:org+course+run/unit_extensions?page=2",
            "previous": null,
            "results": [
                {
                    "username": "student1",
                    "full_name": "John Doe",
                    "email": "john.doe@example.com",
                    "unit_title": "Unit 1: Introduction",
                    "unit_location": "block-v1:org+course+run+type@problem+block@unit1",
                    "extended_due_date": "2023-12-25T23:59:59Z"
                },
                ...
            ]
        }

    **Parameters**

        course_id: Course key for the course.
        page (optional): Page number for pagination.
        page_size (optional): Number of results per page.

    **Returns**

        * 200: OK - Returns paginated list of unit extensions
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_DASHBOARD
    serializer_class = UnitExtensionSerializer
    filter_backends = []

    def _matches_email_or_username(self, unit_extension, filter_value):
        """
        Check if the unit extension matches the email or username filter.
        """
        return (
            filter_value in unit_extension.username.lower()
            or filter_value in unit_extension.email.lower()
        )

    def get_queryset(self):
        """
        Returns the queryset of unit extensions for the specified course.

        This method uses the core logic from get_overrides_for_course to retrieve
        due date extension data and transforms it into a list of normalized objects
        that can be paginated and serialized.

        Supports filtering by:
        - email_or_username: Filter by username or email address
        - block_id: Filter by specific unit/subsection location
        """
        course_id = self.kwargs["course_id"]
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        email_or_username_filter = self.request.query_params.get("email_or_username")
        block_id_filter = self.request.query_params.get("block_id")

        units = get_units_with_due_date(course)
        units_dict = {str(u.location): u for u in units}

        # Fetch and normalize overrides
        if block_id_filter:
            try:
                unit = find_unit(course, block_id_filter)
                query_data = edx_when_api.get_overrides_for_block(course.id, unit.location)
                unit_due_date_extensions = [
                    UnitDueDateExtension.from_block_tuple(row, unit)
                    for row in query_data
                ]
            except InvalidKeyError:
                # If block_id is invalid, return empty list
                unit_due_date_extensions = []
        else:
            query_data = edx_when_api.get_overrides_for_course(course.id)
            unit_due_date_extensions = [
                UnitDueDateExtension.from_course_tuple(row, units_dict)
                for row in query_data
                if str(row[3]) in units_dict  # Ensure unit has due date
            ]

        # Apply filters if any
        filter_value = email_or_username_filter.lower() if email_or_username_filter else None

        results = [
            extension
            for extension in unit_due_date_extensions
            if self._matches_email_or_username(extension, filter_value)
        ] if filter_value else unit_due_date_extensions  # if no filter, use all

        # Sort for consistent ordering
        results.sort(
            key=lambda o: (
                o.username,
                o.unit_title,
            )
        )

        return results


class ORAView(GenericAPIView):
    """
    View to list all Open Response Assessments (ORAs) for a given course.

    * Requires token authentication.
    * Only instructors or staff for the course are able to access this view.
    """
    permission_classes = [IsAuthenticated, permissions.InstructorPermission]
    permission_name = permissions.VIEW_DASHBOARD
    serializer_class = ORASerializer

    def get_course(self):
        """
        Retrieve the course object based on the course_id URL parameter.

        Validates that the course exists and is not deprecated.
        Raises NotFound if the course does not exist.
        """
        course_id = self.kwargs.get("course_id")
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as exc:
            log.error("Unable to find course with course key %s while loading the Instructor Dashboard.", course_id)
            raise NotFound("Course not found") from exc
        if course_key.deprecated:
            raise NotFound("Course not found")
        course = get_course_by_id(course_key, depth=None)
        return course

    def get(self, request, *args, **kwargs):
        """
        Return a list of all ORAs for the specified course.
        """
        course = self.get_course()

        items = get_open_response_assessment_list(course)

        page = self.paginate_queryset(items)
        if page is None:
            # Pagination is required for this endpoint
            return Response(
                {"detail": "Pagination is required for this endpoint."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class ORASummaryView(GenericAPIView):
    """
    View to get a summary of Open Response Assessments (ORAs) for a given course.

    * Requires token authentication.
    * Only instructors or staff for the course are able to access this view.
    """
    permission_classes = [IsAuthenticated, permissions.InstructorPermission]
    permission_name = permissions.VIEW_DASHBOARD
    serializer_class = ORASummarySerializer

    def get_course(self):
        """
        Retrieve the course object based on the course_id URL parameter.

        Validates that the course exists and is not deprecated.
        Raises NotFound if the course does not exist.
        """
        course_id = self.kwargs.get("course_id")
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as exc:
            log.error("Unable to find course with course key %s while loading the Instructor Dashboard.", course_id)
            raise NotFound("Course not found") from exc
        if course_key.deprecated:
            raise NotFound("Course not found")
        course = get_course_by_id(course_key, depth=None)
        return course

    def get(self, request, *args, **kwargs):
        """
        Return a summary of ORAs for the specified course.
        """
        course = self.get_course()

        items = get_ora_summary(course)

        serializer = self.get_serializer(items)
        return Response(serializer.data)
