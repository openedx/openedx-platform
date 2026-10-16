"""API views for the legacy library list."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import DefaultPagination, IterablePaginationMixin
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.v4.legacy_libraries_service import (
    format_legacy_libraries,
    list_legacy_libraries,
)
from cms.djangoapps.contentstore.rest_api.v4.serializers.legacy_libraries import (
    LegacyLibraryListQuerySerializer,
    LegacyLibrarySerializer,
)


class LegacyLibraryPagination(DefaultPagination):
    """
    The standard page envelope, with a schema that describes all seven of its keys.

    ``DefaultPagination`` returns seven keys but inherits a response schema
    that documents only four, so generated clients would drop ``num_pages``,
    ``current_page`` and ``start``. The inherited ``page_size`` description
    also leaves out the default and the cap.

    Delete this subclass and use ``DefaultPagination`` directly once
    edx-drf-extensions documents all seven keys in
    ``DefaultPagination.get_paginated_response_schema`` and the page size
    limits in its ``page_size`` description.
    """

    page_size_query_description = (
        "Number of libraries per page. Defaults to 10; a value above 100 returns 100 libraries per page."
    )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "num_pages", "current_page", "start", "next", "previous", "results"],
            "properties": {
                "count": {"type": "integer", "description": "Total number of items."},
                "num_pages": {"type": "integer", "description": "Total number of pages."},
                "current_page": {"type": "integer", "description": "Number of this page, starting at 1."},
                "start": {"type": "integer", "description": "Zero-based index of the first item on this page."},
                "next": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the next page, or null on the last page.",
                },
                "previous": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the previous page, or null on the first page.",
                },
                "results": schema,
            },
        }


@extend_schema(tags=["openedx-platform-sdk"])
class LegacyLibraryViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The legacy libraries the requester can read, one page at a time.

    Any authenticated user may call it; each library is listed only if the
    requester has Studio read access to it, and ``can_edit`` says whether they
    also have write access. Global staff see every legacy library. The list is
    empty while legacy libraries are disabled. Listing changes nothing.

    A plain ``ViewSet``: legacy libraries are read from the modulestore, not
    from database rows.
    """

    # The Studio library lists this one replaces accept sessions of inactive
    # users, so this one does too and their callers keep the same access.
    # Inactive users fail every library role check: of them, only global staff
    # are shown any library.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = LegacyLibrarySerializer
    pagination_class = LegacyLibraryPagination

    def get_serializer(self, *args, **kwargs):
        """Instantiate the legacy library serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List legacy libraries",
        description=(
            "Returns one page of the legacy libraries the requester can read, ordered by display name "
            "ignoring case and then by library key. Content libraries, which replace legacy libraries, "
            "are listed by `/api/libraries/v2/`."
        ),
        parameters=[LegacyLibraryListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=LegacyLibrarySerializer,
                description="A page of legacy libraries.",
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="A filter value is empty or not valid.",
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester is not authenticated.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    "The requested page does not exist, for example a page past the last one "
                    "or a `page` that is not a number."
                ),
            ),
        },
    )
    def list(self, request):
        """Return one page of the legacy libraries the requester can read."""
        query = LegacyLibraryListQuerySerializer(data=request.query_params.dict())
        query.is_valid(raise_exception=True)
        libraries, migrations = list_legacy_libraries(request.user, **query.validated_data)
        return self.paginate_iterable(
            request,
            libraries,
            serialize=lambda page: self.get_serializer(
                format_legacy_libraries(page, request, migrations), many=True,
            ).data,
        )
