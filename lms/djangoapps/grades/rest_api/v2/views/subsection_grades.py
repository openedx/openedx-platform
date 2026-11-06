"""Subsection grade endpoints for the Grades API v2."""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.scoping import FullScopePolicy, ScopedQuerysetMixin
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lms.djangoapps.grades.models import PersistentSubsectionGradeOverride
from lms.djangoapps.grades.rest_api.v2.filters import SubsectionGradeOverrideHistoryFilterSet
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.permissions import HasGradebookAccess
from lms.djangoapps.grades.rest_api.v2.serializers import (
    SubsectionGradeOverrideHistoryRecordSerializer,
    SubsectionGradeSerializer,
)
from lms.djangoapps.grades.rest_api.v2.services import enrolled_learner, read_subsection_grade

TAG = 'grade'

_PATH_PARAMETERS = [
    OpenApiParameter(
        name='username',
        description='Username of a learner enrolled in the course of the subsection.',
        required=True,
        type=str,
        location=OpenApiParameter.PATH,
    ),
    OpenApiParameter(
        name='usage_key',
        description='Opaque key of the subsection.',
        required=True,
        type=str,
        location=OpenApiParameter.PATH,
    ),
]
_UNAUTHENTICATED = OpenApiResponse(
    response=ErrorResponseSerializer, description='The caller is not authenticated.',
)
_FORBIDDEN = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='The caller may not manage the gradebook of the course the subsection belongs to.',
)


class _SubsectionGradeAddress:
    """Reads the course and learner a subsection grade address names."""

    def get_course_key(self):
        """Return the course of the subsection the request addresses."""
        return self.kwargs['usage_key'].course_key

    def get_learner(self):
        """Return the learner the request addresses, who must be enrolled in the course."""
        return enrolled_learner(self.kwargs['username'], self.get_course_key())


class SubsectionGradeViewSet(StandardizedErrorMixin, _SubsectionGradeAddress, viewsets.ViewSet):
    """
    One learner's grade on one subsection.

    Callers who may manage the gradebook of the subsection's course may read the
    grade of any learner enrolled in it. The grade is read from the grade models
    rather than from one queryset, so this is a plain ViewSet.
    """

    permission_classes = (IsAuthenticated, HasGradebookAccess)
    serializer_class = SubsectionGradeSerializer

    def get_serializer(self, *args, **kwargs):
        """Return the response serializer."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        operation_id='subsection_grades_retrieve',
        tags=[TAG],
        summary="Get a learner's subsection grade",
        description=(
            "Returns the points a learner has earned on a subsection and the override recorded "
            "on them, if any. The history of the override is at override_history_records/."
        ),
        parameters=_PATH_PARAMETERS,
        responses={
            200: SubsectionGradeSerializer,
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The learner is not enrolled in the course, or the course has no such subsection '
                    '(type not-found), or the subsection is not available to the learner '
                    '(type grades/subsection-unavailable).'
                ),
            ),
        },
    )
    def retrieve(self, request, username, usage_key):  # pylint: disable=unused-argument
        """Return one learner's grade on one subsection."""
        grade = read_subsection_grade(self.get_learner(), usage_key)
        return Response(self.get_serializer(grade).data)


@extend_schema_view(
    list=extend_schema(
        operation_id='subsection_grade_override_history_records_list',
        tags=[TAG],
        summary="List the override history of a learner's subsection grade",
        description=(
            'Returns every change made to the override recorded on the grade, newest first. '
            'The page is empty when no grade has been stored for the learner.'
        ),
        parameters=_PATH_PARAMETERS,
        responses={
            200: SubsectionGradeOverrideHistoryRecordSerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer, description='A query parameter is not valid.'),
            401: _UNAUTHENTICATED,
            403: _FORBIDDEN,
            404: OpenApiResponse(
                response=ErrorResponseSerializer, description='The learner is not enrolled in the course.',
            ),
        },
    ),
)
class SubsectionGradeOverrideHistoryViewSet(
    StandardizedErrorMixin,
    _SubsectionGradeAddress,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    The changes made to the override recorded on one learner's grade on one subsection.

    Access is the same as for the grade itself, and every caller admitted sees
    every change.
    """

    permission_classes = (IsAuthenticated, HasGradebookAccess)
    serializer_class = SubsectionGradeOverrideHistoryRecordSerializer
    pagination_class = GradePagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubsectionGradeOverrideHistoryFilterSet
    scoping_policy = FullScopePolicy()
    queryset = PersistentSubsectionGradeOverride.history.select_related('history_user')

    def get_queryset(self):
        """Return the history of the addressed grade, newest first."""
        usage_key = self.kwargs['usage_key']
        return super().get_queryset().filter(
            grade__user_id=self.get_learner().id,
            grade__course_id=usage_key.course_key,
            grade__usage_key=usage_key,
        ).order_by('-history_date', '-history_id')
