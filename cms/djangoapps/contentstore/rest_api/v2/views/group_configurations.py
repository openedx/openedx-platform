"""API views for a course's group configurations."""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import IterablePaginationMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.pagination import DocumentedDefaultPagination
from cms.djangoapps.contentstore.rest_api.v2.group_configurations_service import list_group_configurations
from cms.djangoapps.contentstore.rest_api.v2.serializers.group_configurations import (
    FULL_VIEW,
    MINIMAL_VIEW,
    GroupConfigurationListQuerySerializer,
    GroupConfigurationSerializer,
)
from cms.djangoapps.contentstore.views.permissions import CanViewGroupConfigurations, IsExistingCourse

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=GroupConfigurationListQuerySerializer().fields["view"].help_text,
    required=False,
    location=OpenApiParameter.QUERY,
    type=str,
    enum=[MINIMAL_VIEW, FULL_VIEW],
    default=MINIMAL_VIEW,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_CONFIGURATION_EXAMPLE = OpenApiExample(
    "Content groups",
    summary="The content groups of a course, with view=full",
    response_only=True,
    status_codes=["200"],
    value={
        "id": 1791848226,
        "name": "Content Groups",
        "scheme": "cohort",
        "description": "The groups in this configuration can be mapped to cohorts in the Instructor Dashboard.",
        "parameters": {},
        "groups": [
            {
                "id": 593758473,
                "name": "Honors",
                "usage": [
                    {
                        "label": "Week 1 / Honors problem",
                        "url": "/container/block-v1:edX+DemoX+2026+type@vertical+block@3f9a7c1e",
                    },
                ],
                "version": 1,
            },
        ],
        "usage": None,
        "version": 3,
        "active": True,
        "read_only": False,
        "can_manage": True,
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseGroupConfigurationsViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The group configurations of one course, in the order Studio shows them.

    The configurations Studio lists on its group configurations page come
    first: the enrollment track configuration when the course has more than
    one track, the content type gating configuration when Studio may set it,
    then content groups and the rest. A course without content groups lists an
    unsaved content group configuration in their place, as Studio does, so
    that groups can be added to it; its identifier is chosen afresh for each
    request until then. Experiment configurations follow when the course has
    content experiments turned on, and are left out otherwise. Flags that
    describe the course rather than its group configurations, such as whether
    content experiments are turned on, are not part of this resource.

    By default (``view=minimal``) where each configuration and group is used
    in the course content is left out of the response; ``view=full`` includes
    it. Each configuration says whether the caller may change the course's
    group configurations.

    Anyone who may view the course's group configurations may list them. The
    course is looked up first, so a signed-in caller learns that a course does
    not exist (404) before being refused (403). An action this viewset was not
    written for is refused with a server error instead of being served with
    these checks.

    A plain ``ViewSet``: the configurations are user partitions stored with
    the course in the modulestore, not database rows.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides: staff status
    # admits it, and its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse, CanViewGroupConfigurations)
    # The actions the permissions above were written for. A method with no
    # route (answered 405) has no action; OPTIONS is ``metadata``.
    checked_actions = (None, "list", "metadata")
    serializer_class = GroupConfigurationSerializer
    pagination_class = DocumentedDefaultPagination

    def get_permissions(self):
        """Return the viewset's permissions, or refuse an action they were not written for."""
        if self.action not in self.checked_actions:
            raise ImproperlyConfigured(f"No permission is declared for the {self.action!r} action.")
        return super().get_permissions()

    def get_serializer(self, *args, **kwargs):
        """Instantiate the group configuration serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List a course's group configurations",
        description=(
            "Returns one page of the course's group configurations, in the order Studio shows them: those of "
            "Studio's group configurations page first, then the experiment configurations when the course "
            "has content experiments turned on. A course without content groups lists an unsaved content "
            "group configuration, whose id changes from request to request until a group is added to it. "
            "Flags that describe the course rather than its group configurations, such as whether content "
            "experiments are turned on, are not part of this resource. "
            "With view=full, each configuration and each group also says where it is used in the course "
            "content. can_manage, the same on every configuration, says whether the caller may change the "
            "course's group configurations."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=GroupConfigurationSerializer,
                description="A page of the course's group configurations.",
            ),
            400: _error("view has an invalid value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course's group configurations."),
            404: _error("The course does not exist, or the requested page is out of range."),
        },
        examples=[_CONFIGURATION_EXAMPLE],
    )
    def list(self, request, course_key):
        """Return one page of the course's group configurations, with their usage when ``view=full``."""
        query = GroupConfigurationListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        context = {"include_usage": query.validated_data["view"] == FULL_VIEW}
        return self.paginate_iterable(
            request,
            list_group_configurations(course_key, request.user),
            serialize=lambda page: self.get_serializer(page, many=True, context=context).data,
        )
