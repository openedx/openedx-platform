"""
ProgramEnrollment Views
"""
from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from edx_api_doc_tools import path_parameter, query_parameter, schema
from edx_rest_framework_extensions import permissions
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from organizations.models import Organization
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, UserBasedRole
from common.djangoapps.util.query import read_replica_or_default
from lms.djangoapps.program_enrollments.api import (
    fetch_program_course_enrollments,
    fetch_program_enrollments,
    fetch_program_enrollments_by_student,
    get_provider_slug,
    get_saml_providers_for_organization,
    iter_program_course_grades,
    write_program_course_enrollments,
    write_program_enrollments,
)
from lms.djangoapps.program_enrollments.constants import (
    ProgramCourseOperationStatuses,
    ProgramEnrollmentStatuses,
    ProgramOperationStatuses,
)
from lms.djangoapps.program_enrollments.exceptions import ProviderDoesNotExistException
from openedx.core.apidocs import cursor_paginate_serializer
from openedx.core.djangoapps.catalog.utils import (
    get_programs,
    get_programs_by_type,
    get_programs_for_organization,
    normalize_program_type,
)
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, PaginatedAPIView

from .constants import ENABLE_ENROLLMENT_RESET_FLAG, MAX_ENROLLMENT_RECORDS
from .serializers import (
    CourseRunOverviewListSerializer,
    CourseRunOverviewSerializer,
    ProgramCourseEnrollmentRequestSerializer,
    ProgramCourseEnrollmentSerializer,
    ProgramCourseGradeSerializer,
    ProgramEnrollmentCreateRequestSerializer,
    ProgramEnrollmentSerializer,
    ProgramEnrollmentUpdateRequestSerializer,
)
from .utils import (
    ProgramCourseSpecificViewMixin,
    ProgramEnrollmentPagination,
    ProgramSpecificViewMixin,
    UserProgramCourseEnrollmentPagination,
    UserProgramSpecificViewMixin,
    get_enrollment_http_code,
    get_enrollment_overviews,
    get_enrollments_for_courses_in_program,
    verify_course_exists_and_in_program,
    verify_program_exists,
    verify_user_enrolled_in_program,
)


class EnrollmentWriteMixin:
    """
    Common functionality for viewsets with enrollment-writing POST/PATCH/PUT methods.

    Provides a `handle_write_request` utility method, which depends on the
    definitions of `serializer_class_by_write_method`, `ok_write_statuses`,
    and `perform_enrollment_write`.
    """
    create_update_by_write_method = {
        'POST': (True, False),
        'PATCH': (False, True),
        'PUT': (True, True),
    }

    # Set in subclasses
    serializer_class_by_write_method = "set-me-to-a-dict-with-http-method-keys"
    ok_write_statuses = "set-me-to-a-set"

    def handle_write_request(self):
        """
        Create/modify program enrollments.
        Returns: Response
        """
        serializer_class = self.serializer_class_by_write_method[self.request.method]
        serializer = serializer_class(data=self.request.data, many=True)
        serializer.is_valid(raise_exception=True)
        num_requests = len(self.request.data)
        if num_requests > MAX_ENROLLMENT_RECORDS:
            return Response(
                '{} enrollments requested, but limit is {}.'.format(
                    MAX_ENROLLMENT_RECORDS, num_requests
                ),
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        create, update = self.create_update_by_write_method[self.request.method]
        results = self.perform_enrollment_write(
            serializer.validated_data, create, update
        )
        http_code = get_enrollment_http_code(
            results.values(), self.ok_write_statuses
        )
        return Response(status=http_code, data=results, content_type='application/json')

    def perform_enrollment_write(self, enrollment_requests, create, update):
        """
        Perform the write operation. Implemented in subclasses.

        Arguments:
            enrollment_requests: list[dict]
            create (bool)
            update (bool)

        Returns: dict[str: str]
            Map from external keys to enrollment write statuses.
        """
        raise NotImplementedError()


class ProgramEnrollmentsView(
        EnrollmentWriteMixin,
        DeveloperErrorViewMixin,
        ProgramSpecificViewMixin,
        PaginatedAPIView,
):
    """
    A view for Create/Read/Update methods on Program Enrollment data.

    Path: `/api/program_enrollments/v1/programs/{program_uuid}/enrollments/`
    The path can contain an optional `page_size?=N` query parameter.  The default page size is 100.

    Returns:
      * 200: OK - Contains a paginated set of program enrollment data.
      * 401: The requesting user is not authenticated.
      * 403: The requesting user lacks access for the given program.
      * 404: The requested program does not exist.

    Response:
    In the case of a 200 response code, the response will include a paginated
    data set.  The `results` section of the response consists of a list of
    program enrollment records, where each record contains the following keys:
      * student_key: The identifier of the student enrolled in the program.
      * status: The student's enrollment status.
      * account_exists: A boolean indicating if the student has created an edx-platform user account.
      * curriculum_uuid: The curriculum UUID of the enrollment record for the (student, program).

    Example:
    {
        "next": null,
        "previous": "http://testserver.com/api/program_enrollments/v1/programs/{program_uuid}/enrollments/?curor=abcd",
        "results": [
            {
                "student_key": "user-0", "status": "pending",
                "account_exists": False, "curriculum_uuid": "00000000-1111-2222-3333-444444444444"
            },
            {
                "student_key": "user-1", "status": "pending",
                "account_exists": False, "curriculum_uuid": "00000001-1111-2222-3333-444444444444"
            },
            {
                "student_key": "user-2", "status": "enrolled",
                "account_exists": True, "curriculum_uuid": "00000002-1111-2222-3333-444444444444"
            },
            {
                "student_key": "user-3", "status": "enrolled",
                "account_exists": True, "curriculum_uuid": "00000003-1111-2222-3333-444444444444"
            },
        ],
    }

    Create
    ==========
    Path: `/api/program_enrollments/v1/programs/{program_uuid}/enrollments/`
    Where the program_uuid will be the uuid for a program.

    Request body:
        * The request body will be a list of one or more students to enroll with the following schema:
            {
                'status': A choice of the following statuses: ['enrolled', 'pending', 'canceled', 'suspended', 'ended'],
                student_key: string representation of a learner in partner systems,
                'curriculum_uuid': string representation of a curriculum
            }
        Example:
            [
                {
                    "status": "enrolled",
                    "external_user_key": "123",
                    "curriculum_uuid": "2d7de549-b09e-4e50-835d-4c5c5080c566"
                },{
                    "status": "canceled",
                    "external_user_key": "456",
                    "curriculum_uuid": "2d7de549-b09e-4e50-835d-4c5c5080c566"
                },{
                    "status": "pending",
                    "external_user_key": "789",
                    "curriculum_uuid": "2d7de549-b09e-4e50-835d-4c5c5080c566"
                },{
                    "status": "suspended",
                    "external_user_key": "abc",
                    "curriculum_uuid": "2d7de549-b09e-4e50-835d-4c5c5080c566"
                },
            ]

    Returns:
      * Response Body: {<external_user_key>: <status>} with as many keys as there were in the request body
        * external_user_key - string representation of a learner in partner systems
        * status - the learner's registration status
            * success statuses:
                * 'enrolled'
                * 'pending'
                * 'canceled'
                * 'suspended'
                * 'ended'
            * failure statuses:
                * 'duplicated' - the request body listed the same learner twice
                * 'conflict' - there is an existing enrollment for that learner, curriculum and program combo
                * 'invalid-status' - a status other than 'enrolled', 'pending', 'canceled', 'suspended',
                  or 'ended' was entered
      * 200: OK - All students were successfully enrolled.
        * Example json response:
            {
                '123': 'enrolled',
                '456': 'pending',
                '789': 'canceled,
                'abc': 'suspended'
            }
      * 207: MULTI-STATUS - Some students were successfully enrolled while others were not.
      Details are included in the JSON response data.
        * Example json response:
            {
                '123': 'duplicated',
                '456': 'conflict',
                '789': 'invalid-status,
                'abc': 'suspended'
            }
      * 403: FORBIDDEN - The requesting user lacks access to enroll students in the given program.
      * 404: NOT FOUND - The requested program does not exist.
      * 413: PAYLOAD TOO LARGE - Over 25 students supplied
      * 422: Unprocesable Entity - None of the students were successfully listed.

    Update
    ==========
    Path: `/api/program_enrollments/v1/programs/{program_uuid}/enrollments/`
    Where the program_uuid will be the uuid for a program.

    Request body:
        * The request body will be a list of one or more students with their updated enrollment status:
            {
                'status': A choice of the following statuses: [
                    'enrolled',
                    'pending',
                    'canceled',
                    'suspended',
                    'ended',
                ],
                student_key: string representation of a learner in partner systems
            }
        Example:
            [
                {
                    "status": "enrolled",
                    "external_user_key": "123",
                },{
                    "status": "canceled",
                    "external_user_key": "456",
                },{
                    "status": "pending",
                    "external_user_key": "789",
                },{
                    "status": "suspended",
                    "external_user_key": "abc",
                },
            ]

    Returns:
      * Response Body: {<external_user_key>: <status>} with as many keys as there were in the request body
        * external_user_key - string representation of a learner in partner systems
        * status - the learner's registration status
            * success statuses:
                * 'enrolled'
                * 'pending'
                * 'canceled'
                * 'suspended'
                * 'ended'
            * failure statuses:
                * 'duplicated' - the request body listed the same learner twice
                * 'conflict' - there is an existing enrollment for that learner, curriculum and program combo
                * 'invalid-status' - a status other than 'enrolled', 'pending', 'canceled', 'suspended', 'ended'
                                     was entered
                * 'not-in-program' - the user is not in the program and cannot be updated
      * 200: OK - All students were successfully enrolled.
        * Example json response:
            {
                '123': 'enrolled',
                '456': 'pending',
                '789': 'canceled,
                'abc': 'suspended'
            }
      * 207: MULTI-STATUS - Some students were successfully enrolled while others were not.
      Details are included in the JSON response data.
        * Example json response:
            {
                '123': 'duplicated',
                '456': 'not-in-program',
                '789': 'invalid-status,
                'abc': 'suspended'
            }
      * 403: FORBIDDEN - The requesting user lacks access to enroll students in the given program.
      * 404: NOT FOUND - The requested program does not exist.
      * 413: PAYLOAD TOO LARGE - Over 25 students supplied
      * 422: Unprocesable Entity - None of the students were successfully updated.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)
    pagination_class = ProgramEnrollmentPagination

    # Overridden from `EnrollmentWriteMixin`
    serializer_class_by_write_method = {
        'POST': ProgramEnrollmentCreateRequestSerializer,
        'PATCH': ProgramEnrollmentUpdateRequestSerializer,
        'PUT': ProgramEnrollmentCreateRequestSerializer,
    }
    ok_write_statuses = ProgramOperationStatuses.__OK__

    @verify_program_exists
    def get(self, request, program_uuid=None):  # lint-amnesty, pylint: disable=unused-argument
        """ Defines the GET list endpoint for ProgramEnrollment objects. """
        enrollments = fetch_program_enrollments(
            self.program_uuid
        ).using(read_replica_or_default())
        paginated_enrollments = self.paginate_queryset(enrollments)
        serializer = ProgramEnrollmentSerializer(paginated_enrollments, many=True)
        return self.get_paginated_response(serializer.data)

    @verify_program_exists
    def post(self, request, program_uuid=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Create program enrollments for a list of learners
        """
        return self.handle_write_request()

    @verify_program_exists
    def patch(self, request, program_uuid=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Update program enrollments for a list of learners
        """
        return self.handle_write_request()

    @verify_program_exists
    def put(self, request, program_uuid=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Create/update program enrollments for a list of learners
        """
        return self.handle_write_request()

    def perform_enrollment_write(self, enrollment_requests, create, update):
        """
        Perform the program enrollment write operation.
        Overridden from `EnrollmentWriteMixin`.

        Arguments:
            enrollment_requests: list[dict]
            create (bool)
            update (bool)

        Returns: dict[str: str]
            Map from external keys to enrollment write statuses.
        """
        return write_program_enrollments(
            self.program_uuid, enrollment_requests, create=create, update=update
        )


class ProgramCourseEnrollmentsView(
        EnrollmentWriteMixin,
        DeveloperErrorViewMixin,
        ProgramCourseSpecificViewMixin,
        PaginatedAPIView,
):
    """
    A view for enrolling students in a course through a program,
    modifying program course enrollments, and listing program course
    enrollments.

    Path: ``/api/program_enrollments/v1/programs/{program_uuid}/courses/{course_id}/enrollments/``

    Accepts: [GET, POST, PATCH, PUT]

    For GET requests, the path can contain an optional `page_size?=N` query parameter.
    The default page size is 100.

    ------------------------------------------------------------------------------------
    POST, PATCH, PUT
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: Returns a map of students and their enrollment status.
        * 207: Not all students enrolled. Returns resulting enrollment status.
        * 401: User is not authenticated
        * 403: User lacks read access organization of specified program.
        * 404: Program does not exist, or course does not exist in program
        * 422: Invalid request, unable to enroll students.

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Contains a paginated set of program course enrollment data.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access for the given program/course.
        * 404: The requested program or course does not exist.

    **Response**

        In the case of a 200 response code, the response will include a paginated
        data set.  The `results` section of the response consists of a list of
        program course enrollment records, where each record contains the following keys:
          * student_key: The identifier of the student enrolled in the program and course.
          * status: The student's course enrollment status.
          * account_exists: A boolean indicating if the student has created an edx-platform user account.
          * curriculum_uuid: The curriculum UUID of the enrollment record for the (student, program).

    **Example**

        {
            "next": null,
            "previous": "http://testserver.com/api/program_enrollments/v1/programs/
                         {program_uuid}/courses/{course_id}/enrollments/?curor=abcd",
            "results": [
                {
                    "student_key": "user-0", "status": "inactive",
                    "account_exists": False, "curriculum_uuid": "00000000-1111-2222-3333-444444444444"
                },
                {
                    "student_key": "user-1", "status": "inactive",
                    "account_exists": False, "curriculum_uuid": "00000001-1111-2222-3333-444444444444"
                },
                {
                    "student_key": "user-2", "status": "active",
                    "account_exists": True, "curriculum_uuid": "00000002-1111-2222-3333-444444444444"
                },
                {
                    "student_key": "user-3", "status": "active",
                    "account_exists": True, "curriculum_uuid": "00000003-1111-2222-3333-444444444444"
                },
            ],
        }

    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)
    pagination_class = ProgramEnrollmentPagination

    # Overridden from `EnrollmentWriteMixin`
    serializer_class_by_write_method = {
        'POST': ProgramCourseEnrollmentRequestSerializer,
        'PATCH': ProgramCourseEnrollmentRequestSerializer,
        'PUT': ProgramCourseEnrollmentRequestSerializer,
    }
    ok_write_statuses = ProgramCourseOperationStatuses.__OK__

    @verify_course_exists_and_in_program
    def get(self, request, program_uuid=None, course_id=None):
        """
        Get a list of students enrolled in a course within a program.
        """
        enrollments = fetch_program_course_enrollments(
            program_uuid, course_id
        ).select_related(
            'program_enrollment'
        ).using(read_replica_or_default())
        paginated_enrollments = self.paginate_queryset(enrollments)
        serializer = ProgramCourseEnrollmentSerializer(paginated_enrollments, many=True)
        return self.get_paginated_response(serializer.data)

    @verify_course_exists_and_in_program
    def post(self, request, program_uuid=None, course_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Enroll a list of students in a course in a program
        """
        return self.handle_write_request()

    @verify_course_exists_and_in_program
    def patch(self, request, program_uuid=None, course_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Modify the program course enrollments of a list of learners
        """
        return self.handle_write_request()

    @verify_course_exists_and_in_program
    def put(self, request, program_uuid=None, course_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Create or Update the program course enrollments of a list of learners
        """
        return self.handle_write_request()

    def perform_enrollment_write(self, enrollment_requests, create, update):
        """
        Perform the program enrollment write operation.
        Overridden from `EnrollmentWriteMixin`.

        Arguments:
            enrollment_requests: list[dict]
            create (bool)
            update (bool)

        Returns: dict[str: str]
            Map from external keys to enrollment write statuses.
        """
        return write_program_course_enrollments(
            self.program_uuid,
            self.course_key,
            enrollment_requests,
            create=create,
            update=update,
        )


class ProgramCourseGradesView(
        DeveloperErrorViewMixin,
        ProgramCourseSpecificViewMixin,
        PaginatedAPIView,
):
    """
    A view for retrieving a paginated list of grades for all students enrolled
    in a given courserun through a given program.

    Path: ``/api/program_enrollments/v1/programs/{program_uuid}/courses/{course_id}/grades/``

    Accepts: [GET]

    For GET requests, the path can contain an optional `page_size?=N` query parameter.
    The default page size is 100.

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**
        * 200: OK - Contains a paginated set of program courserun grades.
        * 204: No Content - No grades to return
        * 207: Mixed result - Contains mixed list of program courserun grades
               and grade-fetching errors
        * 422: All failed - Contains list of grade-fetching errors
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access for the given program/course.
        * 404: The requested program or course does not exist.

    **Response**

        In the case of a 200/207/422 response code, the response will include a
        paginated data set.  The `results` section of the response consists of a
        list of grade records, where each successfully loaded record contains:
          * student_key: The identifier of the student enrolled in the program and course.
          * letter_grade: A letter grade as defined in grading policy
            (e.g. 'A' 'B' 'C' for 6.002x) or None.
          * passed: Boolean representing whether the course has been
            passed according to the course's grading policy.
          * percent: A float representing the overall grade for the course.
        and failed-to-load records contain:
          * student_key
          * error: error message from grades Exception

    **Example**

        207 Multi-Status
        {
            "next": null,
            "previous": "http://example.com/api/program_enrollments/v1/programs/
                         {program_uuid}/courses/{course_id}/grades/?cursor=abcd",
            "results": [;
                {
                    "student_key": "01709bffeae2807b6a7317",
                    "letter_grade": "Pass",
                    "percent": 0.95,
                    "passed": true
                },
                {
                    "student_key": "2cfe15e3380a52e7198237",
                    "error": "Timeout while calculating grade"
                },
                ...
            ],
        }
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)
    pagination_class = ProgramEnrollmentPagination

    @verify_course_exists_and_in_program
    def get(self, request, program_uuid=None, course_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Defines the GET list endpoint for ProgramCourseGrade objects.
        """
        grade_results = list(iter_program_course_grades(
            self.program_uuid, self.course_key, self.paginate_queryset
        ))
        serializer = ProgramCourseGradeSerializer(grade_results, many=True)
        response_code = self._calc_response_code(grade_results)
        return self.get_paginated_response(serializer.data, status_code=response_code)

    @staticmethod
    def _calc_response_code(grade_results):
        """
        Returns HTTP status code appropriate for list of results,
        which may be grades or errors.

        Arguments:
            enrollment_grade_results: list[BaseProgramCourseGrade]

        Returns: int
          * 200 for all success
          * 207 for mixed result
          * 422 for all failure
          * 204 for empty
        """
        if not grade_results:
            return status.HTTP_204_NO_CONTENT
        if all(result.is_error for result in grade_results):
            return status.HTTP_422_UNPROCESSABLE_ENTITY
        if any(result.is_error for result in grade_results):
            return status.HTTP_207_MULTI_STATUS
        return status.HTTP_200_OK


class UserProgramReadOnlyAccessView(DeveloperErrorViewMixin, PaginatedAPIView):
    """
    A view for checking the currently logged-in user's program read only access
    There are three major categories of users this API is differentiating. See the table below.

    --------------------------------------------------------------------------------------------
    | User Type        | API Returns                                                           |
    --------------------------------------------------------------------------------------------
    | edX staff        | All programs                                                          |
    --------------------------------------------------------------------------------------------
    | course staff     | All programs containing the courses of which the user is course staff |
    --------------------------------------------------------------------------------------------
    | learner          | All programs the learner is enrolled in                               |
    --------------------------------------------------------------------------------------------

    Path: `/api/program_enrollments/v1/programs/enrollments/`

    Returns:
      * 200: OK - Contains a list of all programs in which the user has read only acccess to.
      * 401: The requesting user is not authenticated.

    The list will be a list of objects with the following keys:
      * `uuid` - the identifier of the program in which the user has read only access to.
      * `slug` - the string from which a link to the corresponding program page can be constructed.

    Example:
    [
      {
        'uuid': '00000000-1111-2222-3333-444444444444',
        'slug': 'deadbeef'
      },
      {
        'uuid': '00000000-1111-2222-3333-444444444445',
        'slug': 'undead-cattle'
      }
    ]
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    DEFAULT_PROGRAM_TYPE = 'masters'

    def get(self, request):
        """
        How to respond to a GET request to this endpoint
        """

        request_user = request.user

        programs = []
        requested_program_type = normalize_program_type(request.GET.get('type', self.DEFAULT_PROGRAM_TYPE))

        if request_user.is_staff:
            programs = get_programs_by_type(request.site, requested_program_type)
        else:
            program_dict = {}
            # Check if the user is a course staff of any course which is a part of a program.
            for staff_program in self.get_programs_user_is_course_staff_for(request_user, requested_program_type):
                program_dict.setdefault(staff_program['uuid'], staff_program)

            # Now get the program enrollments for user purely as a learner add to the list
            for learner_program in self._get_enrolled_programs_from_model(request_user):
                program_dict.setdefault(learner_program['uuid'], learner_program)

            programs = list(program_dict.values())

        programs_in_which_user_has_access = [
            {'uuid': program['uuid'], 'slug': program['marketing_slug']}
            for program in programs
        ]

        return Response(programs_in_which_user_has_access, status.HTTP_200_OK)

    def _get_enrolled_programs_from_model(self, user):
        """
        Return the Program Enrollments linked to the learner within the data model.
        """
        program_enrollments = fetch_program_enrollments_by_student(
            user=user,
            program_enrollment_statuses=ProgramEnrollmentStatuses.__ACTIVE__,
        )
        uuids = [enrollment.program_uuid for enrollment in program_enrollments]
        return get_programs(uuids=uuids) or []

    def get_course_keys_user_is_staff_for(self, user):
        """
        Return all the course keys the user is course instructor or course staff role for
        """
        # Get all the courses of which the user is course staff for. If None, return false
        def filter_ccx(course_access):
            """ CCXs cannot be edited in Studio and should not be filtered """
            return not isinstance(course_access.course_id, CCXLocator)

        instructor_courses = UserBasedRole(user, CourseInstructorRole.ROLE).courses_with_role()
        staff_courses = UserBasedRole(user, CourseStaffRole.ROLE).courses_with_role()
        all_courses = list(filter(filter_ccx, instructor_courses | staff_courses))
        course_keys = {}
        for course_access in all_courses:
            if course_access.course_id is not None:
                course_keys[course_access.course_id] = course_access.course_id

        return list(course_keys.values())

    def get_programs_user_is_course_staff_for(self, user, program_type_filter):
        """
        Return a list of programs the user is course staff for.
        This function would take a list of course runs the user is staff of, and then
        try to get the Masters program associated with each course_runs.
        """
        program_dict = {}
        for course_key in self.get_course_keys_user_is_staff_for(user):
            course_run_programs = get_programs(course=course_key)
            for course_run_program in course_run_programs:
                if course_run_program and course_run_program.get('type').lower() == program_type_filter:
                    program_dict[course_run_program['uuid']] = course_run_program

        return program_dict.values()


class UserProgramCourseEnrollmentView(
        DeveloperErrorViewMixin,
        UserProgramSpecificViewMixin,
        PaginatedAPIView,
):
    """
    A view for getting data associated with a user's course enrollments
    as part of a program enrollment.

    For full documentation, see the `program_enrollments` section of
    http://$LMS_BASE_URL/api-docs/.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseRunOverviewSerializer
    pagination_class = UserProgramCourseEnrollmentPagination

    @schema(
        parameters=[
            path_parameter('username', str, description=(
                'The username of the user for which enrollment overviews will be fetched. '
                'For now, this must be the requesting user; otherwise, 403 will be returned. '
                'In the future, global staff users may be able to supply other usernames.'
            )),
            path_parameter('program_uuid', str, description=(
                'UUID of a program. '
                'Enrollments will be returned for course runs in this program.'
            )),
            query_parameter('page_size', int, description=(
                'Number of results to return per page. '
                'Defaults to 10. Maximum is 25.'
            )),
        ],
        responses={
            200: cursor_paginate_serializer(CourseRunOverviewSerializer),
            401: 'The requester is not authenticated.',
            403: (
                'The requester cannot access the specified program and/or '
                'the requester may not retrieve this data for the specified user.'
            ),
            404: 'The requested program does not exist.'
        },
    )
    @verify_program_exists
    @verify_user_enrolled_in_program
    def get(self, request, username, program_uuid):  # lint-amnesty, pylint: disable=unused-argument
        """
        Get an overview of each of a user's course enrollments associated with a program.

        This endpoint exists to get an overview of each course-run enrollment
        that a user has for course-runs within a given program.
        Fields included are the title, upcoming due dates, etc.
        This API endpoint is intended for use with the
        [Program Learner Portal MFE](https://github.com/openedx/frontend-app-learner-portal-programs).

        It is important to note that the set of enrollments that this endpoint returns
        is different than a user's set of *program-course-run enrollments*.
        Specifically, this endpoint may include course runs that are *within*
        the specified program but were not *enrolled in* via the specified program.

        **Example Response:**
        ```json
        {
            "next": null,
            "previous": null,
            "results": [
                {
                    "course_run_id": "edX+AnimalsX+Aardvarks",
                    "display_name": "Astonishing Aardvarks",
                    "course_run_url": "https://courses.edx.org/courses/course-v1:edX+AnimalsX+Aardvarks/course/",
                    "start_date": "2017-02-05T05:00:00Z",
                    "end_date": "2018-02-05T05:00:00Z",
                    "course_run_status": "completed"
                    "emails_enabled": true,
                    "due_dates": [
                        {
                            "name": "Introduction: What even is an aardvark?",
                            "url": "https://courses.edx.org/courses/course-v1:edX+AnimalsX+Aardvarks/jump_to/
                                  block-v1:edX+AnimalsX+Aardvarks+type@chapter+block@1414ffd5143b4b508f739b563ab468b7",
                            "date": "2017-05-01T05:00:00Z"
                        },
                        {
                            "name": "Quiz: Aardvark or Anteater?",
                            "url": "https://courses.edx.org/courses/course-v1:edX+AnimalsX+Aardvarks/jump_to/
                                    block-v1:edX+AnimalsX+Aardvarks+type@sequential+block@edx_introduction",
                            "date": "2017-03-05T00:00:00Z"
                        }
                    ],
                    "micromasters_title": "Animals",
                    "certificate_download_url": "https://courses.edx.org/certificates/123"
                },
                {
                    "course_run_id": "edX+AnimalsX+Baboons",
                    "display_name": "Breathtaking Baboons",
                    "course_run_url": "https://courses.edx.org/courses/course-v1:edX+AnimalsX+Baboons/course/",
                    "start_date": "2018-02-05T05:00:00Z",
                    "end_date": null,
                    "course_run_status": "in_progress"
                    "emails_enabled": false,
                    "due_dates": [],
                    "micromasters_title": "Animals",
                    "certificate_download_url": "https://courses.edx.org/certificates/123",
                    "resume_course_run_url": "https://courses.edx.org/courses/course-v1:edX+AnimalsX+Baboons/jump_to/
                                               block-v1:edX+AnimalsX+Baboons+type@sequential+block@edx_introduction"
                }
            ]
        }
        ```
        """
        if request.user.username != username:
            # TODO: Should this be case-insensitive?
            raise PermissionDenied()
        enrollments = get_enrollments_for_courses_in_program(
            self.request.user, self.program
        )
        paginated_enrollments = self.paginate_queryset(enrollments)
        paginated_enrollment_overviews = get_enrollment_overviews(
            user=self.request.user,
            program=self.program,
            enrollments=paginated_enrollments,
            request=self.request,
        )
        serializer = CourseRunOverviewSerializer(paginated_enrollment_overviews, many=True)
        return self.get_paginated_response(serializer.data)


class ProgramCourseEnrollmentOverviewView(
        DeveloperErrorViewMixin,
        UserProgramSpecificViewMixin,
        RetrieveAPIView,
):
    """
    A view for getting data associated with a user's course enrollments
    as part of a program enrollment.

    Path: ``/api/program_enrollments/v1/programs/{program_uuid}/overview/``

    DEPRECATED:
    This is deprecated in favor of the new UserProgramCourseEnrollmentView,
    which is paginated.
    It will be removed in a follow-up to MST-126 after the Programs Learner Portal
    has been updated to use UserProgramCourseEnrollmentView.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseRunOverviewListSerializer

    @verify_program_exists
    @verify_user_enrolled_in_program
    def get_object(self):
        """
        Defines the GET endpoint for overviews of course enrollments
        for a user as part of a program.
        """
        enrollments = get_enrollments_for_courses_in_program(
            self.request.user, self.program
        )
        enrollment_overviews = get_enrollment_overviews(
            user=self.request.user,
            program=self.program,
            enrollments=enrollments,
            request=self.request,
        )
        return {'course_runs': enrollment_overviews}


class EnrollmentDataResetView(APIView):
    """
    Resets enrollments and users for a given organization and set of programs.
    Note, this will remove ALL users from the input organization.

    Path: ``/api/program_enrollments/v1/integration-reset/``

    Accepts: [POST]

    ------------------------------------------------------------------------------------
    POST
    ------------------------------------------------------------------------------------

    **Returns**
        * 200: OK - Enrollments and users sucessfully deleted
        * 400: Bad Requeset - Program does not match the requested organization
        * 401: Unauthorized - The requesting user is not authenticated.
        * 404: Not Found - A requested program does not exist.

    **Response**
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)

    @transaction.atomic
    def post(self, request):
        """
        Reset enrollment and user data for organization
        """
        if not settings.FEATURES.get(ENABLE_ENROLLMENT_RESET_FLAG):
            return Response('reset not enabled on this environment', status.HTTP_501_NOT_IMPLEMENTED)

        try:
            org_key = request.data['organization']
        except KeyError:
            return Response("missing required body content 'organization'", status.HTTP_400_BAD_REQUEST)

        try:
            organization = Organization.objects.get(short_name=org_key)
        except Organization.DoesNotExist:
            return Response(f'organization {org_key} not found', status.HTTP_404_NOT_FOUND)

        providers = []
        try:
            providers = get_saml_providers_for_organization(organization)
        except ProviderDoesNotExistException:
            pass

        for provider in providers:
            idp_slug = get_provider_slug(provider)
            call_command('remove_social_auth_users', idp_slug, force=True)

        programs = get_programs_for_organization(organization=organization.short_name)
        if programs:
            call_command('reset_enrollment_data', ','.join(programs), force=True)

        return Response('success')
