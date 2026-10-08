"""Subsection grade override endpoint for the Grades API v2."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lms.djangoapps.grades.rest_api.v2.permissions import (
    CourseExists,
    GradesNotFrozen,
    HasGradebookAccess,
    WritableGradebookEnabled,
)
from lms.djangoapps.grades.rest_api.v2.serializers import (
    SubsectionGradeOverrideListSerializer,
    SubsectionGradeOverrideRequestSerializer,
)
from lms.djangoapps.grades.rest_api.v2.services import course_content, record_subsection_grade_overrides

TAG = 'grade'

_COURSE_KEY_PATH_PARAMETER = OpenApiParameter(
    name='course_key',
    description='Opaque key of the course whose grades to override.',
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_REQUEST_EXAMPLE = OpenApiExample(
    'Two overrides',
    request_only=True,
    value={
        'overrides': [
            {
                'username': 'learner_one',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@homework_1',
                'earned_graded_override': 8.0,
                'possible_graded_override': 10.0,
                'comment': 'Regraded after appeal.',
            },
            {
                'username': 'learner_two',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@homework_1',
                'earned_graded_override': None,
            },
        ],
    },
)
_INVALID_ITEMS_EXAMPLE = OpenApiExample(
    'Invalid items',
    response_only=True,
    status_codes=['400'],
    value={
        'type': 'https://docs.openedx.org/errors/validation',
        'title': 'Validation Error',
        'status': 400,
        # The error handler renders the whole error mapping into detail; clients read errors.
        'detail': (
            "{'overrides[0].username': [ErrorDetail(string='No learner with this username is enrolled in "
            "this course.', code='invalid')], 'overrides[1].usage_key': [ErrorDetail(string='No such "
            "subsection in this course.', code='invalid')]}"
        ),
        'instance': '/api/grade/v2/courses/course-v1:edX+DemoX+Demo_Course/subsection_grade_overrides/',
        'errors': {
            'overrides[0].username': ['No learner with this username is enrolled in this course.'],
            'overrides[1].usage_key': ['No such subsection in this course.'],
        },
    },
)


class SubsectionGradeOverrideViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    Records overrides of learners' subsection grades in one course, as a batch.

    Every item is validated before any is stored, and the items are stored in
    one transaction, so a batch with an invalid item stores nothing. The grades
    the overrides change are recomputed once every item is stored. A failure
    while recomputing is answered with a server error but leaves the overrides
    stored: the error handler returns that error as a response rather than
    raising it, so the request's own transaction still commits.

    Callers who may manage the gradebook of the course may record overrides
    while its writable gradebook is switched on and its grades are not frozen.

    The write goes through the grade models and the grade recalculation task
    rather than a model serializer, so this is a plain ViewSet.
    """

    permission_classes = (
        IsAuthenticated, HasGradebookAccess, CourseExists, WritableGradebookEnabled, GradesNotFrozen,
    )
    serializer_class = SubsectionGradeOverrideListSerializer
    # A batch nests a list of objects, which only a JSON body can carry.
    parser_classes = (JSONParser,)

    def get_serializer(self, *args, **kwargs):
        """Return the response serializer."""
        return self.serializer_class(*args, **kwargs)

    def get_course_key(self):
        """Return the course the request addresses."""
        return self.kwargs['course_key']

    @extend_schema(
        operation_id='subsection_grade_overrides_create',
        tags=[TAG],
        summary='Override subsection grades',
        description=(
            'A batch operation: records an override of one learner\'s grade on one subsection for '
            'each item sent, then recomputes the grades the overrides change. The body must be '
            'JSON. When any item is not valid nothing is recorded, and the errors are keyed by the '
            'position of each failed item. The overrides are recorded before the grades are '
            'recomputed: if recomputing fails, the response is a server error, yet every override '
            'in the batch stays recorded and some of the grades it changes may not have been '
            'recomputed. Sending the same batch again records the same values and recomputes them.'
        ),
        parameters=[_COURSE_KEY_PATH_PARAMETER],
        request=SubsectionGradeOverrideRequestSerializer,
        examples=[_REQUEST_EXAMPLE, _INVALID_ITEMS_EXAMPLE],
        responses={
            200: SubsectionGradeOverrideListSerializer,
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='The body is not valid; nothing was recorded.',
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer, description='The caller is not authenticated.',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The caller may not manage the gradebook of this course (type authz), the course '
                    'has the writable gradebook switched off (type grades/writable-gradebook-disabled), '
                    'or its grades are frozen (type grades/grades-frozen).'
                ),
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer, description='The course does not exist.',
            ),
            500: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Recomputing the grades failed after every override in the batch was recorded.',
            ),
        },
    )
    def create(self, request, course_key):
        """Record a batch of subsection grade overrides."""
        course = course_content(course_key, depth=None)
        batch = SubsectionGradeOverrideRequestSerializer(
            data=request.data, context={'course_key': course_key, 'course': course},
        )
        if not batch.is_valid():
            raise ValidationError(batch.errors_by_path())
        rows = record_subsection_grade_overrides(request.user, course, batch.validated_data['overrides'])
        return Response(self.get_serializer({'overrides': rows}).data)
