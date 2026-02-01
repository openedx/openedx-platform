"""
Defines an endpoint for gradebook data related to a course.
"""


import logging
from collections import namedtuple
from contextlib import contextmanager
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Case, Exists, F, OuterRef, Q, When
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.auth import has_course_author_access, is_ccx_course
from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment, CourseMode
from common.djangoapps.student.roles import BulkRoleCache, CourseInstructorRole, CourseStaffRole
from common.djangoapps.track.event_transaction_utils import (
    create_new_event_transaction_id,
    get_event_transaction_id,
    get_event_transaction_type,
    set_event_transaction_type
)
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.grades.api import CourseGradeFactory, clear_prefetched_course_and_subsection_grades
from lms.djangoapps.grades.api import constants as grades_constants
from lms.djangoapps.grades.api import context as grades_context
from lms.djangoapps.grades.api import events as grades_events
from lms.djangoapps.grades.api import gradebook_bulk_management_enabled
from lms.djangoapps.grades.api import is_writable_gradebook_enabled, prefetch_course_and_subsection_grades
from lms.djangoapps.grades.course_data import CourseData
from lms.djangoapps.grades.grade_utils import are_grades_frozen
# TODO these imports break abstraction of the core Grades layer. This code needs
# to be refactored so Gradebook views only access public Grades APIs.
from lms.djangoapps.grades.models import (
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride
)
from lms.djangoapps.grades.rest_api.serializers import (
    StudentGradebookEntrySerializer,
    SubsectionGradeResponseSerializer
)
from lms.djangoapps.grades.rest_api.v1.utils import USER_MODEL, CourseEnrollmentPagination, GradeViewMixin
from lms.djangoapps.grades.subsection_grade import CreateSubsectionGrade
from lms.djangoapps.grades.subsection_grade_factory import SubsectionGradeFactory
from lms.djangoapps.grades.tasks import recalculate_subsection_grade_v3
from lms.djangoapps.program_enrollments.api import get_external_key_by_user_and_course
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.enrollments.api import get_course_enrollment_details
from openedx.core.djangoapps.util.forms import to_bool
from openedx.core.lib.api.view_utils import (
    DeveloperErrorViewMixin,
    PaginatedAPIView,
    get_course_key,
    verify_course_exists,
    view_auth_classes
)
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.util.misc import get_default_short_labeler  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@contextmanager
def bulk_gradebook_view_context(course_key, users):
    """
    Prefetches all course and subsection grades in the given course for the given
    list of users, also, fetch all the score relavant data,
    storing the result in a RequestCache and deleting grades on context exit.
    """
    prefetch_course_and_subsection_grades(course_key, users)
    CourseEnrollment.bulk_fetch_enrollment_states(users, course_key)
    cohorts.bulk_cache_cohorts(course_key, users)
    BulkRoleCache.prefetch(users)
    try:
        yield
    finally:
        clear_prefetched_course_and_subsection_grades(course_key)


def verify_writable_gradebook_enabled(view_func):
    """
    A decorator to wrap a view function that takes `course_key` as a parameter.

    Raises:
        A 403 API error if the writable gradebook feature is not enabled for the given course.
    """
    @wraps(view_func)
    def wrapped_function(self, request, **kwargs):
        """
        Wraps the given view function.
        """
        course_key = get_course_key(request, kwargs.get('course_id'))
        if not is_writable_gradebook_enabled(course_key):
            raise self.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='The writable gradebook feature is not enabled for this course.',
                error_code='feature_not_enabled'
            )
        return view_func(self, request, **kwargs)
    return wrapped_function


@view_auth_classes()
class BaseCourseView(DeveloperErrorViewMixin, GenericAPIView):
    """
    A base class for course info APIs.
    TODO: https://openedx.atlassian.net/browse/EDUCATOR-3755
    This whole thing is duplicated from cms/djangoapps/contentstore
    """
    @contextmanager
    def get_course(self, request, course_key):
        """
        Context manager that yields a course, given a request and course_key.
        """
        store = modulestore()
        with store.bulk_operations(course_key):
            course = store.get_course(course_key, depth=self._required_course_depth(request))
            yield course

    @staticmethod
    def _required_course_depth(request):
        """
        Returns how far deep we need to go into the course tree to
        get all of the information required.  Will use entire tree if the request's
        `all` param is truthy, otherwise goes to depth of 2 (subsections).
        """
        all_requested = get_bool_param(request, 'all', False)
        if all_requested:
            return None
        return 2

    @classmethod
    @request_cached()
    def _get_visible_subsections(cls, course):
        """
        Returns a list of all visible subsections for a course.
        """
        _, visible_sections = cls._get_sections(course)
        visible_subsections = []
        for section in visible_sections:
            visible_subsections.extend(cls._get_visible_children(section))
        return visible_subsections

    @classmethod
    @request_cached()
    def _get_sections(cls, course):
        """
        Returns all sections in the course.
        """
        return cls._get_all_children(course)

    @classmethod
    def _get_all_children(cls, parent):
        """
        Returns all child nodes of the given parent.
        """
        store = modulestore()
        children = [store.get_item(child_usage_key) for child_usage_key in cls._get_children(parent)]
        visible_children = [
            c for c in children
            if not c.visible_to_staff_only and not c.hide_from_toc
        ]
        return children, visible_children

    @classmethod
    def _get_visible_children(cls, parent):
        """
        Returns only the visible children of the given parent.
        """
        _, visible_chidren = cls._get_all_children(parent)
        return visible_chidren

    @classmethod
    def _get_children(cls, parent):
        """
        Returns the value of the 'children' attribute of a node.
        """
        if not hasattr(parent, 'children'):
            return []
        else:
            return parent.children


def get_bool_param(request, param_name, default):
    """
    Given a request, parameter name, and default value, returns
    either a boolean value or the default.
    """
    param_value = request.query_params.get(param_name, None)
    bool_value = to_bool(param_value)
    if bool_value is None:
        return default
    else:
        return bool_value


def course_author_access_required(view):
    """
    Ensure the user making the API request has course author access to the given course.

    This decorator parses the course_id parameter, checks course access, and passes
    the parsed course_key to the view as a parameter. It will raise a
    403 error if the user does not have author access.

    Usage::
        @course_author_access_required
        def my_view(request, course_key):
            # Some functionality ...
    """
    def _wrapper_view(self, request, course_id, *args, **kwargs):
        """
        Checks for course author access for the given course by the requesting user.
        Calls the view function if has access, otherwise raises a 403.
        """
        course_key = CourseKey.from_string(course_id)
        if not has_course_author_access(request.user, course_key):
            user_has_gradebook_access = any([
                CourseStaffRole(course_key).has_user(request.user),
                CourseInstructorRole(course_key).has_user(request.user),
            ])
            if is_ccx_course(course_key) and user_has_gradebook_access:
                return view(self, request, course_key, *args, **kwargs)

            raise DeveloperErrorViewMixin.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='The requesting user does not have course author permissions.',
                error_code='user_permissions',
            )
        return view(self, request, course_key, *args, **kwargs)
    return _wrapper_view


class CourseGradingView(BaseCourseView):
    """
    Returns information about assignments and assignment types for a course.
    **Example Requests**

        GET /api/grades/v1/gradebook/{course_id}/grading-info

    **GET Parameters**

        A GET request may include the following parameters.

        * graded_only (boolean) - If true, only returns subsection data for graded subsections (defaults to False).

    **GET Response Values**

        The HTTP 200 response has the following values.

        * assignment_types - A dictionary keyed by the assignment type name with the following values:
            * min_count - The minimum number of required assignments of this type.
            * weight - The weight assigned to this assignment type for course grading.
            * type - The name of the assignment type.
            * drop_count - The maximum number of assignments of this type that can be dropped.
            * short_label - The short label prefix used for short labels of assignments of this type (e.g. 'HW').

        * subsections - A list of subsections contained in this course.
            * module_id - The string version of this subsection's location.
            * display_name - The display name of this subsection.
            * graded - Boolean indicating whether this subsection is graded (for at least one user in the course).
            * short_label - A short label for graded assignments (e.g. 'HW 01').
            * assignment_type - The assignment type of this subsection (for graded assignments only).

    """
    @course_author_access_required
    def get(self, request, course_key):
        """
        Returns grading information (which subsections are graded, assignment types) for
        the requested course.
        """
        graded_only = get_bool_param(request, 'graded_only', False)

        with self.get_course(request, course_key) as course:
            results = {
                'grade_cutoffs': course.grade_cutoffs,
                'assignment_types': self._get_assignment_types(course),
                'subsections': self._get_subsections(course, graded_only),
                'grades_frozen': are_grades_frozen(course_key),
                'can_see_bulk_management': self.can_see_bulk_management(course_key),
            }
            return Response(results)

    def can_see_bulk_management(self, course_key):
        """
        Whether or not to show bulk management for this course. Currently, if a course has a
        master's track or is enabled with the grades.bulk_management course waffle flag.
        """
        course_modes = get_course_enrollment_details(str(course_key), include_expired=True).get('course_modes', [])
        course_has_masters_track = any((course_mode['slug'] == CourseMode.MASTERS for course_mode in course_modes))
        return course_has_masters_track or gradebook_bulk_management_enabled(course_key)

    def _get_assignment_types(self, course):
        """
        Helper function that returns a serialized dict of assignment types
        for the given course.
        Args:
            course - A course object.
        """
        serialized_grading_policies = {}
        for grader, assignment_type, weight in course.grader.subgraders:
            serialized_grading_policies[assignment_type] = {
                'type': assignment_type,
                'short_label': grader.short_label,
                'min_count': grader.min_count,
                'drop_count': grader.drop_count,
                'weight': weight,
            }
        return serialized_grading_policies

    def _get_subsections(self, course, graded_only=False):
        """
        Helper function that returns a list of subsections contained in the given course.
        Args:
            course - A course object.
            graded_only - If true, returns only graded subsections (defaults to False).
        """
        subsections = []
        short_labeler = get_default_short_labeler(course)
        for subsection in self._get_visible_subsections(course):
            if graded_only and not subsection.graded:
                continue

            short_label = None
            if subsection.graded:
                short_label = short_labeler(subsection.format)

            subsections.append({
                'assignment_type': subsection.format,
                'graded': subsection.graded,
                'short_label': short_label,
                'module_id': str(subsection.location),
                'display_name': subsection.display_name,
            })
        return subsections


@view_auth_classes()
class GradebookView(GradeViewMixin, PaginatedAPIView):
    """
    **Use Case**
        * Get course gradebook entries of a single user in a course,
        or of all users who are actively enrolled in a course.  The currently logged-in user may request
        all enrolled user's grades information if they are allowed.
    **Example Request**
        GET /api/grades/v1/gradebook/{course_id}/                       - Get gradebook entries for all users in course
        GET /api/grades/v1/gradebook/{course_id}/?username={username}   - Get grades for specific user in course
        GET /api/grades/v1/gradebook/{course_id}/?username={username}?history_record_limit={number}
            - Get grades for specific user in course, only show {number} latest records
        GET /api/grades/v1/gradebook/{course_id}/?user_contains={user_contains}
        GET /api/grades/v1/gradebook/{course_id}/?username_contains={username_contains}
        GET /api/grades/v1/gradebook/{course_id}/?cohort_id={cohort_id}
        GET /api/grades/v1/gradebook/{course_id}/?enrollment_mode={enrollment_mode}
    **GET Parameters**
        A GET request may include the following query parameters.
        * username:  (optional) A string representation of a user's username.
        * user_contains: (optional) A substring against which a case-insensitive substring filter will be performed
          on the USER_MODEL.username, or the USER_MODEL.email, or the PROGRAM_ENROLLMENT.external_user_key fields.
        * username_contains: (optional) A substring against which a case-insensitive substring filter will be performed
          on the USER_MODEL.username field.
        * cohort_id: (optional) The id of a cohort in this course.  If present, will return grades
          only for course enrollees who belong to that cohort.
        * enrollment_mode: (optional) The slug of an enrollment mode (e.g. "verified").  If present, will return grades
          only for course enrollees with the given enrollment mode.
    **GET Response Values**
        If the request for gradebook data is successful,
        an HTTP 200 "OK" response is returned.
        The HTTP 200 response for a single has the following values:
        * course_id: A string representation of a Course ID.
        * email: A string representation of a user's email.
        * user_id: The user's integer id.
        * username: A string representation of a user's username passed in the request.
        * full_name: A string representation of the user's full name.
        * passed: Boolean representing whether the course has been
                  passed according to the course's grading policy.
        * percent: A float representing the overall grade for the course
        * progress_page_url: A link to the user's progress page.
        * section_breakdown: A list of subsection grade details, as specified below.

        A response for all user's grades in the course is paginated, and contains "count", "next" and "previous"
        keys, along with the actual data contained in a "results" list.

        An HTTP 404 may be returned for the following reasons:
            * The requested course_key is invalid.
            * No course corresponding to the requested key exists.
            * No user corresponding to the requested username exists.
            * The requested user is not enrolled in the requested course.

        An HTTP 403 may be returned if the `writable_gradebook` feature is not
        enabled for this course.
    **Example GET Response**
        {
            "user_id": 9,
            "username": "staff",
            "percent": 0.36,
            "section_breakdown": [
                {
                    "are_grades_published": true,
                    "auto_grade": false,
                    "category": null,
                    "chapter_name": "Introduction",
                    "comment": "",
                    "detail": "",
                    "displayed_value": "0.00",
                    "grade_description": "(0.00/0.00)",
                    "is_ag": false,
                    "is_average": false,
                    "is_manually_graded": false,
                    "label": null,
                    "module_id": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@edx_introduction",
                    "percent": 0.0,
                    "score_earned": 0.0,
                    "score_possible": 0.0,
                    "section_block_id": "block-v1:edX+DemoX+Demo_Course+type@chapter+block@abcdefgh123",
                    "subsection_name": "Demo Course Overview"
                },
            ],
        }
    **Paginated GET response**
        When requesting gradebook entries for all users, the response is paginated and contains the following values:
        * next: The URL containing the next page of data.
        * previous: The URL containing the previous page of data.
        * results: A list of user gradebook entries, structured as above.
        * total_users_count: The total number of active users in the course.
        * filtered_users_count: The total number of active users that match
            the filter associated with the provided query parameters.

    Note: It's important that `GradeViewMixin` is the first inherited class here, so that
    self.api_error returns error responses as expected.
    """

    pagination_class = CourseEnrollmentPagination

    def _section_breakdown(self, course, graded_subsections, course_grade):
        """
        Given a course_grade and a list of graded subsections for a given course,
        returns a list of grade data broken down by subsection.

        Args:
            course: A CourseBlock object
            graded_subsections: A list of graded subsection objects in the given course.
            course_grade: A CourseGrade object.
        """
        breakdown = []
        default_labeler = get_default_short_labeler(course)

        for subsection in graded_subsections:
            subsection_grade = course_grade.subsection_grade(subsection.location)
            short_label = default_labeler(subsection_grade.format)

            attempted = False
            score_earned = 0
            score_possible = 0

            # For ZeroSubsectionGrades, we don't want to crawl the subsection's
            # subtree to find the problem scores specific to this user
            # (ZeroSubsectionGrade.attempted_graded is always False).
            # We've already fetched the whole course structure in a non-user-specific way
            # when creating `graded_subsections`.  Looking at the problem scores
            # specific to this user (the user in `course_grade.user`) would require
            # us to re-fetch the user-specific course structure from the modulestore,
            # which is a costly operation.  So we only drill into the `graded_total`
            # attribute if the user has attempted this graded subsection, or if there
            # has been a grade override applied.
            if subsection_grade.attempted_graded or subsection_grade.override:
                attempted = True
                score_earned = subsection_grade.graded_total.earned
                score_possible = subsection_grade.graded_total.possible

            # TODO: https://openedx.atlassian.net/browse/EDUCATOR-3559 -- Some fields should be renamed, others removed:
            # 'displayed_value' should maybe be 'description_percent'
            # 'grade_description' should be 'description_ratio'
            breakdown.append({
                'attempted': attempted,
                'category': subsection_grade.format,
                'label': short_label,
                'module_id': str(subsection_grade.location),
                'percent': subsection_grade.percent_graded,
                'score_earned': score_earned,
                'score_possible': score_possible,
                'subsection_name': subsection_grade.display_name,
            })
        return breakdown

    def _gradebook_entry(self, user, course, graded_subsections, course_grade):
        """
        Returns a dictionary of course- and subsection-level grade data for
        a given user in a given course.

        Args:
            user: A User object.
            course: A CourseBlock object.
            graded_subsections: A list of graded subsections in the given course.
            course_grade: A CourseGrade object.
        """
        user_entry = self._serialize_user_grade(user, course.id, course_grade)
        breakdown = self._section_breakdown(course, graded_subsections, course_grade)

        user_entry['section_breakdown'] = breakdown
        user_entry['progress_page_url'] = reverse(
            'student_progress',
            kwargs=dict(course_id=str(course.id), student_id=user.id)
        )
        user_entry['user_id'] = user.id

        def is_masters_student():
            # If this is a multiple-user lookup (didn't use the username param) we insert
            # user.enrollment_mode in _get_enrolled_users. If it is a single-user lookup
            # (did use username) then we'll need to look up the single user's enrollment mode
            if hasattr(user, 'enrollment_mode'):
                return user.enrollment_mode == CourseMode.MASTERS
            else:
                mode, _ = CourseEnrollment.enrollment_mode_for_user(user, str(course.id))
                return mode == CourseMode.MASTERS

        if is_masters_student():
            user_entry['full_name'] = user.profile.name

        external_user_key = get_external_key_by_user_and_course(user, course.id)
        if external_user_key:
            user_entry['external_user_key'] = external_user_key

        return user_entry

    @verify_course_exists("Requested grade for unknown course {course}")
    @verify_writable_gradebook_enabled
    @course_author_access_required
    def get(self, request, course_key):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Returns a gradebook entry/entries (i.e. both course and subsection-level grade data)
        for all users enrolled in a course, or a single user enrolled in a course
        if a `username` parameter is provided.

        Args:
            request: A Django request object.
            course_key: The edx course opaque key of a course object.
        """
        course = get_course_by_id(course_key, depth=None)

        # We fetch the entire course structure up-front, and use this when iterating
        # over users to determine their subsection grades.  We purposely avoid fetching
        # the user-specific course structure for each user, because that is very expensive.
        course_data = CourseData(user=None, course=course)
        graded_subsections = list(grades_context.graded_subsections_for_course(course_data.collected_structure))

        if request.GET.get('username'):
            with self._get_user_or_raise(request, course_key) as grade_user:
                course_grade = CourseGradeFactory().read(
                    grade_user,
                    course,
                    collected_block_structure=course_data.collected_structure
                )
            entry = self._gradebook_entry(grade_user, course, graded_subsections, course_grade)
            serializer = StudentGradebookEntrySerializer(entry)
            return Response(serializer.data)
        else:
            q_objects = []
            annotations = {}
            if request.GET.get('user_contains'):
                search_term = request.GET.get('user_contains')
                q_objects.append(
                    Q(user__username__icontains=search_term) |
                    Q(programcourseenrollment__program_enrollment__external_user_key__icontains=search_term) |
                    Q(user__email__icontains=search_term)
                )
            if request.GET.get('username_contains'):
                q_objects.append(Q(user__username__icontains=request.GET.get('username_contains')))
            if request.GET.get('cohort_id'):
                cohort = cohorts.get_cohort_by_id(course_key, request.GET.get('cohort_id'))
                if cohort:
                    q_objects.append(Q(user__in=cohort.users.all()))
                else:
                    q_objects.append(Q(user__in=[]))
            if request.GET.get('enrollment_mode'):
                q_objects.append(Q(mode=request.GET.get('enrollment_mode')))
            if request.GET.get('assignment') and (
                    request.GET.get('assignment_grade_max')
                    or request.GET.get('assignment_grade_min')):
                subqueryset = PersistentSubsectionGrade.objects.annotate(
                    effective_grade_percentage=Case(
                        When(override__isnull=False,
                             then=(
                                 F('override__earned_graded_override')
                                 / F('override__possible_graded_override')
                             ) * 100),
                        default=(F('earned_graded') / F('possible_graded')) * 100
                    )
                )
                grade_conditions = {
                    'effective_grade_percentage__range': (
                        request.GET.get('assignment_grade_min', 0),
                        request.GET.get('assignment_grade_max', 100)
                    )
                }
                annotations['selected_assignment_grade_in_range'] = Exists(
                    subqueryset.filter(
                        course_id=OuterRef('course'),
                        user_id=OuterRef('user'),
                        usage_key=UsageKey.from_string(request.GET.get('assignment')),
                        **grade_conditions
                    )
                )
                q_objects.append(Q(selected_assignment_grade_in_range=True))
            if request.GET.get('course_grade_min') or request.GET.get('course_grade_max'):
                grade_conditions = {}
                q_object = Q()
                course_grade_min = request.GET.get('course_grade_min')
                if course_grade_min:
                    course_grade_min = float(request.GET.get('course_grade_min')) / 100
                    grade_conditions['percent_grade__gte'] = course_grade_min

                if request.GET.get('course_grade_max'):
                    course_grade_max = float(request.GET.get('course_grade_max')) / 100
                    grade_conditions['percent_grade__lte'] = course_grade_max

                if not course_grade_min or course_grade_min == 0:
                    subquery_grade_absent = ~Exists(
                        PersistentCourseGrade.objects.filter(
                            course_id=OuterRef('course'),
                            user_id=OuterRef('user_id'),
                        )
                    )

                    annotations['course_grade_absent'] = subquery_grade_absent
                    q_object |= Q(course_grade_absent=True)

                subquery_grade_in_range = Exists(
                    PersistentCourseGrade.objects.filter(
                        course_id=OuterRef('course'),
                        user_id=OuterRef('user_id'),
                        **grade_conditions
                    )
                )
                annotations['course_grade_in_range'] = subquery_grade_in_range
                q_object |= Q(course_grade_in_range=True)

                q_objects.append(q_object)
            if request.GET.get('excluded_course_roles'):
                excluded_course_roles = request.GET.getlist('excluded_course_roles')
                course_access_role_filters = dict(
                    user=OuterRef('user'),
                    course_id=course_key,
                )
                if 'all' not in excluded_course_roles:
                    course_access_role_filters['role__in'] = excluded_course_roles
                annotations['has_excluded_role'] = Exists(
                    CourseAccessRole.objects.filter(**course_access_role_filters)
                )
                # TODO: In django 3.0+, we can directly filter on this 'exists' rather than annotating
                q_objects.append(Q(has_excluded_role=False))
            entries = []
            related_models = ['user']
            users = self._paginate_users(course_key, q_objects, related_models, annotations=annotations)

            users_counts = self._get_users_counts(course_key, q_objects, annotations=annotations)

            with bulk_gradebook_view_context(course_key, users):
                for user, course_grade, exc in CourseGradeFactory().iter(
                    users, course_key=course_key, collected_block_structure=course_data.collected_structure
                ):
                    if not exc:
                        entry = self._gradebook_entry(user, course, graded_subsections, course_grade)
                        entries.append(entry)

            serializer = StudentGradebookEntrySerializer(entries, many=True)
            return self.get_paginated_response(serializer.data, **users_counts)

    def _get_user_count(self, query_args, cache_time=3600, annotations=None):
        """
        Return the user count for the given query arguments to CourseEnrollment.

        caches the count for cache_time seconds.
        """
        queryset = CourseEnrollment.objects
        if annotations:
            queryset = queryset.annotate(**annotations)
        queryset = queryset.filter(*query_args)

        cache_key = 'usercount.%s' % queryset.query
        user_count = cache.get(cache_key, None)
        if user_count is None:
            user_count = queryset.count()
            cache.set(cache_key, user_count, cache_time)

        return user_count

    def _get_users_counts(self, course_key, course_enrollment_filters, annotations=None):
        """
        Return a dictionary containing data about the total number of users and total number
        of users matching a given filter in a given course.

        Arguments:
            course_key: the opaque key for the course
            course_enrollment_filters: a list of Q objects representing filters to be applied to CourseEnrollments
            annotations: Optional dict of fields to add to the queryset via annotation

        Returns:
            dict:
                total_users_count: the number of total active users in the course
                filtered_users_count: the number of active users in the course that match
                    the given course_enrollment_filters
        """

        filter_args = [
            Q(course_id=course_key) & Q(is_active=True)
        ]

        total_users_count = self._get_user_count(filter_args)

        filter_args.extend(course_enrollment_filters or [])

        # if course_enrollment_filters is empty, then the number of filtered users will equal the total number of users
        filtered_users_count = (
            total_users_count
            if not course_enrollment_filters
            else self._get_user_count(filter_args, annotations=annotations)
        )

        return {
            'total_users_count': total_users_count,
            'filtered_users_count': filtered_users_count,
        }


GradebookUpdateResponseItem = namedtuple('GradebookUpdateResponseItem', ['user_id', 'usage_id', 'success', 'reason'])


@view_auth_classes()
class GradebookBulkUpdateView(GradeViewMixin, PaginatedAPIView):
    """
    **Use Case**
        Creates `PersistentSubsectionGradeOverride` objects for multiple (user_id, usage_id)
        pairs in a given course, and invokes a Django signal to update subsection grades in
        an asynchronous celery task.

    **Example Request**
        POST /api/grades/v1/gradebook/{course_id}/bulk-update

    **POST Parameters**
        This endpoint does not accept any URL parameters.

    **Example POST Data**
        [
          {
            "user_id": 9,
            "usage_id": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@basic_questions",
            "grade": {
              "earned_all_override": 11,
              "possible_all_override": 11,
              "earned_graded_override": 11,
              "possible_graded_override": 11,
              "comment": "reason for override"
            }
          },
          {
            "user_id": 9,
            "usage_id": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@advanced_questions",
            "grade": {
              "earned_all_override": 10,
              "possible_all_override": 15,
              "earned_graded_override": 9,
              "possible_graded_override": 12,
              "comment": "reason for override"
            }
          }
        ]

    **POST Response Values**
        An HTTP 202 may be returned if a grade override was created for each of the requested (user_id, usage_id)
        pairs in the request data.
        An HTTP 403 may be returned if the `writable_gradebook` feature is not
        enabled for this course.
        An HTTP 404 may be returned for the following reasons:
            * The requested course_key is invalid.
            * No course corresponding to the requested key exists.
            * The requesting user is not enrolled in the requested course.
        An HTTP 422 may be returned if any of the requested (user_id, usage_id) pairs
        did not have a grade override created due to some exception.  A `reason` detailing the exception
        is provided with each response item.

    **Example successful POST Response**
        [
          {
            "user_id": 9,
            "usage_id": "some-requested-usage-id",
            "success": true,
            "reason": null
          },
          {
            "user_id": 9,
            "usage_id": "an-invalid-usage-id",
            "success": false,
            "reason": "<class 'opaque_keys.edx.locator.BlockUsageLocator'>: not-a-valid-usage-key"
          },
          {
            "user_id": 9,
            "usage_id": "a-valid-usage-key-that-doesn't-exist",
            "success": false,
            "reason": "a-valid-usage-key-that-doesn't-exist does not exist in this course"
          },
          {
            "user_id": 1234-I-DO-NOT-EXIST,
            "usage_id": "a-valid-usage-key",
            "success": false,
            "reason": "User matching query does not exist."
          }
        ]
    """

    @verify_course_exists("Requested grade for unknown course {course}")
    @verify_writable_gradebook_enabled
    @course_author_access_required
    def post(self, request, course_key):
        """
        Creates or updates `PersistentSubsectionGradeOverrides` for the (user_id, usage_key)
        specified in the request data.  The `SUBSECTION_OVERRIDE_CHANGED` signal is invoked
        after the grade override is created, which triggers a celery task to update the
        course and subsection grades for the specified user.
        """
        if are_grades_frozen(course_key):
            raise self.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='Grades are frozen for this course.',
                error_code='grades_frozen'
            )

        course = get_course_by_id(course_key, depth=None)

        result = []

        for user_data in request.data:
            requested_user_id = user_data['user_id']
            requested_usage_id = user_data['usage_id']
            try:
                user = self._get_single_user(request, course_key, requested_user_id)
                usage_key = UsageKey.from_string(requested_usage_id)
            except (USER_MODEL.DoesNotExist, InvalidKeyError, CourseEnrollment.DoesNotExist) as exc:
                self._log_update_result(request.user, requested_user_id, requested_usage_id, success=False)
                result.append(GradebookUpdateResponseItem(
                    user_id=requested_user_id,
                    usage_id=requested_usage_id,
                    success=False,
                    reason=str(exc)
                ))
                continue

            try:
                subsection_grade_model = PersistentSubsectionGrade.objects.get(
                    user_id=user.id,
                    course_id=course_key,
                    usage_key=usage_key
                )
            except PersistentSubsectionGrade.DoesNotExist:
                subsection = course.get_child(usage_key)
                if subsection:
                    subsection_grade_model = self._create_subsection_grade(user, course, subsection)
                else:
                    self._log_update_result(request.user, requested_user_id, requested_usage_id, success=False)
                    result.append(GradebookUpdateResponseItem(
                        user_id=requested_user_id,
                        usage_id=requested_usage_id,
                        success=False,
                        reason=f'usage_key {usage_key} does not exist in this course.'
                    ))
                    continue

            if subsection_grade_model:
                override = self._create_override(request.user, subsection_grade_model, **user_data['grade'])

                self._log_update_result(
                    request.user, requested_user_id, requested_usage_id, subsection_grade_model, override, True
                )
                result.append(GradebookUpdateResponseItem(
                    user_id=user.id,
                    usage_id=str(usage_key),
                    success=True,
                    reason=None
                ))

        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        if all(item.success for item in result):
            status_code = status.HTTP_202_ACCEPTED

        return Response(
            [item._asdict() for item in result],
            status=status_code,
            content_type='application/json'
        )

    def _create_subsection_grade(self, user, course, subsection):
        course_data = CourseData(user, course=course)
        subsection_grade = CreateSubsectionGrade(subsection, course_data.structure, {}, {})
        return subsection_grade.update_or_create_model(user, force_update_subsections=True)

    def _create_override(self, request_user, subsection_grade_model, **override_data):
        """
        Helper method to create a `PersistentSubsectionGradeOverride` object
        and send a `SUBSECTION_OVERRIDE_CHANGED` signal.
        """
        override_data['system'] = grades_constants.GradeOverrideFeatureEnum.gradebook
        override = PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=request_user,
            subsection_grade_model=subsection_grade_model,
            feature=grades_constants.GradeOverrideFeatureEnum.gradebook,
            **override_data
        )

        set_event_transaction_type(grades_events.SUBSECTION_GRADE_CALCULATED)
        create_new_event_transaction_id()

        recalculate_subsection_grade_v3.apply(
            kwargs=dict(
                user_id=subsection_grade_model.user_id,
                anonymous_user_id=None,
                course_id=str(subsection_grade_model.course_id),
                usage_id=str(subsection_grade_model.usage_key),
                only_if_higher=False,
                expected_modified_time=to_timestamp(override.modified),
                score_deleted=False,
                event_transaction_id=str(get_event_transaction_id()),
                event_transaction_type=str(get_event_transaction_type()),
                score_db_table=grades_constants.ScoreDatabaseTableEnum.overrides,
                force_update_subsections=True,
            )
        )
        # Emit events to let our tracking system to know we updated subsection grade
        grades_events.subsection_grade_calculated(subsection_grade_model)
        return override

    @staticmethod
    def _log_update_result(  # lint-amnesty, pylint: disable=missing-function-docstring
        request_user,
        user_id, usage_id,
        subsection_grade_model=None,
        subsection_grade_override=None,
        success=False
    ):

        log.info(
            'Grades: Bulk_Update, UpdatedByUser: %s, User: %s, Usage: %s, Grade: %s, GradeOverride: %s, Success: %s',
            request_user.id,
            user_id,
            usage_id,
            subsection_grade_model,
            subsection_grade_override,
            success
        )


class SubsectionUnavailableToUserException(Exception):
    pass


@view_auth_classes()
class SubsectionGradeView(GradeViewMixin, APIView):
    """
    **Use Case**
        * This api is to get information about a users original grade for a subsection.
        It also exposes any overrides that now replace the original grade and a history of user changes
        with time stamps of all changes.
    **Example Request**
        GET /api/grades/v1/subsection/{subsection_id}/?user_id={user_id}
    **GET Parameters**
        A GET request may include the following query parameters.
        * user_id: (required) An integer represenation of a user
    **GET Response Values**
        If the request for subsection grade data is successful,
        an HTTP 200 "OK" response is returned.
        The HTTP 200 response has the following values:
        * subsection_id: A string representation of the usage_key for a course subsection
        * user_id: The user's integer id
        * course_id: A string representation of a Course ID.
        * original_grade: An object representation of a users original grade containing:
            * earned_all: The float score a user earned for all graded and not graded problems
            * possible_all: The float highest score a user can earn for all graded and not graded problems
            * earned_graded: The float score a user earned for only graded probles
            * possible_graded: The float highest score a user can earn for only graded problems
        * override: An object representation of an over ride for a user's subsection grade containing:
            * earned_all_override: The float overriden score a user earned for all graded and not graded problems
            * possible_all_override: The float overriden highest score a user can earn for all graded
              and not graded problems
            * earned_graded_override: The float overriden grade a user earned for only graded problems
            * possible_graded_override: The float overriden highest possible grade a user can earn
              for only graded problems
        * history: A list of history objects that contain
            * user: The string representation of the user who was responsible for overriding the grade
            * comments: A string comment about why that person changed the grade
            * created: The date timestamp the grade was changed
            * feature: The string representation of the feature through which the grade was overrriden
            * action: The string representation of the CRUD action the override did

        An HTTP 404 may be returned for the following reasons:
            * The requested subsection_id is invalid.
            * The requested user_id is invalid.
            * NOTE: if you pass in a valid subsection_id and a valid user_id with no data representation in the DB
              then you will still recieve a 200 with a response with 'original_grade', 'override' and 'course_id'
              set to None and the 'history' list will be empty.

    **Example GET Response**
        {
            "subsection_id": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@basic_questions",
            "user_id": 2,
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "original_grade": {
                "earned_all": 0,
                "possible_all": 11,
                "earned_graded": 8,
                "possible_graded": 11
            },
            "override": {
                "earned_all_override": null,
                "possible_all_override": null,
                "earned_graded_override": 8,
                "possible_graded_override": null
            },
            "history": [
                {
                    "user": "edx",
                    "comments": null,
                    "created": "2018-12-03T18:52:36.087134Z",
                    "feature": "GRADEBOOK",
                    "action": "CREATEORUPDATE"
                },
                {
                    "user": "edx",
                    "comments": null,
                    "created": "2018-12-03T20:41:02.507685Z",
                    "feature": "GRADEBOOK",
                    "action": "CREATEORUPDATE"
                },
                {
                    "user": "edx",
                    "comments": null,
                    "created": "2018-12-03T20:46:08.933387Z",
                    "feature": "GRADEBOOK",
                    "action": "CREATEORUPDATE"
                }
            ]
        }
    """

    def get(self, request, subsection_id):
        """
        Returns subection grade data, override grade data and a history of changes made to
        a specific users specific subsection grade.

        Args:
            subsection_id: String representation of a usage_key, which is an opaque key of
            a persistant subection grade.
            user_id: An integer represenation of a user

        """
        try:
            usage_key = UsageKey.from_string(subsection_id)
        except InvalidKeyError:
            raise self.api_error(  # lint-amnesty, pylint: disable=raise-missing-from
                status_code=status.HTTP_404_NOT_FOUND,
                developer_message='Invalid UsageKey',
                error_code='invalid_usage_key'
            )

        if not has_course_author_access(request.user, usage_key.course_key):
            raise DeveloperErrorViewMixin.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='The requesting user does not have course author permissions.',
                error_code='user_permissions',
            )

        try:
            user_id = int(request.GET.get('user_id'))
        except ValueError:
            raise self.api_error(  # lint-amnesty, pylint: disable=raise-missing-from
                status_code=status.HTTP_404_NOT_FOUND,
                developer_message='Invalid UserID',
                error_code='invalid_user_id'
            )
        success = True
        err_msg = ""
        override = None
        history = []
        history_record_limit = request.GET.get('history_record_limit')
        if history_record_limit is not None:
            try:
                history_record_limit = int(history_record_limit)
            except ValueError:
                history_record_limit = 0

        try:
            original_grade = PersistentSubsectionGrade.read_grade(user_id, usage_key)
            if original_grade is not None and hasattr(original_grade, 'override'):
                override = original_grade.override
                # pylint: disable=no-member
                history = list(reversed(PersistentSubsectionGradeOverride.history.filter(
                    grade_id=original_grade.id
                ).order_by('-history_date')[:history_record_limit]))
            grade_data = {
                'earned_all': original_grade.earned_all,
                'possible_all': original_grade.possible_all,
                'earned_graded': original_grade.earned_graded,
                'possible_graded': original_grade.possible_graded,
            }
        except PersistentSubsectionGrade.DoesNotExist:
            try:
                grade_data = self._get_grade_data_for_not_attempted_assignment(user_id, usage_key)
            except SubsectionUnavailableToUserException as exc:
                success = False
                err_msg = str(exc)
                grade_data = {
                    'earned_all': 0,
                    'possible_all': 0,
                    'earned_graded': 0,
                    'possible_graded': 0,
                }

        response_data = {
            'success': success,
            'original_grade': grade_data,
            'override': override,
            'history': history,
            'subsection_id': usage_key,
            'user_id': user_id,
            'course_id': usage_key.course_key,
        }
        if not success:
            response_data['error_message'] = err_msg
        results = SubsectionGradeResponseSerializer(response_data)
        return Response(results.data)

    def _get_grade_data_for_not_attempted_assignment(self, user_id, usage_key):
        """
        Return grade for an assignment that wasn't attempted
        """
        student = get_user_model().objects.get(id=user_id)
        course_structure = get_course_blocks(student, usage_key)
        if usage_key not in course_structure:
            raise SubsectionUnavailableToUserException(
                _("Cannot override subsection grade: subsection is not available for target learner.")
            )
        subsection_grade_factory = SubsectionGradeFactory(student, course_structure=course_structure)
        grade = subsection_grade_factory.create(course_structure[usage_key], read_only=True, force_calculate=True)
        grade_data = {
            'earned_all': grade.all_total.earned,
            'possible_all': grade.all_total.possible,
            'earned_graded': grade.graded_total.earned,
            'possible_graded': grade.graded_total.possible,
        }
        return grade_data
