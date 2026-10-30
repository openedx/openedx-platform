"""Views for version 2 of the User API."""

import contextlib
import logging

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from django_ratelimit.core import is_ratelimited
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import IterablePaginationMixin
from edx_rest_framework_extensions.permissions import IsStaff
from edx_rest_framework_extensions.scoping import FullScopePolicy, ScopedQuerysetMixin
from edx_rest_framework_extensions.shaping import MinimalViewMixin, project
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from common.djangoapps.student.models import UserProfile, is_email_retired
from common.djangoapps.student.models_api import confirm_name_change, do_name_change_request, get_pending_name_change
from common.djangoapps.track import segment
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.accounts.api import get_account_settings, update_account_settings
from openedx.core.djangoapps.user_api.accounts.permissions import CanDeactivateUser, CanGetAccountInfo
from openedx.core.djangoapps.user_api.errors import (
    AccountUpdateError,
    AccountValidationError,
    PreferenceUpdateError,
    PreferenceValidationError,
    UserAPIInternalError,
    UserNotAuthorized,
    UserNotFound,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import (
    delete_user_preference,
    get_country_time_zones,
    get_user_preference,
    get_user_preferences,
    set_user_preference,
    update_email_opt_in,
    update_user_preferences,
)
from openedx.core.lib.api.parsers import MergePatchParser

from .errors import CatalogedRequestErrorsMixin, ErrorExtensionsMixin, RetiredAccount
from .filters import StrictOrderingFilter, UserFilterSet, UserPreferenceFilterSet
from .paginators import UserApiPagination
from .permissions import CanSelectAccountsByContact, IsActiveUser, IsAddressedUser, IsStaffOrAbsent
from .schema import ResponseShapes
from .selectors import (
    accounts_holding_emails,
    cancellable_retirement_for_email,
    username_holding_email,
    username_holding_id,
    usernames_among,
)
from .serializers import (
    ACCOUNT_DEFAULT_SORT,
    ACCOUNT_MINIMAL_FIELDS,
    ACCOUNT_PREFERENCE_CHANGES_SCHEMA,
    ACCOUNT_PREFERENCE_DOCUMENT,
    ACCOUNT_PREFERENCES_SCHEMA,
    EMAIL_SEARCH_MAX_ADDRESSES,
    FULL_VIEW,
    MINIMAL_VIEW,
    PREFERENCE_MINIMAL_FIELDS,
    PREFERENCE_NAME_MAX_LENGTH,
    SHARED_VIEW,
    USER_MINIMAL_FIELDS,
    VERIFICATION_DEFAULT_SORT,
    AccountMinimalSerializer,
    AccountPreferenceSerializer,
    AccountPreferencesUpdateSerializer,
    AccountPreferenceValueSerializer,
    AccountProjectionSerializer,
    AccountQuerySerializer,
    AccountSerializer,
    AccountUpdateSerializer,
    EmailOptInSerializer,
    EmailSearchResultSerializer,
    EmailSearchSerializer,
    ErrorResponse,
    NameChangeSerializer,
    RetiredAccountErrorResponse,
    TimeZoneQuerySerializer,
    TimeZoneSerializer,
    UserDirectoryMinimalSerializer,
    UserDirectoryProjectionSerializer,
    UserDirectorySerializer,
    UserPreferenceRecordMinimalSerializer,
    UserPreferenceRecordProjectionSerializer,
    UserPreferenceRecordSerializer,
    VerificationQuerySerializer,
    VerificationSerializer,
    VerificationStatusSerializer,
    full_view_requested,
    why_a_name_cannot_be_stored,
)
from .view_utils import (
    RESP_UNAUTHENTICATED,
    RESP_UNREADABLE_BODY,
    SDK_TAG,
    USER_TAG,
    StrictQueryParametersMixin,
)

log = logging.getLogger(__name__)

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

#: What the identity verification service reports for a user whose proof of
#: identity has been accepted.
APPROVED_STATUS = "approved"


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

#: The presets an account response answers to.
ACCOUNT_PRESETS = (MINIMAL_VIEW, SHARED_VIEW)

#: The member of the account collection that addresses the caller's own account.
OWN_ACCOUNT_IDENTIFIER = "me"

#: The account actions that answer with an account, and so read the parameters
#: that shape one.
SHAPED_ACCOUNT_ACTIONS = ("list", "retrieve", "partial_update")

#: The properties whose change is limited to a rate, and the setting carrying
#: the limit of each.
RATE_LIMITED_ACCOUNT_FIELDS = (
    ("email", "EMAIL_CHANGE_RATE_LIMIT"),
    ("secondary_email", "SECONDARY_EMAIL_RATE_LIMIT"),
)

#: Parameters that shape a response rather than select the rows in it.
VIEW_PARAM = "view"
FIELDS_PARAM = "fields"
RESPONSE_SHAPING_PARAMS = (VIEW_PARAM, FIELDS_PARAM)

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

_RESP_FORBIDDEN = OpenApiResponse(ErrorResponse, description="The requester may not use this endpoint.")
_RESP_NOT_FOUND = OpenApiResponse(ErrorResponse, description="No such resource.")
_RESP_NO_SUCH_PAGE = OpenApiResponse(ErrorResponse, description="There is no such page of results.")
_RESP_BAD_REQUEST = OpenApiResponse(
    ErrorResponse, description="A query parameter is missing, invalid, or not one this endpoint reads.",
)
_RESP_UNUSABLE_BODY = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request body is not well formed in the media type it was sent in, or carries a "
        "value this endpoint will not take. A query parameter this endpoint does not read is "
        "refused here too."
    ),
)
_RESP_RATE_LIMITED = OpenApiResponse(
    ErrorResponse, description="The caller has changed this property too often lately and has to wait.",
)
_RESP_CHANGE_FAILED = OpenApiResponse(
    ErrorResponse,
    description=(
        "The change was asked for correctly and could not be carried out. Sending it again "
        "with something else in it does not help; sending the same request later may."
    ),
)
_RESP_NO_SUCH_ACCOUNT = OpenApiResponse(
    ErrorResponse, description="No account is held at the address given.",
)
_RESP_CHANGED_ACCOUNT_GONE = OpenApiResponse(
    ErrorResponse,
    description=(
        "The account that was addressed is no longer there. Only the caller's own account is "
        "changed here, so a name nobody holds is refused as somebody else's account instead of "
        "being reported as missing."
    ),
)
_RESP_NO_SUCH_PAGE_OR_RETIRED = OpenApiResponse(
    RetiredAccountErrorResponse,
    description=(
        "There is no such page of results, or the `email` address given is one an account held "
        "before it was retired. In that second case the body also carries `can_cancel_retirement` "
        "and `retirement_id`, which name what is left of that account to a caller able to reverse "
        "the retirement."
    ),
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

_ACCOUNT_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose account is addressed. "
    f"`{OWN_ACCOUNT_IDENTIFIER}` names the account of the caller, so an account holding that "
    "name is read through the collection, with `username=` naming it.",
    _USERNAME_PATTERN,
)
_DEACTIVATION_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose account is deactivated. The name is read as it is stored, "
    f"`{OWN_ACCOUNT_IDENTIFIER}` included: this action disables the account the address names, "
    "never the account of the caller making the request.",
    _USERNAME_PATTERN,
)
_ACCOUNT_VIEW_PARAM = _query_param(
    VIEW_PARAM,
    "Response preset. `minimal` reduces the account to the properties that identify its owner; "
    "`shared` returns only the properties the owner shares with everybody, even to a caller who "
    "may see more. Omit it, or send it empty, to receive everything the caller may see.",
    enum=[*ACCOUNT_PRESETS, ""],
)

#: An account comes back with every property the caller may see, as the
#: identifying properties the ``minimal`` preset keeps, or as the properties
#: ``fields`` selected.
_ACCOUNT_SHAPES = ResponseShapes(
    component_name="AccountResponse",
    serializers=[
        AccountSerializer,
        AccountMinimalSerializer,
        AccountProjectionSerializer,
    ],
)

_ACCOUNT_IMAGE_EXAMPLE = {
    "has_image": True,
    "image_url_full": "https://example.com/media/profile_images/jane_doe_500.jpg",
    "image_url_large": "https://example.com/media/profile_images/jane_doe_120.jpg",
    "image_url_medium": "https://example.com/media/profile_images/jane_doe_50.jpg",
    "image_url_small": "https://example.com/media/profile_images/jane_doe_30.jpg",
}

_ACCOUNT_EXAMPLES = [
    OpenApiExample(
        "Your own account",
        description=(
            "Everything an account carries, which is what its owner and a staff caller receive."
        ),
        value={
            "username": "jane_doe",
            "profile_image": _ACCOUNT_IMAGE_EXAMPLE,
            "account_privacy": "custom",
            "email": "jane.doe@example.com",
            "id": 42,
            "name": "Jane Doe",
            "bio": "Teaches mathematics.",
            "country": "US",
            "state": "MA",
            "date_joined": "2024-03-01T09:30:00Z",
            "last_login": "2026-02-11T18:02:41Z",
            "is_active": True,
            "gender": "f",
            "year_of_birth": 1985,
            "level_of_education": "m",
            "goals": "Finish the course.",
            "mailing_address": "12 Park Avenue",
            "phone_number": "+18005555555",
            "language_proficiencies": [{"code": "fr"}],
            "social_links": [
                {"platform": "twitter", "social_link": "https://www.twitter.com/example"},
            ],
            "extended_profile": [{"field_name": "occupation", "field_value": "Teacher"}],
            "requires_parental_consent": False,
            "pending_name_change": None,
            "verified_name": "Jane Doe",
            "secondary_email": None,
            "secondary_email_enabled": None,
            "course_certificates": None,
            "time_zone": None,
        },
        response_only=True,
        status_codes=["200"],
    ),
    OpenApiExample(
        "An account somebody else owns",
        description=(
            "The properties this owner shares with every learner. An owner who shares field by "
            "field shares fewer, and one who shares nothing leaves only the three properties "
            "every account carries."
        ),
        value={
            "username": "jane_doe",
            "profile_image": _ACCOUNT_IMAGE_EXAMPLE,
            "account_privacy": "all_users",
            "bio": "Teaches mathematics.",
            "country": "US",
            "course_certificates": None,
            "date_joined": "2024-03-01T09:30:00Z",
            "language_proficiencies": [{"code": "fr"}],
            "level_of_education": "m",
            "social_links": [
                {"platform": "twitter", "social_link": "https://www.twitter.com/example"},
            ],
            "time_zone": None,
        },
        response_only=True,
        status_codes=["200"],
    ),
    OpenApiExample(
        "An account reduced to what identifies its owner",
        description="What `view=minimal` answers with; `fields` selects any other set.",
        value={
            "username": "jane_doe",
            "email": "jane.doe@example.com",
            "name": "Jane Doe",
            "profile_image": _ACCOUNT_IMAGE_EXAMPLE,
        },
        response_only=True,
        status_codes=["200"],
    ),
]

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


def _user_named(username):
    """Return the user an address names, or refuse the address."""
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist as exc:
        raise NotFound from exc


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


class _ResponsePresetMixin(MinimalViewMixin):
    """
    Applies the ``view`` and ``fields`` presets to an already serialized payload.

    A view lists the presets it answers to, and a ``view`` value outside that
    list is refused: a caller that mistypes a preset asked for a smaller
    payload and would otherwise be handed the full one without being told.
    The same holds for a field name the response cannot carry, which would
    otherwise select nothing and leave the caller with empty objects.

    Both are settled as the request enters, before the address it names is
    read or written. Which properties an answer can carry depends on the
    caller and on the address, so the refusal comes back the same at a name
    nobody holds as at one somebody does, and nothing is read or changed on
    the way to it.
    """

    #: Presets this view answers to. An empty ``view`` narrows nothing, the way
    #: an empty value of any other parameter does.
    response_presets = ()

    def initial(self, request, *args, **kwargs):
        """Check the requested preset and fields once the caller has been admitted, before anything else."""
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
            401: RESP_UNAUTHENTICATED,
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
            401: RESP_UNAUTHENTICATED,
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
    StrictQueryParametersMixin,
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
            401: RESP_UNAUTHENTICATED,
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
            401: RESP_UNAUTHENTICATED,
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
    StrictQueryParametersMixin,
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
    StrictQueryParametersMixin,
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


def _shareable_account_fields():
    """Return every property an account's owner is able to share with another user."""
    return set(settings.ACCOUNT_VISIBILITY_CONFIGURATION["custom_shareable_fields"])


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List accounts",
        description=(
            "Returns a page of accounts. Name them with `username`, or select the one holding an "
            "`email` address or an `lms_user_id`; omit all three to receive your own account. A "
            "name, an address or an identifier no account holds is left out of the page, which is "
            "empty when nothing given is held. Each account carries the properties the caller may "
            "see: everything for your own account and for a staff caller, and otherwise the "
            "properties its owner shares. "
            "A property named in `fields` that an owner does not share is left out of their "
            "account rather than refused, so a row of the page can come back empty."
        ),
        parameters=[AccountQuerySerializer, _FIELDS_PARAM],
        responses={
            200: _ACCOUNT_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE_OR_RETIRED,
        },
    ),
    retrieve=extend_schema(
        summary="Get one account",
        description=(
            "Returns the account of this user, carrying the properties the caller may see. "
            "Address `me` for the account of the caller. A property named in `fields` that "
            "this owner does not share is left out rather than refused, so the answer can be "
            "an empty object."
        ),
        parameters=[_ACCOUNT_PATH_PARAM, _ACCOUNT_VIEW_PARAM, _FIELDS_PARAM],
        responses={
            200: _ACCOUNT_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_ACCOUNT,
        },
        examples=_ACCOUNT_EXAMPLES,
    ),
    partial_update=extend_schema(
        summary="Change one account",
        description=(
            "Changes the properties named in the body and returns the account as it now stands. "
            "A user may change only their own account, which `me` also addresses. The body is a "
            "merge patch: only the properties it carries are changed, and `social_links` are "
            "merged into the stored list by platform. A new email address takes effect only once "
            "the user confirms it from the message sent there."
        ),
        parameters=[_ACCOUNT_PATH_PARAM, _ACCOUNT_VIEW_PARAM, _FIELDS_PARAM],
        request=AccountUpdateSerializer,
        responses={
            200: _ACCOUNT_SHAPES,
            400: _RESP_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_CHANGED_ACCOUNT_GONE,
            415: RESP_UNREADABLE_BODY,
            429: _RESP_RATE_LIMITED,
            500: _RESP_CHANGE_FAILED,
        },
    ),
)
class AccountViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    ErrorExtensionsMixin,
    _ActionAddressMixin,
    _ResponsePresetMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    Accounts of the platform's users, addressed by username or by ``me``.

    The properties of an account are read from the account service rather than
    from a queryset of this app's, so this is a plain ViewSet; which accounts
    the answer carries is settled first, from the accounts holding the names
    the caller gave, in the order the page is cut in. Access is layered: the
    permission classes decide who may call an action at all, the service
    decides which properties of each account the caller may see -- everything
    for their own account and for a staff caller, the owner's shared
    properties otherwise -- and the selectors decide which accounts the page
    carries.

    A selector that finds nothing narrows the page to nothing, whichever of
    the three it is, so a selection no account matches is answered by an empty
    page rather than by a refusal. An address an account held before it was
    retired is the one exception: that account was there and is not a row this
    page can carry, so it is refused, with whether the retirement can still be
    cancelled. That refusal is why the selectors are read from the query
    string by a serializer and applied here rather than declared as a filter
    set, which has no room for it.

    A selection of properties is measured against the properties a response of
    this kind can carry for this caller, so one no account could ever show
    them is refused rather than answered as an empty object. Which of the rest
    a particular owner shares is settled per account, so a selected property
    that this owner keeps to themselves is left out of their object, which can
    leave it empty.

    ``me`` stands for the caller's own account wherever a caller reads or
    changes one here.
    """

    # Account settings are reached from the site's own pages by users who have
    # not confirmed their email address yet, and the platform default refuses
    # a session belonging to such an account. Only the session class differs in
    # what it admits: a token is read by the library class the platform's own
    # pair is built on, named here because a view declaring its own classes is
    # not one that pair serves.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (
        permissions.IsAuthenticated,
        CanGetAccountInfo,
        CanSelectAccountsByContact,
    )
    #: A change is a merge patch: a body in any other media type is refused.
    parser_classes = (MergePatchParser,)
    serializer_class = AccountSerializer
    #: Validates the query string of the list, and names the parameters it reads.
    query_serializer_class = AccountQuerySerializer
    pagination_class = UserApiPagination
    lookup_field = "username"
    # The router's default lookup pattern excludes dots, which usernames allow.
    lookup_value_regex = settings.USERNAME_REGEX_PARTIAL
    minimal_fields = ACCOUNT_MINIMAL_FIELDS
    response_presets = ACCOUNT_PRESETS
    extra_query_params = RESPONSE_SHAPING_PARAMS
    #: The validated query string of the request being served, read once.
    _validated_selection = None

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def known_query_params(self):
        """Return the query parameters the action being served reads."""
        names = super().known_query_params()
        if self.action != "list":
            # One account is answered whole, so neither the selectors that
            # choose which accounts a page carries nor the parameters that cut
            # it into pages are read at a member address.
            names -= set(self.query_serializer_class().fields) - {VIEW_PARAM}
            names -= {
                self.pagination_class.page_query_param,
                self.pagination_class.page_size_query_param,
            }
        if self.action not in SHAPED_ACCOUNT_ACTIONS:
            names -= set(RESPONSE_SHAPING_PARAMS)
        return names

    def shapeable_fields(self):
        """Return the properties an account response can carry for the caller making it."""
        describable = set(AccountSerializer().fields)
        if self.is_minimal_view_requested():
            describable &= set(self.minimal_fields)
        if self._whole_accounts_are_readable(self.request):
            return describable
        return describable & _shareable_account_fields()

    def _whole_accounts_are_readable(self, request):
        """Whether every account this request addresses comes back whole to this caller."""
        if request.query_params.get(VIEW_PARAM) == SHARED_VIEW:
            return False
        if request.user.is_staff:
            return True
        return self._addressed_names(request) == [request.user.username]

    def _selection(self, request):
        """
        Return the query string as the serializer that validates it reads it.

        Which accounts a request addresses is read both before the handler
        runs, to settle what the answer can carry, and inside it, to cut the
        page. Both read the values the serializer validated, so a name sent
        with spaces around it names the same account at each point; and the
        serializer runs once, because it is what refuses a selector the list
        does not take.
        """
        if self._validated_selection is None:
            query = self.query_serializer_class(data=request.query_params)
            query.is_valid(raise_exception=True)
            self._validated_selection = query.validated_data
        return self._validated_selection

    def _addressed_names(self, request):
        """Return the usernames this request addresses, as the address and the query string give them."""
        if self.action != "list":
            return [self._addressed_username(request, self.kwargs.get("username"))]
        selection = self._selection(request)
        named = selection.get("username")
        if named:
            return [name for name in named.strip(",").split(",") if name]
        if {"email", "lms_user_id"} & set(selection):
            return []
        return [request.user.username]

    def list(self, request):
        """Return a page of accounts, shaped as the caller asked."""
        selection = self._selection(request)
        preset = selection.get(VIEW_PARAM)
        page = self.paginate_iterable(
            request,
            self._selected_usernames(request, selection),
            serialize=lambda usernames: self._accounts_named(request, usernames, preset),
        )
        return self.shape_page(page, request)

    def retrieve(self, request, username):
        """Return one account, shaped as the caller asked."""
        account = self._accounts_named(
            request,
            [self._addressed_username(request, username)],
            request.query_params.get(VIEW_PARAM),
        )[0]
        return Response(self.shape(account, request))

    def partial_update(self, request, username):
        """Change the properties the body carries and return the account as it now stands."""
        username = self._addressed_username(request, username)
        changes = request.data
        if not isinstance(changes, dict):
            raise ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: ["Send the properties to change as an object."],
            })
        # The body is measured against the shape this endpoint publishes before
        # anything is done with it, and then carried out as it was sent: the
        # account service takes properties this version does not publish, and
        # refuses the ones that cannot be changed, which dropping them here
        # would turn into silence. Every property is optional of itself, which
        # is what makes the body a merge patch, and a property that is an
        # object of its own is still sent whole.
        AccountUpdateSerializer(data=changes).is_valid(raise_exception=True)
        if "email" in changes and not settings.ALLOW_EMAIL_ADDRESS_CHANGE:
            raise ValidationError({"email": ["This site does not let an email address be changed here."]})
        self._check_change_rate(request, changes)
        try:
            with transaction.atomic():
                update_account_settings(request.user, changes, username=username)
                account = self._accounts_named(
                    request, [username], request.query_params.get(VIEW_PARAM),
                )[0]
        except UserNotAuthorized as exc:
            raise PermissionDenied from exc
        except UserNotFound as exc:
            raise NotFound from exc
        except AccountValidationError as exc:
            raise self._refused_changes(username, exc) from exc
        except AccountUpdateError as exc:
            raise self._failed_change(username, exc) from exc
        return Response(self.shape(account, request))

    def _addressed_username(self, request, username):
        """Return the username an address names, reading ``me`` as the caller's own."""
        if username == OWN_ACCOUNT_IDENTIFIER:
            return request.user.username
        return username

    def _accounts_named(self, request, usernames, preset=None):
        """Return each named account, in the order asked for, as this caller may see it."""
        names = list(usernames)
        if not names:
            return []
        try:
            found = get_account_settings(request, names, view=preset if preset == SHARED_VIEW else None)
        except UserNotFound as exc:
            raise NotFound from exc
        # The accounts come back in the order the database returned them, which
        # is not the order of the page they were selected for. Each name is
        # matched to its account by the name that account stores, and failing
        # that by its case-folded form: the database decides which names a
        # selection matches, and the collation MySQL applies to these columns
        # by default matches names differing only in case, so an account can
        # come back stored under a spelling other than the one asked for.
        by_name = {account["username"]: account for account in found}
        folded = {}
        for stored, account in by_name.items():
            folded.setdefault(stored.casefold(), account)
        ordered = []
        for name in names:
            account = by_name.get(name)
            if account is None:
                account = folded.get(name.casefold())
            if account is not None:
                ordered.append(account)
        return ordered

    def _selected_usernames(self, request, selection):
        """Return the usernames the selectors name, in the order the page carries them."""
        return usernames_among(
            self._selected_names(request, selection),
            selection.get("ordering") or ACCOUNT_DEFAULT_SORT,
        )

    def _selected_names(self, request, selection):
        """
        Return the usernames named by whichever selector the caller used.

        Every selector narrows the same collection, so each one that finds
        nothing narrows it to nothing and leaves the page empty, whether it
        was given a name, an address or an identifier. An address an account
        held before it was retired is the exception: the account was there and
        is not a row this page can carry, which is reported rather than left
        out in silence.
        """
        usernames = selection.get("username")
        if usernames:
            return [name for name in usernames.strip(",").split(",") if name]
        email = selection.get("email")
        if email:
            return self._usernames_holding_email(email)
        identifier = selection.get("lms_user_id")
        if identifier is not None:
            return self._usernames_holding_identifier(identifier)
        return [request.user.username]

    def _usernames_holding_email(self, email):
        """Return the account holding this address, and nothing when none does."""
        if is_email_retired(email):
            raise self._retired_account(email)
        username = username_holding_email(email)
        return [username] if username is not None else []

    def _usernames_holding_identifier(self, identifier):
        """Return the account holding this numeric identifier, and nothing when none does."""
        username = username_holding_id(identifier)
        return [username] if username is not None else []

    def _retired_account(self, email):
        """Build the refusal for an address an account held before it was retired."""
        retirement = cancellable_retirement_for_email(email)
        return RetiredAccount(
            accounts.RETIRED_EMAIL_MSG,
            can_cancel_retirement=retirement is not None,
            retirement_id=retirement.id if retirement is not None else None,
        )

    def _check_change_rate(self, request, changes):
        """Refuse a property the caller has changed too often lately."""
        for field, limit_setting in RATE_LIMITED_ACCOUNT_FIELDS:
            limit = getattr(settings, limit_setting, None)
            if not (changes.get(field) and limit):
                continue
            if is_ratelimited(
                request=request,
                group=f"{field}_change_rate_limit",
                key="user",
                rate=limit,
                increment=True,
            ):
                log.info("Change of %r refused for %r: over the rate limit", field, request.user.username)
                raise Throttled(detail=f"Wait before changing {field} again.")

    def _refused_changes(self, username, error):
        """Return the refusal naming the property that each failed validation."""
        log.info("Account %r not changed: %r", username, error.field_errors)
        refusals = {}
        for field, reported in error.field_errors.items():
            message = reported.get("user_message") if isinstance(reported, dict) else None
            refusals[field] = [message or "This value cannot be used here."]
        return ValidationError(refusals)

    def _failed_change(self, username, error):
        """
        Return the failure for a change that was asked for correctly and could not be carried out.

        The body has already been measured against what this endpoint takes,
        and a value the account refuses is reported property by property, so
        what is left here is the work itself failing: a row that would not
        save, a message that would not send. Neither is answered by sending
        the request again with something else in it, so neither is the
        caller's fault.
        """
        log.error("Account %r not changed: %s", username, error.developer_message)
        return APIException("The change could not be carried out.")


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    post=extend_schema(
        summary="Deactivate an account",
        description=(
            "Makes the account's password unusable, so its owner can no longer sign in with it. "
            "The account and the data belonging to it are kept, and none of it is removed. Asking "
            "for a password reset does not undo this: the reset flow refuses an account whose "
            "password is unusable, so signing in again takes a new password set on the account by "
            "other means."
        ),
        parameters=[_DEACTIVATION_PATH_PARAM],
        request=None,
        responses={
            204: OpenApiResponse(description="The account was deactivated."),
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
        },
    ),
)
class AccountDeactivationView(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    APIView,
):
    """
    Disables the account the address names.

    This is an operation a privileged caller carries out on somebody else's
    account rather than a property of that account, and it shares nothing with
    the way an account is read or changed: the caller holds the privilege to
    disable accounts and has confirmed their own address, the body is read as
    JSON rather than as a merge patch, and the name in the address is read as
    a stored username, ``me`` included, so a caller that addressed one account
    is never answered by disabling another. It is therefore its own view
    rather than an action of the account collection.
    """

    permission_classes = (permissions.IsAuthenticated, IsActiveUser, CanDeactivateUser)

    def post(self, request, username):
        """Make the password of the named account unusable and record it as disabled."""
        user = _user_named(username)
        segment.identify(user.id, {"is_disabled": "true"})
        user.set_unusable_password()
        user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Search accounts by email address",
        description=(
            "Returns a page of the accounts holding the email addresses in the body. An address "
            "no account holds is left out of the answer, and a search names at most "
            f"{EMAIL_SEARCH_MAX_ADDRESSES} addresses. The addresses travel in the body so that "
            "they are not written to a server log or a browser history, which is why this reads "
            "with a POST. For the same reason the `next` and `previous` links of the page are "
            "reached by sending the same body to them, the way this request was sent, rather "
            "than by fetching them."
        ),
        request=EmailSearchSerializer,
        responses={
            200: EmailSearchResultSerializer(many=True),
            400: _RESP_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class EmailSearchViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    The accounts holding a given set of email addresses.

    The addresses are sent in a request body rather than in a query string, so
    that they stay out of server logs and browser histories, which is why a
    search is made with a POST, and why the links on a page of results are
    followed by sending the body to them again rather than by fetching them.
    The search is a collection of its own rather than an action under the
    accounts, because what follows the account collection in an address is the
    name of an account, and an account may be named anything a user may
    register. Only a platform administrator may search, and every other caller
    is told there is nothing at this address.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        IsActiveUser,
        CanGetAccountInfo,
        IsStaffOrAbsent,
    )
    parser_classes = (JSONParser,)
    serializer_class = EmailSearchResultSerializer
    pagination_class = UserApiPagination

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Return a page of the accounts holding the addresses the body carries."""
        search = EmailSearchSerializer(data=request.data)
        search.is_valid(raise_exception=True)
        return self.paginate_iterable(request, accounts_holding_emails(search.validated_data["emails"]))


_NAME_CHANGE_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose requested change is confirmed. The name is read as it is "
    f"stored, so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than "
    "the account of the caller making the request.",
    _USERNAME_PATTERN,
)


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Request a profile name change",
        description=(
            "Records the full name the caller wants on their profile. The profile keeps its "
            "current name until the request is confirmed, and a caller who already has a request "
            "outstanding replaces it with this one."
        ),
        request=NameChangeSerializer,
        responses={
            201: NameChangeSerializer,
            400: _RESP_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            404: OpenApiResponse(
                ErrorResponse,
                description="The account of the caller carries no profile to put a name on.",
            ),
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class NameChangeViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    _ActionAddressMixin,
    StrictQueryParametersMixin,
    viewsets.ViewSet,
):
    """
    Requests to put a different full name on a user's profile.

    A user asks for the name they want; the profile keeps the name it has
    until a member of staff confirms the request, which is the point at which
    the old name is recorded in the profile's history.
    """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = NameChangeSerializer
    lookup_field = "username"
    # The router's default lookup pattern excludes dots, which usernames allow.
    lookup_value_regex = settings.USERNAME_REGEX_PARTIAL

    def get_serializer(self, *args, **kwargs):
        """Build the serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Record the name the caller wants on their profile."""
        requested = self.get_serializer(data=request.data)
        requested.is_valid(raise_exception=True)
        name = requested.validated_data["name"]
        rationale = f"Name change requested through account API by {request.user.username}"
        pending = self._requested_change(request.user, name, rationale)
        if pending is None:
            raise ValidationError({"name": ["The profile already carries this name."]})
        return Response(
            self.get_serializer({"name": pending.new_name}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Confirm a requested profile name change",
        description=(
            "Puts the name the user asked for on their profile and keeps the name it carried "
            "until now in the profile's history. Only a member of staff may confirm a request."
        ),
        parameters=[_NAME_CHANGE_PATH_PARAM],
        request=None,
        responses={
            204: OpenApiResponse(description="The profile now carries the name that was asked for."),
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(
                ErrorResponse,
                description="No such user, or the user has asked for no name change.",
            ),
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="confirmations",
        url_name="confirmation",
        # Confirming somebody else's requested name is a privilege, so this
        # action also refuses a caller whose own account is no longer active.
        permission_classes=[permissions.IsAuthenticated, IsActiveUser, IsStaff],
    )
    def confirmations(self, request, username):
        """Put the name the named user asked for on their profile."""
        user = _user_named(username)
        pending = get_pending_name_change(user)
        if pending is None:
            raise NotFound
        self._confirm_change(user, pending)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _requested_change(self, user, name, rationale):
        """Store the name this user asks for, or return nothing when it is the name they have."""
        try:
            return do_name_change_request(user, name, rationale)[0]
        except UserProfile.DoesNotExist as exc:
            log.warning("Name change refused for %r: the account carries no profile", user.username)
            raise NotFound from exc

    def _confirm_change(self, user, pending):
        """Put the requested name on the profile."""
        try:
            confirm_name_change(user, pending)
        except UserProfile.DoesNotExist as exc:
            log.warning("Name change refused for %r: the account carries no profile", user.username)
            raise NotFound from exc


_VERIFICATION_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose identity verification is addressed. The name is read as it is "
    f"stored, so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than "
    "the account of the caller making the request.",
    _USERNAME_PATTERN,
)


def _verification_sort_key(attempt):
    """Return what an attempt is ordered by, with a tie-break that is always comparable."""
    return (attempt.updated_at, str(getattr(attempt, "receipt_id", "") or ""))


@extend_schema(tags=[SDK_TAG])
class IDVerificationStatusView(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    APIView,
):
    """
    Whether a user has proved their identity, as one summary.

    An account has one such summary, computed rather than stored and never
    listed, so it is served from this single address rather than routed as a
    collection. The summary is read from the identity verification service
    rather than from this app's own tables, and the username in the address
    names the user whose summary is read. A user may read their own summary,
    and a staff caller whose own account is active may read anyone's, which is
    not what the account collection admits, so the summary is served by a view
    of its own rather than as an action of that collection. Every account has a
    summary: one belonging to a user who has been through no check says so, and
    the address is absent only when the user it names is.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        # Reading somebody else's summary is a privilege, so the staff half
        # also refuses a caller whose own account is no longer active.
        (IsStaff & IsActiveUser) | IsAddressedUser,
    )
    serializer_class = VerificationStatusSerializer

    def get_serializer(self, *args, **kwargs):
        """Build the serializer; a plain APIView has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get the identity verification status",
        description=(
            "Returns where this user stands on having proved their identity. A user may read "
            "their own status, and a staff caller may read anyone's. A user who has been through "
            "no check stands at `none`."
        ),
        parameters=[_VERIFICATION_PATH_PARAM],
        responses={
            200: VerificationStatusSerializer,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(ErrorResponse, description="No such user."),
        },
    )
    def get(self, request, username):
        """Return where this user stands on having proved their identity."""
        standing = IDVerificationService.user_status(_user_named(username))
        return Response(self.get_serializer({
            "is_verified": standing.get("status") == APPROVED_STATUS,
            "status": standing.get("status"),
            "expiration_datetime": standing.get("verification_expiry", ""),
        }).data)


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List identity verification attempts",
        description=(
            "Returns a page of the attempts this user has made at proving their identity, the "
            "most recently changed one first. A user who has been through no checks has an "
            "empty page. A user may read their own attempts, and a staff caller may read "
            "anyone's."
        ),
        # The sort is published from the serializer that validates it, so the
        # values a caller is held to are the values it reads.
        parameters=[_VERIFICATION_PATH_PARAM, VerificationQuerySerializer],
        responses={
            200: VerificationSerializer(many=True),
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(
                ErrorResponse, description="No such user, or no such page of attempts.",
            ),
        },
    ),
)
class IDVerificationViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    The attempts one user has made at proving their identity.

    The attempts are collected from the identity verification service, which
    holds a table per kind of check, so there is no queryset of this app's to
    narrow: the page is cut from what those tables return and is sorted here
    rather than by a filter backend. An attempt belongs to the user who made
    it and is reached by no other route, so the collection is addressed
    beneath that user's account. A user may read their own attempts, and a
    staff caller may read anyone's. A user who has been through no checks has
    an empty page: the address is absent only when the user it names is.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        # Reading somebody else's attempts is a privilege, so the staff half
        # also refuses a caller whose own account is no longer active.
        (IsStaff & IsActiveUser) | IsAddressedUser,
    )
    serializer_class = VerificationSerializer
    pagination_class = UserApiPagination
    #: Validates the query string, and names the parameters this view reads.
    query_serializer_class = VerificationQuerySerializer

    def get_serializer(self, *args, **kwargs):
        """Build the serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def list(self, request, username):
        """Return a page of this user's attempts, in the order the caller asked for."""
        query = self.query_serializer_class(data=request.query_params)
        query.is_valid(raise_exception=True)
        attempts = IDVerificationService.verifications_for_user(_user_named(username))
        ordering = query.validated_data.get("ordering") or VERIFICATION_DEFAULT_SORT
        return self.paginate_iterable(
            request,
            sorted(attempts, key=_verification_sort_key, reverse=ordering.startswith("-")),
        )


_ACCOUNT_PREFERENCES_PATH_PARAM = _path_param(
    "username",
    "Unique name of the user whose preferences are addressed. The name is read as it is stored, "
    f"so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than the "
    "account of the caller making the request.",
    _USERNAME_PATTERN,
)
_ACCOUNT_PREFERENCE_KEY_PATH_PARAM = _path_param(
    "key",
    "Name the preference is stored under, which is the whole segment, punctuation included. A "
    "name carrying a slash has no address of its own, because the segment ends at the slash; "
    "such a name is read with the whole document and removed by sending a null for it there. "
    f"Nothing is stored under a name longer than {PREFERENCE_NAME_MAX_LENGTH} characters, nor "
    "under one carrying no letter, digit, hyphen or underscore.",
    rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$",
)

#: The actions of the preference resource an active administrator may call
#: against an account other than their own.
STAFF_READABLE_PREFERENCE_ACTIONS = ("retrieve_all", "retrieve")

#: The action recorded for a request that asks what an address answers rather
#: than for what it holds. Such a request is answered to whoever may read the
#: address; the description of a write it carries is built behind a second
#: check, which carries the method of that write.
DESCRIBE_ADDRESS_ACTION = "metadata"

#: Who may read the preferences of the account an address names, and who may
#: change them. Reading somebody else's is a privilege, so the administrator
#: half also refuses a caller whose own account is no longer active; changing
#: them belongs to their owner alone.
_PREFERENCE_READ_PERMISSIONS = (
    permissions.IsAuthenticated,
    (IsStaff & IsActiveUser) | IsAddressedUser,
)
_PREFERENCE_WRITE_PERMISSIONS = (permissions.IsAuthenticated, IsAddressedUser)

#: The media type a change to the whole document is sent in: a merge patch, in
#: which a null removes a name. Every other body of this resource is read in
#: the media types the platform reads everywhere.
_DOCUMENT_PARSERS = (MergePatchParser,)

_RESP_PREFERENCE_DOCUMENT = OpenApiResponse(
    ACCOUNT_PREFERENCES_SCHEMA, description="The preferences this account stores.",
)
_RESP_NO_SUCH_PREFERENCE = OpenApiResponse(
    ErrorResponse, description="No account is held at the address given, or it stores no such preference.",
)
_RESP_PREFERENCE_NOT_STORED = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request body is not well formed in the media type it was sent in, or a preference "
        "name is longer than one can be stored under, or a value is one a preference cannot "
        "hold. A query parameter this endpoint does not read is refused here too."
    ),
)
_RESP_PREFERENCE_SERVICE_FAILED = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request was correct and could not be carried out. Sending it again with something "
        "else in it does not help; sending the same request later may."
    ),
)


@extend_schema(tags=[SDK_TAG])
class AccountPreferenceViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    viewsets.ViewSet,
):
    """
    The preferences of the one account an address names.

    The preferences are read and written through the preference service rather
    than from a queryset of this app's, so this is a plain ViewSet. They are
    one document: an object of preference name to stored value, read and
    merge-patched whole, which is how the pages that keep a user's settings
    read and save them. It is not a list of records and carries no pages; the
    record-shaped view of the same data, across accounts, is the stored
    preference collection of this version.

    Which account the answer is about is settled by the name in the address
    alone: a user reads and changes their own preferences, and a staff caller
    whose own account is active reads anyone's but changes nobody else's. The
    name is read as it is stored, so ``me`` names an account holding that name
    rather than the account of the caller.

    A name is read as the whole segment of the address, so the names carrying
    punctuation that the platform's own features and the profile pages store
    under can be addressed. A name carrying a slash cannot, because the
    segment ends there; the document reads and removes such a name, which is
    also the only way one arrives.
    """

    # The language a user picked is written from every page of the site, and
    # the settings page these preferences are kept on is reached by users who
    # have not confirmed their email address yet, whom the platform default
    # refuses. Only the session class differs in what it admits: a token is
    # read by the library class the platform's own pair is built on, named here
    # because a view declaring its own classes is not one that pair serves.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = _PREFERENCE_WRITE_PERMISSIONS
    serializer_class = AccountPreferenceSerializer

    #: Action being served, which the parsers follow. It is declared here
    #: because it is recorded while the request is being built, which is
    #: before the view machinery would otherwise set it.
    action = None

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def get_permissions(self):
        """Return the classes deciding who may make the request being served."""
        reads = self.action in STAFF_READABLE_PREFERENCE_ACTIONS or self._describes_the_address()
        permission_classes = _PREFERENCE_READ_PERMISSIONS if reads else self.permission_classes
        return [permission() for permission in permission_classes]

    def _describes_the_address(self):
        """
        Return whether the request asks what the address answers rather than for what it holds.

        Such a request is guarded as a read of the address, so a caller who may
        read somebody else's preferences may also ask what the address does
        with them. The description of a write that the answer carries is built
        behind a second check made with the method of that write, which is
        guarded as that write and so is left out for a caller who may not make
        it.
        """
        return self.action == DESCRIBE_ADDRESS_ACTION and self.request.method == "OPTIONS"

    def initialize_request(self, request, *args, **kwargs):
        """
        Record which action is being served before anything reads the request.

        The media type a body is read in follows the action here, and the
        parsers are chosen while the request is being built, which is before
        the action would otherwise be recorded.
        """
        self.action = self.action_map.get(request.method.lower())
        return super().initialize_request(request, *args, **kwargs)

    def get_parsers(self):
        """Return the parsers that read the body the action being served takes."""
        if self.action == "partial_update_all":
            return [parser() for parser in _DOCUMENT_PARSERS]
        return super().get_parsers()

    @extend_schema(
        summary="Get the preferences of one account",
        description=(
            "Returns every preference stored for this user, as an object of preference name to "
            "stored value. A user may read their own preferences, and a staff caller may read "
            "anyone's. An account that stores none is answered with an empty object. The names "
            "come back sorted by name, character by character and by the code of each character, "
            "so a name beginning with a digit or a capital letter comes before one beginning in "
            "lower case."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM],
        responses={
            200: _RESP_PREFERENCE_DOCUMENT,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_ACCOUNT,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    )
    def retrieve_all(self, request, username):
        """Return every preference stored for the account the address names."""
        with self._service_failures_named(username):
            stored = get_user_preferences(request.user, username=username)
        return Response(ACCOUNT_PREFERENCE_DOCUMENT.to_representation(stored))

    @extend_schema(
        summary="Change the preferences of one account",
        description=(
            "Changes the preferences the body names and leaves the rest as they are. The body is "
            "a merge patch: a value is the text to store under that name, and a null removes the "
            "name. A user may change only their own preferences. Nothing is stored unless every "
            "name in the body can be, and a value that cannot be stored is reported under the "
            "name that carried it."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM],
        request={MergePatchParser.media_type: ACCOUNT_PREFERENCE_CHANGES_SCHEMA},
        responses={
            204: OpenApiResponse(description="The preferences now stand as the body asked."),
            400: _RESP_PREFERENCE_NOT_STORED,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            415: RESP_UNREADABLE_BODY,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    )
    def partial_update_all(self, request, username):
        """Store the preferences the body names and remove the ones it sends a null for."""
        changes = AccountPreferencesUpdateSerializer(data=request.data)
        changes.is_valid(raise_exception=True)
        with self._service_failures_named(username):
            with transaction.atomic():
                update_user_preferences(request.user, changes.validated_data, user=username)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Get one preference of one account",
        description=(
            "Returns one preference of this user, as the name it is stored under and the value "
            "stored there. A user may read their own preferences, and a staff caller may read "
            "anyone's. A name the account stores no value under is absent."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM, _ACCOUNT_PREFERENCE_KEY_PATH_PARAM],
        responses={
            200: AccountPreferenceSerializer,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PREFERENCE,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    )
    def retrieve(self, request, username, key):
        """Return one preference of the account the address names."""
        with self._service_failures_named(username):
            value = get_user_preference(request.user, key, username=username)
        if value is None:
            raise NotFound
        return Response(self.get_serializer({"key": key, "value": value}).data)

    @extend_schema(
        summary="Store one preference of one account",
        description=(
            "Stores a value under one preference of this user, whether or not the name already "
            "holds one. A user may change only their own preferences. A value a preference "
            "cannot hold is refused under `value`, and a preference cannot hold an empty value. "
            "A name nothing can be stored under, whether because it is longer than the store "
            "holds or because it carries none of the characters a name is made of, is refused "
            "under `key`, which is the part of the address that carried it."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM, _ACCOUNT_PREFERENCE_KEY_PATH_PARAM],
        request=AccountPreferenceValueSerializer,
        responses={
            204: OpenApiResponse(description="The preference now holds this value."),
            400: _RESP_PREFERENCE_NOT_STORED,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            415: RESP_UNREADABLE_BODY,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    )
    def update(self, request, username, key):
        """Store a value under one preference of the account the address names."""
        self._refuse_a_name_nothing_is_stored_under(key)
        sent = AccountPreferenceValueSerializer(data=request.data)
        sent.is_valid(raise_exception=True)
        with self._service_failures_named(username, carried_by="value"):
            set_user_preference(
                request.user, key, sent.validated_data["value"], username=username,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Remove one preference of one account",
        description=(
            "Removes one preference of this user, so the name is no longer stored. A user may "
            "remove only their own preferences. A name the account stores no value under is "
            "absent."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM, _ACCOUNT_PREFERENCE_KEY_PATH_PARAM],
        responses={
            204: OpenApiResponse(description="The account stores no value under that name."),
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PREFERENCE,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    )
    def destroy(self, request, username, key):
        """Remove one preference of the account the address names."""
        with self._service_failures_named(username):
            removed = delete_user_preference(request.user, key, username=username)
        if not removed:
            raise NotFound
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _refuse_a_name_nothing_is_stored_under(key):
        """
        Refuse a name no value can be stored under, as the part of the address that carried it.

        The name arrives in the address rather than in the body, so a name the
        store refuses is reported under the address member that carried it: a
        caller cannot mend it by changing the value they sent. Which names are
        held is the store's own to decide, so what it refuses is read from the
        column that keeps them rather than from a list kept here.
        """
        refused = why_a_name_cannot_be_stored(key)
        if refused:
            raise ValidationError({"key": refused})

    @contextlib.contextmanager
    def _service_failures_named(self, username, carried_by=None):
        """
        Answer a failure of the preference service in the body this API answers with.

        A value the preference refuses is reported under the member of the
        body that carried it, which is the preference name in a document and
        the one property of a single change. Work that was asked for correctly
        and failed anyway is not the caller's to correct, so what it says about
        itself is logged rather than published.
        """
        try:
            yield
        except UserNotAuthorized as exc:
            raise PermissionDenied from exc
        except UserNotFound as exc:
            raise NotFound from exc
        except PreferenceValidationError as exc:
            raise self._refused_preferences(username, exc, carried_by) from exc
        except (PreferenceUpdateError, UserAPIInternalError) as exc:
            log.error("Preferences of %r could not be served: %s", username, exc)
            raise APIException("The request could not be carried out.") from exc

    def _refused_preferences(self, username, error, carried_by=None):
        """Return the refusal naming the member of the body that carried each value refused."""
        log.info("Preferences of %r not changed: %r", username, error.preference_errors)
        refusals = {}
        for key, reported in error.preference_errors.items():
            message = reported.get("user_message") if isinstance(reported, dict) else None
            refusals[carried_by or key] = [message or "This value cannot be stored here."]
        return ValidationError(refusals)


_RESP_SESSION_WITHOUT_ITS_TOKEN = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request was made with a session cookie and carried none of the request token the "
        "site hands out with it, so the site cannot tell that the request came from a page it "
        "served. Sending that token with the session, or a signed token in place of the "
        "session, is answered."
    ),
)


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Record whether to send a user an organization's messages",
        description=(
            "Records whether the caller wants messages from the organization running the course "
            "named in the body. The choice is kept per organization, so it covers every course "
            "that organization runs, and it is always the caller's own. A user whose account "
            "needs a guardian's consent is recorded as not wanting them, whatever the body says."
        ),
        request=EmailOptInSerializer,
        responses={
            204: OpenApiResponse(description="The choice was recorded."),
            400: _RESP_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_SESSION_WITHOUT_ITS_TOKEN,
            404: OpenApiResponse(
                ErrorResponse,
                description="The account of the caller carries no profile to read a consent rule from.",
            ),
            415: RESP_UNREADABLE_BODY,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    ),
)
class EmailOptInViewSet(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    viewsets.ViewSet,
):
    """
    The choices users have made about the messages organizations send them.

    A choice belongs to the user who made it, so it is recorded against the
    caller and no username is read from the address, the body or the query
    string. The organization it applies to is the one running the course the
    body names, which is how the pages a user makes the choice on identify it.
    The choice is held per organization rather than per course, and asking for
    one course's organization twice records one choice.
    """

    # The choice is made while enrolling, which a user reaches before they have
    # confirmed their email address, and the platform default refuses a session
    # belonging to such an account. Only the session class differs in what it
    # admits: a token is read by the library class the platform's own pair is
    # built on, named here because a view declaring its own classes is not one
    # that pair serves.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = EmailOptInSerializer

    def get_serializer(self, *args, **kwargs):
        """Build the serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Record whether the caller wants messages from the organization running this course."""
        choice = self.get_serializer(data=request.data)
        choice.is_valid(raise_exception=True)
        try:
            update_email_opt_in(
                request.user,
                choice.validated_data["course_key"].org,
                choice.validated_data["email_opt_in"],
            )
        except UserNotFound as exc:
            raise NotFound from exc
        except UserAPIInternalError as exc:
            log.error("Email choice of %r not recorded: %s", request.user.username, exc)
            raise APIException("The choice could not be recorded.") from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


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
