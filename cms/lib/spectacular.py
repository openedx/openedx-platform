"""Helper functions for drf-spectacular"""

import re

from drf_spectacular.openapi import AutoSchema

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
