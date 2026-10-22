"""API views for a course container."""

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.url_converters import UsageKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.container_service import get_container, get_container_page
from cms.djangoapps.contentstore.rest_api.v2.serializers.containers import (
    CONTAINER_FULL_VIEW,
    CONTAINER_MINIMAL_VIEW,
    CourseContainerFullSerializer,
    CourseContainerQuerySerializer,
    CourseContainerSerializer,
)
from cms.djangoapps.contentstore.views.permissions import CanViewCourse

_USAGE_KEY_DESCRIPTION = (
    "Usage key of a section, subsection, unit or other container of a course, such as "
    "block-v1:edX+DemoX+2026+type@vertical+block@welcome."
)

_USAGE_KEY_PARAMETER = OpenApiParameter(
    name="usage_key",
    description=_USAGE_KEY_DESCRIPTION,
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{UsageKeyConverter.regex}$",
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=CourseContainerQuerySerializer().fields["view"].help_text,
    required=False,
    location=OpenApiParameter.QUERY,
    type=str,
    enum=[CONTAINER_MINIMAL_VIEW, CONTAINER_FULL_VIEW],
    default=CONTAINER_MINIMAL_VIEW,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_EXAMPLE_COURSE = "course-v1:edX+DemoX+2026"
_EXAMPLE_BLOCK = "block-v1:edX+DemoX+2026+type@{}+block@{}"
_EXAMPLE_UNIT = _EXAMPLE_BLOCK.format("vertical", "welcome")
_EXAMPLE_SECTION = _EXAMPLE_BLOCK.format("chapter", "intro")
_EXAMPLE_SUBSECTION = _EXAMPLE_BLOCK.format("sequential", "getting_started")

MINIMAL_VIEW_EXAMPLE = OpenApiExample(
    "Container",
    summary="Default view of a unit",
    response_only=True,
    status_codes=["200"],
    value={
        "id": _EXAMPLE_UNIT,
        "category": "vertical",
        "display_name": "Welcome",
        "display_type": "Unit",
        "course_key": _EXAMPLE_COURSE,
        "is_unit": True,
        "unit_id": _EXAMPLE_UNIT,
        "position": 1,
        "ancestors": [
            {"id": _EXAMPLE_SECTION, "category": "chapter", "display_name": "Introduction"},
            {"id": _EXAMPLE_SUBSECTION, "category": "sequential", "display_name": "Getting started"},
        ],
    },
)

SUBSECTION_EXAMPLE = OpenApiExample(
    "Subsection",
    summary="Default view of a subsection",
    description="No unit holds a subsection, so unit_id and position are null.",
    response_only=True,
    status_codes=["200"],
    value={
        "id": _EXAMPLE_SUBSECTION,
        "category": "sequential",
        "display_name": "Getting started",
        "display_type": "Subsection",
        "course_key": _EXAMPLE_COURSE,
        "is_unit": False,
        "unit_id": None,
        "position": None,
        "ancestors": [
            {"id": _EXAMPLE_SECTION, "category": "chapter", "display_name": "Introduction"},
        ],
    },
)

FULL_VIEW_EXAMPLE = OpenApiExample(
    "Unit page data",
    summary="view=full, abridged",
    description="xblock_info shows a few of its keys and component_templates one of its groups.",
    response_only=True,
    status_codes=["200"],
    value={
        "language_code": "en",
        "xblock": {"display_name": "Welcome", "display_type": "Unit", "category": "vertical"},
        "is_unit_page": True,
        "is_collapsible": False,
        "position": 1,
        "prev_url": None,
        "next_url": "%2Fcontainer%2Fblock-v1%3AedX%2BDemoX%2B2026%2Btype%40vertical%2Bblock%40first_steps",
        "new_unit_category": "vertical",
        "outline_url": f"/course/{_EXAMPLE_COURSE}?format=concise",
        "ancestor_xblocks": [
            {
                "children": [
                    {
                        "url": f"/course/{_EXAMPLE_COURSE}?show=block-v1%3AedX%2BDemoX%2B2026%2Btype%40chapter%2Bblock%40intro",
                        "display_name": "Introduction",
                        "usage_key": _EXAMPLE_SECTION,
                    },
                ],
                "title": "Introduction",
                "is_last": False,
            },
            {
                "children": [
                    {
                        "url": (
                            f"/course/{_EXAMPLE_COURSE}"
                            "?show=block-v1%3AedX%2BDemoX%2B2026%2Btype%40sequential%2Bblock%40getting_started"
                        ),
                        "display_name": "Getting started",
                        "usage_key": _EXAMPLE_SUBSECTION,
                    },
                ],
                "title": "Getting started",
                "is_last": True,
            },
        ],
        "component_templates": [
            {
                "type": "html",
                "templates": [
                    {
                        "display_name": "Text",
                        "category": "html",
                        "boilerplate_name": None,
                        "hinted": False,
                        "tab": "common",
                        "support_level": True,
                    },
                ],
                "display_name": "Text",
                "support_legend": {
                    "show_legend": False,
                    "allow_unsupported_xblocks": False,
                    "documentation_label": "Open edX Support Levels:",
                },
                "beta": False,
            },
        ],
        "xblock_info": {
            "id": _EXAMPLE_UNIT,
            "display_name": "Welcome",
            "category": "vertical",
            "has_children": True,
            "published": True,
            "visibility_state": "live",
        },
        "draft_preview_link": f"https://lms.example.com/courses/{_EXAMPLE_COURSE}/jump_to/{_EXAMPLE_UNIT}?preview=1",
        "published_preview_link": f"https://lms.example.com/courses/{_EXAMPLE_COURSE}/jump_to/{_EXAMPLE_UNIT}",
        "show_unit_tags": True,
        "is_fullwidth_content": False,
        "assets_url": f"/assets/{_EXAMPLE_COURSE}/",
        "unit_block_id": "welcome",
        "subsection_location": _EXAMPLE_SUBSECTION,
        "course_sequence_ids": [_EXAMPLE_SUBSECTION],
        "library_content_picker_url": None,
    },
)

_READ_RESPONSE = PolymorphicProxySerializer(
    component_name="CourseContainerRead",
    serializers=[CourseContainerSerializer, CourseContainerFullSerializer],
    resource_type_field_name=None,
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseContainerViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    A container of a course: a section, a subsection, a unit, or a container
    placed in one of them, such as a split test inside a unit.

    By default the container is returned with the blocks above it; the
    database and Mongo reads this takes depend on how deeply it is nested,
    not on the size of the course. With ``view=full`` a unit, or a container
    inside a unit, is returned with everything the Studio unit page shows;
    the requesting user's clipboard is not included, as it has its own
    endpoint. Sections and subsections have no unit page, so ``view=full`` is
    answered with 404 for them.

    Callable by anyone who may view the course in Studio. The course is looked
    up first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403), and only a caller who may view the course
    learns whether a block exists in it.

    A plain ``ViewSet``: the container is read from the modulestore, not from
    database rows.
    """

    permission_classes = (IsAuthenticated, CanViewCourse)
    serializer_class = CourseContainerSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the container serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get a container",
        description=(
            "Returns a container of the course, with the blocks above it: a section, a subsection, a unit, or "
            "a container placed in one of them, such as a split test or library content block inside a unit. "
            "With view=full, available only for a unit or a container inside a unit, the response is "
            "everything the Studio unit page shows: the add-component menu, the container's editing state, the "
            "neighbouring units and the Studio and LMS addresses the page links to. The course itself, "
            "components such as problems, and blocks the course does not reach are not containers and are "
            "answered with 404."
        ),
        parameters=[_USAGE_KEY_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=_READ_RESPONSE,
                description="The container, or with view=full the unit page's data.",
            ),
            400: _error("view has an invalid value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course in Studio."),
            404: _error(
                "The course does not exist, the key does not name a block of a course, no container in the "
                "course has this key, or view=full was requested for a container that is not a unit or inside one."
            ),
            406: _error("The Accept header admits no JSON media type."),
        },
        examples=[MINIMAL_VIEW_EXAMPLE, SUBSECTION_EXAMPLE, FULL_VIEW_EXAMPLE],
    )
    def retrieve(self, request, usage_key):
        """Return the container, or with ``view=full`` the unit page's data."""
        query = CourseContainerQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        if query.validated_data["view"] == CONTAINER_FULL_VIEW:
            return Response(CourseContainerFullSerializer(get_container_page(request, usage_key)).data)
        return Response(self.get_serializer(get_container(usage_key)).data)
