"""
Instructor API v2 views.

This module contains the v2 API endpoints for instructor functionality.
These APIs are designed to be consumed by MFEs and other API clients.
"""

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple  # noqa: UP035

import edx_api_doc_tools as apidocs
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django_filters.rest_framework import DjangoFilterBackend
from edx_rest_framework_extensions.paginators import DefaultPagination
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

from common.djangoapps.student.api import is_user_enrolled_in_course
from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE,
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    CourseEnrollment,
    ManualEnrollmentAudit,
)
from common.djangoapps.student.models.user import get_user_by_username_or_email
from common.djangoapps.student.roles import CourseBetaTesterRole
from common.djangoapps.util.json_request import JsonResponseBadRequest
from lms.djangoapps.certificates import api as certs_api
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import (
    CertificateAllowlist,
    CertificateGenerationHistory,
    CertificateInvalidation,
    GeneratedCertificate,
)
from lms.djangoapps.course_home_api.toggles import course_home_mfe_progress_tab_is_active
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.access import (
    FORUM_ROLES,
    ROLE_DISPLAY_NAMES,
    ROLES,
    allow_access,
    is_forum_role,
    list_forum_members,
    list_with_level,
    revoke_access,
    update_forum_role,
)
from lms.djangoapps.instructor.constants import ReportType
from lms.djangoapps.instructor.enrollment import (
    enroll_email,
    get_email_params,
    get_user_email_language,
    send_beta_role_email,
    unenroll_email,
)
from lms.djangoapps.instructor.ora import get_open_response_assessment_list, get_ora_summary
from lms.djangoapps.instructor.views.api import _display_unit, get_student_from_identifier
from lms.djangoapps.instructor.views.instructor_task_helpers import extract_task_features
from lms.djangoapps.instructor_analytics import basic as instructor_analytics_basic
from lms.djangoapps.instructor_analytics import csvs as instructor_analytics_csvs
from lms.djangoapps.instructor_task import api as task_api
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError, QueueConnectionError
from lms.djangoapps.instructor_task.models import InstructorTask, ReportStore
from lms.djangoapps.instructor_task.tasks_helper.utils import upload_csv_file_to_report_store
from openedx.core.djangoapps.course_groups.cohorts import is_course_cohorted
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .filters_v2 import CourseEnrollmentFilter
from .serializers_v2 import (
    BetaTesterModifyRequestSerializerV2,
    BetaTesterModifyResponseSerializerV2,
    BlockDueDateSerializerV2,
    CertificateGenerationHistorySerializer,
    CourseEnrollmentSerializerV2,
    CourseInformationSerializerV2,
    CourseTeamModifySerializer,
    CourseTeamRevokeSerializer,
    EnrollmentModifyRequestSerializerV2,
    EnrollmentModifyResponseSerializerV2,
    GradingConfigSerializer,
    InstructorTaskListSerializer,
    IssuedCertificateSerializer,
    LearnerSerializer,
    ORASerializer,
    ORASummarySerializer,
    ProblemSerializer,
    RegenerateCertificatesSerializer,
    TaskStatusSerializer,
    UnitExtensionSerializer,
)
from .tools import find_unit, get_units_with_due_date, keep_field_private, set_due_date_extension, title_or_url

User = get_user_model()
log = logging.getLogger(__name__)

VALID_TEAM_ROLES = frozenset(ROLES.keys()) | frozenset(FORUM_ROLES)


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
    extended_due_date: Optional[str]  # noqa: UP045

    @classmethod
    def from_block_tuple(cls, row: Tuple, unit):  # noqa: UP006
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
    def from_course_tuple(cls, row: Tuple, units_dict: dict):  # noqa: UP006
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

        # TODO: This filtering should ideally live in edx-when get_overrides_for_block/get_overrides_for_course.
        # See https://github.com/openedx/edx-when/issues/353
        # Filter out reset extensions (None dates or dates matching the original due date)
        if unit_due_date_extensions:
            version = getattr(course, 'course_version', None)
            schedule = Schedule(start_date=course.start)
            base_dates = edx_when_api.get_dates_for_course(
                course.id, schedule=schedule, published_version=version
            )
            relevant_locations = {str(ext.unit_location) for ext in unit_due_date_extensions}
            original_due_dates = {
                str(loc): date
                for (loc, field), date in base_dates.items()
                if field == 'due' and str(loc) in relevant_locations
            }
            unit_due_date_extensions = [
                ext for ext in unit_due_date_extensions
                if ext.extended_due_date is not None
                and ext.extended_due_date != original_due_dates.get(str(ext.unit_location))
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
        elif 'cohort_results' in name_lower:
            return ReportType.COHORT_RESULTS.value
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


class IssuedCertificatesView(ListAPIView):
    """
    View to retrieve issued certificates for a course with allowlist and invalidation details.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_id}/certificates/issued
        GET /api/instructor/v2/courses/{course_id}/certificates/issued?search=username
        GET /api/instructor/v2/courses/{course_id}/certificates/issued?filter=received
        GET /api/instructor/v2/courses/{course_id}/certificates/issued?page=2&page_size=50

    **Response Values**

        {
            "count": 100,
            "next": "http://example.com/api/instructor/v2/courses/.../certificates/issued?page=2",
            "previous": null,
            "results": [
                {
                    "username": "student1",
                    "email": "student1@example.com",
                    "enrollment_track": "verified",
                    "certificate_status": "downloadable",
                    "special_case": "Exception",
                    "exception_granted": "January 15, 2024",
                    "exception_notes": "Medical emergency",
                    "invalidated_by": null,
                    "invalidation_date": null
                },
                ...
            ]
        }

    **Parameters**

        course_id: Course key for the course
        search (optional): Filter by username or email
        filter (optional): Filter certificates by category:
            - "all": All Learners (default)
            - "received": Received (downloadable certificates)
            - "not_received": Not Received (not passing, unavailable)
            - "audit_passing": Audit - Passing
            - "audit_not_passing": Audit - Not Passing
            - "error": Error State
            - "granted_exceptions": Granted Exceptions (allowlisted)
            - "invalidated": Invalidated
        page (optional): Page number for pagination
        page_size (optional): Number of results per page

    **Returns**

        * 200: OK - Returns paginated list of issued certificates
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ISSUED_CERTIFICATES
    serializer_class = IssuedCertificateSerializer

    def _apply_certificate_status_filter(self, certificates, filter_type, cert_statuses, course_key):
        """Apply status-based filters to certificate queryset."""
        if filter_type == "received":
            return certificates.filter(status=cert_statuses.downloadable)
        elif filter_type == "not_received":
            return certificates.filter(
                status__in=[cert_statuses.notpassing, cert_statuses.unavailable]
            )
        elif filter_type == "audit_passing":
            return certificates.filter(status=cert_statuses.audit_passing)
        elif filter_type == "audit_not_passing":
            return certificates.filter(status=cert_statuses.audit_notpassing)
        elif filter_type == "error":
            return certificates.filter(status=cert_statuses.error)
        elif filter_type == "granted_exceptions":
            return certificates.filter(
                user_id__in=CertificateAllowlist.objects.filter(
                    course_id=course_key, allowlist=True
                ).values_list('user_id', flat=True)
            )
        elif filter_type == "invalidated":
            return certificates.filter(
                user_id__in=CertificateInvalidation.objects.filter(
                    generated_certificate__course_id=course_key, active=True
                ).values_list('generated_certificate__user_id', flat=True)
            )
        return certificates

    def get_serializer_context(self):
        """
        Provide enrollment, allowlist, and invalidation data in serializer context.
        """
        context = super().get_serializer_context()
        course_id = self.kwargs["course_id"]
        course_key = CourseKey.from_string(course_id)

        # Get enrollment data
        enrollments = CourseEnrollment.objects.filter(
            course_id=course_key
        ).select_related('user')
        context['enrollment_dict'] = {e.user_id: e.mode for e in enrollments}

        # Get allowlist data
        allowlist_entries = CertificateAllowlist.objects.filter(
            course_id=course_key,
            allowlist=True
        ).select_related('user')
        context['allowlist_dict'] = {
            entry.user_id: {
                'created': entry.created.isoformat(),
                'notes': entry.notes or ''
            }
            for entry in allowlist_entries
        }

        # Get invalidation data
        invalidations = CertificateInvalidation.objects.filter(
            generated_certificate__course_id=course_key,
            active=True
        ).select_related('generated_certificate__user', 'invalidated_by')
        context['invalidation_dict'] = {
            inv.generated_certificate.user_id: {
                'invalidated_by': inv.invalidated_by.email,
                'created': inv.created.isoformat()
            }
            for inv in invalidations
        }

        return context

    def get_queryset(self):
        """
        Returns the queryset of issued certificates for the course.

        This method returns a Django QuerySet that will be further processed
        by DRF's default pagination.
        """
        course_id = self.kwargs["course_id"]
        course_key = CourseKey.from_string(course_id)

        # Validate that the course exists
        get_course_by_id(course_key)

        # Get query parameters
        filter_type = self.request.query_params.get("filter", "all")
        search = self.request.query_params.get("search", "").strip()

        # Get certificates for the course
        if filter_type in ['audit_passing', 'audit_not_passing', 'all']:
            certificates = GeneratedCertificate.objects.filter(
                course_id=course_key
            ).select_related('user', 'user__profile')
        else:
            certificates = GeneratedCertificate.eligible_certificates.filter(
                course_id=course_key
            ).select_related('user', 'user__profile')

        # Apply search filter at database level
        if search:
            certificates = certificates.filter(
                Q(user__username__icontains=search) | Q(user__email__icontains=search)
            )

        # Debug logging
        log.debug(
            "Certificate query for course %s: filter_type: %s",
            course_key, filter_type
        )

        # Apply filter based on filter type (includes granted_exceptions and invalidated)
        certificates = self._apply_certificate_status_filter(
            certificates, filter_type, CertificateStatuses, course_key
        )

        # Order by username for consistent pagination
        return certificates.order_by('user__username')


class CertificateGenerationHistoryView(ListAPIView):
    """
    View to retrieve certificate generation history for a course.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_id}/certificates/generation_history
        GET /api/instructor/v2/courses/{course_id}/certificates/generation_history?page=2

    **Response Values**

        {
            "count": 25,
            "next": "http://example.com/api/instructor/v2/courses/.../certificates/generation_history?page=2",
            "previous": null,
            "results": [
                {
                    "task_name": "Regenerated",
                    "date": "January 15, 2024",
                    "details": "audit not passing states"
                },
                {
                    "task_name": "Generated",
                    "date": "January 10, 2024",
                    "details": "For exceptions"
                },
                ...
            ]
        }

    **Parameters**

        course_id: Course key for the course
        page (optional): Page number for pagination
        page_size (optional): Number of results per page

    **Returns**

        * 200: OK - Returns paginated list of certificate generation history
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ISSUED_CERTIFICATES
    serializer_class = CertificateGenerationHistorySerializer

    def get_queryset(self):
        """
        Returns the queryset of certificate generation history.

        This method returns a Django QuerySet that will be paginated by DRF.
        """
        course_id = self.kwargs["course_id"]
        course_key = CourseKey.from_string(course_id)

        # Validate that the course exists
        get_course_by_id(course_key)

        # Get generation history ordered by creation date
        return CertificateGenerationHistory.objects.filter(
            course_id=course_key
        ).select_related('generated_by', 'instructor_task').order_by('-created')


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class RegenerateCertificatesView(DeveloperErrorViewMixin, APIView):
    """
    View to regenerate certificates for a course.

    **Use Cases**

        Regenerate certificates for learners in a course, optionally filtering by certificate status
        or student set (all learners or allowlisted learners).

    **Example Requests**

        POST /api/instructor/v2/courses/{course_id}/certificates/regenerate

        Request Body:
        {
            "statuses": ["downloadable", "notpassing"],
            "student_set": "all"
        }

    **Request Body Parameters**

        statuses (optional): List of certificate statuses to regenerate
        student_set (optional): "all" for all learners, "allowlisted" for allowlisted learners only

    **Response Values**

        {
            "task_id": "abc-123",
            "message": "Certificate regeneration task has been started"
        }

    **Returns**

        * 200: OK - Certificate regeneration task started successfully
        * 400: Bad Request - Invalid parameters
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.START_CERTIFICATE_REGENERATION

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
        ],
        body=RegenerateCertificatesSerializer,
        responses={
            200: "Certificate regeneration task started successfully",
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def post(self, request, course_id):
        """
        Initiate certificate regeneration for a course.
        """
        course_key = CourseKey.from_string(course_id)
        get_course_by_id(course_key)

        serializer = RegenerateCertificatesSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        statuses = serializer.validated_data.get('statuses', [])
        student_set = serializer.validated_data.get('student_set', 'all')

        try:
            # Submit certificate generation/regeneration task
            if student_set == 'allowlisted':
                # Generate for allowlisted students only
                task = task_api.generate_certificates_for_students(
                    request,
                    course_key,
                    student_set='all_allowlisted'
                )
            elif statuses:
                # Regenerate for specified statuses
                task = task_api.regenerate_certificates(
                    request,
                    course_key,
                    statuses_to_regenerate=statuses
                )
            else:
                # Generate for all students
                task = task_api.generate_certificates_for_students(
                    request,
                    course_key
                )

            return Response({
                'task_id': task.task_id,
                'message': _('Certificate regeneration task has been started')
            }, status=status.HTTP_200_OK)

        except (AlreadyRunningError, QueueConnectionError) as exc:
            log.error("Error starting certificate regeneration: %s", exc)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST
            )


class CertificateConfigView(DeveloperErrorViewMixin, APIView):
    """
    View to retrieve certificate configuration for a course.

    **Use Cases**

        Check if certificate generation is enabled for the platform and validate course existence.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_id}/certificates/config

    **Response Values**

        {
            "enabled": true
        }

    **Returns**

        * 200: OK - Returns certificate configuration
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
        * 404: Not Found - Course does not exist
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ISSUED_CERTIFICATES

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="Course key for the course.",
            ),
        ],
        responses={
            200: "Returns certificate configuration.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request, course_id):
        """
        Retrieve certificate configuration.
        """
        course_key = CourseKey.from_string(course_id)
        # Validate that the course exists
        get_course_by_id(course_key)

        # Check if certificate generation is enabled (not available for CCX courses)
        enabled = certs_api.is_certificate_generation_enabled() and not hasattr(course_key, 'ccx')

        return Response({'enabled': enabled}, status=status.HTTP_200_OK)


class CourseEnrollmentsView(DeveloperErrorViewMixin, ListAPIView):
    """
    List all active enrollments for a course with optional search, filtering, and pagination.

    **Example Requests**

        GET /api/instructor/v2/courses/{course_id}/enrollments
        GET /api/instructor/v2/courses/{course_id}/enrollments?search=john
        GET /api/instructor/v2/courses/{course_id}/enrollments?is_beta_tester=true
        GET /api/instructor/v2/courses/{course_id}/enrollments?page=2&page_size=50

    **Response Values**

        {
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "count": 150,
            "num_pages": 15,
            "current_page": 1,
            "start": 0,
            "next": "http://example.com/api/instructor/v2/courses/.../enrollments?page=2",
            "previous": null,
            "results": [
                {
                    "username": "learner1",
                    "full_name": "Jane Doe",
                    "email": "jane@example.com",
                    "mode": "audit",
                    "is_beta_tester": false
                },
                ...
            ]
        }

    **Parameters**

        course_id: Course key for the course.
        search (optional): Filter by username, email, first name, or last name.
        is_beta_tester (optional): Filter by beta tester status (true/false).
        page (optional): Page number for pagination.
        page_size (optional): Number of results per page (default: 10, max: 100).

    **Returns**

        * 200: OK - Returns paginated list of active enrollments
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ENROLLMENTS
    serializer_class = CourseEnrollmentSerializerV2
    filter_backends = [DjangoFilterBackend]
    filterset_class = CourseEnrollmentFilter

    def get_queryset(self):
        course_key = CourseKey.from_string(self.kwargs['course_id'])
        return CourseEnrollment.objects.filter(
            course_id=course_key,
            is_active=True
        ).select_related('user', 'user__profile').order_by('user__username')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        course_key = CourseKey.from_string(self.kwargs['course_id'])
        context['beta_tester_ids'] = set(
            CourseBetaTesterRole(course_key).users_with_role().values_list('id', flat=True)
        )
        return context

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['course_id'] = self.kwargs['course_id']
        return response


class LearnerView(DeveloperErrorViewMixin, APIView):
    """
    API view for retrieving learner information.

    **GET Example Response:**
    ```json
    {
        "username": "john_harvard",
        "email": "john@example.com",
        "full_name": "John Harvard",
        "progress_url": "https://example.com/courses/course-v1:edX+DemoX+Demo_Course/progress/john_harvard/"
    }
    ```
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
            apidocs.string_parameter(
                'email_or_username',
                apidocs.ParameterLocation.PATH,
                description="Learner's username or email address",
            ),
        ],
        responses={
            200: 'Learner information retrieved successfully',
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "Learner not found or course does not exist.",
        },
    )
    def get(self, request, course_id, email_or_username):
        """
        Retrieve comprehensive learner information including profile, enrollment status,
        progress URLs, and current grading data.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {'error': 'Invalid course key'},
                status=status.HTTP_400_BAD_REQUEST
            )

        UserModel = get_user_model()
        try:
            student = get_user_by_username_or_email(email_or_username)
        except UserModel.DoesNotExist:
            return Response(
                {'error': 'Learner not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except UserModel.MultipleObjectsReturned:
            return Response(
                {'error': 'Multiple learners found for the given identifier'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Build progress URL (MFE or legacy depending on feature flag)
        if course_home_mfe_progress_tab_is_active(course_key):
            progress_url = get_learning_mfe_home_url(course_key, url_fragment='progress')
            progress_url += f'/{student.id}/'
        else:
            progress_url = reverse(
                'student_progress',
                kwargs={'course_id': str(course_key), 'student_id': student.id}
            )

        learner_data = {
            'username': student.username,
            'email': student.email,
            'full_name': student.profile.name,
            'progress_url': progress_url,
        }

        serializer = LearnerSerializer(learner_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ProblemView(DeveloperErrorViewMixin, APIView):
    """
    API view for retrieving problem metadata.

    **GET Example Response:**
    ```json
    {
        "id": "block-v1:edX+DemoX+Demo_Course+type@problem+block@sample_problem",
        "name": "Sample Problem",
        "breadcrumbs": [
            {"display_name": "Demonstration Course"},
            {
                "display_name": "Week 1",
                "usage_key": "block-v1:edX+DemoX+Demo_Course+type@chapter+block@week1"
            },
            {
                "display_name": "Homework",
                "usage_key": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@hw1"
            },
            {
                "display_name": "Sample Problem",
                "usage_key": "block-v1:edX+DemoX+Demo_Course+type@problem+block@sample_problem"
            }
        ],
        "current_score": {
            "score": 7.0,
            "total": 10.0
        },
        "attempts": {
            "current": 3,
            "total": null
        }
    }
    ```
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
            apidocs.string_parameter(
                'location',
                apidocs.ParameterLocation.PATH,
                description="Problem block usage key",
            ),
        ],
        responses={
            200: 'Problem information retrieved successfully',
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "Problem not found or course does not exist.",
        },
    )
    def get(self, request, course_id, location):
        """
        Retrieve problem metadata including display name, location in course hierarchy,
        and usage key.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {'error': 'Invalid course key'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            problem_key = UsageKey.from_string(location)
        except InvalidKeyError:
            return Response(
                {'error': 'Invalid problem location'},
                status=status.HTTP_400_BAD_REQUEST
            )

        store = modulestore()

        try:
            problem = store.get_item(problem_key)
        except ItemNotFoundError:
            return Response(
                {'error': 'Problem not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Build breadcrumbs by walking up the parent chain
        breadcrumbs = []
        current = problem
        while current:
            breadcrumbs.insert(0, {
                'display_name': current.display_name,
                'usage_key': str(current.location) if current.location.block_type != 'course' else None
            })
            parent = current.get_parent() if hasattr(current, 'get_parent') else None
            if not parent:
                break
            current = parent

        problem_data = {
            'id': str(problem.location),
            'name': problem.display_name,
            'breadcrumbs': breadcrumbs,
            'current_score': None,
            'attempts': None,
        }

        learner_identifier = request.query_params.get('email_or_username')
        if learner_identifier:
            UserModel = get_user_model()
            try:
                student = get_user_by_username_or_email(learner_identifier)
            except UserModel.DoesNotExist:
                return Response(
                    {'error': 'Learner not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            except UserModel.MultipleObjectsReturned:
                return Response(
                    {'error': 'Multiple learners found for the given identifier'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                student_module = StudentModule.objects.get(
                    course_id=course_key,
                    module_state_key=problem_key,
                    student=student,
                )
                problem_data['current_score'] = {
                    'score': student_module.grade,
                    'total': student_module.max_grade,
                }
                state = json.loads(student_module.state) if student_module.state else {}
                problem_data['attempts'] = {
                    'current': state.get('attempts', 0),
                    'total': problem.max_attempts,
                }
            except StudentModule.DoesNotExist:
                pass  # Leave current_score and attempts as None

        serializer = ProblemSerializer(problem_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TaskStatusView(DeveloperErrorViewMixin, APIView):
    """
    API view for checking background task status.

    **GET Example Response:**
    ```json
    {
        "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "state": "completed",
        "progress": {
            "current": 150,
            "total": 150
        },
        "result": {
            "success": true,
            "message": "Reset attempts for 150 learners"
        },
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-01-15T10:35:23Z"
    }
    ```
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
                'task_id',
                apidocs.ParameterLocation.PATH,
                description="Task identifier returned from async operation",
            ),
        ],
        responses={
            200: 'Task status retrieved successfully',
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "Task not found.",
        },
    )
    def get(self, request, course_id, task_id):
        """
        Check the status of a background task.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {'error': 'Invalid course key'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get task from InstructorTask model
        try:
            task = InstructorTask.objects.get(task_id=task_id, course_id=course_key)
        except InstructorTask.DoesNotExist:
            return Response(
                {'error': 'Task not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Map task state
        state_map = {
            'PENDING': 'pending',
            'QUEUING': 'pending',
            'SCHEDULED': 'pending',
            'RECEIVED': 'pending',
            'STARTED': 'running',
            'PROGRESS': 'running',
            'RETRY': 'running',
            'SUCCESS': 'completed',
            'FAILURE': 'failed',
            'REVOKED': 'failed',
        }

        task_data = {
            'task_id': str(task.task_id),
            'state': state_map.get(task.task_state, 'pending'),
            'created_at': task.created,
            'updated_at': task.updated,
        }

        # Add progress if available
        if hasattr(task, 'task_output') and task.task_output:
            try:
                output = json.loads(task.task_output)
                if 'current' in output and 'total' in output:
                    task_data['progress'] = {
                        'current': output['current'],
                        'total': output['total']
                    }
                if task.task_state == 'SUCCESS' and 'message' in output:
                    task_data['result'] = {
                        'success': True,
                        'message': output['message']
                    }
            except (json.JSONDecodeError, KeyError):
                pass

        # Add error if failed
        if task.task_state in ['FAILURE', 'REVOKED']:
            task_data['error'] = {
                'code': 'TASK_FAILED',
                'message': str(task.task_output) if task.task_output else 'Task failed'
            }

        serializer = TaskStatusSerializer(task_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GradingConfigView(DeveloperErrorViewMixin, APIView):
    """
    API view for retrieving course grading configuration.

    **GET Example Response:**
    ```json
    {
        "graders": [
            {
                "type": "Homework",
                "short_label": "HW",
                "min_count": 12,
                "drop_count": 2,
                "weight": 0.15
            },
            {
                "type": "Final Exam",
                "short_label": "Final",
                "min_count": 1,
                "drop_count": 0,
                "weight": 0.40
            }
        ],
        "grade_cutoffs": {
            "A": 0.9,
            "B": 0.8,
            "C": 0.7
        }
    }
    ```
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
            200: 'Grading configuration retrieved successfully',
            400: "Invalid parameters provided.",
            401: "The requesting user is not authenticated.",
            403: "The requesting user lacks instructor access to the course.",
            404: "Course does not exist.",
        },
    )
    def get(self, request, course_id):
        """
        Retrieve the grading configuration for a course, including assignment type
        weights and grade cutoff thresholds.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                {'error': 'Invalid course key'},
                status=status.HTTP_400_BAD_REQUEST
            )

        course = get_course_by_id(course_key)
        grading_policy = course.grading_policy
        config_data = {
            'graders': grading_policy.get('GRADER', []),
            'grade_cutoffs': grading_policy.get('GRADE_CUTOFFS', {}),
        }
        serializer = GradingConfigSerializer(config_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class EnrollmentModifyView(DeveloperErrorViewMixin, APIView):
    """
    Enroll or unenroll one or more learners in a course.

    **Example Request**

        POST /api/instructor/v2/courses/{course_id}/enrollments/modify

    **Parameters**

        * identifier (required): List of emails or usernames of learners
        * action (required): 'enroll' or 'unenroll'
        * auto_enroll (optional): Auto-enroll in verified track (enroll action; default: false)
        * email_students (optional): Send email notification (default: false)
        * reason (optional): Reason for the change

    **Response Values**

        {
            "action": "enroll",
            "results": [
                {
                    "identifier": "learner@example.com",
                    "success": true,
                    "user_is_registered": true,
                    "enrollment": true,
                    "allowed": false,
                    "auto_enroll": false
                },
                {
                    "identifier": "bad@",
                    "success": false,
                    "error": "Invalid email address: bad@"
                }
            ]
        }

    **Returns**

        * 200: OK - Per-identifier results returned (successes and failures)
        * 400: Bad Request - Invalid top-level parameters
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks staff permissions
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_ENROLL

    def _compute_enroll_state_transition(self, before, after):
        """Compute the state transition constant for an enroll action."""
        if before.user:
            if after.enrollment:
                if before.enrollment:
                    return ENROLLED_TO_ENROLLED
                if before.allowed:
                    return ALLOWEDTOENROLL_TO_ENROLLED
                return UNENROLLED_TO_ENROLLED
        if after.allowed:
            return UNENROLLED_TO_ALLOWEDTOENROLL
        return DEFAULT_TRANSITION_STATE

    def _compute_unenroll_state_transition(self, before):
        """Compute the state transition constant for an unenroll action."""
        if before.enrollment:
            return ENROLLED_TO_UNENROLLED
        if before.allowed:
            return ALLOWEDTOENROLL_TO_UNENROLLED
        return UNENROLLED_TO_UNENROLLED

    def _resolve_identifier(self, identifier):
        """Resolve identifier to (user, email, language); returns (None, identifier, None) if user not found."""
        try:
            user = get_student_from_identifier(identifier)
            return user, user.email, get_user_email_language(user)
        except User.DoesNotExist:
            return None, identifier, None

    def _enroll_one(self, course_key, course, identifier, auto_enroll, email_students, reason, request_user, is_secure):
        """Enroll a single identifier. Returns a v1-shaped result dict."""
        try:
            identified_user, email, language = self._resolve_identifier(identifier)
        except User.MultipleObjectsReturned:
            log.exception("Ambiguous identifier while enrolling: %s", identifier)
            return {'identifier': identifier, 'error': True}

        try:
            validate_email(email)
        except DjangoValidationError:
            return {'identifier': identifier, 'invalid_identifier': True}

        try:
            email_params = {}
            if email_students:
                email_params = get_email_params(course, auto_enroll, secure=is_secure)

            before, after, enrollment_obj = enroll_email(
                course_key, email, auto_enroll, email_students, email_params, language=language
            )
            ManualEnrollmentAudit.create_manual_enrollment_audit(
                request_user, email, self._compute_enroll_state_transition(before, after), reason, enrollment_obj
            )
        except Exception:  # pylint: disable=broad-except
            log.exception("Error while enrolling student")
            return {'identifier': identifier, 'error': True}

        return {
            'identifier': identifier,
            'before': before.to_dict(),
            'after': after.to_dict(),
        }

    def _unenroll_one(self, course_key, course, identifier, email_students, reason, request_user, is_secure):
        """Unenroll a single identifier. Returns a v1-shaped result dict."""
        try:
            identified_user, email, language = self._resolve_identifier(identifier)
        except User.MultipleObjectsReturned:
            log.exception("Ambiguous identifier while unenrolling: %s", identifier)
            return {'identifier': identifier, 'error': True}

        try:
            validate_email(email)
        except DjangoValidationError:
            return {'identifier': identifier, 'invalid_identifier': True}

        try:
            email_params = {}
            if email_students:
                email_params = get_email_params(course, False, secure=is_secure)

            before, after = unenroll_email(
                course_key, email, email_students, email_params, language=language
            )
            enrollment_obj = (
                CourseEnrollment.get_enrollment(identified_user, course_key)
                if identified_user else None
            )
            ManualEnrollmentAudit.create_manual_enrollment_audit(
                request_user, email, self._compute_unenroll_state_transition(before), reason, enrollment_obj
            )
        except Exception:  # pylint: disable=broad-except
            log.exception("Error while unenrolling student")
            return {'identifier': identifier, 'error': True}

        return {
            'identifier': identifier,
            'before': before.to_dict(),
            'after': after.to_dict(),
        }

    @apidocs.schema(
        body=EnrollmentModifyRequestSerializerV2,
        responses={
            200: EnrollmentModifyResponseSerializerV2,
            400: "Invalid parameters",
            403: "User does not have permission",
        },
    )
    def post(self, request, course_id):
        """
        Enroll or unenroll one or more learners in the course.
        """
        course_key = CourseKey.from_string(course_id)

        serializer = EnrollmentModifyRequestSerializerV2(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        identifiers = serializer.validated_data['identifier']
        action = serializer.validated_data['action']
        auto_enroll = serializer.validated_data['auto_enroll']
        email_students = serializer.validated_data['email_students']
        reason = serializer.validated_data.get('reason', '')

        course = get_course_by_id(course_key) if email_students else None
        is_secure = request.is_secure()

        results = []
        for identifier in identifiers:
            if action == 'enroll':
                results.append(self._enroll_one(
                    course_key, course, identifier, auto_enroll, email_students, reason, request.user, is_secure
                ))
            else:
                results.append(self._unenroll_one(
                    course_key, course, identifier, email_students, reason, request.user, is_secure
                ))

        response_serializer = EnrollmentModifyResponseSerializerV2({
            'action': action,
            'auto_enroll': auto_enroll,
            'results': results,
        })
        return Response(response_serializer.data)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class BetaTesterModifyView(DeveloperErrorViewMixin, APIView):
    """
    Add or remove one or more beta testers for a course.

    **Example Request**

        POST /api/instructor/v2/courses/{course_id}/beta_testers/modify

    **Parameters**

        * identifier (required): List of emails or usernames of learners
        * action (required): 'add' or 'remove'
        * email_students (optional): Send email notification (default: false)
        * auto_enroll (optional): Auto-enroll in the course (add action; default: false)

    **Response Values**

        {
            "action": "add",
            "results": [
                {"identifier": "learner@example.com", "success": true, "is_active": true},
                {"identifier": "missing", "success": false, "error": "User not found: missing"}
            ]
        }

    **Returns**

        * 200: OK - Per-identifier results returned (successes and failures)
        * 400: Bad Request - Invalid top-level parameters
        * 401: Unauthorized - User is not authenticated
        * 403: Forbidden - User lacks instructor permissions
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_BETATEST

    def _modify_one(self, action, course, course_key, identifier, email_students, auto_enroll, is_secure):
        """Add or remove a single identifier as a beta tester. Returns a v1-shaped result dict."""
        error = False
        user_does_not_exist = False
        user_active = None
        try:
            user = get_student_from_identifier(identifier)
            user_active = user.is_active
            if action == 'add':
                allow_access(course, user, 'beta')
            else:
                revoke_access(course, user, 'beta')
        except User.DoesNotExist:
            error = True
            user_does_not_exist = True
        except User.MultipleObjectsReturned:
            log.exception("Ambiguous identifier for beta tester action: %s", identifier)
            error = True
        except Exception:  # pylint: disable=broad-except
            log.exception("Error while modifying beta tester")
            error = True
        else:
            if email_students:
                email_params = get_email_params(course, False, secure=is_secure)
                send_beta_role_email(action, user, email_params)
            if auto_enroll and action == 'add' and not CourseEnrollment.is_enrolled(user, course_key):
                CourseEnrollment.enroll(user, course_key)

        return {
            'identifier': identifier,
            'error': error,
            'user_does_not_exist': user_does_not_exist,
            'is_active': user_active,
        }

    @apidocs.schema(
        body=BetaTesterModifyRequestSerializerV2,
        responses={
            200: BetaTesterModifyResponseSerializerV2,
            400: "Invalid parameters",
            403: "User does not have permission",
        },
    )
    def post(self, request, course_id):
        """
        Add or remove one or more beta testers for the course.
        """
        course_key = CourseKey.from_string(course_id)

        serializer = BetaTesterModifyRequestSerializerV2(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        identifiers = serializer.validated_data['identifier']
        action = serializer.validated_data['action']
        email_students = serializer.validated_data['email_students']
        auto_enroll = serializer.validated_data['auto_enroll']

        course = get_course_by_id(course_key)
        is_secure = request.is_secure()

        results = [
            self._modify_one(action, course, course_key, identifier, email_students, auto_enroll, is_secure)
            for identifier in identifiers
        ]

        response_serializer = BetaTesterModifyResponseSerializerV2({
            'action': action,
            'results': results,
        })
        return Response(response_serializer.data)


class CourseTeamRolesView(DeveloperErrorViewMixin, APIView):
    """
    List the available course team roles for a specific course.

    The returned roles are filtered based on course configuration.
    For example, the ``ccx_coach`` role is only included when the
    ``CUSTOM_COURSES_EDX`` feature flag is enabled **and** the course
    has CCX enabled (``course.enable_ccx``).

    **GET Example Request**

        GET /api/instructor/v2/courses/{course_id}/team/roles

    **GET Response Values**

        {
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "results": [
                {"role": "beta", "display_name": "Beta Tester"},
                {"role": "data_researcher", "display_name": "Data Researcher"},
                {"role": "instructor", "display_name": "Admin"},
                {"role": "limited_staff", "display_name": "Limited Staff"},
                {"role": "staff", "display_name": "Staff"}
            ]
        }

    **Returns**

        * 200: OK
        * 401: User is not authenticated
        * 403: User lacks instructor permissions
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EDIT_COURSE_ACCESS

    def get(self, request, course_id):
        """Return the list of available course team roles for this course."""
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        roles = set(ROLES.keys()) | set(FORUM_ROLES)

        ccx_enabled = settings.FEATURES.get('CUSTOM_COURSES_EDX', False) and course.enable_ccx
        if not ccx_enabled:
            roles.discard('ccx_coach')

        results = [
            {'role': rolename, 'display_name': str(ROLE_DISPLAY_NAMES[rolename])}
            for rolename in sorted(roles)
        ]

        return Response({
            'course_id': str(course_key),
            'results': results,
        }, status=status.HTTP_200_OK)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class CourseTeamView(DeveloperErrorViewMixin, APIView):
    """
    List course team members, or grant/revoke a role for one or more users.

    **GET Example Requests**

        GET /api/instructor/v2/courses/{course_id}/team
        GET /api/instructor/v2/courses/{course_id}/team?role=staff

    **GET Response Values**

    Each result is one record per user, aggregating all of that user's roles
    into a ``roles`` array. Each role object contains the ``role`` identifier
    and its localized ``display_name``.

    When ``role`` is omitted, returns all team members across all roles:

        {
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "role": null,
            "results": [
                {
                    "username": "jane_doe",
                    "email": "jane@example.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "roles": [
                        {"role": "staff", "display_name": "Staff"},
                        {"role": "beta", "display_name": "Beta Tester"},
                        {"role": "ccx_coach", "display_name": "CCX Coach"}
                    ]
                }
            ]
        }

    When ``role`` is specified, returns only members with that role (still
    one record per user, with the ``roles`` array containing only that role):

        {
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "role": "staff",
            "results": [
                {
                    "username": "staff_user1",
                    "email": "staff1@example.com",
                    "first_name": "Bob",
                    "last_name": "Jones",
                    "roles": [
                        {"role": "staff", "display_name": "Staff"}
                    ]
                }
            ]
        }

    **POST Example Request (grant)**

        POST /api/instructor/v2/courses/{course_id}/team
        {
            "identifiers": ["jane_doe", "john@example.com"],
            "role": "staff",
            "action": "allow"
        }

    **POST Example Request (revoke)**

        POST /api/instructor/v2/courses/{course_id}/team
        {
            "identifiers": ["jane_doe"],
            "role": "staff",
            "action": "revoke"
        }

    **POST Response Values**

        {
            "action": "allow",
            "role": "staff",
            "results": [
                {
                    "identifier": "jane_doe",
                    "error": false,
                    "userDoesNotExist": false,
                    "is_active": true
                },
                {
                    "identifier": "john@example.com",
                    "error": false,
                    "userDoesNotExist": false,
                    "is_active": true
                }
            ]
        }

    **Returns**

        * 200: OK (GET, POST - role granted/revoked)
        * 400: Invalid parameters
        * 401: User is not authenticated
        * 403: User lacks instructor permissions
        * 404: Course not found
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EDIT_COURSE_ACCESS

    def get(self, request, course_id):
        """
        List course team members, optionally filtered by role and identity.

        If no role is specified, returns members across all course and forum
        roles. The optional ``email_or_username`` query param performs a
        case-insensitive substring match against username and email.

        Results are paginated via ``page`` and ``page_size``.
        """
        course_key = CourseKey.from_string(course_id)
        role = request.query_params.get('role')
        email_or_username = (request.query_params.get('email_or_username') or '').strip()

        if role and role not in VALID_TEAM_ROLES:
            return Response(
                {'error': _("Invalid role '%(role)s'. Must be one of: %(valid)s") % {
                    'role': role,
                    'valid': ', '.join(sorted(VALID_TEAM_ROLES)),
                }},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify course exists
        get_course_by_id(course_key)

        if role:
            roles_to_query = [role]
        else:
            roles_to_query = list(ROLES.keys()) + list(FORUM_ROLES)

        users_by_id = {}
        for rolename in roles_to_query:
            if is_forum_role(rolename):
                users = list_forum_members(course_key, rolename)
            else:
                users = list_with_level(course_key, rolename)
            for user in users:
                entry = users_by_id.get(user.id)
                if entry is None:
                    entry = {
                        'username': user.username,
                        'email': user.email,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'roles': [],
                    }
                    users_by_id[user.id] = entry
                entry['roles'].append({
                    'role': rolename,
                    'display_name': str(ROLE_DISPLAY_NAMES.get(rolename, rolename)),
                })

        results = sorted(users_by_id.values(), key=lambda r: r['username'].lower())

        if email_or_username:
            needle = email_or_username.lower()
            results = [
                r for r in results
                if needle in r['username'].lower() or needle in r['email'].lower()
            ]

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(results, request, view=self)
        paginated = paginator.get_paginated_response(page).data
        paginated['course_id'] = str(course_key)
        paginated['role'] = role
        paginated['email_or_username'] = email_or_username or None
        return Response(paginated, status=status.HTTP_200_OK)

    def post(self, request, course_id):
        """Grant or revoke a course role for one or more users."""
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        serializer = CourseTeamModifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        identifiers = serializer.validated_data['identifiers']
        rolename = serializer.validated_data['role']
        action = serializer.validated_data['action']

        results = []
        for identifier in identifiers:
            error = False
            user_does_not_exist = False
            user_active = None
            try:
                user = get_student_from_identifier(identifier)
                user_active = user.is_active

                if not user.is_active:
                    error = True
                elif action == 'allow':
                    if is_forum_role(rolename):
                        update_forum_role(course_key, user, rolename, 'allow')
                    else:
                        allow_access(course, user, rolename)
                        if not is_user_enrolled_in_course(user, course_key):
                            CourseEnrollment.enroll(user, course_key)
                elif action == 'revoke':
                    if rolename == 'instructor' and user == request.user:
                        error = True
                    elif is_forum_role(rolename):
                        update_forum_role(course_key, user, rolename, 'revoke')
                    else:
                        revoke_access(course, user, rolename)
            except User.DoesNotExist:
                error = True
                user_does_not_exist = True
            except Exception:  # pylint: disable=broad-except
                log.exception("Error while %s role %s for %s", action, rolename, identifier)
                error = True
            finally:
                results.append({
                    'identifier': identifier,
                    'error': error,
                    'userDoesNotExist': user_does_not_exist,
                    'is_active': user_active,
                })

        return Response({
            'action': action,
            'role': rolename,
            'results': results,
        }, status=status.HTTP_200_OK)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class CourseTeamMemberView(DeveloperErrorViewMixin, APIView):
    """
    Revoke one or more course roles from a user.

    **DELETE Example Request (single role)**

        DELETE /api/instructor/v2/courses/{course_id}/team/jane_doe
        {
            "roles": ["staff"]
        }

    **DELETE Example Request (multiple roles)**

        DELETE /api/instructor/v2/courses/{course_id}/team/jane_doe
        {
            "roles": ["staff", "beta"]
        }

    **DELETE Response Values**

        {
            "identifier": "jane_doe",
            "roles": ["staff", "beta"],
            "action": "revoke",
            "success": true
        }

    **Returns**

        * 200: Role(s) revoked successfully
        * 400: Invalid parameters
        * 401: User is not authenticated
        * 403: User lacks instructor permissions
        * 404: Course or user not found
        * 409: Cannot remove own instructor access
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EDIT_COURSE_ACCESS

    def delete(self, request, course_id, email_or_username):
        """Revoke one or more course roles from a user."""
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        revoke_serializer = CourseTeamRevokeSerializer(data=request.data)
        if not revoke_serializer.is_valid():
            return Response(revoke_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        roles = revoke_serializer.validated_data['roles']

        try:
            user = get_student_from_identifier(email_or_username)
        except User.DoesNotExist:
            return Response(
                {'error': _("User '%(identifier)s' not found.") % {'identifier': email_or_username}},
                status=status.HTTP_404_NOT_FOUND
            )

        if not user.is_active:
            return Response(
                {'error': _("User '%(identifier)s' is inactive.") % {'identifier': email_or_username}},
                status=status.HTTP_400_BAD_REQUEST
            )

        if 'instructor' in roles and user == request.user:
            return Response(
                {'error': _('Instructors cannot remove their own instructor access.')},
                status=status.HTTP_409_CONFLICT
            )

        for rolename in roles:
            if is_forum_role(rolename):
                update_forum_role(course_key, user, rolename, 'revoke')
            else:
                revoke_access(course, user, rolename)

        return Response({
            'identifier': user.username,
            'roles': roles,
            'action': 'revoke',
            'success': True,
        }, status=status.HTTP_200_OK)
