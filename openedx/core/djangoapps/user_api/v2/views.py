"""Views for version 2 of the User API."""

import contextlib
import logging

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.db.models import F, Prefetch
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from django_ratelimit.core import is_ratelimited
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
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
from rest_framework.metadata import SimpleMetadata
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
from openedx.core.djangoapps.user_api.accounts.permissions import (
    CanCancelUserRetirement,
    CanDeactivateUser,
    CanGetAccountInfo,
    CanRetireUser,
)
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
from openedx.core.lib.api.permissions import ApiKeyHeaderPermission

from .errors import AmbiguousAccountName, ErrorExtensionsMixin, ReadableErrorDetailMixin, RetiredAccount
from .filters import RetirementFilterSet, StrictOrderingFilter, UserFilterSet, UserPreferenceFilterSet
from .paginators import UserApiPagination
from .permissions import (
    CanSelectAccountsByContact,
    IsActiveUser,
    IsAddressedUser,
    IsStaffOrAbsent,
    RestrictedApplicationGuardMixin,
)
from .schema import ResponseShapes
from .selectors import (
    accounts_holding_emails,
    accounts_named,
    cancellable_retirement_for_email,
    partner_reports,
    retirement_of_the_account_named,
    retirements,
    unclaimed_partner_reports,
    username_holding_id,
    usernames_among,
    usernames_holding_email,
)
from .serializers import (
    ACCOUNT_DEFAULT_SORT,
    ACCOUNT_MINIMAL_FIELDS,
    ACCOUNT_PREFERENCE_CHANGES_SCHEMA,
    ACCOUNT_PREFERENCE_DOCUMENT,
    ACCOUNT_PREFERENCES_SCHEMA,
    EMAIL_SEARCH_MAX_ADDRESSES,
    EMBEDDABLE_OWNER_IDS,
    FULL_VIEW,
    MINIMAL_VIEW,
    PARTNER_REPORT_CLAIM_MAX,
    PREFERENCE_MINIMAL_FIELDS,
    PREFERENCE_NAME_MAX_LENGTH,
    RETIREMENT_MINIMAL_FIELDS,
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
    RetirementBatchOutcomeSerializer,
    RetirementBatchSerializer,
    RetirementErasureSerializer,
    RetirementMinimalSerializer,
    RetirementPartnerReportQuerySerializer,
    RetirementPartnerReportRecordSerializer,
    RetirementProjectionSerializer,
    RetirementSerializer,
    RetirementUpdateSerializer,
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
from .view_services import (
    NO_SUCH_RETIREMENT,
    cancel_retirement,
    claim_partner_reports,
    delete_partner_reports,
    delete_retirements,
    erase_account_data,
    move_retirement,
    record_partner_report,
    retirement_open_to_action,
    retirement_requested_as,
)
from .view_utils import (
    RESP_UNAUTHENTICATED,
    RESP_UNREADABLE_BODY,
    SDK_TAG,
    USER_TAG,
    USERNAME_PATTERN,
    StrictQueryParametersMixin,
    names_in,
    path_param,
    query_param,
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

_USER_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose directory entry is returned.",
    USERNAME_PATTERN,
)
_PREFERENCE_USERNAME_PATH_PARAM = path_param(
    "username",
    "Username of the user the preference belongs to: the part of the address before the comma.",
    USERNAME_PATTERN,
)
_PREFERENCE_KEY_PATH_PARAM = path_param(
    "key",
    "Name the preference is stored under: the part of the address after the comma.",
    rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$",
)

#: The presets an account response answers to.
ACCOUNT_PRESETS = (MINIMAL_VIEW, SHARED_VIEW)

#: The account-collection member naming the caller's own account. Addresses
#: beneath an account do not translate it: there it is a stored username.
OWN_ACCOUNT_IDENTIFIER = "me"

#: What a caller is told about a member address that more than one account
#: answers to. One account is addressed there, so an address several of them
#: answer to names none of them in particular.
AMBIGUOUS_ACCOUNT_ADDRESS = "More than one account answers to this address. Name one of them as it is stored."

#: The account actions that answer with an account, and so read the parameters
#: that shape one.
SHAPED_ACCOUNT_ACTIONS = ("list", "retrieve", "partial_update")

#: The properties whose change is limited to a rate, and the setting carrying
#: the limit of each. Both settings are empty until a deployment gives one of
#: them a value, and an empty limit limits nothing, so a site that has
#: configured neither takes every change to either property.
RATE_LIMITED_ACCOUNT_FIELDS = (
    ("email", "EMAIL_CHANGE_RATE_LIMIT"),
    ("secondary_email", "SECONDARY_EMAIL_RATE_LIMIT"),
)

#: Parameters that shape a response rather than select the rows in it.
VIEW_PARAM = "view"
FIELDS_PARAM = "fields"
RESPONSE_SHAPING_PARAMS = (VIEW_PARAM, FIELDS_PARAM)

_FIELDS_PARAM = query_param(
    FIELDS_PARAM,
    "Comma-separated list of top-level field names to keep in each returned object. "
    "A name the response cannot carry is refused. Selection reaches the top level only: "
    "a name written as a path, such as `user.email`, names no field a response carries "
    "and is refused with the names that can be selected.",
)
#: The presets each list answers to, published as the parameter's enum and
#: accepted by the view from the same declaration.
USER_PRESETS = (MINIMAL_VIEW,)
PREFERENCE_PRESETS = (FULL_VIEW, MINIMAL_VIEW)

_MINIMAL_VIEW_PARAM = query_param(
    VIEW_PARAM,
    "Response preset. `minimal` reduces each user to its identifying fields "
    "(`id`, `username`, `email`). Omit it to receive the full entry.",
    enum=list(USER_PRESETS),
)
_PREFERENCE_VIEW_PARAM = query_param(
    VIEW_PARAM,
    "Response preset. `full` embeds the owning user's whole directory entry in each "
    "preference; `minimal` returns only `username`, `key` and `value`. Omit it to "
    "receive the owner's username alongside the preference.",
    enum=list(PREFERENCE_PRESETS),
)


class AddressMetadata(SimpleMetadata):
    """
    Describes an address by what it reads, rather than by what its view declares.

    The standard description is built from the parsers and the serializer
    declared on the view, which belong to the view as a whole rather than to
    the address being asked about: an address that reads one media type is
    otherwise described as reading every media type the platform reads
    anywhere, a method whose handler reads no body is described as taking the
    one the view's serializer carries, and a method whose body differs from
    what the view answers with is described as taking the answer. A caller
    that negotiates from this description sends what it was told to send, so
    each part of it is answered from the address and the method being
    described.
    """

    def determine_metadata(self, request, view):
        """Describe the address, naming the media types the bodies it takes are read in."""
        description = super().determine_metadata(request, view)
        description["parses"] = [parser.media_type for parser in view.get_parsers()]
        return description

    def determine_actions(self, request, view):
        """Describe the body of each method that takes one, and leave out the methods that take none."""
        described = super().determine_actions(request, view)
        served = getattr(view, "action_map", None) or {}
        bodiless = set(getattr(view, "bodiless_actions", ()))
        taken = getattr(view, "request_serializer_class", None)
        if taken is not None:
            described = {method: self.get_serializer_info(taken()) for method in described}
        return {
            method: fields for method, fields in described.items()
            if served.get(method.lower()) not in bodiless
        }


def _ordering_param(sort_fields, default):
    """Build the sort parameter of a list, naming the fields it accepts."""
    return query_param(
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

#: How a caller is admitted to the user directory and to the stored preferences.
_API_KEY_ADMISSION = "Answered only to a request carrying the site's API key in the `X-Edx-Api-Key` header."
#: Published by every operation of this version. Which representations an
#: answer can be given in is settled as the request enters, before the address
#: it names is read or written, so an ``Accept`` header naming none of them is
#: refused at every address whether or not that address answers with a body.
_RESP_SESSION_WITHOUT_ITS_TOKEN = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request was made with a session cookie and carried none of the request token the "
        "site hands out with it, so the site cannot tell that the request came from a page it "
        "served. Sending that token with the session, or a signed token in place of the "
        "session, is answered."
    ),
)
RESP_NOT_ACCEPTABLE = OpenApiResponse(
    ErrorResponse,
    description=(
        "The `Accept` header of the request names no representation this endpoint can answer in."
    ),
)
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
    ErrorResponse,
    description=(
        "The caller has changed this property too often lately and has to wait. Only a site that "
        "has given a limit for the property answers this; a site that has given none, which is "
        "how the platform ships, takes every change."
    ),
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

_ACCOUNT_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose account is addressed. "
    f"`{OWN_ACCOUNT_IDENTIFIER}` names the account of the caller, so an account holding that "
    "name is read through the collection, with `username=` naming it.",
    USERNAME_PATTERN,
)
_DEACTIVATION_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose account is deactivated. The name is read as it is stored, "
    f"`{OWN_ACCOUNT_IDENTIFIER}` included: this action disables the account the address names, "
    "never the account of the caller making the request.",
    USERNAME_PATTERN,
)
_ACCOUNT_VIEW_PARAM = query_param(
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


def _the_one_addressed(username, records, name_of):
    """
    Return the one of these records the address names, or refuse the address.

    A name can be answered with more than one record, in which case the record
    stored under exactly the name given is the one addressed. Where none of
    them is stored under it the address names no one of them, so it is refused
    rather than worked on: which record came back first is no basis for
    reading or changing an account. The refusal is reported under the path
    parameter that carried the name.
    """
    stored_under_it = [record for record in records if name_of(record) == username]
    if stored_under_it:
        return stored_under_it[0]
    if len(records) > 1:
        raise AmbiguousAccountName({"username": [AMBIGUOUS_ACCOUNT_ADDRESS]})
    if not records:
        raise NotFound
    return records[0]


def _user_named(username):
    """Return the user an address names, or refuse the address."""
    return _the_one_addressed(username, accounts_named(username), lambda user: user.username)


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
    """
    Return the property names a ``fields`` value asks for, each as it was written.

    A name is held against the properties the answer carries whole, so a name
    written with a dot in it names no such property and is refused. Reading
    only the part before the dot would hand back the whole of a property the
    caller asked one piece of, and would take a part after it that names
    nothing at all without saying so.
    """
    if not raw:
        return []
    return [name.strip() for name in raw.split(",") if name.strip()]


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

    #: What a caller is told in place of the names, at a view that may withhold
    #: them. A view that narrows what a response carries by who is asking says
    #: here what it tells a caller it has narrowed the answer for.
    withheld_fields_message = ""

    def shapeable_fields_may_be_named(self):
        """Whether a refusal may list the properties a response of this view can carry."""
        return True

    def check_requested_fields(self, request):
        """Refuse a selected field the response cannot carry, naming the ones it can."""
        available = self.shapeable_fields()
        unknown = sorted(set(_requested_field_names(request.query_params.get(FIELDS_PARAM))) - available)
        if unknown:
            if self.shapeable_fields_may_be_named():
                rest = f"Select any of: {', '.join(sorted(available))}."
            else:
                rest = self.withheld_fields_message
            raise ValidationError({
                FIELDS_PARAM: [f"No such field: {', '.join(unknown)}. {rest}"],
            })

    def shape(self, data, request):
        """Return ``data`` reduced to the preset and the fields the caller asked for."""
        data = self.shape_minimal(data, request)
        requested_fields = _requested_field_names(request.query_params.get(FIELDS_PARAM))
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
            "`course_key` to keep only the holders of a course forum role. "
            f"{_API_KEY_ADMISSION}"
        ),
        parameters=[_MINIMAL_VIEW_PARAM, _FIELDS_PARAM, _USER_ORDERING_PARAM],
        responses={
            200: _USER_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
    retrieve=extend_schema(
        summary="Get one user",
        description=f"Returns the directory entry of the user with this username. {_API_KEY_ADMISSION}",
        parameters=[_USER_PATH_PARAM, _MINIMAL_VIEW_PARAM, _FIELDS_PARAM],
        responses={
            200: _USER_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
)
class UserViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    _ActionAddressMixin,
    _MemberLookupMixin,
    _ResponsePresetMixin,
    StrictQueryParametersMixin,
    ScopedQuerysetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Directory entries of the platform's users, each addressed by username.

    An entry carries the identifying properties of one account together with
    the preferences stored against it. It is read by server integrations,
    which send the site's API key in the X-Edx-Api-Key header, and is
    refused to every request that does not carry that key.
    """

    # Who may read the directory is settled in three layers, each of which can
    # be replaced on its own: the permission classes below settle who may call
    # the collection at all, the record-visibility policy settles which rows
    # such a caller may see, and the filter set narrows those rows by the
    # parameters the request carries.

    permission_classes = (ApiKeyHeaderPermission,)
    serializer_class = UserDirectorySerializer
    pagination_class = UserApiPagination
    queryset = User.objects.all().select_related("profile")
    # A caller holding the API key may read every entry, so the policy keeps
    # the queryset whole; a narrower policy can replace it without touching the
    # filtering below.
    scoping_policy = FullScopePolicy()
    #: The preferences carried inside an entry are rows of the stored
    #: preference collection, so which of them a caller may see is that
    #: collection's to decide and not this one's. It is declared separately for
    #: that reason, and applied where the page is fetched rather than once per
    #: entry, which would cost a query per row.
    embedded_preference_policy = FullScopePolicy()
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

    def get_queryset(self):
        """Fetch the preferences carried inside each entry through the policy that governs them."""
        return super().get_queryset().prefetch_related(
            Prefetch(
                "preferences",
                queryset=self.embedded_preference_policy.scope(
                    UserPreference.objects.all(), self.request.user,
                ),
            ),
        )

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
            "preferences stored under that name, or `username` to keep only one user's. "
            f"{_API_KEY_ADMISSION}"
        ),
        parameters=[_PREFERENCE_VIEW_PARAM, _FIELDS_PARAM, _PREFERENCE_ORDERING_PARAM],
        responses={
            200: _PREFERENCE_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
    retrieve=extend_schema(
        summary="Get one stored preference",
        description=(
            "Returns the preference stored under this key for this user. A preference is "
            "identified by the pair, which the address carries as the username, a comma and "
            "the key, for example `alice,time_zone`. "
            f"{_API_KEY_ADMISSION}"
        ),
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
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
)
class UserPreferenceViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    _ActionAddressMixin,
    _ResponsePresetMixin,
    StrictQueryParametersMixin,
    ScopedQuerysetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Preferences stored for the platform's users, each addressed by its owner and its name.

    A preference is identified by the two together, so a member address
    carries the owner's username, a comma and the name the preference is
    stored under, as in alice,time_zone. Like the user directory, these are
    read only by a request carrying the site's API key.
    """

    # The pair identifies a row, so the member address is built from it rather
    # than from a database id, and the route that serves it is declared with
    # the collection rather than generated. Who may read these rows is settled
    # in the same three layers as the user directory.

    permission_classes = (ApiKeyHeaderPermission,)
    serializer_class = UserPreferenceRecordSerializer
    pagination_class = UserApiPagination
    queryset = (
        UserPreference.objects.all()
        .select_related("user", "user__profile")
        .annotate(username=F("user__username"))
    )
    scoping_policy = FullScopePolicy()
    #: The entry of a preference's owner carries that owner's own preferences,
    #: which are rows of this same collection but are reached through the
    #: entry rather than as rows of the page, so the policy that governs them
    #: is applied where they are fetched.
    embedded_preference_policy = FullScopePolicy()
    #: Which owners' user entries a row may embed, settled once per page; a row
    #: whose owner is left out keeps only the owner's name.
    embedded_user_policy = FullScopePolicy()
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

    def get_queryset(self):
        """Fetch each owner's own preferences through the policy that governs them, when embedding."""
        queryset = super().get_queryset()
        if full_view_requested(getattr(self, "request", None)):
            queryset = queryset.prefetch_related(
                Prefetch(
                    "user__preferences",
                    queryset=self.embedded_preference_policy.scope(
                        UserPreference.objects.all(), self.request.user,
                    ),
                ),
            )
        return queryset

    def get_serializer(self, *args, **kwargs):
        """Build the serializer, having settled which owners the rows it is given may carry."""
        serializer = super().get_serializer(*args, **kwargs)
        if args and full_view_requested(getattr(self, "request", None)):
            serializer.context[EMBEDDABLE_OWNER_IDS] = self._embeddable_owner_ids(args[0])
        return serializer

    def _embeddable_owner_ids(self, rows):
        """Return the ids of the owners of ``rows`` whose entry the caller may be given, in one query."""
        if isinstance(rows, UserPreference):
            rows = [rows]
        owner_ids = {row.user_id for row in rows}
        if not owner_ids:
            return frozenset()
        # The owners of the page are taken out of the policy's answer rather
        # than handed to it: a policy free to answer with a collection of its
        # own is then still narrowed to this page, and what comes back is the
        # size of the page rather than the size of the collection.
        owners = self.embedded_user_policy.scope(User.objects.all(), self.request.user)
        return frozenset(owners.filter(pk__in=owner_ids).values_list("pk", flat=True))

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
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    _ActionAddressMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    Time zones commonly used in a country, or every time zone when no country is named.

    The list is the same for every caller and is open to anyone.
    """

    # The rows come from the time-zone database rather than from a table of
    # this app's, so there is no queryset to narrow and the one parameter the
    # view reads is validated by a serializer instead of a filter set. The
    # data is reference data, which is why no caller is turned away.

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
            406: RESP_NOT_ACCEPTABLE,
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


#: Admission classes for every operation on an account, wherever it is served.
#: Read from here, not from a view attribute that middleware may extend in place.
ACCOUNT_RESOURCE_PERMISSIONS = (
    permissions.IsAuthenticated,
    CanGetAccountInfo,
    CanSelectAccountsByContact,
)


class AccountResourceMixin:
    """
    Carries the rules of the account resource into the operations on one account.

    An address beneath an account works on a row of the collection the account
    resource answers from, so a caller that resource refuses is a caller the
    operation refuses too: the classes the resource declares are applied first
    and the ones this address declares after, which keeps an address reached
    this way from standing open to a caller the resource is closed to. The
    account the address names is resolved here as well, so the record a
    permission settled the request on is the record the work lands on.
    """

    def account_resource_permissions(self):
        """Return the classes the account resource admits a caller by."""
        return ACCOUNT_RESOURCE_PERMISSIONS

    def declared_permissions(self):
        """Return the classes the address being served declares of its own."""
        return super().get_permissions()

    def get_permissions(self):
        """Return the collection's classes, then the ones this address declares."""
        declared = self.declared_permissions()
        already_carried = {type(permission) for permission in declared}
        carried = [
            permission() for permission in self.account_resource_permissions()
            if permission not in already_carried
        ]
        return carried + declared

    def addressed_account(self):
        """Return the account this address names, refusing an address that names no one account."""
        return _user_named(self.kwargs["username"])


@extend_schema(tags=[SDK_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List accounts",
        description=(
            "Returns a page of accounts. Name them with `username`, or select the accounts "
            "holding an `email` address or the one holding an `lms_user_id`; omit all three to "
            "receive your own account. A name, an address or an identifier no account holds is "
            "left out of the page, which is empty when nothing given is held. An address held by "
            "more than one account brings back each of them. "
            "Each account carries the properties the caller may "
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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
            415: RESP_UNREADABLE_BODY,
            429: _RESP_RATE_LIMITED,
            500: _RESP_CHANGE_FAILED,
        },
    ),
)
class AccountViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    ErrorExtensionsMixin,
    AccountResourceMixin,
    _ActionAddressMixin,
    _ResponsePresetMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    Accounts of the platform's users. Address one by username, or me for your own.

    An account carries the properties the caller may see: all of them for your
    own account and for a staff caller, and otherwise the ones its owner
    shares. A property an owner does not share is left out of their account
    rather than refused, so an account can come back as an empty object.

    Selecting accounts that nothing holds gives an empty page rather than a
    refusal. An address an account held before it was retired is refused
    instead, and that refusal says whether the retirement can still be
    cancelled.

    A property no account of this kind could show this caller is refused
    rather than answered as an empty object.
    """

    # Accounts come from the account service, not a queryset, and a filter set
    # cannot report a retired address, so the selection is applied in the handler.

    # Admits the session of an account whose email is not yet confirmed, which
    # the platform default refuses; tokens are read as the platform reads them.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = ACCOUNT_RESOURCE_PERMISSIONS
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
    withheld_fields_message = "An account another user holds carries only the properties its owner shares."
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

    def shapeable_fields_may_be_named(self):
        """
        Whether a refusal may list the properties an answer of this kind can carry.

        Which properties an owner shares is that owner's to decide, and the
        set the site offers them to choose from is the site's own
        configuration. A caller addressing an account it does not hold is
        therefore told that it may not select what it selected, and not what
        the site holds for other people: the list would otherwise name every
        property the site is configured to let an owner share, including ones
        nobody on the site shares. A caller that narrowed the answer itself,
        by asking for a preset, is still told what that preset carries.
        """
        return not self._only_shared_properties_are_readable(self.request)

    def _whole_accounts_are_readable(self, request):
        """Whether every account this request addresses comes back whole to this caller."""
        if request.query_params.get(VIEW_PARAM) == SHARED_VIEW:
            return False
        return not self._only_shared_properties_are_readable(request)

    def _only_shared_properties_are_readable(self, request):
        """Whether who the caller is keeps them from every property of the accounts addressed."""
        if request.user.is_staff:
            return False
        return self._addressed_names(request) != [request.user.username]

    def _selection(self, request):
        """
        Return the query string as the serializer that validates it reads it.

        Which accounts a request addresses is read both before the handler
        runs, to settle what the answer can carry, and inside it, to cut the
        page. Both read the values the serializer validated, so the two cannot
        disagree about which accounts are meant; and the serializer runs once,
        because it is what refuses a selector the list does not take.
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
        account = self._account_addressed(
            request,
            self._addressed_username(request, username),
            request.query_params.get(VIEW_PARAM),
        )
        return Response(self.shape(account, request))

    def partial_update(self, request, username):
        """Change the properties the body carries and return the account as it now stands."""
        username = self._addressed_username(request, username)
        changes = request.data
        if not isinstance(changes, dict):
            raise ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: ["Send the properties to change as an object."],
            })
        # Validated against the published shape, then passed on as sent so the
        # account service still refuses properties that cannot be changed.
        AccountUpdateSerializer(data=changes).is_valid(raise_exception=True)
        if "email" in changes and not settings.ALLOW_EMAIL_ADDRESS_CHANGE:
            raise ValidationError({"email": ["This site does not let an email address be changed here."]})
        self._check_change_rate(request, changes)
        try:
            with transaction.atomic():
                update_account_settings(request.user, changes, username=username)
                account = self._account_addressed(
                    request, username, request.query_params.get(VIEW_PARAM),
                )
        except UserNotAuthorized as exc:
            raise PermissionDenied from exc
        # The caller's own row is read again here and can be gone by now.
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
        found = self._accounts_found(request, names, preset)
        # Restore the page's order; an account stored under none of the selected
        # spellings is kept, at the end.
        by_name = {account["username"]: account for account in found}
        claimed = {name for name in names if name in by_name}
        ordered = [by_name[name] for name in names if name in by_name]
        ordered.extend(account for account in found if account["username"] not in claimed)
        return ordered

    def _account_addressed(self, request, username, preset=None):
        """
        Return the one account a member address names, as this caller may see it.

        The lookup settles which stored account an address names, so the
        account it comes back with is the answer here whatever name that
        account is stored under. An address the lookup comes back from with
        several accounts and none of them stored under exactly the name given
        names no one of them, so it is refused rather than answered with
        whichever of them came back first.
        """
        found = self._accounts_found(request, [username], preset)
        return _the_one_addressed(username, found, lambda account: account["username"])

    def _accounts_found(self, request, names, preset):
        """Return the accounts the lookup comes back with for these names."""
        try:
            return get_account_settings(request, names, view=preset if preset == SHARED_VIEW else None)
        except UserNotFound as exc:
            raise NotFound from exc

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
            return names_in(usernames)
        email = selection.get("email")
        if email:
            return self._usernames_holding_email(email)
        identifier = selection.get("lms_user_id")
        if identifier is not None:
            return self._usernames_holding_identifier(identifier)
        return [request.user.username]

    def _usernames_holding_email(self, email):
        """Return the accounts holding this address, by name, and nothing when none does."""
        if is_email_retired(email):
            raise self._retired_account(email)
        return usernames_holding_email(email)

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
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
)
class AccountDeactivationView(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    AccountResourceMixin,
    StrictQueryParametersMixin,
    APIView,
):
    """
    Disables the account the address names, so its owner can no longer sign in with it.

    The account and everything belonging to it are kept, and none of it is
    removed. Asking for a password reset does not undo this.
    """

    # A view of its own: its callers and body differ from the account collection's.

    permission_classes = (permissions.IsAuthenticated, IsActiveUser, CanDeactivateUser)

    def post(self, request, username):
        """Make the password of the named account unusable and record it as disabled."""
        user = self.addressed_account()
        segment.identify(user.id, {"is_disabled": "true"})
        user.set_unusable_password()
        user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class _SearchedAccountsMixin:
    """
    The accounts a search names, before it is narrowed to the ones a caller may see.

    A view's scoping policy is applied on top of the selection the classes
    resolved after it answer with, so the unnarrowed selection is declared
    here rather than on the view, where it would stand in front of the layer
    that narrows it and leave the policy with nothing to do.
    """

    #: The addresses asked about, held for as long as one request is served.
    searched_emails = ()

    def get_queryset(self):
        """Return the accounts holding the addresses this request asked about."""
        return accounts_holding_emails(self.searched_emails)


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
            406: RESP_NOT_ACCEPTABLE,
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class EmailSearchViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    ScopedQuerysetMixin,
    _SearchedAccountsMixin,
    viewsets.ViewSet,
):
    """
    The accounts holding a given set of email addresses.

    The addresses to look for are sent in the request body, so a search is
    made with POST, and the links on a page of results are followed by sending
    the body to them again rather than by fetching them. Only a platform
    administrator may search here; every other caller is told there is nothing
    at this address, whatever state their own account is in. An administrator
    whose account is not active is refused.
    """

    # A POST so the addresses stay out of server logs and browser histories.

    # The session of an account that has not confirmed its email address is
    # read rather than dropped, so it gets the answer a token of that account
    # gets instead of the request being treated as anonymous. A token is read
    # by the library class the platform's own pair is built on, named because
    # a view declaring its own classes is not one that pair serves.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    # A caller who is not an administrator is told there is nothing here
    # before the state of their own account is read, so an inactive account
    # is not refused in a way that confirms the search exists.
    permission_classes = (
        permissions.IsAuthenticated,
        CanGetAccountInfo,
        IsStaffOrAbsent,
        IsActiveUser,
    )
    # A platform administrator may read every account the addresses match, so
    # the policy keeps the selection whole; a narrower policy can replace it
    # without touching the search below, and the accounts come from the same
    # table the other collections of this version narrow.
    scoping_policy = FullScopePolicy()
    parser_classes = (JSONParser,)
    metadata_class = AddressMetadata
    serializer_class = EmailSearchResultSerializer
    #: A search is asked for by sending the addresses to look for and is
    #: answered with an account per address found, so the body it takes is not
    #: the shape it answers with.
    request_serializer_class = EmailSearchSerializer
    pagination_class = UserApiPagination

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Return a page of the accounts holding the addresses the body carries."""
        search = EmailSearchSerializer(data=request.data)
        search.is_valid(raise_exception=True)
        self.searched_emails = search.validated_data["emails"]
        return self.paginate_iterable(request, self.get_queryset())


_NAME_CHANGE_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose requested change is confirmed. The name is read as it is "
    f"stored, so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than "
    "the account of the caller making the request.",
    USERNAME_PATTERN,
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
            403: _RESP_SESSION_WITHOUT_ITS_TOKEN,
            404: OpenApiResponse(
                ErrorResponse,
                description="The account of the caller carries no profile to put a name on.",
            ),
            406: RESP_NOT_ACCEPTABLE,
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class NameChangeViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
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
    #: Describes each address by the body and the media types it reads.
    metadata_class = AddressMetadata
    #: The actions whose handler reads no body, which their address describes
    #: none for. Confirming a change carries out the one already asked for, so
    #: there is nothing for a caller to send.
    bodiless_actions = ("confirmations",)
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
            406: RESP_NOT_ACCEPTABLE,
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


_VERIFICATION_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose identity verification is addressed. The name is read as it is "
    f"stored, so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than "
    "the account of the caller making the request.",
    USERNAME_PATTERN,
)


def _verification_sort_key(attempt):
    """Return what an attempt is ordered by, with a tie-break that is always comparable."""
    return (attempt.updated_at, str(getattr(attempt, "receipt_id", "") or ""))


@extend_schema(tags=[SDK_TAG])
class IDVerificationStatusView(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    AccountResourceMixin,
    StrictQueryParametersMixin,
    APIView,
):
    """
    Whether a user has proved their identity, as one summary.

    Every account has a summary, and one belonging to a user who has been
    through no check says so; the address is absent only when the user it
    names is. A user may read their own summary, and a staff caller whose own
    account is still active may read anyone's.
    """

    # Computed by the identity verification service, one per account; a view of
    # its own because it admits different callers from the account collection.

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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
)
class IDVerificationViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    AccountResourceMixin,
    StrictQueryParametersMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    The attempts one user has made at proving their identity.

    A user may read their own attempts, and a staff caller may read anyone's.
    A user who has been through no checks has an empty page; the address is
    absent only when the user it names is.
    """

    # Collected from the identity verification service's tables and sorted
    # here, since there is no queryset of this app's to narrow.

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


_ACCOUNT_PREFERENCES_PATH_PARAM = path_param(
    "username",
    "Unique name of the user whose preferences are addressed. The name is read as it is stored, "
    f"so `{OWN_ACCOUNT_IDENTIFIER}` here names an account holding that name rather than the "
    "account of the caller making the request.",
    USERNAME_PATTERN,
)
_ACCOUNT_PREFERENCE_KEY_PATH_PARAM = path_param(
    "key",
    "Name the preference is stored under, which is the whole segment, punctuation included. A "
    "name carrying a slash has no address of its own, because the segment ends at the slash; "
    "such a name is read with the whole document and removed by sending a null for it there. "
    f"Nothing is stored under a name longer than {PREFERENCE_NAME_MAX_LENGTH} characters, nor "
    "under one carrying no letter, digit, hyphen or underscore. A name with whitespace at either "
    "end names no preference: one is stored under its name without the whitespace around it.",
    rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$",
)

#: The actions of the preference resource an active administrator may call
#: against an account other than their own.
STAFF_READABLE_PREFERENCE_ACTIONS = ("retrieve_all", "retrieve")

#: The action that changes the whole preference document of one account.
DOCUMENT_CHANGE_ACTION = "partial_update_all"

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
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    AccountResourceMixin,
    StrictQueryParametersMixin,
    viewsets.ViewSet,
):
    """
    The preferences of the one account an address names.

    They are one document: an object of preference name to stored value, read
    whole and changed with a merge patch. Each preference is also reached on
    its own. A user reads and changes their own preferences, and a staff
    caller whose own account is still active reads anyone's but changes
    nobody else's.

    A preference name takes the whole of its segment of the address, so the
    names carrying punctuation that the platform's own features store under
    can be addressed. A name carrying a slash cannot, because the segment ends
    there; such a name is read and removed through the whole document, which
    is also the only way one arrives.
    """

    # The preferences are read and written through the preference service
    # rather than from a table of this app's, so there is no queryset here.
    # Treating them as one document is what the pages that keep a user's
    # settings read and save; this collection carries no pages of its own, and
    # the record-shaped view of the same data across accounts is the stored
    # preference collection of this version.

    # The language a user picked is written from every page of the site, and
    # the settings page these preferences are kept on is reached by users who
    # have not confirmed their email address yet, whom the platform default
    # refuses. Only the session class differs in what it admits: a token is
    # read by the library class the platform's own pair is built on, named here
    # because a view declaring its own classes is not one that pair serves.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = _PREFERENCE_WRITE_PERMISSIONS
    serializer_class = AccountPreferenceSerializer
    #: Describes each address by the body and the media types it reads.
    metadata_class = AddressMetadata

    #: Action being served, which the parsers follow. It is declared here
    #: because it is recorded while the request is being built, which is
    #: before the view machinery would otherwise set it.
    action = None

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def declared_permissions(self):
        """Return the classes this address declares for the request being served."""
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

    def initial(self, request, *args, **kwargs):
        """
        Admit the caller, then answer an address whose name has whitespace at either end as absent.

        The preference service stores a name without the whitespace around it,
        so a value stored from such an address would land under another name
        and could not be read back or removed there. The address is therefore
        absent whatever is asked of it.
        """
        super().initial(request, *args, **kwargs)
        key = kwargs.get("key")
        if key is not None and key != key.strip():
            raise NotFound

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
        """Return the parsers that read the bodies the address being served takes."""
        if self.action == DOCUMENT_CHANGE_ACTION or self._describes_an_address_the_document_is_changed_at():
            return [parser() for parser in _DOCUMENT_PARSERS]
        return super().get_parsers()

    def _describes_an_address_the_document_is_changed_at(self):
        """
        Return whether the request asks what an address that takes the whole document answers.

        Such a request is answered with the media types that address reads,
        which are the ones of the change it serves rather than the ones read
        everywhere else; the action recorded for it is not the change, so the
        methods the address serves are what decide.
        """
        return (
            self.action == DESCRIBE_ADDRESS_ACTION
            and DOCUMENT_CHANGE_ACTION in (self.action_map or {}).values()
        )

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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
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
            "under `key`, which is the part of the address that carried it. A name with "
            "whitespace at either end is absent, and nothing is stored."
        ),
        parameters=[_ACCOUNT_PREFERENCES_PATH_PARAM, _ACCOUNT_PREFERENCE_KEY_PATH_PARAM],
        request=AccountPreferenceValueSerializer,
        responses={
            204: OpenApiResponse(description="The preference now holds this value."),
            400: _RESP_PREFERENCE_NOT_STORED,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(
                ErrorResponse,
                description="The name in the address has whitespace at one end or both, so it names no preference.",
            ),
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
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
            406: RESP_NOT_ACCEPTABLE,
            415: RESP_UNREADABLE_BODY,
            500: _RESP_PREFERENCE_SERVICE_FAILED,
        },
    ),
)
class EmailOptInViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    StrictQueryParametersMixin,
    viewsets.ViewSet,
):
    """
    The choices users have made about the messages organizations send them.

    A choice belongs to the user who made it, so it is recorded against the
    caller and no username is named anywhere in the request. The organization
    it applies to is the one running the course the body names. The choice is
    held per organization rather than per course, so naming two courses of one
    organization records one choice.
    """

    # Naming the course rather than the organization is how the pages a user
    # makes this choice on identify the organization they are making it about.

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


#: Fields the retirement queue sorts on. A retirement's own identifier is not
#: among them: it is the order the queue is worked in and the order a page
#: falls back to, but it is a database key rather than something a caller
#: chooses a working order by.
RETIREMENT_SORT_FIELDS = ("created", "modified", "original_username", "current_state")

#: The order the queue is worked in, which is the order retirements were
#: requested in.
RETIREMENT_DEFAULT_SORT = ("id",)

#: The presets a retirement response answers to.
RETIREMENT_PRESETS = (MINIMAL_VIEW,)

#: The retirement actions that answer with a retirement, and so read the
#: parameters that shape one.
SHAPED_RETIREMENT_ACTIONS = ("list", "retrieve")

_RETIREMENT_PATH_PARAM = path_param(
    "username",
    "Name the account being retired was registered under. A retired account is stored under a "
    "hashed name, and either name addresses the same retirement when one is read; a write "
    "addresses the registered name alone, spelled exactly as it was registered.",
    USERNAME_PATTERN,
)
_RETIREMENT_VIEW_PARAM = query_param(
    VIEW_PARAM,
    "Response preset. `minimal` reduces each retirement to which account it is for and where it "
    "stands, carrying none of the account owner's own data. Omit it to receive the whole record.",
    enum=list(RETIREMENT_PRESETS),
)
#: The number of days is published from the filter that reads it, so the two
#: cannot disagree. It is declared here because the kind of value a filter
#: taking a method of its own reads cannot be worked out from the records.
_COOL_OFF_FILTER = RetirementFilterSet.base_filters["cool_off_days"]
_RETIREMENT_COOL_OFF_PARAM = query_param(
    "cool_off_days",
    _COOL_OFF_FILTER.extra["help_text"],
    type={
        "type": "integer",
        "minimum": _COOL_OFF_FILTER.extra["min_value"],
        "maximum": _COOL_OFF_FILTER.extra["max_value"],
    },
)
_RETIREMENT_ORDERING_PARAM = query_param(
    "ordering",
    "Fields to sort by, in order of precedence, each optionally prefixed with `-` to sort "
    "descending. A field that is not listed here is refused. Omit it to receive the retirements "
    "in the order they were requested, which is the order the pipeline works them in.",
    many=True,
    enum=[*RETIREMENT_SORT_FIELDS, *(f"-{field}" for field in RETIREMENT_SORT_FIELDS)],
    style="form",
    explode=False,
)
_PARTNER_REPORT_PATH_PARAM = path_param(
    "username",
    "Name the account was registered under, spelled exactly as it was registered.",
    USERNAME_PATTERN,
)

#: A retirement comes back whole, as the fields the ``minimal`` preset keeps,
#: or as the properties ``fields`` selected.
_RETIREMENT_SHAPES = ResponseShapes(
    component_name="RetirementResponse",
    serializers=[
        RetirementSerializer,
        RetirementMinimalSerializer,
        RetirementProjectionSerializer,
    ],
)

_RESP_NO_SUCH_RETIREMENT = OpenApiResponse(
    ErrorResponse, description="No retirement was requested under that name.",
)
#: What a refusal says at an address that finds the record it acts on by the
#: name the record was requested under. That name is not unique, so more than
#: one record may answer to it, and the request is refused rather than one of
#: them picked.
_RESP_AMBIGUOUS_OR_BAD_REQUEST = OpenApiResponse(
    ErrorResponse,
    description=(
        "A query parameter is missing, invalid, or not one this endpoint reads, or more than one "
        "record was requested under the name given. Nothing was changed."
    ),
)
_RESP_AMBIGUOUS_OR_UNUSABLE_BODY = OpenApiResponse(
    ErrorResponse,
    description=(
        "The request body is not well formed in the media type it was sent in, or carries a value "
        "this endpoint will not take, or more than one record was requested under a name the "
        "request gives. A query parameter this endpoint does not read is refused here too. "
        "Nothing was changed."
    ),
)
_RESP_RETIREMENT_STATE = OpenApiResponse(
    ErrorResponse,
    description=(
        "The retirement does not stand at a step of the pipeline that admits this operation. "
        "Nothing was changed."
    ),
)
_RESP_BATCH_STATE = OpenApiResponse(
    ErrorResponse,
    description=(
        "One or more of the records named is not in a state this operation acts on. Nothing was "
        "deleted, and `errors` names each record at fault and why."
    ),
)
_RESP_RETIREMENT_MOVED = OpenApiResponse(description="The retirement now stands at the step asked for.")
_RESP_RETIREMENT_CANCELLED = OpenApiResponse(
    description="The retirement request is gone and the account it was made for is usable again.",
)
_RESP_DATA_ERASED = OpenApiResponse(description="The data named by the scope has been erased.")
_RESP_ERASURE_FAILED = OpenApiResponse(
    ErrorResponse,
    description=(
        "A step of the erasure did not finish. Part of the data named by the scope may already "
        "be gone, and the request may be sent again: each step clears what it clears whether or "
        "not it has run before."
    ),
)
_RESP_PARTNER_REPORT_QUEUED = OpenApiResponse(
    description=(
        "The account is queued for reporting to the organizations it studied with, or was not "
        "enrolled with any and so needs no report."
    ),
)

class _RetirementRecordAccessMixin:
    """
    Who may act on the retirement records, and which of those records a caller acts on.

    The records this version lists, moves and cancels are the records its
    batch disposal deletes, so the capability each operation asks for and the
    policy deciding which records a caller acts on are declared here, where
    all of them resolve them, rather than once per view. A rule added here is
    asked of the disposal as well as of the operations on one record.

    These are declared in a class resolved before the library mixin that
    reads the policy, which declares a policy of its own for a view that has
    none; the selection the policy narrows is declared in a class resolved
    after it instead.
    """

    permission_classes = (permissions.IsAuthenticated,)
    # The operations these addresses replace read a body as JSON alone, and
    # one of them erases personal data for good, so the same narrowing is kept
    # rather than widened to every media type the platform can parse.
    parser_classes = (JSONParser,)
    # Any caller holding the pipeline's capability may act on every
    # retirement, so the policy keeps the records whole; a narrower policy can
    # replace it here and is applied by every operation below.
    scoping_policy = FullScopePolicy()
    #: The capability each action asks for beyond the classes above.
    action_permissions = {}

    def get_permissions(self):
        """Return the classes deciding who may make the request being served."""
        if self.action is None:
            # No action serves this method at this address, so there is no
            # capability to ask for: the request is admitted by the classes
            # every action shares and answered as a method this address does
            # not serve. Each address this replaces serves one method and asks
            # its capability of every caller it is given, so a caller without
            # that capability is refused there before the method is weighed.
            return [permission() for permission in self.permission_classes]
        # An action this mapping does not name reaches no capability of this
        # resource, rather than inheriting the one every other action shares.
        specific = self.action_permissions.get(self.action, (~permissions.AllowAny,))
        return [permission() for permission in (*self.permission_classes, *specific)]


class _RetirementRecordsMixin:
    """
    The retirement records, before they are narrowed to the ones a caller may act on.

    A view's policy is applied on top of the selection the classes resolved
    after it answer with, so the unnarrowed selection is declared here rather
    than on the view, where it would stand in front of the layer that narrows
    it and leave the policy with nothing to do.
    """

    def get_queryset(self):
        """Return the retirement records, before the policy narrows them to the caller's."""
        return retirements()


#: Who may make each request the retirement collection serves. Cancelling a
#: retirement is the one operation of this collection that is not pipeline
#: work: it undoes a request rather than carrying it out, so it is open to
#: whoever handles that for a learner and closed to the pipeline itself. An
#: action this mapping does not name is refused, so one added later cannot
#: reach the pipeline's capability by inheriting it.
RETIREMENT_ACTION_PERMISSIONS = {
    "list": (CanRetireUser,),
    "retrieve": (CanRetireUser,),
    "partial_update": (CanRetireUser,),
    "erasures": (CanRetireUser,),
    "destroy": (CanCancelUserRetirement,),
    "metadata": (CanRetireUser,),
}


# How this collection is built, which is written here rather than in the
# description the class carries: that description is what an OPTIONS request
# is answered with, so it is published text and belongs to the caller.
#
# A retirement is not written through generic machinery -- every change goes
# through the pipeline's own model layer, which is what decides whether a step
# may run. Access is layered: the permission classes decide who may call an
# action at all, the visibility policy decides which retirements such a caller
# may act on, and the filters narrow the page a list carries further. Every
# action resolves the records it reads or changes from the selection the policy
# left, a member address included, so a retirement the policy leaves out is
# reported as absent rather than refused.
@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    list=extend_schema(
        summary="List retirements",
        description=(
            "Returns a page of the retirement queue. Supply `state` to keep only the retirements "
            "standing at one of the pipeline steps it names, `cool_off_days` to keep only those "
            "requested at least that many days ago, and `created_after` or `created_before` to "
            "bound when they were requested."
        ),
        parameters=[
            _RETIREMENT_VIEW_PARAM,
            _FIELDS_PARAM,
            _RETIREMENT_COOL_OFF_PARAM,
            _RETIREMENT_ORDERING_PARAM,
        ],
        responses={
            200: _RETIREMENT_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_PAGE,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
    retrieve=extend_schema(
        summary="Get one retirement",
        description=(
            "Returns the retirement of the account this name addresses. A retired account is "
            "stored under a hashed name, and both that name and the one the account was "
            "registered under address the same retirement."
        ),
        parameters=[_RETIREMENT_PATH_PARAM, _RETIREMENT_VIEW_PARAM, _FIELDS_PARAM],
        responses={
            200: _RETIREMENT_SHAPES,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_RETIREMENT,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
    partial_update=extend_schema(
        summary="Move a retirement to another pipeline step",
        description=(
            "Records that this retirement now stands at another step of the pipeline, and "
            "appends what happened at the step it is leaving to its log. The steps and their "
            "order are configured by the operator, and a step that is not a later one than the "
            "current step is refused unless `force` is sent."
        ),
        parameters=[_RETIREMENT_PATH_PARAM],
        request=RetirementUpdateSerializer,
        responses={
            204: _RESP_RETIREMENT_MOVED,
            400: _RESP_AMBIGUOUS_OR_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_RETIREMENT,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_RETIREMENT_STATE,
            415: RESP_UNREADABLE_BODY,
        },
    ),
    destroy=extend_schema(
        summary="Cancel a retirement",
        description=(
            "Deletes this retirement request and returns the account it was made for to the "
            "address it was registered under, with a password nobody holds, so its owner signs "
            "in again by asking for a new one. Only a retirement the pipeline has not started "
            "work on can be cancelled."
        ),
        parameters=[_RETIREMENT_PATH_PARAM],
        responses={
            204: _RESP_RETIREMENT_CANCELLED,
            400: _RESP_AMBIGUOUS_OR_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_RETIREMENT,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_RETIREMENT_STATE,
        },
    ),
    erasures=extend_schema(
        summary="Erase a retiring account's data",
        description=(
            "Erases one set of the retiring account's personal data. This cannot be undone, and "
            "which set is erased is named by `scope` in the body. The retirement is not moved by "
            "this request: the caller records where it stands."
        ),
        parameters=[_RETIREMENT_PATH_PARAM],
        request=RetirementErasureSerializer,
        responses={
            204: _RESP_DATA_ERASED,
            400: _RESP_AMBIGUOUS_OR_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_RETIREMENT,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_RETIREMENT_STATE,
            415: RESP_UNREADABLE_BODY,
            500: _RESP_ERASURE_FAILED,
        },
    ),
)
class RetirementViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    ErrorExtensionsMixin,
    _ActionAddressMixin,
    _ResponsePresetMixin,
    StrictQueryParametersMixin,
    _RetirementRecordAccessMixin,
    ScopedQuerysetMixin,
    _RetirementRecordsMixin,
    viewsets.GenericViewSet,
):
    """
    The accounts being retired, addressed by the name each was registered under.

    A retirement records one account's progress through the steps the operator
    has configured. The requests here carry that work out; where an account
    stands is recorded separately, by the caller driving them.

    A read answers for either the name the account was registered under or the
    hashed name it was given when retired: both address the same account. A
    write takes the registered name alone, spelled exactly as it was
    registered, so it reaches one account and no other.

    Cancelling a retirement is open to a different caller from the requests
    that carry one out.
    """

    serializer_class = RetirementSerializer
    pagination_class = UserApiPagination
    action_permissions = RETIREMENT_ACTION_PERMISSIONS
    lookup_field = "username"
    # The router's default lookup pattern excludes dots, which usernames allow.
    lookup_value_regex = settings.USERNAME_REGEX_PARTIAL
    filter_backends = (DjangoFilterBackend, StrictOrderingFilter)
    filterset_class = RetirementFilterSet
    ordering_fields = RETIREMENT_SORT_FIELDS
    ordering = RETIREMENT_DEFAULT_SORT
    # A sort the caller asks for is completed with the order the queue is
    # worked in, so that retirements comparing equal keep one relative order
    # from page to page and none is seen twice or missed.
    ordering_tiebreak = ("id",)
    minimal_fields = RETIREMENT_MINIMAL_FIELDS
    response_presets = RETIREMENT_PRESETS
    extra_query_params = RESPONSE_SHAPING_PARAMS

    def known_query_params(self):
        """Return the query parameters the action being served reads."""
        names = super().known_query_params()
        if self.action != "list":
            # One retirement is answered whole, so neither the filters that
            # choose which retirements a page carries nor the parameters that
            # cut it into pages are read at a member address.
            names -= set(self.filterset_class.base_filters)
            names -= {
                StrictOrderingFilter.ordering_param,
                self.pagination_class.page_query_param,
                self.pagination_class.page_size_query_param,
            }
        if self.action not in SHAPED_RETIREMENT_ACTIONS:
            names -= set(RESPONSE_SHAPING_PARAMS)
        return names

    def list(self, request, *args, **kwargs):
        """Return a page of the retirement queue, shaped as the caller asked."""
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        serializer = self.get_serializer(page, many=True)
        return self.shape_page(self.get_paginated_response(serializer.data), request)

    def retrieve(self, request, username):
        """Return the retirement of the account this name addresses."""
        retirement = retirement_of_the_account_named(self.get_queryset(), username)
        if retirement is None:
            log.info("No retirement is held for an account addressed as %r.", username)
            raise NotFound(NO_SUCH_RETIREMENT)
        return Response(self.shape(self.get_serializer(retirement).data, request))

    def partial_update(self, request, username):
        """Record that this retirement now stands at another step of the pipeline."""
        changes = RetirementUpdateSerializer(data=request.data)
        changes.is_valid(raise_exception=True)
        move_retirement(
            retirement_requested_as(self.get_queryset(), username),
            changes.validated_data["new_state"],
            changes.validated_data["response"],
            changes.validated_data["force"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, username):
        """Undo this retirement request and return the account to its owner."""
        cancel_retirement(retirement_requested_as(self.get_queryset(), username))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="erasures", url_name="erasure")
    def erasures(self, request, username):
        """Erase one set of the retiring account's personal data."""
        erasure = RetirementErasureSerializer(data=request.data)
        erasure.is_valid(raise_exception=True)
        erase_account_data(
            retirement_open_to_action(self.get_queryset(), username),
            erasure.validated_data["scope"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)



class _PartnerReportAccessMixin:
    """
    Who may act on the partner report entries, and which of those entries a caller acts on.

    The entries this version queues and takes for processing are the entries
    its batch disposal deletes, so the capability every operation of them asks
    for and the policy deciding which of them a caller acts on are declared
    here, where all of them resolve them. A rule added here is asked of the
    disposal as well as of the queue. Which entries each operation starts from
    differs -- one takes those nobody has taken, the other disposes of those
    that have been -- so that selection is declared by each of them, in a
    class resolved after the library mixin that reads the policy.
    """

    permission_classes = (permissions.IsAuthenticated, CanRetireUser)
    # The operations these addresses replace read a body as JSON alone, and
    # that narrowing is kept rather than widened to every media type the
    # platform can parse.
    parser_classes = (JSONParser,)
    scoping_policy = FullScopePolicy()


class _UnclaimedPartnerReportsMixin:
    """
    The partner reports waiting to be processed, before they are narrowed to the ones a caller may see.

    A view's scoping policy is applied on top of the selection the classes
    resolved after it answer with, so the unnarrowed selection is declared
    here rather than on the view, where it would stand in front of the layer
    that narrows it and leave the policy with nothing to do.
    """

    def get_queryset(self):
        """Return the partner reports nobody has taken for processing."""
        return unclaimed_partner_reports()


class _EveryPartnerReportMixin:
    """
    Every partner report entry, before they are narrowed to the ones a caller may act on.

    Declared in a class of its own for the same reason as the queue's own
    selection, and separately from it because an entry is disposed of once it
    has been taken for processing, which is what the queue leaves out.
    """

    def get_queryset(self):
        """Return every partner report entry, taken for processing or not."""
        return partner_reports()


_RESP_PARTNER_REPORTS_TAKEN = OpenApiResponse(
    RetirementPartnerReportRecordSerializer(many=True),
    description=(
        "The entries taken by this request, which are exactly the entries it marked as being "
        "processed. A second request returns the entries added since."
    ),
)


@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Take partner reports for processing",
        description=(
            "Returns the queued partner reports nobody has taken yet and marks the ones it "
            "returns as being processed, so that two callers do not report the same account. "
            "Nothing releases an entry once it has been taken, which is why this is a write "
            "rather than a read and why the answer is not cut into pages: a page would hand the "
            "caller the first entries while taking all of them."
        ),
        parameters=[
            query_param(
                "limit",
                f"Take at most this many entries, from 1 to {PARTNER_REPORT_CLAIM_MAX}. Omit it to "
                "take every entry waiting.",
                type={"type": "integer", "minimum": 1, "maximum": PARTNER_REPORT_CLAIM_MAX},
            ),
        ],
        request=None,
        responses={
            200: _RESP_PARTNER_REPORTS_TAKEN,
            400: _RESP_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            406: RESP_NOT_ACCEPTABLE,
        },
    ),
    update=extend_schema(
        summary="Queue an account for partner reporting",
        description=(
            "Queues the retiring account this name addresses for reporting to the organizations "
            "running the courses it was enrolled in. An account enrolled with no organization "
            "needs no report and is not queued. Sending this twice for the same account leaves "
            "one entry."
        ),
        parameters=[_PARTNER_REPORT_PATH_PARAM],
        request=None,
        responses={
            204: _RESP_PARTNER_REPORT_QUEUED,
            400: _RESP_AMBIGUOUS_OR_BAD_REQUEST,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            404: _RESP_NO_SUCH_RETIREMENT,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_RETIREMENT_STATE,
        },
    ),
)
class RetirementPartnerReportViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    ErrorExtensionsMixin,
    _ActionAddressMixin,
    StrictQueryParametersMixin,
    _PartnerReportAccessMixin,
    ScopedQuerysetMixin,
    _UnclaimedPartnerReportsMixin,
    viewsets.ViewSet,
):
    """
    The accounts whose retirement still has to be reported to the organizations they studied with.

    An entry is added as the pipeline finishes an account, and carries the name
    and address that account held before it was retired together with the short
    names of the organizations running the courses it was enrolled in.

    Taking entries is a write rather than a read: a request is answered with
    exactly the entries it has marked as taken, so an entry added while that
    answer is being built is left for the next caller rather than reported to
    this one. Nothing releases an entry once it has been taken.
    """

    serializer_class = RetirementPartnerReportRecordSerializer
    #: Validates the query string, and names the parameters this view reads.
    query_serializer_class = RetirementPartnerReportQuerySerializer
    lookup_field = "username"
    # The router's default lookup pattern excludes dots, which usernames allow.
    lookup_value_regex = settings.USERNAME_REGEX_PARTIAL

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def known_query_params(self):
        """Return the query parameters the action being served reads."""
        names = super().known_query_params()
        if self.action != "create":
            # How many entries to take is only meaningful where entries are taken.
            names -= set(self.query_serializer_class().fields)
        return names

    def create(self, request):
        """Take the queued partner reports and return them."""
        query = self.query_serializer_class(data=request.query_params)
        query.is_valid(raise_exception=True)
        reports = claim_partner_reports(self.get_queryset(), query.validated_data.get("limit"))
        return Response(self.get_serializer(reports, many=True).data)

    def update(self, request, username):
        """Queue the retiring account this name addresses for partner reporting."""
        # The record this queues belongs to the retirement collection rather
        # than to this one, whose own selection is of queue entries, so the
        # records it is resolved from are named here and the policy this view
        # declares is applied to them, as it is to everything else it acts on.
        records = self.scoping_policy.scope(retirements(), request.user)
        record_partner_report(retirement_open_to_action(records, username))
        return Response(status=status.HTTP_204_NO_CONTENT)


# The entries a request answers with are read first and marked by the
# identifiers that were read, which is what keeps an entry added during the
# read from being marked by a caller that never saw it; the enrolments behind
# the organization names are fetched with the entries so that the cost of the
# answer does not grow with the number of entries. Written here rather than in
# the description the class carries, which is published to every caller that
# asks an address what it does.
_RESP_BATCH_OUTCOME = OpenApiResponse(
    RetirementBatchOutcomeSerializer,
    description=(
        "What was done about each name given. A name with no record left is reported as such "
        "rather than refused, so a repeat of a request already carried out is answered."
    ),
)


# One operation over a set of records named in its body, with a condition that
# covers the whole set and no member address of its own, so it stands at a
# collection of its own rather than among the operations on one record. Who may
# call it and which records it deletes are resolved through the layers those
# operations resolve them through, so a rule added to the records is asked of
# their disposal too. Written here rather than in the description the class
# carries, which is published to every caller that asks what this address does.
@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Delete finished retirement records",
        description=(
            "Deletes the retirement records of the accounts named, once the pipeline has "
            "finished with them. The record's own copies of the account's name, address and "
            "full name are written over before it is deleted, so that a system syncing these "
            "rows and treating a delete as a soft one is left holding the replacement. A record "
            "the pipeline has not finished with refuses the whole batch, and nothing is deleted."
        ),
        request=RetirementBatchSerializer,
        responses={
            200: _RESP_BATCH_OUTCOME,
            400: _RESP_AMBIGUOUS_OR_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_BATCH_STATE,
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class RetirementCleanupViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    ErrorExtensionsMixin,
    StrictQueryParametersMixin,
    _RetirementRecordAccessMixin,
    ScopedQuerysetMixin,
    _RetirementRecordsMixin,
    viewsets.ViewSet,
):
    """
    Disposes of a set of finished retirement records.

    The accounts to dispose of are named in the body, and every one of them has
    to name a retirement the pipeline has finished with: if any does not, the
    whole batch is refused and nothing is deleted.

    This is not how a retirement request is called off. That works on a request
    the pipeline has not started on, is asked for one account at a time, and is
    open to a different caller.
    """

    serializer_class = RetirementBatchOutcomeSerializer
    #: Disposing of finished records is pipeline work, so it asks for the
    #: capability the rest of that work asks for.
    action_permissions = {"create": (CanRetireUser,), "metadata": (CanRetireUser,)}

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Delete the retirement records of the accounts the body names."""
        batch = RetirementBatchSerializer(data=request.data)
        batch.is_valid(raise_exception=True)
        results = delete_retirements(self.get_queryset(), batch.validated_data["usernames"])
        return Response(self.get_serializer({"results": results}).data)


# This disposal stands at a collection of its own for the same reason the
# retirement records' own disposal does, and the two are kept apart because
# they act on different records in different tables under different conditions.
# Who may call this one and which entries it deletes are resolved through the
# layers the operations on those entries resolve them through. Written here
# rather than in the description the class carries, which is published to every
# caller that asks what this address does.
@extend_schema(tags=[USER_TAG])
@extend_schema_view(
    create=extend_schema(
        summary="Delete reported partner report entries",
        description=(
            "Deletes the partner report entries of the accounts named, once they have been "
            "reported on. An entry nobody has taken for processing has not been reported on "
            "yet, so it refuses the whole batch and nothing is deleted."
        ),
        request=RetirementBatchSerializer,
        responses={
            200: _RESP_BATCH_OUTCOME,
            400: _RESP_AMBIGUOUS_OR_UNUSABLE_BODY,
            401: RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
            406: RESP_NOT_ACCEPTABLE,
            409: _RESP_BATCH_STATE,
            415: RESP_UNREADABLE_BODY,
        },
    ),
)
class RetirementPartnerReportCleanupViewSet(
    StandardizedErrorMixin,
    RestrictedApplicationGuardMixin,
    ReadableErrorDetailMixin,
    ErrorExtensionsMixin,
    StrictQueryParametersMixin,
    _PartnerReportAccessMixin,
    ScopedQuerysetMixin,
    _EveryPartnerReportMixin,
    viewsets.ViewSet,
):
    """
    Disposes of a set of partner report entries that have been reported on.

    The accounts whose entries are to be disposed of are named in the body, and
    every one of them has to name an entry that was taken for reporting: an
    entry nobody has taken has not been reported on yet, so it refuses the
    whole batch and nothing is deleted.
    """

    serializer_class = RetirementBatchOutcomeSerializer

    def get_serializer(self, *args, **kwargs):
        """Build the output serializer; a plain ViewSet has no generic machinery to do it."""
        return self.serializer_class(*args, **kwargs)

    def create(self, request):
        """Delete the partner report entries of the accounts the body names."""
        batch = RetirementBatchSerializer(data=request.data)
        batch.is_valid(raise_exception=True)
        results = delete_partner_reports(self.get_queryset(), batch.validated_data["usernames"])
        return Response(self.get_serializer({"results": results}).data)
