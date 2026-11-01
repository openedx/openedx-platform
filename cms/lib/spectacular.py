"""Helper functions for drf-spectacular"""

import re

from drf_spectacular.openapi import AutoSchema

# Legacy operations superseded by a conforming address. Each contentstore one
# is listed in the truncated spelling as well as the full one: the routes
# make the final character optional, so both addresses resolve, and which one
# the generator emits depends on how it renders that regex.
SUPERSEDED_PATHS = (
    "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
    # The user API this service mounts as the LMS does; the LMS serves the replacements.
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

# Legacy addresses of APIs migrated to /api/authoring/, marked deprecated for
# their deprecation window.
LEGACY_MIGRATED_PATH_PREFIXES = (
    "/api/contentstore/v1/xblock/",             # → /api/authoring/v1/xblocks/
    "/api/contentstore/v3/home/",               # → /api/authoring/v3/home/
    "/api/contentstore/v3/course_details/",     # → /api/authoring/v3/courses/{course_key}/details/
    "/api/contentstore/v3/authoring_grading/",  # → /api/authoring/v3/courses/{course_key}/grading/
    "/api/contentstore/v4/home/courses/",       # → /api/authoring/v4/courses/
)

# Legacy addresses whose path differs from their conforming twin only in the
# stripped SCHEMA_PATH_PREFIX, so both tokenize to the same operationId. The
# other legacy addresses already have distinct ids and keep them.
COLLIDING_LEGACY_PATH_PREFIXES = (
    "/api/contentstore/v3/home/",
)

# BFF surfaces, marked x-internal so clients can tell them from a stable
# resource contract. Both the legacy and conforming mounts.
INTERNAL_BFF_PATH_PREFIXES = (
    "/api/contentstore/v3/home/",
    "/api/authoring/v3/home/",
)


def cms_api_filter(endpoints):
    """
    Pre-processing hook: keep only contentstore + authoring versioned
    endpoints and select course-level endpoints.
    """
    filtered = []
    CMS_PATH_PATTERN = re.compile(r"^/api/(contentstore|authoring)/v\d+/")

    for path, path_regex, method, callback in endpoints:
        if (
            CMS_PATH_PATTERN.match(path)
            or (
                path.startswith("/api/courses/")
                and "bulk_enable_disable_discussions" in path
            )
        ):
            filtered.append((path, path_regex, method, callback))

    return filtered


def cms_mark_superseded_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: mark the operations of superseded paths deprecated.
    """
    for path in SUPERSEDED_PATHS:
        for operation in result.get("paths", {}).get(path, {}).values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result


class CmsAutoSchema(AutoSchema):
    """
    Give a colliding legacy address a distinct operationId.

    Where a legacy mount and its conforming mount tokenize to the same
    operationId, drf-spectacular would otherwise break the tie with a numeral
    suffix in registration order. The conforming address keeps the clean id;
    the deprecated one is suffixed ``_legacy``.
    """

    def get_operation_id(self):
        """Suffix the colliding legacy address so the pair never shares an operationId."""
        operation_id = super().get_operation_id()
        if self.path.startswith(COLLIDING_LEGACY_PATH_PREFIXES):
            return f"{operation_id}_legacy"
        return operation_id


def cms_mark_migrated_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: mark the legacy addresses of
    migrated APIs ``deprecated: true`` and BFF surfaces ``x-internal``.
    """
    for path, path_item in result.get("paths", {}).items():
        legacy = path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        internal = path.startswith(INTERNAL_BFF_PATH_PREFIXES)
        if not (legacy or internal):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            if legacy:
                operation["deprecated"] = True
            if internal:
                operation["x-internal"] = True
    return result
