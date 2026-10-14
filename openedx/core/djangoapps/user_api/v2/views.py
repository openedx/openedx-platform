"""Views for version 2 of the User API."""

import logging

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import F
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import IterablePaginationMixin
from edx_rest_framework_extensions.scoping import FullScopePolicy, ScopedQuerysetMixin
from edx_rest_framework_extensions.shaping import MinimalViewMixin, project
from rest_framework import permissions, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.reverse import reverse
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import get_country_time_zones

from .errors import CatalogedRequestErrorsMixin
from .filters import StrictOrderingFilter, UserFilterSet, UserPreferenceFilterSet
from .paginators import UserApiPagination
from .permissions import IsActiveUser
from .schema import ResponseShapes
from .serializers import (
    FULL_VIEW,
    MINIMAL_VIEW,
    PREFERENCE_MINIMAL_FIELDS,
    USER_MINIMAL_FIELDS,
    ErrorResponse,
    TimeZoneQuerySerializer,
    TimeZoneSerializer,
    UserDirectoryMinimalSerializer,
    UserDirectoryProjectionSerializer,
    UserDirectorySerializer,
    UserPreferenceRecordMinimalSerializer,
    UserPreferenceRecordProjectionSerializer,
    UserPreferenceRecordSerializer,
    full_view_requested,
)

log = logging.getLogger(__name__)

USER_TAG = "user"

#: Fields the user directory sorts on, and the order it uses when asked for none.
USER_SORT_FIELDS = ("username", "date_joined")
USER_DEFAULT_SORT = ("username",)

#: Fields the stored-preference list sorts on, and the order it uses when asked for none.
PREFERENCE_SORT_FIELDS = ("key", "username")
PREFERENCE_DEFAULT_SORT = ("username", "key")

#: The part of a preference's address that carries its key. The key is
#: free-form text that may itself contain a comma, so it takes the rest of the
#: segment; the route and the published address are built from this one pattern.
PREFERENCE_KEY_PATTERN_PARTIAL = "[^/]+"


def _query_param(name, description, **kwargs):
    """Build an optional query parameter for the published schema."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=False,
        location=OpenApiParameter.QUERY,
        **kwargs,
    )


def _path_param(name, description, pattern):
    """Build a path parameter for the published schema, carrying what its route matches."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=True,
        location=OpenApiParameter.PATH,
        type=str,
        pattern=pattern,
    )


_USERNAME_PATTERN = rf"^{settings.USERNAME_REGEX_PARTIAL}$"

_USER_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose directory entry is returned.",
    _USERNAME_PATTERN,
)
_PREFERENCE_USERNAME_PATH_PARAM = _path_param(
    "username",
    "Username of the user the preference belongs to: the part of the address before the comma.",
    _USERNAME_PATTERN,
)
_PREFERENCE_KEY_PATH_PARAM = _path_param(
    "key",
    "Name the preference is stored under: the part of the address after the comma.",
    rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$",
)

#: Parameters that shape a response rather than select the rows in it.
VIEW_PARAM = "view"
FIELDS_PARAM = "fields"
RESPONSE_SHAPING_PARAMS = (VIEW_PARAM, FIELDS_PARAM)

#: The framework's own representation selector. This deployment reads nothing
#: from it, since ``URL_FORMAT_OVERRIDE`` is unset and JSON is the only
#: renderer, and a caller that sends it anyway is answered rather than refused.
FORMAT_PARAM = "format"

_FIELDS_PARAM = _query_param(
    FIELDS_PARAM,
    "Comma-separated list of top-level field names to keep in each returned object. "
    "A name the response cannot carry is refused.",
)
#: The presets each list answers to, published as the parameter's enum and
#: accepted by the view from the same declaration.
USER_PRESETS = (MINIMAL_VIEW,)
PREFERENCE_PRESETS = (FULL_VIEW, MINIMAL_VIEW)

_MINIMAL_VIEW_PARAM = _query_param(
    VIEW_PARAM,
    "Response preset. `minimal` reduces each user to its identifying fields "
    "(`id`, `username`, `email`). Omit it to receive the full entry.",
    enum=list(USER_PRESETS),
)
_PREFERENCE_VIEW_PARAM = _query_param(
    VIEW_PARAM,
    "Response preset. `full` embeds the owning user's whole directory entry in each "
    "preference; `minimal` returns only `username`, `key` and `value`. Omit it to "
    "receive the owner's username alongside the preference.",
    enum=list(PREFERENCE_PRESETS),
)


def _ordering_param(sort_fields, default):
    """Build the sort parameter of a list, naming the fields it accepts."""
    return _query_param(
        "ordering",
        "Fields to sort by, in order of precedence, each optionally prefixed with `-` to sort "
        "descending. A field that is not listed here is refused.",
        many=True,
        enum=[*sort_fields, *(f"-{field}" for field in sort_fields)],
        default=list(default),
        style="form",
        explode=False,
    )


_USER_ORDERING_PARAM = _ordering_param(USER_SORT_FIELDS, USER_DEFAULT_SORT)
_PREFERENCE_ORDERING_PARAM = _ordering_param(PREFERENCE_SORT_FIELDS, PREFERENCE_DEFAULT_SORT)

_RESP_UNAUTHENTICATED = OpenApiResponse(ErrorResponse, description="The requester is not authenticated.")
_RESP_FORBIDDEN = OpenApiResponse(ErrorResponse, description="The requester may not use this endpoint.")
_RESP_NOT_FOUND = OpenApiResponse(ErrorResponse, description="No such resource.")
_RESP_NO_SUCH_PAGE = OpenApiResponse(ErrorResponse, description="There is no such page of results.")
_RESP_BAD_REQUEST = OpenApiResponse(
    ErrorResponse, description="A query parameter is missing, invalid, or not one this endpoint reads.",
)

#: A directory entry comes back whole, as the identifying fields the ``minimal``
#: preset keeps, or as the properties ``fields`` selected.
_USER_SHAPES = ResponseShapes(
    component_name="UserDirectoryResponse",
    serializers=[
        UserDirectorySerializer,
        UserDirectoryMinimalSerializer,
        UserDirectoryProjectionSerializer,
    ],
)

#: A stored preference comes back whole, as the three fields the ``minimal``
#: preset keeps, or as the properties ``fields`` selected.
_PREFERENCE_SHAPES = ResponseShapes(
    component_name="UserPreferenceRecordResponse",
    serializers=[
        UserPreferenceRecordSerializer,
        UserPreferenceRecordMinimalSerializer,
        UserPreferenceRecordProjectionSerializer,
    ],
)


class _MemberLookupMixin:
    """Report a missing member through the standard error envelope."""

    def get_object(self):
        try:
            return super().get_object()
        except Http404 as exc:
            raise NotFound from exc


class _ActionAddressMixin:
    """
    Builds the address of an action from the snake_case names these routes carry.

    A router names an action ``<basename>-<action>`` by default, and that name
    belongs to an earlier mount of the same resource, so the default lookup
    would hand out the older address instead of this one.
    """

    #: The namespace these routes are mounted under, read when an address is
    #: asked for outside a request and there is no resolver match to take it from.
    url_namespace = "user_v2"

    def reverse_action(self, url_name, *args, **kwargs):
        """Return the address of one of this viewset's actions."""
        request = getattr(self, "request", None)
        namespace = self.url_namespace
        if request is not None and request.resolver_match is not None:
            namespace = request.resolver_match.namespace or namespace
        kwargs.setdefault("request", request)
        return reverse(f"{namespace}:{self.basename}_{url_name}", *args, **kwargs)


def _requested_field_names(raw):
    """Return the top-level property names a ``fields`` value asks for."""
    if not raw:
        return []
    return [name.strip().split(".", 1)[0] for name in raw.split(",") if name.strip()]


class _StrictQueryParametersMixin:
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


class _ResponsePresetMixin(MinimalViewMixin):
    """
    Applies the ``view`` and ``fields`` presets to an already serialized payload.

    A view lists the presets it answers to, and a ``view`` value outside that
    list is refused: a caller that mistypes a preset asked for a smaller
    payload and would otherwise be handed the full one without being told.
    The same holds for a field name the response cannot carry, which would
    otherwise select nothing and leave the caller with empty objects.
    """

    #: Presets this view answers to. An empty ``view`` narrows nothing, the way
    #: an empty value of any other parameter does.
    response_presets = ()

    def initial(self, request, *args, **kwargs):
        """Check the requested preset and fields once the caller has been admitted."""
        super().initial(request, *args, **kwargs)
        preset = request.query_params.get(VIEW_PARAM)
        if preset and preset not in self.response_presets:
            raise ValidationError({
                VIEW_PARAM: [
                    f"Ask for any of: {', '.join(self.response_presets)}, "
                    "or omit this parameter for the default response.",
                ],
            })
        self.check_requested_fields(request)

    def shapeable_fields(self):
        """Return the names of the properties a response of this view can carry."""
        if self.is_minimal_view_requested():
            return set(self.minimal_fields)
        return set(self.get_serializer_class()().fields)

    def check_requested_fields(self, request):
        """Refuse a selected field the response cannot carry, naming the ones it can."""
        available = self.shapeable_fields()
        unknown = sorted(set(_requested_field_names(request.query_params.get(FIELDS_PARAM))) - available)
        if unknown:
            raise ValidationError({
                FIELDS_PARAM: [
                    f"No such field: {', '.join(unknown)}. "
                    f"Select any of: {', '.join(sorted(available))}.",
                ],
            })

    def shape(self, data, request):
        """Return ``data`` reduced to the preset and the fields the caller asked for."""
        data = self.shape_minimal(data, request)
        requested_fields = request.query_params.get(FIELDS_PARAM)
        if not requested_fields:
            return data
        if isinstance(data, list):
            return [project(item, requested_fields) for item in data]
        return project(data, requested_fields)

    def shape_page(self, response, request):
        """Shape the items of a paginated response in place."""
        response.data["results"] = self.shape(response.data["results"], request)
        return response


@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List users",
        description=(
            "Returns a page of the user directory. Supply `preference_key` to keep only the "
            "users who have stored a preference under that key, or `forum_role` together with "
            "`course_key` to keep only the holders of a course forum role."
        ),
        parameters=[_MINIMAL_VIEW_PARAM, _FIELDS_PARAM, _USER_ORDERING_PARAM],
        responses={
            200: _USER_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: _RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE,
        },
    ),
    retrieve=extend_schema(
        summary="Get one user",
        description="Returns the directory entry of the user with this username.",
        parameters=[_USER_PATH_PARAM, _MINIMAL_VIEW_PARAM, _FIELDS_PARAM],
        responses={
            200: _USER_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: _RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
        },
    ),
)
class UserViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    _ActionAddressMixin,
    _MemberLookupMixin,
    _ResponsePresetMixin,
    _StrictQueryParametersMixin,
    ScopedQuerysetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Read-only directory of platform users, addressed by username.

    Access is layered: ``permission_classes`` admits only platform
    administrators whose account is still active, the scoping policy decides
    which rows such an administrator may see, and the filter set narrows those
    rows further by the caller's query parameters.
    """

    permission_classes = (permissions.IsAuthenticated, IsActiveUser, permissions.IsAdminUser)
    serializer_class = UserDirectorySerializer
    pagination_class = UserApiPagination
    queryset = User.objects.all().prefetch_related("preferences").select_related("profile")
    # Platform administrators may read every entry, so the policy keeps the
    # queryset whole; a narrower policy can replace it without touching the
    # filtering below.
    scoping_policy = FullScopePolicy()
    lookup_field = "username"
    # The router's default lookup pattern excludes dots, which usernames allow.
    lookup_value_regex = settings.USERNAME_REGEX_PARTIAL
    filter_backends = (DjangoFilterBackend, StrictOrderingFilter)
    filterset_class = UserFilterSet
    ordering_fields = USER_SORT_FIELDS
    ordering = USER_DEFAULT_SORT
    # A sort the caller asks for is completed with these so that it is total:
    # rows that compare equal would otherwise be ordered differently from one
    # page to the next, and a client reading the pages in turn would see some
    # of them twice and miss others.
    ordering_tiebreak = ("username",)
    minimal_fields = USER_MINIMAL_FIELDS
    response_presets = USER_PRESETS
    extra_query_params = RESPONSE_SHAPING_PARAMS

    def list(self, request, *args, **kwargs):
        """Return a page of directory entries, shaped as the caller asked."""
        return self.shape_page(super().list(request, *args, **kwargs), request)

    def retrieve(self, request, *args, **kwargs):
        """Return one directory entry, shaped as the caller asked."""
        response = super().retrieve(request, *args, **kwargs)
        response.data = self.shape(response.data, request)
        return response


@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List stored preferences",
        description=(
            "Returns a page of stored user preferences. Supply `key` to keep only the "
            "preferences stored under that name, or `username` to keep only one user's."
        ),
        parameters=[_PREFERENCE_VIEW_PARAM, _FIELDS_PARAM, _PREFERENCE_ORDERING_PARAM],
        responses={
            200: _PREFERENCE_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: _RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE,
        },
    ),
    retrieve=extend_schema(
        summary="Get one stored preference",
        description=(
            "Returns the preference stored under this key for this user. A preference is "
            "identified by the pair, which the address carries as the username, a comma and "
            "the key, for example `alice,time_zone`."
        ),
        operation_id="v2_user_preferences_retrieve",
        parameters=[
            _PREFERENCE_USERNAME_PATH_PARAM,
            _PREFERENCE_KEY_PATH_PARAM,
            _PREFERENCE_VIEW_PARAM,
            _FIELDS_PARAM,
        ],
        responses={
            200: _PREFERENCE_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: _RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
        },
    ),
)
class UserPreferenceViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    _ActionAddressMixin,
    _ResponsePresetMixin,
    _StrictQueryParametersMixin,
    ScopedQuerysetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Read-only list of stored user preferences, each addressed by its owner and key.

    A preference is identified by the pair, so the member address is
    ``{username},{key}`` rather than a database id. Access is layered the same
    way as the user directory: permission classes, then the scoping policy,
    then the filter set.
    """

    permission_classes = (permissions.IsAuthenticated, IsActiveUser, permissions.IsAdminUser)
    serializer_class = UserPreferenceRecordSerializer
    pagination_class = UserApiPagination
    queryset = (
        UserPreference.objects.all()
        .select_related("user", "user__profile")
        .annotate(username=F("user__username"))
    )
    scoping_policy = FullScopePolicy()
    filter_backends = (DjangoFilterBackend, StrictOrderingFilter)
    filterset_class = UserPreferenceFilterSet
    ordering_fields = PREFERENCE_SORT_FIELDS
    ordering = PREFERENCE_DEFAULT_SORT
    # The owner and the key identify a row, so they complete any sort the
    # caller asks for and keep the order of a page stable.
    ordering_tiebreak = ("username", "key")
    minimal_fields = PREFERENCE_MINIMAL_FIELDS
    response_presets = PREFERENCE_PRESETS
    extra_query_params = RESPONSE_SHAPING_PARAMS

    def filter_queryset(self, queryset):
        """Fetch each owner's own preferences too when they are about to be embedded."""
        queryset = super().filter_queryset(queryset)
        if full_view_requested(getattr(self, "request", None)):
            queryset = queryset.prefetch_related("user__preferences")
        return queryset

    def get_object(self):
        """Look the preference up by the username and key that identify it."""
        try:
            preference = self.get_queryset().get(
                user__username=self.kwargs["username"], key=self.kwargs["key"],
            )
        except UserPreference.DoesNotExist as exc:
            raise NotFound from exc
        self.check_object_permissions(self.request, preference)
        return preference

    def list(self, request, *args, **kwargs):
        """Return a page of stored preferences, shaped as the caller asked."""
        return self.shape_page(super().list(request, *args, **kwargs), request)

    def retrieve(self, request, *args, **kwargs):
        """Return one stored preference, shaped as the caller asked."""
        response = super().retrieve(request, *args, **kwargs)
        response.data = self.shape(response.data, request)
        return response


@extend_schema(tags=[USER_TAG])
class CountryTimeZoneViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    _ActionAddressMixin,
    _StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    Time zones commonly used in a country, or every time zone when no country is named.

    The rows come from the time-zone database rather than from a queryset, so
    this is a plain ViewSet whose one query parameter is validated by a
    serializer. The reference data it returns is the same for every caller and
    is public on purpose.
    """

    permission_classes = (permissions.AllowAny,)
    serializer_class = TimeZoneSerializer
    #: Validates the query string, and names the parameters this view reads.
    query_serializer_class = TimeZoneQuerySerializer
    pagination_class = UserApiPagination

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List time zones",
        description=(
            "Returns a page of time zones, ordered by their display form. Supply "
            "`country_code` to receive the time zones commonly used in one country."
        ),
        # The parameters are published from the serializer that validates them,
        # so the constraint a caller is held to is the one it reads.
        parameters=[TimeZoneQuerySerializer],
        responses={
            200: TimeZoneSerializer,
            400: _RESP_BAD_REQUEST,
            404: _RESP_NO_SUCH_PAGE,
        },
    )
    def list(self, request):
        """Return a page of time zones, for one country or for the world."""
        query = self.query_serializer_class(data=request.query_params)
        query.is_valid(raise_exception=True)
        time_zones = get_country_time_zones(query.validated_data.get("country_code"))
        return self.paginate_iterable(request, time_zones)


@extend_schema(exclude=True)
class UnknownAddressView(StandardizedErrorMixin, APIView):
    """
    Refuses every address under this version of the API that no route serves.

    It serves no resource: it exists so that a mistyped address is answered
    with the same JSON body as every other failure instead of the service's
    HTML error page, which a client that asked for JSON cannot read.
    """

    permission_classes = (permissions.AllowAny,)

    def initial(self, request, *args, **kwargs):
        """Refuse the address before a handler is chosen, whatever the method."""
        raise NotFound
