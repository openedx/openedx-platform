"""Helper functions for drf-spectacular (LMS schema)."""

import re
from collections import namedtuple
from functools import cache

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiResponse

LEGACY_V2_PREFIX = "/api/enrollment/v2/"

# The one legacy address that differs from its conforming twin only by the
# trailing slash, so both tokenize to the same operationId. The other legacy
# addresses already have distinct ids and keep them.
COLLIDING_LEGACY_PATH = "/api/enrollment/v2/enrollments"

# Addresses listed here are superseded by a conforming address this schema also
# publishes. Each entry is spelled out rather than matched by prefix, so an
# address whose replacement has not been written yet is never marked.
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
)

# A path parameter leaves no token behind, and the version 1 account collection
# has no trailing slash to tell it from the member address, so the two tokenize
# to one operation id and are told apart only by a numeral handed out in the
# order the addresses sort. The collection answers with a list of accounts, which the
# generator names for a list, while the document that trims nothing off the
# front of each path has always published that address under the name derived
# for a single account -- the name that leaves the member address the numeral it
# carries there -- so that one is pinned. The key carries the id the generator
# derives, so an entry applies only to the document that derives it; a key no
# document derives would never be reached.
#
# The two preference reads tokenize to one id for the same reason. The numeral
# a document hands the second of them says which of the two addresses sorts
# first rather than what the operation reads, so the read of one preference is
# named for what it reads and the read of the whole document keeps the name
# both derive. Of the version 1 pair only the document that trims the area off
# the front of each path is covered: the one that trims nothing already
# publishes that read under the numeral, and a name a client imports by is not
# taken away. Neither document has published either name of the version 2
# pair, so both are covered there, each through the name the document it
# covers derives; that leaves the five operations of that resource carrying the
# same leading tokens in every document, which is what a client generated from
# one takes its module from.
RENAMED_OPERATION_IDS = {
    ("/api/user/v1/accounts", "GET", "api_user_v1_accounts_list"): "api_user_v1_accounts_retrieve",
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
}

PUBLISHED_PATH_PATTERN = re.compile(r"^/api/enrollment/v\d+/")

# The user addresses this schema documents are the ones the version below
# serves, and beside each of those the address it supersedes. Apps older than
# that version are mounted under the same ``/api/user/v2/`` prefix, so matching
# the prefix alone would publish endpoints described nowhere here; the packages
# a view may come from decide instead. One resource of this version lives in
# the app that owns it rather than in the user API's own package.
PUBLISHED_USER_PREFIX = "/api/user/v2/"
PUBLISHED_USER_VIEW_MODULES = (
    "openedx.core.djangoapps.user_api.v2.",
    "openedx.core.djangoapps.profile_images.v2.",
)

# Every user address, whatever its version, belongs to one tag group. A tag is
# the first path segment left once the configured prefix is removed, which in a
# document that trims ``/api/user`` is the version, so user operations would be
# grouped with the enrollment operations that carry the same version. A document
# that trims nothing already derives a tag naming an area, and regrouping its
# operations would move them in every client generated from it.
USER_PATH_PREFIX = "/api/user/"
USER_TAG = "user"

_PATH_PARAMETER = re.compile(r"\{([\w-]+)\}")
_VERSION_TAG = re.compile(r"v\d+")

# The superseded addresses with their parameter names removed. The generator
# renames a ``{pk}`` parameter to ``{id}`` only after this filter has run, so
# the comparison cannot depend on how a parameter is spelled.
_SUPERSEDED_USER_SHAPES = frozenset(_PATH_PARAMETER.sub("{}", path) for path in SUPERSEDED_USER_PATHS)


#: What one superseded operation reads and what it answers with on success.
_SupersededBodies = namedtuple("_SupersededBodies", ("request", "response"))


@cache
def superseded_bodies():
    """
    Return the bodies of each superseded operation, keyed by address and method.

    The views serving those addresses name no serializer, so an address
    published beside the one that replaces it would carry nothing on either
    side of the exchange. Each entry below names the shape the handler reads or
    returns with the serializer describing that same shape at the replacing
    address, which the parity tests of this area hold to the same values; a
    request of ``None`` is an operation that reads no body. An address whose
    view names a serializer of its own is absent from the table and keeps the
    component it has always published.

    A request is keyed by media type, because a shape named on its own is
    published under every type the view's parsers accept: where that is more
    types than the handler reads, a client generated from the document would
    pick one it refuses, and where the handler reads a body no serializer
    describes, such as an image sent as the whole body, the document would
    leave that form out. An address whose body is also read at the address
    replacing it takes the same mapping from there, so the two cannot drift.
    """
    # The serializers read models, which are not loaded when this module is
    # first imported.
    # pylint: disable=import-outside-toplevel
    from openedx.core.djangoapps.profile_images.v2.serializers import IMAGE_UPLOAD_REQUEST
    from openedx.core.djangoapps.user_api.v2.serializers import (
        ACCOUNT_PREFERENCE_CHANGES_SCHEMA,
        ACCOUNT_PREFERENCES_SCHEMA,
        AccountSerializer,
        AccountUpdateSerializer,
        EmailSearchResultSerializer,
        EmailSearchSerializer,
        NameChangeSerializer,
        VerificationStatusSerializer,
    )
    from openedx.core.lib.api.parsers import MergePatchParser

    account = {200: AccountSerializer}
    # One preference is answered and stored as the bare value, which is what
    # the address replacing it carries as a property of an object.
    preference_value = {"type": "string"}
    preferences_changed = {
        204: OpenApiResponse(description="The preferences now stand as the body asked."),
    }
    described = {
        ("/api/user/v1/accounts", "GET"): _SupersededBodies(None, {200: AccountSerializer(many=True)}),
        ("/api/user/v1/accounts/{username}", "GET"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/{username}", "PATCH"): _SupersededBodies(
            {MergePatchParser.media_type: AccountUpdateSerializer}, account,
        ),
        ("/api/user/v1/accounts/search_emails", "POST"): _SupersededBodies(
            EmailSearchSerializer, {200: EmailSearchResultSerializer(many=True)},
        ),
        ("/api/user/v1/accounts/{username}/deactivate/", "POST"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/name_change/", "POST"): _SupersededBodies(
            NameChangeSerializer,
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
            None, {200: VerificationStatusSerializer},
        ),
        ("/api/user/v1/preferences/{username}", "GET"): _SupersededBodies(
            None,
            {
                200: OpenApiResponse(
                    ACCOUNT_PREFERENCES_SCHEMA, description="The preferences this account stores.",
                ),
            },
        ),
        ("/api/user/v1/preferences/{username}", "PATCH"): _SupersededBodies(
            {MergePatchParser.media_type: ACCOUNT_PREFERENCE_CHANGES_SCHEMA}, preferences_changed,
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "GET"): _SupersededBodies(
            None, {200: preference_value},
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "PUT"): _SupersededBodies(
            {"application/json": preference_value}, preferences_changed,
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "DELETE"): _SupersededBodies(
            None, {204: OpenApiResponse(description="The account stores no value under that name.")},
        ),
        # The handler reads the choice from the submitted form rather than from
        # a body of its own, and reads each value as the text a form carries.
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
    }
    # Parameter names are spelled differently at different points of
    # generation, so an address is keyed by its shape alone.
    return {
        (_PATH_PARAMETER.sub("{}", path), method): bodies
        for (path, method), bodies in described.items()
    }


def _is_legacy_path(path):
    """Conforming routes always end in a slash, so a slashless v2 path is a legacy address."""
    return path.startswith(LEGACY_V2_PREFIX) and not path.endswith("/")


def _is_published_user_path(path, callback):
    """A user address is documented when this version serves it, or when this version replaces it."""
    if _PATH_PARAMETER.sub("{}", path) in _SUPERSEDED_USER_SHAPES:
        return True
    if not path.startswith(PUBLISHED_USER_PREFIX):
        return False
    view = getattr(callback, "cls", None)
    return getattr(view, "__module__", "").startswith(PUBLISHED_USER_VIEW_MODULES)


def lms_api_filter(endpoints):
    """
    Pre-processing hook: keep only the endpoints this schema documents.
    """
    filtered = []

    for path, path_regex, method, callback in endpoints:
        if PUBLISHED_PATH_PATTERN.match(path) or _is_published_user_path(path, callback):
            filtered.append((path, path_regex, method, callback))

    return filtered


class LmsAutoSchema(AutoSchema):
    """
    Keep colliding addresses distinct, group each operation under its own area,
    and name the bodies of the addresses this schema publishes as superseded,
    marking a body the operation cannot be called without as one it needs.

    The slashless Enrollment v2 address tokenizes to the id of its conforming
    twin, so the two would be told apart only by a numeral suffix, which says
    which of the two addresses sorts first rather than anything about the
    operation; it is suffixed ``_legacy`` instead, leaving the conforming
    address the clean id, and the address listed above keeps the name it is
    already published under. Every address ``RENAMED_OPERATION_IDS`` does not
    name keeps the id and the tag the generator derives for it, because a
    client generated from a document takes a module from each operationId and
    groups it by tag, so renaming either drops what a consumer already imports.
    """

    def get_operation_id(self):
        """Keep each address that tokenizes to another one's id distinct from it."""
        operation_id = super().get_operation_id()
        renamed = RENAMED_OPERATION_IDS.get((self.path, self.method, operation_id))
        if renamed is not None:
            return renamed
        if self.path == COLLIDING_LEGACY_PATH:
            return f"{operation_id}_legacy"
        return operation_id

    def get_tags(self):
        """Name the area whenever the tag derived for a user address would name a version."""
        tags = super().get_tags()
        if self.path.startswith(USER_PATH_PREFIX) and tags and _VERSION_TAG.fullmatch(tags[0]):
            return [USER_TAG]
        return tags

    def _get_request_body(self, direction="request"):
        """
        Publish a body as one the operation needs when any form it may be sent in is.

        An operation that names the media types it reads loses that mark as
        soon as one of them is the bare body of a file, which carries no field
        that can be called required. The forms are alternatives rather than
        parts of one request, so a body that is required in any of them is a
        body the operation cannot be called without.
        """
        body = super()._get_request_body(direction)
        shapes = self.get_request_serializer()
        if not body or body.get("required") or not isinstance(shapes, dict):
            return body
        if any(self._get_request_for_media_type(shape, direction)[1] for shape in shapes.values()):
            body["required"] = True
        return body

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
        return superseded_bodies().get((_PATH_PARAMETER.sub("{}", self.path), self.method))


def lms_mark_legacy_paths_deprecated(result, generator, request, public):  # pylint: disable=unused-argument
    """Mark every superseded address ``deprecated: true``."""
    for path, path_item in result.get("paths", {}).items():
        if not (_is_legacy_path(path) or path in SUPERSEDED_USER_PATHS):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
