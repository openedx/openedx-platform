"""Submission history endpoint for the Grades API v2."""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.scoping import FullScopePolicy, ScopedQuerysetMixin
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAdminUser

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from common.djangoapps.util.query import use_read_replica_if_available
from lms.djangoapps.grades.rest_api.v2.filters import SubmissionHistoryFilterSet
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.serializers import (
    SubmissionHistoryQuerySerializer,
    SubmissionHistorySerializer,
)
from lms.djangoapps.grades.rest_api.v2.services import submission_history_rows
from openedx.core.djangoapps.enrollments.views import EnrollmentUserThrottle

TAG = 'grade'
FULL_VIEW = 'full'


@can_disable_rate_limit
@extend_schema_view(
    list=extend_schema(
        operation_id='submission_histories_list',
        tags=[TAG],
        summary='List the submission histories of a course',
        description=(
            'Returns one row per active enrollment in the course, listing the scored problems the '
            'learner has submitted answers to and each recorded state of those answers. Global '
            'staff only. Requests are rate limited.'
        ),
        parameters=[
            OpenApiParameter(
                name='course_key',
                description='Opaque key of the course.',
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            ),
            OpenApiParameter(
                name='view',
                description="Send 'full' to include the definition of each problem.",
                required=False,
                type=str,
                enum=[FULL_VIEW],
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={
            200: SubmissionHistorySerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer, description='A query parameter is not valid.'),
            401: OpenApiResponse(response=ErrorResponseSerializer, description='The caller is not authenticated.'),
            403: OpenApiResponse(response=ErrorResponseSerializer, description='The caller is not global staff.'),
            404: OpenApiResponse(response=ErrorResponseSerializer, description='The course does not exist.'),
            429: OpenApiResponse(response=ErrorResponseSerializer, description='Too many requests.'),
        },
    ),
)
class SubmissionHistoryViewSet(
    StandardizedErrorMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    The submission history of every learner enrolled in one course.

    Only global staff may read it, and every row is visible to them. Requests
    are throttled like the enrollment API, unless rate limiting is switched
    off for the site. Problem definitions can be large, so they are returned
    only on request.
    """

    permission_classes = (IsAdminUser,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = SubmissionHistorySerializer
    pagination_class = GradePagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubmissionHistoryFilterSet
    scoping_policy = FullScopePolicy()
    queryset = CourseEnrollment.objects.select_related('user')

    def get_queryset(self):
        """Return the active enrollments in the requested course, in a stable order."""
        queryset = super().get_queryset().filter(course_id=self.kwargs['course_key'], is_active=True)
        return use_read_replica_if_available(queryset).order_by('id')

    def list(self, request, *args, **kwargs):
        """Return one page of submission histories."""
        query = SubmissionHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        rows = submission_history_rows(
            page, self.kwargs['course_key'], with_data=query.validated_data.get('view') == FULL_VIEW,
        )
        return self.get_paginated_response(self.get_serializer(rows, many=True).data)
