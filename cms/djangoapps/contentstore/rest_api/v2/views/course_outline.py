"""API views for a course's outline."""

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.course_outline_service import get_full_outline, get_outline_tree
from cms.djangoapps.contentstore.rest_api.v2.serializers.course_outline import (
    OUTLINE_DEFAULT_DEPTH,
    OUTLINE_FULL_VIEW,
    OUTLINE_MAX_DEPTH,
    OUTLINE_MINIMAL_VIEW,
    CourseOutlineFullSerializer,
    CourseOutlineNodeSerializer,
    CourseOutlineQuerySerializer,
)
from cms.djangoapps.contentstore.views.permissions import CanViewCourse


def _query_parameter(name, **kwargs):
    return OpenApiParameter(
        name=name,
        description=CourseOutlineQuerySerializer().fields[name].help_text,
        required=False,
        location=OpenApiParameter.QUERY,
        **kwargs,
    )


_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_EXAMPLE_COURSE = "course-v1:edX+DemoX+2026"
_EXAMPLE_BLOCK = "block-v1:edX+DemoX+2026+type@{}+block@{}"
_EXAMPLE_COURSE_BLOCK = _EXAMPLE_BLOCK.format("course", "course")

MINIMAL_VIEW_EXAMPLE = OpenApiExample(
    "Outline tree",
    summary="Default view, two levels deep",
    description="A subsection with units carries children_url instead of children.",
    response_only=True,
    status_codes=["200"],
    value={
        "id": _EXAMPLE_COURSE_BLOCK,
        "category": "course",
        "display_name": "Demonstration Course",
        "children": [
            {
                "id": _EXAMPLE_BLOCK.format("chapter", "intro"),
                "category": "chapter",
                "display_name": "Introduction",
                "children": [
                    {
                        "id": _EXAMPLE_BLOCK.format("sequential", "welcome"),
                        "category": "sequential",
                        "display_name": "Welcome",
                        "children_url": (
                            f"https://studio.example.com/api/authoring/v2/courses/{_EXAMPLE_COURSE}/outline/"
                            "?usage_key=block-v1%3AedX%2BDemoX%2B2026%2Btype%40sequential%2Bblock%40welcome&depth=1"
                        ),
                    },
                    {
                        "id": _EXAMPLE_BLOCK.format("sequential", "coming_soon"),
                        "category": "sequential",
                        "display_name": "Coming soon",
                        "children": [],
                    },
                ],
            },
        ],
    },
)

FULL_VIEW_EXAMPLE = OpenApiExample(
    "Outline page data",
    summary="view=full, abridged",
    description="course_structure is cut to the course block and shows a few of each block's keys.",
    response_only=True,
    status_codes=["200"],
    value={
        "course_release_date": "Jan 15, 2026 at 00:00 UTC",
        "course_structure": {
            "id": _EXAMPLE_COURSE_BLOCK,
            "display_name": "Demonstration Course",
            "category": "course",
            "has_children": True,
            "published": True,
            "visibility_state": "live",
            "start": "2026-01-15T00:00:00Z",
            "child_info": {"category": "chapter", "display_name": "Section", "children": []},
        },
        "deprecated_blocks_info": {
            "deprecated_enabled_block_types": [],
            "blocks": [],
            "advance_settings_url": f"/settings/advanced/{_EXAMPLE_COURSE}",
        },
        "discussions_incontext_feedback_url": "",
        "discussions_incontext_learnmore_url": "",
        "discussions_settings": {
            "enable_in_context": True,
            "enable_graded_units": False,
            "unit_level_visibility": True,
            "discussion_configuration_url": (
                f"https://apps.example.com/authoring/course/{_EXAMPLE_COURSE}/pages-and-resources/discussion/settings"
            ),
        },
        "initial_state": None,
        "language_code": "en",
        "lms_link": f"https://lms.example.com/courses/{_EXAMPLE_COURSE}/jump_to/{_EXAMPLE_COURSE_BLOCK}",
        "mfe_proctored_exam_settings_url": "",
        "notification_dismiss_url": None,
        "proctoring_errors": [],
        "reindex_link": None,
        "rerun_notification_id": None,
        "advance_settings_url": f"/settings/advanced/{_EXAMPLE_COURSE}",
        "is_custom_relative_dates_active": False,
        "created_on": "2025-11-02T09:30:00Z",
    },
)

_READ_RESPONSE = PolymorphicProxySerializer(
    component_name="CourseOutlineRead",
    serializers=[CourseOutlineNodeSerializer, CourseOutlineFullSerializer],
    resource_type_field_name=None,
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseOutlineViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The outline of one course: its sections, subsections and units.

    By default the outline is a tree cut at a requested depth, where a block
    with deeper children carries the address that returns them. With
    ``view=full`` the whole outline is returned with everything the Studio
    outline page shows beside it.

    Callable by anyone who may view the course in Studio. The course is looked
    up first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403).

    A plain ``ViewSet``: the outline is read from the modulestore, not from
    database rows.
    """

    permission_classes = (IsAuthenticated, CanViewCourse)
    serializer_class = CourseOutlineNodeSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the outline node serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get a course's outline",
        description=(
            "Returns the course outline as a tree of blocks, by default two levels deep: sections and "
            "their subsections. A block whose children lie below the requested depth carries "
            "children_url, which returns them one level deep. usage_key returns the tree under any "
            "container in the course outline, such as a section, subsection or unit. With view=full the "
            "response is the whole outline down to units with the course's release date, discussion and "
            "proctoring settings, rerun notification and the Studio addresses the outline page links to."
        ),
        parameters=[
            _COURSE_KEY_PARAMETER,
            _query_parameter(
                "view", type=str, enum=[OUTLINE_MINIMAL_VIEW, OUTLINE_FULL_VIEW], default=OUTLINE_MINIMAL_VIEW,
            ),
            _query_parameter(
                "depth",
                type={"type": "integer", "minimum": 1, "maximum": OUTLINE_MAX_DEPTH},
                default=OUTLINE_DEFAULT_DEPTH,
            ),
            _query_parameter("usage_key", type=str),
            _query_parameter("show", type=str),
        ],
        responses={
            200: OpenApiResponse(
                response=_READ_RESPONSE,
                description="The outline tree, or with view=full the outline page's data.",
            ),
            400: _error(
                "A query parameter has an invalid value, does not apply to the requested view, or "
                "usage_key is not a container in this course's outline."
            ),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course in Studio."),
            404: _error("The course does not exist."),
        },
        examples=[MINIMAL_VIEW_EXAMPLE, FULL_VIEW_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the course outline, as a tree or with ``view=full`` as the outline page's data."""
        query = CourseOutlineQuerySerializer(data=request.query_params, context={"course_key": course_key})
        query.is_valid(raise_exception=True)
        params = query.validated_data
        if params["view"] == OUTLINE_FULL_VIEW:
            outline = get_full_outline(request, course_key, show=params.get("show"))
            return Response(CourseOutlineFullSerializer(outline).data)
        tree = get_outline_tree(request, course_key, params["depth"], usage_key=params.get("usage_key"))
        return Response(self.get_serializer(tree).data)
