"""API views for a course's grading policy (v3)."""

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.routers import COURSE_KEY_LOOKUP_REGEX
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v3.grading_service import (
    get_full_grading_settings,
    get_grading_policy,
    update_grading_policy,
)
from cms.djangoapps.contentstore.rest_api.v3.permissions import CanEditGradingSettings, CanViewGradingSettings
from cms.djangoapps.contentstore.rest_api.v3.serializers.grading import (
    GRADING_FULL_VIEW,
    CourseGradingFullSerializer,
    CourseGradingSerializer,
    CourseGradingUpdateSerializer,
    GradingViewQuerySerializer,
)
from cms.djangoapps.contentstore.rest_api.v3.utils import resolve_course_key

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, for example course-v1:edX+DemoX+Demo_Course.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=GradingViewQuerySerializer().fields["view"].help_text,
    required=False,
    type=str,
    enum=[GRADING_FULL_VIEW],
    location=OpenApiParameter.QUERY,
)


# The full shape is listed first: the default shape allows extra keys, so a
# client trying each shape in order would otherwise read a full response as the
# default one and lose the view=full fields. As the shapes overlap, the CMS
# schema hooks publish this component as anyOf (cms.lib.spectacular.ANY_OF_COMPONENTS).
_READ_RESPONSE = PolymorphicProxySerializer(
    component_name="authoring_grading.CourseGradingRead.v3",
    serializers=[CourseGradingFullSerializer, CourseGradingSerializer],
    resource_type_field_name=None,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


@extend_schema(tags=["openedx-platform-sdk"])
class AuthoringGradingViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The grading policy of one course: its assignment types and their weights,
    letter-grade cutoffs, grace period, and minimum grade for course credit.

    Reading the policy needs permission to view the course's grading settings,
    changing it permission to edit them. The course is looked up first, so a
    signed-in caller learns that a course does not exist (404) before being
    refused (403).

    A plain ``ViewSet``: the policy is a settings field of the course in the
    modulestore, not database rows.
    """

    # Studio authors whose account is not yet activated use this endpoint from
    # the authoring app; the default session class would reject them.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    action_permission_classes = {
        "retrieve": CanViewGradingSettings,
        "partial_update": CanEditGradingSettings,
    }
    serializer_class = CourseGradingSerializer

    lookup_field = "course_key"
    lookup_value_regex = COURSE_KEY_LOOKUP_REGEX

    _course_key = None

    def get_permissions(self):
        """Add the permission of the requested action to the authentication check."""
        permissions = super().get_permissions()
        action_permission = self.action_permission_classes.get(self.action)
        if action_permission is not None:
            permissions.append(action_permission())
        return permissions

    def get_course_key(self):
        """Return the course in the path, parsed and checked to exist (404 otherwise)."""
        if self._course_key is None:
            self._course_key = resolve_course_key(self.kwargs["course_key"])
        return self._course_key

    def get_serializer(self, *args, **kwargs):
        """Instantiate the policy serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get a course's grading settings",
        description=(
            "Returns the course's grading policy. With view=full the response also lists the graded "
            "subsections of each assignment type, the default letter grade names, and the course's "
            "credit and proctoring context."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=_READ_RESPONSE,
                description="The grading policy, in the full shape with view=full and the default shape otherwise.",
            ),
            400: _error("The view parameter has an unknown value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester cannot view the grading settings of this course."),
            404: _error("The course does not exist."),
        },
    )
    # The legacy router also routes "<course key>.json" here, passing ``format``.
    def retrieve(
        self, request: Request, course_key: str, format=None,  # pylint: disable=unused-argument,redefined-builtin
    ):
        """Return the grading policy, with the grading page's context when ``view=full``."""
        query = GradingViewQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        if query.validated_data.get("view") == GRADING_FULL_VIEW:
            full = get_full_grading_settings(self.get_course_key())
            return Response(CourseGradingFullSerializer(full).data)
        return Response(self.get_serializer(get_grading_policy(self.get_course_key())).data)

    @extend_schema(
        summary="Update a course's grading settings",
        description=(
            "Changes the fields present in the body and leaves the others as stored. graders replaces "
            "every assignment type. A null grace_period or minimum_grade_credit leaves that field "
            "unchanged. Unknown fields are ignored. Returns the stored policy."
        ),
        request={"application/json": CourseGradingUpdateSerializer},
        parameters=[_COURSE_KEY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseGradingSerializer,
                description="The grading policy as stored after the update.",
            ),
            400: _error("The body is not a valid grading policy update."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester cannot edit the grading settings of this course."),
            404: _error("The course does not exist."),
        },
    )
    def partial_update(
        self, request: Request, course_key: str, format=None,  # pylint: disable=unused-argument,redefined-builtin
    ):
        """Change the grading policy fields present in the body."""
        CourseGradingUpdateSerializer(data=request.data).is_valid(raise_exception=True)
        # The model receives the body as sent: it does its own conversions, and
        # converting here first would change what it stores.
        policy = update_grading_policy(self.get_course_key(), request.data, request.user)
        return Response(self.get_serializer(policy).data)
