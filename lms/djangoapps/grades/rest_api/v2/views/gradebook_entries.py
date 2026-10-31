"""Gradebook endpoints for the Grades API v2."""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.scoping import FullScopePolicy, ScopedQuerysetMixin
from edx_rest_framework_extensions.shaping import MinimalViewMixin
from rest_framework import mixins, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.query import use_read_replica_if_available
from lms.djangoapps.grades.rest_api.v2.filters import GradebookEntryFilterSet
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.permissions import CourseExists, HasGradebookAccess, WritableGradebookEnabled
from lms.djangoapps.grades.rest_api.v2.serializers import GradebookEntryQuerySerializer, GradebookEntrySerializer
from lms.djangoapps.grades.rest_api.v2.services import gradebook_entry_row, gradebook_entry_rows

TAG = 'grade'

_COURSE_KEY_PATH_PARAMETER = OpenApiParameter(
    name='course_key',
    description='Opaque key of the course whose gradebook to read.',
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_USERNAME_PATH_PARAMETER = OpenApiParameter(
    name='username',
    description='Username of the learner whose row to return.',
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_VIEW_PARAMETER = OpenApiParameter(
    name='view',
    description="Send 'minimal' to reduce each row to the learner's username and overall percent.",
    required=False,
    type=str,
    enum=['minimal'],
    location=OpenApiParameter.QUERY,
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
        'The caller may not manage the gradebook of this course (type authz), or the course '
        'has the writable gradebook switched off (type grades/writable-gradebook-disabled).'
    ),
)
_NOT_FOUND = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='The course does not exist, or the learner is not enrolled in it.',
)


@extend_schema_view(
    list=extend_schema(
        operation_id='gradebook_entries_list',
        tags=[TAG],
        summary='List the gradebook of a course',
        description=(
            "Returns one row per active enrollment in the course, with the learner's overall "
            'percent and their score on each graded subsection. Callers who may manage the '
            'gradebook of the course see every learner in it. A learner whose grade cannot be '
            'computed is left out of the page, but count and num_pages still include them, so a '
            'page may hold fewer rows than page_size.'
        ),
        parameters=[_COURSE_KEY_PATH_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: GradebookEntrySerializer,
            400: _BAD_REQUEST,
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
            404: _NOT_FOUND,
        },
    ),
    retrieve=extend_schema(
        operation_id='gradebook_entries_retrieve',
        tags=[TAG],
        summary="Get a learner's gradebook row",
        description="Returns one learner's row in the gradebook of one course. The enrollment need not be active.",
        parameters=[_COURSE_KEY_PATH_PARAMETER, _USERNAME_PATH_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: GradebookEntrySerializer,
            400: _BAD_REQUEST,
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
            404: _NOT_FOUND,
        },
    ),
)
class GradebookEntryViewSet(
    StandardizedErrorMixin,
    ScopedQuerysetMixin,
    MinimalViewMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    The gradebook of a course: one row per learner enrolled in it.

    Access is checked before the course is looked up and before its writable
    gradebook setting is read, so a caller without access to a course learns
    neither whether it exists nor how it is configured. Every caller admitted
    to a course sees every learner in it.

    The collection lists active enrollments only; the member address also
    finds an inactive enrollment.
    """

    permission_classes = (IsAuthenticated, HasGradebookAccess, CourseExists, WritableGradebookEnabled)
    serializer_class = GradebookEntrySerializer
    pagination_class = GradePagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = GradebookEntryFilterSet
    minimal_fields = ('username', 'percent')
    scoping_policy = FullScopePolicy()
    queryset = CourseEnrollment.objects.select_related('user', 'user__profile')

    def get_course_key(self):
        """Return the course the request addresses."""
        return self.kwargs['course_key']

    def get_queryset(self):
        """Return the enrollments in the requested course, in a stable order."""
        queryset = super().get_queryset().filter(course_id=self.get_course_key())
        if self.action == 'list':
            queryset = queryset.filter(is_active=True)
        return use_read_replica_if_available(queryset).order_by('id')

    def get_object(self):
        """Return the enrollment of the learner named in the URL."""
        enrollment = self.get_queryset().filter(user__username=self.kwargs['username']).first()
        if enrollment is None:
            raise NotFound()
        self.check_object_permissions(self.request, enrollment)
        return enrollment

    def list(self, request, *args, **kwargs):
        """Return one page of gradebook rows."""
        self._validate_query()
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        rows = gradebook_entry_rows(page, self.get_course_key())
        data = self.get_serializer(rows, many=True).data
        return self.get_paginated_response(self.shape_minimal(data, request))

    def retrieve(self, request, *args, **kwargs):
        """Return one learner's gradebook row."""
        self._validate_query()
        row = gradebook_entry_row(self.get_object(), self.get_course_key())
        return Response(self.shape_minimal(self.get_serializer(row).data, request))

    def _validate_query(self):
        """Refuse a representation this endpoint does not offer."""
        GradebookEntryQuerySerializer(data=self.request.query_params).is_valid(raise_exception=True)
