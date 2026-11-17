"""Course grading policy endpoint for the Grades API v2."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.shaping import MinimalViewMixin
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.grades.rest_api.v2.permissions import (
    COURSE_GRADING_POLICY_READ_ACCESS,
    CourseExists,
    HasGradebookAccessUnlessMinimal,
)
from lms.djangoapps.grades.rest_api.v2.serializers import (
    CourseGradingPolicyQuerySerializer,
    CourseGradingPolicySerializer,
)
from lms.djangoapps.grades.rest_api.v2.services import course_grading_policy

SDK_TAG = 'openedx-platform-sdk'


class CourseGradingPolicyView(StandardizedErrorMixin, MinimalViewMixin, APIView):
    """
    How one course is graded: its grade cutoffs, assignment types and subsections.

    A course has exactly one grading policy, so the address carries no
    identifier of its own and there is no collection to list; that is why this
    is an APIView. Course staff, and anyone who may manage the gradebook of the
    course, may read the minimal view; the full view is for those who may
    manage the gradebook only. The minimal view reads the course content one
    level deep only, since it leaves the subsections out.
    """

    permission_classes = (COURSE_GRADING_POLICY_READ_ACCESS, HasGradebookAccessUnlessMinimal, CourseExists)
    serializer_class = CourseGradingPolicySerializer
    minimal_fields = ('grade_cutoffs', 'assignment_types')

    def get_course_key(self):
        """Return the course the request addresses."""
        return self.kwargs['course_key']

    @extend_schema(
        operation_id='course_grading_policy_retrieve',
        tags=[SDK_TAG],
        summary='Get the grading policy of a course',
        description=(
            'Returns the grade cutoffs of the course, the assignment types it grades against, '
            'and the subsections learners can see. Course staff may read the minimal view; the '
            'full view requires access to manage the gradebook of the course.'
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
                description="Send 'minimal' for the grade cutoffs and assignment types only.",
                required=False,
                type=str,
                enum=['minimal'],
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name='graded_only',
                description='Send true to leave the subsections that carry no grade out.',
                required=False,
                type=bool,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={
            200: CourseGradingPolicySerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer, description='A query parameter is not valid.'),
            401: OpenApiResponse(response=ErrorResponseSerializer, description='The caller is not authenticated.'),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The caller may not manage the gradebook of this course and, for the minimal view, '
                    'is not course staff either.'
                ),
            ),
            404: OpenApiResponse(response=ErrorResponseSerializer, description='The course does not exist.'),
        },
    )
    def get(self, request, course_key):
        """Return the grading policy of one course."""
        query = CourseGradingPolicyQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        policy = course_grading_policy(
            course_key,
            with_subsections=not self.is_minimal_view_requested(request),
            graded_only=query.validated_data['graded_only'],
        )
        return Response(self.shape_minimal(self.serializer_class(policy).data, request))
