"""Helper functions for drf-spectacular (LMS schema)."""

import re

from drf_spectacular.openapi import AutoSchema

LEGACY_V2_PREFIX = "/api/enrollment/v2/"

# The one legacy address that differs from its conforming twin only by the
# trailing slash, so both tokenize to the same operationId. The other legacy
# addresses already have distinct ids and keep them.
COLLIDING_LEGACY_PATH = "/api/enrollment/v2/enrollments"

# Addresses listed here are superseded by a conforming address this schema also
# publishes. Each entry is spelled out rather than matched by prefix, so an
# address whose replacement has not been written yet is never marked.
SUPERSEDED_USER_PATHS = (
    "/api/user/v1/users/",
    "/api/user/v1/users/{id}/",
    "/api/user/v1/user_prefs/",
    "/api/user/v1/user_prefs/{id}/",
    "/api/user/v1/preferences/{pref_key}/users/",
    "/api/user/v1/forum_roles/{name}/users/",
    "/api/user/v1/preferences/time_zones/",
)

PUBLISHED_PATH_PATTERN = re.compile(r"^/api/enrollment/v\d+/")

# The user addresses this schema documents are the ones the version below
# serves, and beside each of those the address it supersedes. Apps older than
# that version are mounted under the same ``/api/user/v2/`` prefix, so matching
# the prefix alone would publish endpoints described nowhere here; the package
# a view comes from decides instead.
PUBLISHED_USER_PREFIX = "/api/user/v2/"
PUBLISHED_USER_VIEW_MODULE = "openedx.core.djangoapps.user_api.v2."

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
    return getattr(view, "__module__", "").startswith(PUBLISHED_USER_VIEW_MODULE)


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
    Keep the one colliding address distinct, and group each operation under its own area.

    The slashless Enrollment v2 address tokenizes to the id of its conforming
    twin, so the two would be told apart only by a numeral suffix handed out
    in registration order, which says nothing about the operation and moves
    when the routes move; it is suffixed ``_legacy`` instead, leaving the
    conforming address the clean id. Every other address keeps the id and the
    tag the generator derives for it, because a client generated from a
    document takes a module from each operationId and groups it by tag, so
    renaming either drops what a consumer already imports.
    """

    def get_operation_id(self):
        """Suffix the one colliding address so the pair never shares an operationId."""
        operation_id = super().get_operation_id()
        if self.path == COLLIDING_LEGACY_PATH:
            return f"{operation_id}_legacy"
        return operation_id

    def get_tags(self):
        """Name the area whenever the tag derived for a user address would name a version."""
        tags = super().get_tags()
        if self.path.startswith(USER_PATH_PREFIX) and tags and _VERSION_TAG.fullmatch(tags[0]):
            return [USER_TAG]
        return tags


def lms_mark_legacy_paths_deprecated(result, generator, request, public):  # pylint: disable=unused-argument
    """Mark every superseded address ``deprecated: true``."""
    for path, path_item in result.get("paths", {}).items():
        if not (_is_legacy_path(path) or path in SUPERSEDED_USER_PATHS):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
