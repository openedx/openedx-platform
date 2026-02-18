"""
Instructor API v2 views.

This module contains the v2 API endpoints for instructor functionality.
These APIs are designed to be consumed by MFEs and other API clients.
"""

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


import edx_api_doc_tools as apidocs
from django.conf import settings
from django.db import transaction
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from edx_when import api as edx_when_api
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from pytz import UTC
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from common.djangoapps.util.json_request import JsonResponseBadRequest
from openedx.core.djangoapps.course_groups.cohorts import is_course_cohorted
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.views.api import _display_unit, get_student_from_identifier
from lms.djangoapps.instructor.views.instructor_task_helpers import extract_task_features
from lms.djangoapps.instructor_task import api as task_api
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError, QueueConnectionError
from lms.djangoapps.instructor.constants import ReportType
from lms.djangoapps.instructor.ora import get_open_response_assessment_list, get_ora_summary
from lms.djangoapps.instructor_analytics import basic as instructor_analytics_basic
from lms.djangoapps.instructor_analytics import csvs as instructor_analytics_csvs
from lms.djangoapps.instructor_task.models import ReportStore
from lms.djangoapps.instructor_task.tasks_helper.utils import upload_csv_file_to_report_store
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
    keep_field_private,
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


class ReportDownloadsView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        List all available report downloads for a course.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_key}/reports

    **Response Values**

        {
            "downloads": [
                {
                    "report_name": "course-v1_edX_DemoX_Demo_Course_grade_report_2024-01-26-1030.csv",
                    "report_url":
                        "/grades/course-v1:edX+DemoX+Demo_Course/"
                        "course-v1_edX_DemoX_Demo_Course_grade_report_2024-01-26-1030.csv",
                    "date_generated": "2024-01-26T10:30:00Z",
                    "report_type": "grade"  # Uses ReportType.GRADE.value
                }
            ]
        }

    **Parameters**

        course_key: Course key for the course.

    **Returns**

        * 200: OK - Returns list of available reports
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks staff access to the course
        * 404: Not Found - Course does not exist
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    # Use ENROLLMENT_REPORT permission which allows course staff and data researchers
    # to view generated reports, aligning with the intended audience of instructors/course staff
    permission_name = permissions.ENROLLMENT_REPORT

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
        ],
        responses={
            200: "Returns list of available report downloads.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request, course_id):
        """
        List all available report downloads for a course.
        """
        course_key = CourseKey.from_string(course_id)
        # Validate that the course exists
        get_course_by_id(course_key)

        report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')

        downloads = []
        for name, url in report_store.links_for(course_key):
            # Determine report type from filename using helper method
            report_type = self._detect_report_type_from_filename(name)

            # Extract date from filename if possible (format: YYYY-MM-DD-HHMM)
            date_generated = self._extract_date_from_filename(name)

            downloads.append({
                'report_name': name,
                'report_url': url,
                'date_generated': date_generated,
                'report_type': report_type,
            })

        return Response({'downloads': downloads}, status=status.HTTP_200_OK)

    def _detect_report_type_from_filename(self, filename):
        """
        Detect report type from filename using pattern matching.
        Check more specific patterns first to avoid false matches.

        Args:
            filename: The name of the report file

        Returns:
            str: The report type identifier
        """
        name_lower = filename.lower()

        # Check more specific patterns first to avoid false matches
        # Match exact report names from the filename format: {course_prefix}_{csv_name}_{timestamp}.csv
        if 'inactive_enrolled' in name_lower:
            return ReportType.PENDING_ACTIVATIONS.value
        elif 'problem_grade_report' in name_lower:
            return ReportType.PROBLEM_GRADE.value
        elif 'ora2_submission' in name_lower or 'submission_files' in name_lower or 'ora_submission' in name_lower:
            return ReportType.ORA2_SUBMISSION_FILES.value
        elif 'ora2_summary' in name_lower or 'ora_summary' in name_lower:
            return ReportType.ORA2_SUMMARY.value
        elif 'ora2_data' in name_lower or 'ora_data' in name_lower:
            return ReportType.ORA2_DATA.value
        elif 'may_enroll' in name_lower:
            return ReportType.PENDING_ENROLLMENTS.value
        elif 'student_state' in name_lower or 'problem_responses' in name_lower:
            return ReportType.PROBLEM_RESPONSES.value
        elif 'anonymized_ids' in name_lower or 'anon' in name_lower:
            return ReportType.ANONYMIZED_STUDENT_IDS.value
        elif 'issued_certificates' in name_lower or 'certificate' in name_lower:
            return ReportType.ISSUED_CERTIFICATES.value
        elif 'grade_report' in name_lower:
            return ReportType.GRADE.value
        elif 'enrolled_students' in name_lower or 'profile' in name_lower:
            return ReportType.ENROLLED_STUDENTS.value

        return ReportType.UNKNOWN.value

    def _extract_date_from_filename(self, filename):
        """
        Extract date from filename (format: YYYY-MM-DD-HHMM).

        Args:
            filename: The name of the report file

        Returns:
            str: ISO formatted date string or None
        """
        date_match = re.search(r'_(\d{4}-\d{2}-\d{2}-\d{4})', filename)
        if date_match:
            date_str = date_match.group(1)
            try:
                # Parse the date string (YYYY-MM-DD-HHMM) directly
                dt = datetime.strptime(date_str, '%Y-%m-%d-%H%M')
                # Format as ISO 8601 with UTC timezone
                return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except ValueError:
                pass
        return None


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GenerateReportView(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Generate a specific type of report for a course.

    **Example Requests**

        POST /api/instructor/v2/courses/{course_key}/reports/enrolled_students/generate
        POST /api/instructor/v2/courses/{course_key}/reports/grade/generate
        POST /api/instructor/v2/courses/{course_key}/reports/problem_responses/generate

    **Response Values**

        {
            "status": "The report is being created. Please check the data downloads section for the status."
        }

    **Parameters**

        course_key: Course key for the course.
        report_type: Type of report to generate. Valid values:
            - enrolled_students: Enrolled Students Report
            - pending_enrollments: Pending Enrollments Report
            - pending_activations: Pending Activations Report (inactive users with enrollments)
            - anonymized_student_ids: Anonymized Student IDs Report
            - grade: Grade Report
            - problem_grade: Problem Grade Report
            - problem_responses: Problem Responses Report
            - ora2_summary: ORA Summary Report
            - ora2_data: ORA Data Report
            - ora2_submission_files: ORA Submission Files Report
            - issued_certificates: Issued Certificates Report

    **Returns**

        * 200: OK - Report generation task has been submitted
        * 400: Bad Request - Task is already running or invalid report type
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)

    @property
    def permission_name(self):
        """
        Return the appropriate permission name based on the requested report type.
        For the issued certificates report, mirror the v1 behavior by using
        VIEW_ISSUED_CERTIFICATES (course-level staff access). For all other reports,
        require CAN_RESEARCH.
        """
        report_type = self.kwargs.get('report_type')
        if report_type == ReportType.ISSUED_CERTIFICATES.value:
            return permissions.VIEW_ISSUED_CERTIFICATES
        return permissions.CAN_RESEARCH

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
            apidocs.string_parameter(
                'report_type',
                apidocs.ParameterLocation.PATH,
                description=(
                    "Type of report to generate. Valid values: "
                    "enrolled_students, pending_enrollments, pending_activations, "
                    "anonymized_student_ids, grade, problem_grade, problem_responses, "
                    "ora2_summary, ora2_data, ora2_submission_files, issued_certificates"
                ),
            ),
        ],
        responses={
            200: "Report generation task has been submitted successfully.",
            400: "The requested task is already running or invalid report type.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def post(self, request, course_id, report_type):
        """
        Generate a specific type of report for a course.
        """
        course_key = CourseKey.from_string(course_id)

        # Map report types to their submission functions
        report_handlers = {
            ReportType.ENROLLED_STUDENTS.value: self._generate_enrolled_students_report,
            ReportType.PENDING_ENROLLMENTS.value: self._generate_pending_enrollments_report,
            ReportType.PENDING_ACTIVATIONS.value: self._generate_pending_activations_report,
            ReportType.ANONYMIZED_STUDENT_IDS.value: self._generate_anonymized_ids_report,
            ReportType.GRADE.value: self._generate_grade_report,
            ReportType.PROBLEM_GRADE.value: self._generate_problem_grade_report,
            ReportType.PROBLEM_RESPONSES.value: self._generate_problem_responses_report,
            ReportType.ORA2_SUMMARY.value: self._generate_ora2_summary_report,
            ReportType.ORA2_DATA.value: self._generate_ora2_data_report,
            ReportType.ORA2_SUBMISSION_FILES.value: self._generate_ora2_submission_files_report,
            ReportType.ISSUED_CERTIFICATES.value: self._generate_issued_certificates_report,
        }

        handler = report_handlers.get(report_type)
        if not handler:
            return Response(
                {'error': f'Invalid report type: {report_type}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            success_message = handler(request, course_key)
        except AlreadyRunningError as error:
            log.warning("Task already running for %s report: %s", report_type, error)
            return Response(
                {'error': _('A report generation task is already running. Please wait for it to complete.')},
                status=status.HTTP_400_BAD_REQUEST
            )
        except QueueConnectionError as error:
            log.error("Queue connection error for %s report task: %s", report_type, error)
            return Response(
                {'error': _('Unable to connect to the task queue. Please try again later.')},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as error:
            log.error("Error submitting %s report task: %s", report_type, error)
            return Response(
                {'error': str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({'status': success_message}, status=status.HTTP_200_OK)

    def _generate_enrolled_students_report(self, request, course_key):
        """Generate enrolled students report."""
        course = get_course_by_id(course_key)
        available_features = instructor_analytics_basic.get_available_features(course_key)

        # Allow for sites to be able to define additional columns.
        # Note that adding additional columns has the potential to break
        # the student profile report due to a character limit on the
        # asynchronous job input which in this case is a JSON string
        # containing the list of columns to include in the report.
        # TODO: Refactor the student profile report code to remove the list of columns
        # that should be included in the report from the asynchronous job input.
        # We need to clone the list because we modify it below
        query_features = list(configuration_helpers.get_value('student_profile_download_fields', []))

        if not query_features:
            query_features = [
                'id', 'username', 'name', 'email', 'language', 'location',
                'year_of_birth', 'gender', 'level_of_education', 'mailing_address',
                'goals', 'enrollment_mode', 'last_login', 'date_joined', 'external_user_key',
                'enrollment_date',
            ]

        additional_attributes = configuration_helpers.get_value_for_org(
            course_key.org,
            "additional_student_profile_attributes"
        )
        if additional_attributes:
            # Fail fast: must be list/tuple of strings.
            if not isinstance(additional_attributes, (list, tuple)):
                raise ValueError(
                    _('Invalid additional student attribute configuration: expected list of strings, got {type}.')
                    .format(type=type(additional_attributes).__name__)
                )
            if not all(isinstance(v, str) for v in additional_attributes):
                raise ValueError(
                    _('Invalid additional student attribute configuration: all entries must be strings.')
                )
            # Reject empty string entries explicitly.
            if any(v == '' for v in additional_attributes):
                raise ValueError(
                    _('Invalid additional student attribute configuration: empty attribute names are not allowed.')
                )
            # Validate each attribute is in available_features; allow duplicates as provided.
            invalid = [v for v in additional_attributes if v not in available_features]
            if invalid:
                raise ValueError(
                    _('Invalid additional student attributes: {attrs}').format(
                        attrs=', '.join(invalid)
                    )
                )
            query_features.extend(additional_attributes)

        for field in settings.PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS:
            keep_field_private(query_features, field)

        if is_course_cohorted(course.id):
            query_features.append('cohort')

        if course.teams_enabled:
            query_features.append('team')

        # For compatibility reasons, city and country should always appear last.
        query_features.append('city')
        query_features.append('country')

        task_api.submit_calculate_students_features_csv(request, course_key, query_features)
        return _('The enrolled student report is being created.')

    def _generate_pending_enrollments_report(self, request, course_key):
        """Generate pending enrollments report."""
        query_features = ['email']
        task_api.submit_calculate_may_enroll_csv(request, course_key, query_features)
        return _('The pending enrollments report is being created.')

    def _generate_pending_activations_report(self, request, course_key):
        """Generate pending activations report."""
        query_features = ['email']
        task_api.submit_calculate_inactive_enrolled_students_csv(request, course_key, query_features)
        return _('The pending activations report is being created.')

    def _generate_anonymized_ids_report(self, request, course_key):
        """Generate anonymized student IDs report."""
        task_api.generate_anonymous_ids(request, course_key)
        return _('The anonymized student IDs report is being created.')

    def _generate_grade_report(self, request, course_key):
        """Generate grade report."""
        task_api.submit_calculate_grades_csv(request, course_key)
        return _('The grade report is being created.')

    def _generate_problem_grade_report(self, request, course_key):
        """Generate problem grade report."""
        task_api.submit_problem_grade_report(request, course_key)
        return _('The problem grade report is being created.')

    def _generate_problem_responses_report(self, request, course_key):
        """
        Generate problem responses report.

        Requires a problem_location (section or problem block id).
        Supports optional filtering by problem types.
        """
        problem_location = request.data.get('problem_location', '').strip()
        problem_types_filter = request.data.get('problem_types_filter')

        if not problem_location:
            raise ValueError(_('Specify Section or Problem block id is required.'))

        # Validate problem location
        try:
            usage_key = UsageKey.from_string(problem_location).map_into_course(course_key)
        except InvalidKeyError as exc:
            raise ValueError(_('Invalid problem location format.')) from exc

        # Check if the problem actually exists in the modulestore
        store = modulestore()
        try:
            store.get_item(usage_key)
        except ItemNotFoundError as exc:
            raise ValueError(_('The problem location does not exist in this course.')) from exc

        problem_locations_str = problem_location

        task_api.submit_calculate_problem_responses_csv(
            request, course_key, problem_locations_str, problem_types_filter
        )
        return _('The problem responses report is being created.')

    def _generate_ora2_summary_report(self, request, course_key):
        """Generate ORA2 summary report."""
        task_api.submit_export_ora2_summary(request, course_key)
        return _('The ORA2 summary report is being created.')

    def _generate_ora2_data_report(self, request, course_key):
        """Generate ORA2 data report."""
        task_api.submit_export_ora2_data(request, course_key)
        return _('The ORA2 data report is being created.')

    def _generate_ora2_submission_files_report(self, request, course_key):
        """Generate ORA2 submission files archive."""
        task_api.submit_export_ora2_submission_files(request, course_key)
        return _('The ORA2 submission files archive is being created.')

    def _generate_issued_certificates_report(self, request, course_key):
        """Generate issued certificates report."""
        # Query features for the report
        query_features = ['course_id', 'mode', 'total_issued_certificate', 'report_run_date']
        query_features_names = [
            ('course_id', _('CourseID')),
            ('mode', _('Certificate Type')),
            ('total_issued_certificate', _('Total Certificates Issued')),
            ('report_run_date', _('Date Report Run'))
        ]

        # Get certificates data
        certificates_data = instructor_analytics_basic.issued_certificates(course_key, query_features)

        # Format the data for CSV
        __, data_rows = instructor_analytics_csvs.format_dictlist(certificates_data, query_features)

        # Generate CSV content as a file-like object
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([col_header for __, col_header in query_features_names])

        # Write data rows
        for row in data_rows:
            writer.writerow(row)

        # Reset the buffer position to the beginning
        output.seek(0)

        # Store the report using the standard helper function with UTC timestamp
        timestamp = datetime.now(UTC)
        upload_csv_file_to_report_store(
            output,
            'issued_certificates',
            course_key,
            timestamp,
            config_name='GRADES_DOWNLOAD'
        )

        return _('The issued certificates report has been created.')


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
