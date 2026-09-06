"""Helper functions for drf-spectacular"""

import re

# Legacy schema paths of the APIs migrated to their ADR 0038-conforming
# /api/authoring/ addresses. The legacy routes stay live for their OEP-21
# deprecation window and are marked ``deprecated: true`` in the schema so
# generated clients steer to the conforming address. Paths are as they appear
# in the schema, i.e. after SCHEMA_PATH_PREFIX_TRIM strips /api/contentstore.
LEGACY_MIGRATED_PATH_PREFIXES = (
    "/v1/xblock/",             # → /api/authoring/v1/xblocks/
    "/v3/home/",               # → /api/authoring/v3/home/
    "/v3/course_details/",     # → /api/authoring/v3/courses/{course_key}/details/
    "/v3/authoring_grading/",  # → /api/authoring/v3/courses/{course_key}/grading/
    "/v4/home/courses/",       # → /api/authoring/v4/courses/
)

# BFF surfaces (ADR 0038): kept under /api/ with one canonical conforming
# mount, but marked ``x-internal`` so clients can tell them apart from a
# stable resource contract. Applies to both the legacy and conforming mounts.
INTERNAL_BFF_PATH_PREFIXES = (
    "/v3/home/",
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


def cms_mark_migrated_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook (ADR 0038 / OEP-21): mark the legacy addresses of
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
