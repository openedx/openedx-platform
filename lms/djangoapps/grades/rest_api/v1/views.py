""" API v0 views. """


import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from typing import List  # noqa: UP035

from django.core.exceptions import ValidationError  # pylint: disable=wrong-import-order
from django.db.models import Q
from edx_rest_framework_extensions import permissions
from edx_rest_framework_extensions.auth.bearer.authentication import BearerAuthentication
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from common.djangoapps.student.models.course_enrollment import CourseEnrollment
from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course
from lms.djangoapps.courseware.models import BaseStudentModuleHistory, StudentModule
from lms.djangoapps.grades.api import CourseGradeFactory, clear_prefetched_course_grades, prefetch_course_grades
from lms.djangoapps.grades.rest_api.serializers import GradingPolicySerializer
from lms.djangoapps.grades.rest_api.v1.utils import CourseEnrollmentPagination, GradeViewMixin
from openedx.core.djangoapps.enrollments.forms import CourseEnrollmentsApiListForm
from openedx.core.djangoapps.enrollments.views import EnrollmentUserThrottle
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import PaginatedAPIView, get_course_key, verify_course_exists
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@contextmanager
def bulk_course_grade_context(course_key, users):
    """
    Prefetches grades for the given users in the given course
    within a context, storing in a RequestCache and deleting
    on context exit.
    """
    prefetch_course_grades(course_key, users)
    try:
        yield
    finally:
        clear_prefetched_course_grades(course_key)


class CourseGradesView(GradeViewMixin, PaginatedAPIView):
    """
    **Use Case**
        * Get course grades of all users who are enrolled in a course.
        The currently logged-in user may request all enrolled user's grades information
        if they are allowed.
    **Example Request**
        GET /api/grades/v1/courses/{course_id}/                              - Get grades for all users in course
        GET /api/grades/v1/courses/{course_id}/?username={username}          - Get grades for specific user in course
        GET /api/grades/v1/courses/?course_id={course_id}                    - Get grades for all users in course
        GET /api/grades/v1/courses/?course_id={course_id}&username={username}- Get grades for specific user in course
    **GET Parameters**
        A GET request may include the following parameters.
        * course_id: (required) A string representation of a Course ID.
        * username:  (optional) A string representation of a user's username.
    **GET Response Values**
        If the request for information about the course grade
        is successful, an HTTP 200 "OK" response is returned.
        The HTTP 200 response has the following values.
        * username: A string representation of a user's username passed in the request.
        * email: A string representation of a user's email.
        * course_id: A string representation of a Course ID.
        * passed: Boolean representing whether the course has been
                  passed according to the course's grading policy.
        * percent: A float representing the overall grade for the course
        * letter_grade: A letter grade as defined in grading policy (e.g. 'A' 'B' 'C' for 6.002x) or None
    **Example GET Response**
        [{
            "username": "bob",
            "email": "bob@example.com",
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "passed": false,
            "percent": 0.03,
            "letter_grade": null,
        },
        {
            "username": "fred",
            "email": "fred@example.com",
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "passed": true,
            "percent": 0.83,
            "letter_grade": "B",
        },
        {
            "username": "kate",
            "email": "kate@example.com",
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "passed": false,
            "percent": 0.19,
            "letter_grade": null,
        }]
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)

    pagination_class = CourseEnrollmentPagination

    required_scopes = ['grades:read']

    @verify_course_exists("Requested grade for unknown course {course}")
    def get(self, request, course_id=None):
        """
        Gets a course progress status.
        Args:
            request (Request): Django request object.
            course_id (string): URI element specifying the course location.
                                Can also be passed as a GET parameter instead.
        Return:
            A JSON serialized representation of the requesting user's current grade status.
        """
        username = request.GET.get('username')

        course_key = get_course_key(request, course_id)

        if username:
            # If there is a username passed, get grade for a single user
            with self._get_user_or_raise(request, course_key) as grade_user:
                return self._get_single_user_grade(grade_user, course_key)
        else:
            # If no username passed, get paginated list of grades for all users in course
            return self._get_user_grades(course_key)

    def _get_user_grades(self, course_key):
        """
        Get paginated grades for users in a course.
        Args:
            course_key (CourseLocator): The course to retrieve user grades for.

        Returns:
            A serializable list of grade responses
        """
        user_grades = []
        users = self._paginate_users(course_key)

        with bulk_course_grade_context(course_key, users):
            for user, course_grade, exc in CourseGradeFactory().iter(users, course_key=course_key):
                if not exc:
                    user_grades.append(self._serialize_user_grade(user, course_key, course_grade))

        return self.get_paginated_response(user_grades)


class CourseGradingPolicy(GradeViewMixin, ListAPIView):
    """
    **Use Case**

        Get the course grading policy.

    **Example requests**:

        GET /api/grades/v1/policy/courses/{course_id}/

    **Response Values**

        * assignment_type: The type of the assignment, as configured by course
          staff. For example, course staff might make the assignment types Homework,
          Quiz, and Exam.

        * count: The number of assignments of the type.

        * dropped: Number of assignments of the type that are dropped.

        * weight: The weight, or effect, of the assignment type on the learner's
          final grade.
    """
    allow_empty = False

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    def _get_course(self, request, course_id):
        """
        Returns the course after parsing the id, checking access, and checking existence.
        """
        try:
            course_key = get_course_key(request, course_id)
        except InvalidKeyError:
            raise self.api_error(  # pylint: disable=raise-missing-from  # noqa: B904
                status_code=status.HTTP_400_BAD_REQUEST,
                developer_message='The provided course key cannot be parsed.',
                error_code='invalid_course_key'
            )

        if not has_access(request.user, 'staff', course_key):
            raise self.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='The course does not exist.',
                error_code='user_or_course_does_not_exist',
            )

        course = modulestore().get_course(course_key, depth=0)
        if not course:
            raise self.api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                developer_message='The course does not exist.',
                error_code='user_or_course_does_not_exist',
            )
        return course

    def get(self, request, course_id, *args, **kwargs):  # pylint: disable=arguments-differ
        course = self._get_course(request, course_id)
        return Response(GradingPolicySerializer(course.raw_grader, many=True).data)


class SectionGradesBreakdown(GradeViewMixin, PaginatedAPIView):
    """ Section grades breakdown gives out the overall grade for a user in a course
        accompanied by grades for each section of the course for the user.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthentication,
        SessionAuthentication,
    )
    permission_classes = (permissions.IsStaff,)
    pagination_class = CourseEnrollmentPagination

    def get(self, request):  # pylint: disable=arguments-differ
        """
        **Use Cases**

            Get a list of all grades for all sections, optionally filtered by a course ID or list of usernames.

        **Example Requests**

            GET /api/grades/v1/section_grades_breakdown

            GET /api/grades/v1/section_grades_breakdown?course_id={course_id}

            GET /api/grades/v1/section_grades_breakdown?username={username},{username},{username}

            GET /api/grades/v1/section_grades_breakdown?course_id={course_id}&username={username}

        **Query Parameters for GET**

            * course_id: Filters the result to course grade status for the course corresponding to the
                given course ID. The value must be URL encoded. Optional.

            * username: List of comma-separated usernames. Filters the result to the course grade status
                of the given users. Optional.

            * page_size: Number of results to return per page. Optional.

        **Response Values**

            If the request for information about the course grade status is successful, an HTTP 200 "OK" response
            is returned.

            The HTTP 200 response has the following values.

            * results: A list of the course grading status matching the request.

                * course_id: Course ID of the course in the course grading status.

                * user: Username of the user in the course enrollment.

                * passed: Boolean flag for user passing the course.

                * current_grade: An integer representing the current grade of the course.

                * section_breakdown: A summary of each course section's grade.

                A dictionary in the section_breakdown list has the following keys:
                    * percent: A float percentage for the section.
                    * label: A short string identifying the section. Preferably fixed-length. E.g. "HW  3".
                    * detail: A string explanation of the score. E.g. "Homework 1 - Ohms Law - 83% (5/6)"
                    * category: A string identifying the category.
                    * prominent: A boolean value indicating that this section should be displayed as more prominent
                        than other items.

            * next: The URL to the next page of results, or null if this is the
                last page.

            * previous: The URL to the next page of results, or null if this
                is the first page.

            If the user is not logged in, a 401 error is returned.

            If the user is not global staff, a 403 error is returned.

            If the specified course_id is not valid or any of the specified usernames
            are not valid, a 400 error is returned.

            If the specified course_id does not correspond to a valid course or if all the specified
            usernames do not correspond to valid users, an HTTP 200 "OK" response is returned with an
            empty 'results' field.
        """
        course_grading_status = []
        username_filter = []

        form = CourseEnrollmentsApiListForm(self.request.query_params)
        if not form.is_valid():
            raise ValidationError(form.errors)
        usernames = form.cleaned_data.get('username')
        course_id = form.cleaned_data.get('course_id')
        if usernames:
            username_filter = [Q(user__username__in=usernames)]
        course_enrollments = self._paginate_course_enrollment(course_id, course_enrollment_filter=username_filter)
        enrolled_course_user_map = SectionGradesBreakdown._get_enrolled_course_user_map(course_enrollments)

        for course_key, users in enrolled_course_user_map.items():
            with bulk_course_grade_context(course_key, users):
                for user, course_grade, exc in CourseGradeFactory().iter(users, course_key=course_key):
                    if not exc:
                        course_grading_status.append(
                            SectionGradesBreakdown._serialize_section_grades(user, course_key, course_grade)
                        )
        return self.get_paginated_response(course_grading_status)

    @staticmethod
    def _get_enrolled_course_user_map(enrollments):
        """ Returns a map of courses with all the users enrolled in them.
        """
        enrolled_course_user_map = defaultdict(list)
        for enrollment in enrollments:
            enrolled_course_user_map[enrollment.course_id].append(enrollment.user)
        return enrolled_course_user_map

    @staticmethod
    def _serialize_section_grades(user, course_key, course_grade):
        """
        Convert the extracted information into a serialized structure.

        Returns a dictionary with the following information about the course & course grade.
            * course_id: Course id of the given course.
            * username: Username of the user on the platform.
            * passed: If the user passed the course or not.
            * current_grade: An integer representing the current grade of the course.
            * section_breakdown: A summary of each course section's grade.
        """
        summary = []
        for section in course_grade.summary.get('section_breakdown'):
            summary.append(section)
        course_grading_status = {
            'course_id': str(course_key),
            'username': user.username,
            'passed': course_grade.passed,
            'current_grade': int(course_grade.percent * 100),
            'section_breakdown': summary,
        }
        return course_grading_status


@can_disable_rate_limit
class SubmissionHistoryView(GradeViewMixin, PaginatedAPIView):
    """
    Submission history corresponding to ProblemBlocks present in the course.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthentication,
        SessionAuthentication,
    )
    permission_classes = (permissions.IsStaff,)
    throttle_classes = (EnrollmentUserThrottle,)
    pagination_class = CourseEnrollmentPagination

    def get(self, request, course_id=None):
        """
        Get submission history details. This submission history is related to only
        ProblemBlock and it doesn't support LegacyLibraryContentBlock or ContentLibraries
        as of now.

        **Usecases**:

            Users with GlobalStaff status can retrieve everyone's submission history.

        **Example Requests**:

            GET /api/grades/v1/submission_history/{course_id}
            GET /api/grades/v1/submission_history/{course_id}/?username={username}

        **Query Parameters for GET**

            * course_id: Course id to retrieve submission history.
            * username: Single username for which this view will retrieve the submission history details.

        **Response Values**:

            If there's an error while getting the submission history an empty response will
            be returned.
            The submission history response has the following attributes:

                * Results: A list of submission history:
                    * course_id: Course id
                    * course_name: Course name
                    * user: Username
                    * problems: List of problems
                        * location: problem location
                        * name: problem's display name
                        * submission_history: List of submission history
                            * state: State of submission.
                            * grade: Grade.
                            * max_grade: Maximum possible grade.
                        * data: problem's data.
        """
        data = []
        username_filter = []
        username = request.GET.get('username')
        try:
            course_id = get_course_key(request, course_id)
        except InvalidKeyError:
            raise self.api_error(  # pylint: disable=raise-missing-from  # noqa: B904
                status_code=status.HTTP_400_BAD_REQUEST,
                developer_message='The provided course key cannot be parsed.',
                error_code='invalid_course_key'
            )

        if username:
            username_filter = [Q(user__username=username)]
        course_enrollments = self._paginate_course_enrollment(course_id, course_enrollment_filter=username_filter)

        course_xblock_structure = SubmissionHistoryView._generate_course_structure(course_enrollments)
        for course_key, course_info in course_xblock_structure.items():
            course_data = SubmissionHistoryView._get_course_data(
                course_key,
                course_info.get('course_enrollments'),
                course_info.get('course'),
                course_info.get('blocks')
            )
            data.extend(course_data)
        return self.get_paginated_response(data)

    @staticmethod
    def _generate_course_structure(enrollments):
        """ Generate a map of course to course enrollment and problem
            xblocks for each of the course.
        """
        course_enrollment_id_map = defaultdict(list)
        course_xblock_structure = {}
        for course_enrollment in enrollments:
            course_enrollment_id_map[str(course_enrollment.course_id)].append(course_enrollment)
        for course_key, course_enrollments in course_enrollment_id_map.items():
            course_id = CourseKey.from_string(course_key)
            course = get_course(course_id, depth=4)
            course_xblock_structure[course_key] = {
                'course_enrollments': course_enrollments,
                'blocks': SubmissionHistoryView.get_problem_blocks(course),
                'course': course
            }
        return course_xblock_structure

    @staticmethod
    def get_problem_blocks(course):
        """ Get a list of problem xblock for the course.
            This doesn't support LegacyLibraryContentBlock or ContentLibraries
            as of now
        """
        blocks = []
        for section in course.get_children():
            for subsection in section.get_children():
                for vertical in subsection.get_children():
                    for block in vertical.get_children():
                        if block.category == 'problem' and getattr(block, 'has_score', False):
                            blocks.append(block)
        return blocks

    @staticmethod
    def _get_course_data(course_key: str, course_enrollments: List[CourseEnrollment], course, blocks):  # noqa: UP006
        """
        Extracts the fields needed from course enrollments and course block.
        This function maps the ProblemBlock data of the course to it's enrollment.

        Params:
        --------
        course: course
        block: XBlock to analyze.
        """
        course_grouped_data = []
        for course_enrollment in course_enrollments:
            course_data = {
                'course_id': course_key,
                'course_name': course.display_name_with_default,
                'user': course_enrollment.user.username,
                'problems': []
            }
            for block in blocks:
                problem_data = SubmissionHistoryView._get_problem_data(course_enrollment, block)
                if problem_data["submission_history"]:
                    course_data['problems'].append(problem_data)
            course_grouped_data.append(course_data)
        return course_grouped_data

    @staticmethod
    def _get_problem_data(course_enrollment: CourseEnrollment, block):
        """
        Get problem data from a course enrollment.

        Args:
        -----
        block: XBlock to analyze.
        """
        problem_data = {
            'location': str(block.scope_ids.usage_id),
            'name': block.display_name,
            'submission_history': [],
            'data': block.data
        }
        csm = StudentModule.objects.filter(
            module_state_key=block.location,
            student=course_enrollment.user,
            course_id=course_enrollment.course_id
        )

        scores = BaseStudentModuleHistory.get_history(csm)
        for score in scores:
            state = score.state
            if state is not None:
                state = json.loads(state)

            history_data = {
                'state': state,
                'grade': score.grade,
                'max_grade': score.max_grade
            }
            problem_data['submission_history'].append(history_data)

        return problem_data
