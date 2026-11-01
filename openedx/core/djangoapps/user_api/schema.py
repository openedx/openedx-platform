"""How a document describes the addresses of this API that a conforming one replaces."""

import re
from collections import namedtuple
from functools import cache

from drf_spectacular.utils import OpenApiResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.settings import api_settings

# Addresses superseded by a conforming address this schema also publishes, spelled
# out rather than matched by prefix so an address with no replacement is never marked.
SUPERSEDED_USER_PATHS = (
    "/api/user/v1/me",
    "/api/user/v1/accounts",
    "/api/user/v1/accounts/search_emails",
    "/api/user/v1/accounts/{username}",
    "/api/user/v1/accounts/{username}/deactivate/",
    "/api/user/v1/accounts/{username}/image",
    "/api/user/v1/accounts/{username}/verification_status/",
    "/api/user/v1/accounts/{username}/verifications/",
    "/api/user/v1/accounts/name_change/",
    "/api/user/v1/accounts/name_change/{username}/confirm/",
    "/api/user/v1/users/",
    "/api/user/v1/users/{id}/",
    "/api/user/v1/user_prefs/",
    "/api/user/v1/user_prefs/{id}/",
    "/api/user/v1/preferences/{username}",
    "/api/user/v1/preferences/{username}/{preference_key}",
    "/api/user/v1/preferences/email_opt_in/",
    "/api/user/v1/preferences/{pref_key}/users/",
    "/api/user/v1/forum_roles/{name}/users/",
    "/api/user/v1/preferences/time_zones/",
    "/api/user/v1/accounts/{username}/retirement_status/",
    "/api/user/v1/accounts/retirement_queue/",
    "/api/user/v1/accounts/retirements_by_status_and_date/",
    "/api/user/v1/accounts/update_retirement_status/",
    "/api/user/v1/accounts/retirement_cleanup/",
    "/api/user/v1/accounts/retirement_partner_report/",
    "/api/user/v1/accounts/retirement_partner_report_cleanup/",
    "/api/user/v1/accounts/cancel_retirement/",
    "/api/user/v1/accounts/retire/",
    "/api/user/v1/accounts/retire_misc/",
    "/api/user/v1/account/registration/",
    "/api/user/v2/account/registration/",
    "/api/user/v1/account/password_reset/",
    "/api/user/v1/account/password_reset/token/validate/",
    "/api/user/v1/validation/registration",
    "/api/user/{api_version}/account/login_session/",
)

# Ids pinned over the one a document derives, keyed by (path, method, derived id): the
# v1 account collection keeps the id it is already published under, and the preference
# member read drops the bare "," token its two path parameters leave. The read of one
# account preference is named for what it reads rather than for the numeral its sort
# position earns it, in each document that has not already published that numeral.
# The two v1 retirement queue reads keep the id they are already published under.
RENAMED_OPERATION_IDS = {
    ("/api/user/v1/accounts", "GET", "api_user_v1_accounts_list"): "api_user_v1_accounts_retrieve",
    (
        "/api/user/v1/accounts/retirement_queue/",
        "GET",
        "api_user_v1_accounts_retirement_queue_list",
    ): "api_user_v1_accounts_retirement_queue_retrieve",
    (
        "/api/user/v1/accounts/retirements_by_status_and_date/",
        "GET",
        "api_user_v1_accounts_retirements_by_status_and_date_list",
    ): "api_user_v1_accounts_retirements_by_status_and_date_retrieve",
    ("/api/user/v1/preferences/{username}/{preference_key}", "GET", "v1_preferences_retrieve"): (
        "v1_preferences_retrieve_one"
    ),
    (
        "/api/user/v2/accounts/{username}/preferences/{key}/",
        "GET",
        "v2_accounts_preferences_retrieve",
    ): "v2_accounts_preferences_retrieve_one",
    (
        "/api/user/v2/accounts/{username}/preferences/{key}/",
        "GET",
        "api_user_v2_accounts_preferences_retrieve",
    ): "api_user_v2_accounts_preferences_retrieve_one",
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "GET",
        "v2_user_preferences_,_retrieve",
    ): "v2_user_preferences_retrieve",
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "GET",
        "api_user_v2_user_preferences_,_retrieve",
    ): "api_user_v2_user_preferences_retrieve",
}

# Operations whose handler answers a request carrying no body with a 400. The
# body of each is described in place rather than through a serializer, and a
# shape given in place carries no field the generator can read requiredness
# from, so it publishes no mark at all and a client generated from the document
# makes the body an argument the caller may leave out. Each address is spelled
# with its parameter names removed, as the table of bodies below is.
#
# An address belongs here only once its handler has been sent a request with
# the form named and no body behind it and has refused it. The write of one
# stored preference is deliberately absent: that handler takes a request with
# no body and answers that the preference now stands as the request asked.
REQUIRED_REQUEST_BODIES = frozenset({
    ("/api/user/v1/preferences/{}", "PATCH"),
    ("/api/user/v1/preferences/email_opt_in/", "POST"),
    ("/api/user/v2/accounts/{}/preferences/", "PATCH"),
})


_PATH_PARAMETER = re.compile(r"\{([\w-]+)\}")

# Compared without parameter names: the generator renames ``{pk}`` to ``{id}`` after filtering.
_SUPERSEDED_USER_SHAPES = frozenset(_PATH_PARAMETER.sub("{}", path) for path in SUPERSEDED_USER_PATHS)


def is_superseded_user_path(path):
    """Say whether this address is one of those a conforming address replaces."""
    return _PATH_PARAMETER.sub("{}", path) in _SUPERSEDED_USER_SHAPES

#: What one superseded operation reads and what it answers with on success.
_SupersededBodies = namedtuple("_SupersededBodies", ("request", "response"))


@cache
def superseded_bodies():
    """
    Return the request and success bodies of each superseded operation, keyed by address and method.

    The v1 views name no serializer. A response is described by the v2 serializer
    of the same shape; a request by the members the v1 handler reads, without the
    v2 bounds, under the media types that handler reads; ``None`` reads no body.
    Where a v1 view names a serializer describing neither, the shapes its handler
    exchanges are named here instead.
    """
    # Imported here because the serializers need loaded models.
    # pylint: disable=import-outside-toplevel
    from openedx.core.djangoapps.profile_images.v2.serializers import IMAGE_UPLOAD_REQUEST
    from openedx.core.djangoapps.user_api.accounts import serializers as retirement
    from openedx.core.djangoapps.user_api.v2 import authn_schemas
    from openedx.core.djangoapps.user_api.v2 import serializers as replacing
    from openedx.core.lib.api.parsers import MergePatchParser

    account = {200: replacing.AccountSerializer}
    account_change = {
        "type": "object",
        "properties": {name: {} for name in replacing.AccountUpdateSerializer().fields},
    }
    email_search = {
        "type": "object",
        "properties": {"emails": {"type": "array", "items": {}}},
        "required": ["emails"],
    }
    name_change = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    json_type = "application/json"

    def in_every_form(shape):
        """Name a shape under each media type a view that declares no parsers reads a body in."""
        return {parser.media_type: shape for parser in api_settings.DEFAULT_PARSER_CLASSES}

    retirement_erased = {
        204: OpenApiResponse(description="The data this step erases is gone."),
    }
    #: The name of one account, which is how the retirement pipeline's steps
    #: say which account they are acting on.
    named_account = {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "Name the account being retired was registered under.",
            },
        },
        "required": ["username"],
    }
    #: The names of several accounts, which is how a step acting on a batch of
    #: retirement records says which ones.
    named_accounts = {
        "type": "object",
        "properties": {
            "usernames": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names the accounts were registered under.",
            },
        },
        "required": ["usernames"],
    }
    # One preference is answered as the bare value it is stored as. The writes
    # store the text of a value of any type, so their values carry no type.
    preference_value = {"type": "string"}
    preference_changes = {
        "type": "object",
        "minProperties": 1,
        "additionalProperties": {},
        "description": (
            "Preferences to change, as an object of preference name to the value to store under it. "
            "A value that is not text is stored as its text, and a null removes the preference. Names "
            "the body leaves out are left as they are. At least one name must be given."
        ),
    }
    preference_change = {
        "description": "Value to store under the name. A value that is not text is stored as its text.",
    }
    preferences_changed = {
        204: OpenApiResponse(description="The preferences now stand as the body asked."),
    }
    described = {
        ("/api/user/v1/accounts", "GET"): _SupersededBodies(None, {200: replacing.AccountSerializer(many=True)}),
        ("/api/user/v1/accounts/{username}", "GET"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/{username}", "PATCH"): _SupersededBodies(
            {MergePatchParser.media_type: account_change}, account,
        ),
        ("/api/user/v1/accounts/search_emails", "POST"): _SupersededBodies(
            {JSONParser.media_type: email_search, MergePatchParser.media_type: email_search},
            {200: replacing.EmailSearchResultSerializer(many=True)},
        ),
        ("/api/user/v1/accounts/{username}/deactivate/", "POST"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/name_change/", "POST"): _SupersededBodies(
            dict.fromkeys(
                (JSONParser.media_type, FormParser.media_type, MultiPartParser.media_type), name_change,
            ),
            {201: OpenApiResponse(description="The change was recorded and waits to be confirmed.")},
        ),
        ("/api/user/v1/accounts/name_change/{username}/confirm/", "POST"): _SupersededBodies(
            None, {204: OpenApiResponse(description="The profile carries the name that was asked for.")},
        ),
        ("/api/user/v1/accounts/{username}/image", "POST"): _SupersededBodies(
            IMAGE_UPLOAD_REQUEST,
            {204: OpenApiResponse(description="The account now carries this image.")},
        ),
        ("/api/user/v1/accounts/{username}/image", "DELETE"): _SupersededBodies(
            None, {204: OpenApiResponse(description="The account carries no image of its own.")},
        ),
        ("/api/user/v1/accounts/{username}/verification_status/", "GET"): _SupersededBodies(
            None, {200: replacing.VerificationStatusSerializer},
        ),
        ("/api/user/v1/preferences/{username}", "GET"): _SupersededBodies(
            None,
            {
                200: OpenApiResponse(
                    replacing.ACCOUNT_PREFERENCES_SCHEMA, description="The preferences this account stores.",
                ),
            },
        ),
        ("/api/user/v1/preferences/{username}", "PATCH"): _SupersededBodies(
            {MergePatchParser.media_type: preference_changes}, preferences_changed,
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "GET"): _SupersededBodies(
            None, {200: preference_value},
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "PUT"): _SupersededBodies(
            {"application/json": preference_change}, preferences_changed,
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "DELETE"): _SupersededBodies(
            None, {204: OpenApiResponse(description="The account stores no value under that name.")},
        ),
        # The handler reads the choice from the submitted form rather than from
        # a body of its own, and reads each value as the text a form carries.
        # The queue and the by-date list both answer with an array of the
        # shape their view names, which is published for one of them.
        ("/api/user/v1/accounts/retirement_queue/", "GET"): _SupersededBodies(
            None, {200: retirement.UserRetirementStatusSerializer(many=True)},
        ),
        ("/api/user/v1/accounts/retirements_by_status_and_date/", "GET"): _SupersededBodies(
            None, {200: retirement.UserRetirementStatusSerializer(many=True)},
        ),
        # The move reads the name, step and log entry, refuses anything else, and
        # answers with nothing.
        ("/api/user/v1/accounts/update_retirement_status/", "PATCH"): _SupersededBodies(
            {
                json_type: {
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "Name the account being retired was registered under.",
                        },
                        "new_state": {
                            "type": "string",
                            "description": "Name of the pipeline step to move this retirement to.",
                        },
                        "response": {
                            "type": "string",
                            "description": "What happened at the step being left, appended to the log.",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Move the retirement even where the step asked for is not a later one.",
                        },
                    },
                    "required": ["username", "new_state", "response"],
                },
            },
            {204: OpenApiResponse(description="The retirement now stands at the step asked for.")},
        ),
        ("/api/user/v1/accounts/retirement_cleanup/", "POST"): _SupersededBodies(
            {json_type: named_accounts},
            {204: OpenApiResponse(description="The retirement records named are gone.")},
        ),
        # The claim reads no body and answers with the entries it took.
        ("/api/user/v1/accounts/retirement_partner_report/", "POST"): _SupersededBodies(
            None, {200: retirement.UserRetirementPartnerReportSerializer(many=True)},
        ),
        ("/api/user/v1/accounts/retirement_partner_report/", "PUT"): _SupersededBodies(
            {json_type: named_account},
            {
                204: OpenApiResponse(
                    description=(
                        "The account is queued for reporting to the organizations it studied "
                        "with, or was enrolled with none and so needs no report."
                    ),
                ),
            },
        ),
        # This batch is named by a bare array rather than by an object.
        ("/api/user/v1/accounts/retirement_partner_report_cleanup/", "POST"): _SupersededBodies(
            {
                json_type: {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "original_username": {
                                "type": "string",
                                "description": "Name the account was registered under.",
                            },
                        },
                        "required": ["original_username"],
                    },
                },
            },
            {204: OpenApiResponse(description="The partner report entries named are gone.")},
        ),
        # The cancellation's view declares no parsers, so every platform media type
        # is read.
        ("/api/user/v1/accounts/cancel_retirement/", "POST"): _SupersededBodies(
            in_every_form({
                "type": "object",
                "properties": {
                    "retirement_id": {
                        "type": "integer",
                        "description": "Identifier of the retirement to cancel.",
                    },
                },
                "required": ["retirement_id"],
            }),
            {
                200: {
                    "type": "object",
                    "properties": {
                        "success": {
                            "type": "boolean",
                            "description": "True once the retirement request has been undone.",
                        },
                    },
                    "required": ["success"],
                },
            },
        ),
        ("/api/user/v1/accounts/retire/", "POST"): _SupersededBodies(
            {json_type: named_account}, retirement_erased,
        ),
        ("/api/user/v1/accounts/retire_misc/", "POST"): _SupersededBodies(
            {json_type: named_account}, retirement_erased,
        ),
        ("/api/user/v1/preferences/email_opt_in/", "POST"): _SupersededBodies(
            {
                "application/x-www-form-urlencoded": {
                    "type": "object",
                    "properties": {
                        "course_id": {
                            "type": "string",
                            "description": (
                                "Course the organization runs, which is how the organization the "
                                "choice applies to is identified."
                            ),
                        },
                        "email_opt_in": {
                            "type": "string",
                            "description": (
                                "`True` if this user wants messages from that organization. Any "
                                "other value is read as not wanting them."
                            ),
                        },
                    },
                    "required": ["course_id", "email_opt_in"],
                },
            },
            {200: OpenApiResponse(description="The choice was recorded.")},
        ),
        # Registering, signing in and resetting a password are described with
        # the bodies of the addresses replacing them. Signing in by posting has
        # no entry: its handler is described where it is defined, which is
        # published in place of anything named here.
        ("/api/user/v1/account/registration/", "GET"): _SupersededBodies(
            None, authn_schemas.REGISTRATION_FORM_RESPONSES,
        ),
        ("/api/user/v1/account/registration/", "POST"): _SupersededBodies(
            authn_schemas.REGISTRATION_REQUEST, authn_schemas.REGISTRATION_RESPONSES,
        ),
        ("/api/user/v2/account/registration/", "GET"): _SupersededBodies(
            None, authn_schemas.REGISTRATION_FORM_RESPONSES,
        ),
        ("/api/user/v2/account/registration/", "POST"): _SupersededBodies(
            authn_schemas.REGISTRATION_REQUEST, authn_schemas.REGISTRATION_RESPONSES,
        ),
        ("/api/user/v1/account/password_reset/", "GET"): _SupersededBodies(
            None, authn_schemas.PASSWORD_RESET_FORM_RESPONSES,
        ),
        ("/api/user/v1/account/password_reset/token/validate/", "POST"): _SupersededBodies(
            authn_schemas.TOKEN_VALIDATION_REQUEST, authn_schemas.TOKEN_VALIDATION_RESPONSES,
        ),
        ("/api/user/v1/validation/registration", "POST"): _SupersededBodies(
            authn_schemas.REGISTRATION_VALIDATION_REQUEST, authn_schemas.REGISTRATION_VALIDATION_RESPONSES,
        ),
        ("/api/user/{api_version}/account/login_session/", "GET"): _SupersededBodies(
            None, authn_schemas.LOGIN_FORM_RESPONSES,
        ),
    }
    # Parameter names are spelled differently at different points of
    # generation, so an address is keyed by its shape alone.
    return {
        (_PATH_PARAMETER.sub("{}", path), method): bodies
        for (path, method), bodies in described.items()
    }


def _names_what_it_needs(shape):
    """
    Say whether a body written out as a shape names something it cannot be sent without.

    A shape is published as it is written rather than built from a
    serializer's fields, so nothing has judged whether the operation can be
    called with no body at all. One that requires a member, or that is a list
    whose members require one, cannot be satisfied by an absent body.
    """
    if not isinstance(shape, dict):
        return False
    if shape.get("required"):
        return True
    return _names_what_it_needs(shape.get("items"))


class SupersededUserOperations:
    """
    Describe each superseded user address by what it reads and answers with, under the name it keeps.

    Ids not named in ``RENAMED_OPERATION_IDS`` stay as the generator derives them.
    """

    def get_operation_id(self):
        """Keep the name an address is published under where describing its bodies would move it."""
        operation_id = super().get_operation_id()
        return RENAMED_OPERATION_IDS.get((self.path, self.method, operation_id), operation_id)

    def _get_request_body(self, direction="request"):
        """
        Mark the body required when any of its media types requires it.

        Overrides a private method of drf-spectacular 0.30, which requires a body
        only when every media type does and never requires an inline shape. An
        operation named in ``REQUIRED_REQUEST_BODIES`` is marked required outright.
        An inline shape is required when it, or the items of a list it describes, name a required member.
        """
        body = super()._get_request_body(direction)
        if not body:
            return body
        if self._operation_shape() in REQUIRED_REQUEST_BODIES:
            body["required"] = True
            return body
        shapes = self.get_request_serializer()
        if body.get("required") or not isinstance(shapes, dict):
            return body
        if any(self._requires_a_body(shape, direction) for shape in shapes.values()):
            body["required"] = True
        return body

    def _requires_a_body(self, shape, direction):
        """Say whether this form of the body names a member the request must carry."""
        return _names_what_it_needs(shape) or self._get_request_for_media_type(shape, direction)[1]

    def get_request_serializer(self):
        """Return the body this operation reads, named here for the addresses this one supersedes."""
        bodies = self._superseded_bodies()
        return super().get_request_serializer() if bodies is None else bodies.request

    def get_response_serializers(self):
        """Return the body this operation answers with, named here for the addresses this one supersedes."""
        bodies = self._superseded_bodies()
        return super().get_response_serializers() if bodies is None else bodies.response

    def _superseded_bodies(self):
        """Return the bodies named for this operation, or ``None`` where none are named."""
        return superseded_bodies().get(self._operation_shape())

    def _operation_shape(self):
        """This operation as the tables above key one: its address without parameter names, and its method."""
        return (_PATH_PARAMETER.sub("{}", self.path), self.method)


def mark_superseded_user_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """Post-processing hook: mark every address a conforming one replaces ``deprecated: true``."""
    for path, path_item in result.get("paths", {}).items():
        if path not in SUPERSEDED_USER_PATHS:
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
