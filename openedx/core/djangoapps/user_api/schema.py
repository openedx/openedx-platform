"""How a document describes the addresses of this API that a conforming one replaces."""

import re
from collections import namedtuple
from functools import cache
from importlib import import_module

from drf_spectacular.utils import OpenApiResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

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
    "/api/user/v1/preferences/{pref_key}/users/",
    "/api/user/v1/forum_roles/{name}/users/",
    "/api/user/v1/preferences/time_zones/",
)

# Ids pinned over the one a document derives, keyed by (path, method, derived id): the
# v1 account collection keeps the id it is already published under, and the preference
# member read drops the bare "," token its two path parameters leave.
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
    """
    # Imported here because the serializers need loaded models; the v2 user
    # serializers reach single-service models, so they are imported by name.
    # pylint: disable=import-outside-toplevel
    from openedx.core.djangoapps.profile_images.v2.serializers import IMAGE_UPLOAD_REQUEST
    from openedx.core.lib.api.parsers import MergePatchParser

    replacing = import_module("openedx.core.djangoapps.user_api.v2.serializers")

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
    }
    # Parameter names are spelled differently at different points of
    # generation, so an address is keyed by its shape alone.
    return {
        (_PATH_PARAMETER.sub("{}", path), method): bodies
        for (path, method), bodies in described.items()
    }


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
        only when every media type does and never requires an inline shape.
        """
        body = super()._get_request_body(direction)
        if not body:
            return body
        shapes = self.get_request_serializer()
        if body.get("required") or not isinstance(shapes, dict):
            return body
        if any(self._requires_a_body(shape, direction) for shape in shapes.values()):
            body["required"] = True
        return body

    def _requires_a_body(self, shape, direction):
        """Say whether this form of the body names a member the request must carry."""
        if isinstance(shape, dict):
            return bool(shape.get("required"))
        return self._get_request_for_media_type(shape, direction)[1]

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
