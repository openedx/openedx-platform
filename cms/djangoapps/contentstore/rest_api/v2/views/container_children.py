"""API views for the children of a course container."""

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import DefaultPagination, IterablePaginationMixin
from edx_rest_framework_extensions.url_converters import UsageKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.v2.container_children_service import list_container_children
from cms.djangoapps.contentstore.rest_api.v2.permissions import CanViewCourse
from cms.djangoapps.contentstore.rest_api.v2.serializers.container_children import (
    CourseContainerChildFullSerializer,
    CourseContainerChildQuerySerializer,
    CourseContainerChildSerializer,
)

CONTAINER_MINIMAL_VIEW = "minimal"
CONTAINER_FULL_VIEW = "full"


class CourseContainerChildPagination(DefaultPagination):
    """
    The standard page envelope, with a schema that describes all seven of its keys.

    ``DefaultPagination`` returns seven keys but inherits a response schema
    that documents only four, so generated clients would drop ``num_pages``,
    ``current_page`` and ``start``.

    Delete this subclass and use ``DefaultPagination`` directly once
    edx-drf-extensions documents all seven keys in
    ``DefaultPagination.get_paginated_response_schema``.
    """

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "num_pages", "current_page", "start", "next", "previous", "results"],
            "properties": {
                "count": {"type": "integer", "description": "Total number of children.", "example": 2},
                "num_pages": {"type": "integer", "description": "Total number of pages.", "example": 1},
                "current_page": {"type": "integer", "description": "Number of this page, starting at 1.", "example": 1},
                "start": {
                    "type": "integer",
                    "description": "Zero-based index of the first child on this page.",
                    "example": 0,
                },
                "next": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the next page, or null on the last page.",
                    "example": None,
                },
                "previous": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the previous page, or null on the first page.",
                    "example": None,
                },
                "results": schema,
            },
        }


_USAGE_KEY_PARAMETER = OpenApiParameter(
    name="usage_key",
    description=(
        "Usage key of a section, subsection, unit or other container of a course, such as "
        "block-v1:edX+DemoX+2026+type@vertical+block@welcome."
    ),
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{UsageKeyConverter.regex}$",
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=str(CourseContainerChildQuerySerializer().fields["view"].help_text or ""),
    required=False,
    location=OpenApiParameter.QUERY,
    type=str,
    enum=[CONTAINER_MINIMAL_VIEW, CONTAINER_FULL_VIEW],
    default=CONTAINER_MINIMAL_VIEW,
)

MINIMAL_VIEW_EXAMPLE = OpenApiExample(
    "Child",
    summary="A child in the default view",
    description="A unit holding a split test: the child holds children of its own.",
    response_only=True,
    status_codes=["200"],
    value={
        "id": "block-v1:edX+DemoX+2026+type@split_test+block@experiment",
        "category": "split_test",
        "display_name": "Experiment",
        "display_type": "Content Experiment",
        "has_children": True,
    },
)

FULL_VIEW_EXAMPLE = OpenApiExample(
    "Child with unit page data",
    summary="A child with view=full",
    description="A block linked to a library block that has a newer version.",
    response_only=True,
    status_codes=["200"],
    value={
        "name": "Introduction",
        "block_id": "block-v1:edX+DemoX+2026+type@html+block@intro_text",
        "block_type": "html",
        "user_partition_info": {
            "selectable_partitions": [],
            "selected_partition_index": -1,
            "selected_groups_label": "",
        },
        "user_partitions": [
            {
                "id": 50,
                "name": "Enrollment Track Groups",
                "scheme": "enrollment_track",
                "groups": [{"id": 1, "name": "Audit", "selected": False, "deleted": False}],
            },
        ],
        "upstream_link": {
            "upstream_ref": "lb:edX:demo_library:html:shared_text",
            "version_synced": 1,
            "version_available": 2,
            "version_declined": None,
            "error_message": None,
            "ready_to_sync": True,
            "downstream_customized": [],
            "top_level_parent_key": None,
        },
        "actions": {
            "can_copy": True,
            "can_duplicate": True,
            "can_move": True,
            "can_manage_access": True,
            "can_delete": True,
            "can_manage_tags": True,
        },
        "validation_messages": [
            {"text": "This component's access settings contradict its parent's access settings.", "type": "error"},
        ],
        "render_error": "",
    },
)

_ITEM_SCHEMA = PolymorphicProxySerializer(
    component_name="CourseContainerChildRead",
    serializers=[CourseContainerChildSerializer, CourseContainerChildFullSerializer],
    resource_type_field_name=None,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseContainerChildrenViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The blocks placed directly in a container of a course, in the order the container stores them.

    One level only: a child that is itself a container is listed as one block,
    and its own children are read from its address. Only the children on the
    requested page are described in full, so the cost of a request follows the
    page size, not the size of the container.

    Callable by anyone who may view the course in Studio. The course is looked
    up first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403), and only a caller who may view the course
    learns whether a block exists in it. Every child is listed for such a
    caller, including children hidden from learners.

    A plain ``ViewSet``: the children are read from the modulestore, not from
    database rows. Their order is the order of the container, so there is no
    ordering parameter.
    """

    permission_classes = (IsAuthenticated, CanViewCourse)
    serializer_class = CourseContainerChildSerializer
    pagination_class = CourseContainerChildPagination

    def get_serializer(self, *args, **kwargs):
        """Instantiate the child serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List the children of a container",
        description=(
            "Returns one page of the blocks placed directly in a section, subsection, unit or other container of "
            "the course, in the order the container stores them. By default each child is described briefly. "
            "With view=full each child carries what the Studio unit page shows for it: the groups it is "
            "visible to, its link to a library block, the actions available, its validation messages and "
            "whether it renders. The course itself, components that cannot hold children, and blocks that "
            "do not exist are not containers and are answered with 404."
        ),
        parameters=[_USAGE_KEY_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=_ITEM_SCHEMA,
                description="A page of the container's children.",
            ),
            400: _error("view has an invalid value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course in Studio."),
            404: _error(
                "The course does not exist, the key does not name a block of a course, the block is not a "
                "container, or the requested page is out of range."
            ),
            406: _error("The Accept header admits no JSON media type."),
        },
        examples=[MINIMAL_VIEW_EXAMPLE, FULL_VIEW_EXAMPLE],
    )
    def list(self, request, usage_key):
        """Return one page of the container's children."""
        query = CourseContainerChildQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        full = query.validated_data["view"] == CONTAINER_FULL_VIEW
        serializer = CourseContainerChildFullSerializer if full else CourseContainerChildSerializer

        def paginate(children, describe_page):
            return self.paginate_iterable(
                request, children, serialize=lambda page: serializer(describe_page(page), many=True).data,
            )

        return list_container_children(request, usage_key, full, paginate)
