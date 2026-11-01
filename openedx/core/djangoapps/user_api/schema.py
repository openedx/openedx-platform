"""How a document describes the addresses of this API that a conforming one replaces."""

import re
from collections import namedtuple
from functools import cache
from importlib import import_module

from drf_spectacular.utils import OpenApiResponse

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
    "/api/user/v1/preferences/{pref_key}/users/",
    "/api/user/v1/forum_roles/{name}/users/",
    "/api/user/v1/preferences/time_zones/",
)

# A path parameter leaves no token behind, and the version 1 account collection
# has no trailing slash to tell it from the member address, so the two tokenize
# to one operation id and are told apart only by a numeral handed out in
# registration order. The collection answers with a list of accounts, which the
# generator names for a list, while the document that trims nothing off the
# front of each path has always published that address under the name derived
# for a single account -- the name that leaves the member address the numeral it
# carries there -- so that one is pinned. The key carries the id the generator
# derives, so an entry applies only to the document that derives it; a key no
# document derives would never be reached.
#
# A stored preference is addressed by the pair that identifies it, and the
# comma between the two parameters is left behind as a token of its own, so
# every document derives an id for that read carrying a bare separator where
# the rest of the resource carries a word. That read is named here in each
# document through the id that document derives, which leaves the collection
# read and the member read of the resource under the same leading tokens
# wherever they are published; neither document has published either name, so
# nothing a client imports is taken away.
RENAMED_OPERATION_IDS = {
    ("/api/user/v1/accounts", "GET", "api_user_v1_accounts_list"): "api_user_v1_accounts_retrieve",
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


_PATH_PARAMETER = re.compile(r"\{([\w-]+)\}")

# The superseded addresses with their parameter names removed. The generator
# renames a ``{pk}`` parameter to ``{id}`` only after this filter has run, so
# the comparison cannot depend on how a parameter is spelled.
_SUPERSEDED_USER_SHAPES = frozenset(_PATH_PARAMETER.sub("{}", path) for path in SUPERSEDED_USER_PATHS)


def is_superseded_user_path(path):
    """Say whether this address is one of those a conforming address replaces."""
    return _PATH_PARAMETER.sub("{}", path) in _SUPERSEDED_USER_SHAPES

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
    # first imported, so each module is read inside this one. One of them
    # reaches models belonging to a single service, which code shared by both
    # services may not import: a service picks that application up by name
    # through its settings, and that module is picked up by name here for the
    # same reason.
    # pylint: disable=import-outside-toplevel
    from openedx.core.djangoapps.profile_images.v2.serializers import IMAGE_UPLOAD_REQUEST
    from openedx.core.lib.api.parsers import MergePatchParser

    replacing = import_module("openedx.core.djangoapps.user_api.v2.serializers")

    account = {200: replacing.AccountSerializer}
    described = {
        ("/api/user/v1/accounts", "GET"): _SupersededBodies(None, {200: replacing.AccountSerializer(many=True)}),
        ("/api/user/v1/accounts/{username}", "GET"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/{username}", "PATCH"): _SupersededBodies(
            {MergePatchParser.media_type: replacing.AccountUpdateSerializer}, account,
        ),
        ("/api/user/v1/accounts/search_emails", "POST"): _SupersededBodies(
            replacing.EmailSearchSerializer, {200: replacing.EmailSearchResultSerializer(many=True)},
        ),
        ("/api/user/v1/accounts/{username}/deactivate/", "POST"): _SupersededBodies(None, account),
        ("/api/user/v1/accounts/name_change/", "POST"): _SupersededBodies(
            replacing.NameChangeSerializer,
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
    }
    # Parameter names are spelled differently at different points of
    # generation, so an address is keyed by its shape alone.
    return {
        (_PATH_PARAMETER.sub("{}", path), method): bodies
        for (path, method), bodies in described.items()
    }


class SupersededUserOperations:
    """
    Describe the superseded user addresses wherever a service publishes them.

    Both services mount that API at the same addresses and both publish an
    unauthenticated document of everything they serve, so what one of those
    documents says about one of these addresses is what the other has to say
    about it as well: a client generated from either reads the same body on
    each side of the exchange, branches on the same success code, and imports
    the same name. The behaviour is shared rather than restated in each
    service's schema class, which is what keeps the two documents a deployment
    publishes from describing one address two ways.

    Every address ``RENAMED_OPERATION_IDS`` does not name keeps the id the
    generator derives for it, because a client generated from a document takes
    a module from each operationId, so renaming one drops what a consumer
    already imports.
    """

    def get_operation_id(self):
        """Keep the name an address is published under where describing its bodies would move it."""
        operation_id = super().get_operation_id()
        return RENAMED_OPERATION_IDS.get((self.path, self.method, operation_id), operation_id)

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
        if not body:
            return body
        shapes = self.get_request_serializer()
        if body.get("required") or not isinstance(shapes, dict):
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


def mark_superseded_user_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """Post-processing hook: mark every address a conforming one replaces ``deprecated: true``."""
    for path, path_item in result.get("paths", {}).items():
        if path not in SUPERSEDED_USER_PATHS:
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
