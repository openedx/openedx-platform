"""Course grade endpoints for the Grades API v2."""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.scoping import ScopedQuerysetMixin
from rest_framework import mixins, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.query import use_read_replica_if_available
from lms.djangoapps.grades.rest_api.v2.filters import CourseGradeFilterSet
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.permissions import COURSE_GRADE_READ_ACCESS, HasGradeBreakdownAccess
from lms.djangoapps.grades.rest_api.v2.scoping import CourseGradeScopingPolicy
from lms.djangoapps.grades.rest_api.v2.serializers import CourseGradeQuerySerializer, CourseGradeSerializer
from lms.djangoapps.grades.rest_api.v2.services import course_grade_row, course_grade_rows

SDK_TAG = 'openedx-platform-sdk'
FULL_VIEW = 'full'

_VIEW_PARAMETER = OpenApiParameter(
    name='view',
    description=(
        "Send 'full' to include the breakdown of each grade by subsection and assignment type. "
        "Only global staff may ask for it."
    ),
    required=False,
    type=str,
    enum=[FULL_VIEW],
    location=OpenApiParameter.QUERY,
)
_USERNAME_PATH_PARAMETER = OpenApiParameter(
    name='username',
    description='Username of the learner whose grade to return.',
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_COURSE_KEY_PATH_PARAMETER = OpenApiParameter(
    name='course_key',
    description='Opaque key of the course the grade belongs to.',
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_BAD_REQUEST = OpenApiResponse(
    response=ErrorResponseSerializer, description='A query parameter is not valid.',
)
_UNAUTHENTICATED = OpenApiResponse(
    response=ErrorResponseSerializer, description='The caller is not authenticated.',
)
_FORBIDDEN = OpenApiResponse(
    response=ErrorResponseSerializer,
    description=(
        'The caller authenticated with a restricted token that lacks the grades:read scope, '
        'or asked for the full view without being global staff.'
    ),
)
_NOT_FOUND = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='No enrollment of this learner in this course is visible to the caller.',
)


@extend_schema_view(
    list=extend_schema(
        operation_id='course_grades_list',
        tags=[SDK_TAG],
        summary='List course grades',
        description=(
            "Returns one grade per active enrollment the caller may read. Learners see their own "
            "grades in every course they are enrolled in; global staff see every learner's grades; "
            "a restricted token sees the organizations and learners its filters name. Without a "
            "course_key the list spans every course. Only global staff, not through a restricted "
            "token, may ask for the full view. A grade that cannot be computed is left out of the "
            "page, but count and num_pages still include it, so a page may hold fewer rows than "
            "page_size."
        ),
        parameters=[_VIEW_PARAMETER],
        responses={
            200: CourseGradeSerializer,
            400: _BAD_REQUEST,
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
        },
    ),
    retrieve=extend_schema(
        operation_id='course_grades_retrieve',
        tags=[SDK_TAG],
        summary="Get a learner's course grade",
        description=(
            "Returns one learner's grade in one course. The enrollment need not be active. "
            "Learners may read their own grade; global staff may read anyone's. Only global staff, "
            "not through a restricted token, may ask for the full view."
        ),
        parameters=[_USERNAME_PATH_PARAMETER, _COURSE_KEY_PATH_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: CourseGradeSerializer,
            400: _BAD_REQUEST,
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
            404: _NOT_FOUND,
        },
    ),
)
class CourseGradeViewSet(
    StandardizedErrorMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Course grades, one per learner and course.

    A grade is identified by the learner and the course together, so the member
    address carries both. The rows are enrollments, which DRF scopes, filters
    and pages; the grade values themselves are computed by the grades app for
    each page rather than read from the enrollment.

    The collection lists active enrollments only. The member address also finds
    an inactive enrollment, so a learner who unenrolled can still read the
    grade they earned. The full view is refused to all but global staff,
    whatever rows the caller may otherwise read.
    """

    permission_classes = (COURSE_GRADE_READ_ACCESS, HasGradeBreakdownAccess)
    required_scopes = ['grades:read']
    serializer_class = CourseGradeSerializer
    pagination_class = GradePagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CourseGradeFilterSet
    queryset = CourseEnrollment.objects.select_related('user')

    @property
    def scoping_policy(self):
        """Return the record-visibility policy for this request."""
        return CourseGradeScopingPolicy(self.request)

    def get_queryset(self):
        """Return the enrollments whose grades the caller may read, in a stable order."""
        queryset = super().get_queryset()
        if self.action == 'list':
            queryset = queryset.filter(is_active=True)
        return use_read_replica_if_available(queryset).order_by('id')

    def get_object(self):
        """Return the enrollment addressed by the learner and course in the URL."""
        enrollment = self.get_queryset().filter(
            user__username=self.kwargs['username'],
            course_id=self.kwargs['course_key'],
        ).first()
        if enrollment is None:
            raise NotFound()
        self.check_object_permissions(self.request, enrollment)
        return enrollment

    def list(self, request, *args, **kwargs):
        """Return one page of course grades."""
        with_breakdown = self.breakdown_requested()
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        rows = course_grade_rows(page, with_section_breakdown=with_breakdown)
        return self.get_paginated_response(self.get_serializer(rows, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        """Return one learner's grade in one course."""
        with_breakdown = self.breakdown_requested()
        row = course_grade_row(self.get_object(), with_section_breakdown=with_breakdown)
        return Response(self.get_serializer(row).data)

    def breakdown_requested(self):
        """Validate the requested representation and return True when it is the full one."""
        query = CourseGradeQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return query.validated_data.get('view') == FULL_VIEW
