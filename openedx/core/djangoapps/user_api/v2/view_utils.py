"""View behaviour shared by the endpoints of version 2 of the User API."""

import logging

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

from .serializers import ErrorResponse

log = logging.getLogger(__name__)

#: Groups the user addresses of every version under one heading in the schema.
USER_TAG = "user"

#: Marks the operations a generated client library is built from.
SDK_TAG = "openedx-platform-sdk"

#: The framework's own representation selector. This deployment reads nothing
#: from it, since ``URL_FORMAT_OVERRIDE`` is unset and JSON is the only
#: renderer, and a caller that sends it anyway is answered rather than refused.
FORMAT_PARAM = "format"

#: The whole of a username, as every route of this version matches one. A
#: client built from the published document holds a name to this shape before
#: it sends a request, so each address that carries a name is described with
#: it and no address of one resource admits a name another refuses.
USERNAME_PATTERN = rf"^{settings.USERNAME_REGEX_PARTIAL}$"


def query_param(name, description, **kwargs):
    """Build an optional query parameter for the published schema."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=False,
        location=OpenApiParameter.QUERY,
        **kwargs,
    )


def path_param(name, description, pattern):
    """Build a path parameter for the published schema, carrying what its route matches."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=True,
        location=OpenApiParameter.PATH,
        type=str,
        pattern=pattern,
    )


def names_in(value):
    """
    Return the names a comma-separated value carries, in the order they were sent.

    A position carrying nothing names nothing, so an empty one is left out
    rather than read as a name no account holds. Every collection of this
    version that selects rows by name reads its value through here, so the
    same value selects the same rows wherever it is sent.
    """
    return [name for name in value.strip(",").split(",") if name]


RESP_UNAUTHENTICATED = OpenApiResponse(ErrorResponse, description="The requester is not authenticated.")
RESP_UNREADABLE_BODY = OpenApiResponse(
    ErrorResponse, description="The request body is in a media type this endpoint cannot read.",
)


class StrictQueryParametersMixin:
    """
    Refuses a query parameter the view reads nothing from.

    An unknown parameter is otherwise dropped in silence: a caller that
    mistyped a filter, or kept one an earlier version of the endpoint
    answered, receives the whole collection and reads it as the narrowed page
    it asked for. The names a view answers to are assembled from what it
    declares (its paginator, its sort backend, its filter set, and the
    serializer that validates its query string), so a parameter that is added
    or renamed cannot fall out of step with this check.
    """

    #: Names the view reads that none of the declarations below account for.
    extra_query_params = ()

    def known_query_params(self):
        """Return the names of the query parameters this view reads."""
        names = {FORMAT_PARAM, api_settings.URL_FORMAT_OVERRIDE, *self.extra_query_params}
        pagination_class = getattr(self, "pagination_class", None)
        if pagination_class is not None:
            names.update({pagination_class.page_query_param, pagination_class.page_size_query_param})
        for backend in getattr(self, "filter_backends", ()):
            names.add(getattr(backend, "ordering_param", None))
        filterset_class = getattr(self, "filterset_class", None)
        if filterset_class is not None:
            names.update(filterset_class.base_filters)
        query_serializer_class = getattr(self, "query_serializer_class", None)
        if query_serializer_class is not None:
            names.update(query_serializer_class().fields)
        return names - {None}

    def initial(self, request, *args, **kwargs):
        """Check the query string once the caller has been admitted."""
        super().initial(request, *args, **kwargs)
        known = self.known_query_params()
        unknown = sorted(set(request.query_params) - known)
        if unknown:
            log.info("Query refused on %s: %r", type(self).__name__, unknown)
            offered = ", ".join(sorted(known))
            raise ValidationError({
                name: [f"No such query parameter. Send any of: {offered}."]
                for name in unknown
            })
