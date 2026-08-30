"""Helper functions for drf-spectacular (LMS schema)."""

import re


def lms_api_filter(endpoints):
    """
    Pre-processing hook: keep only enrollment v2 endpoints tagged for the SDK.
    """
    filtered = []
    ENROLLMENT_PATH_PATTERN = re.compile(r"^/api/enrollment/v\d+/")

    for path, path_regex, method, callback in endpoints:
        if ENROLLMENT_PATH_PATTERN.match(path):
            filtered.append((path, path_regex, method, callback))

    return filtered


def lms_mark_legacy_paths_deprecated(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook (ADR 0038 / OEP-21): mark the legacy slashless
    Enrollment v2 addresses ``deprecated: true``.

    ADR 0038 rule 6 requires the trailing slash on every conforming route, so
    within the migrated v2 surface a path without one is, by construction, a
    legacy address whose slashed (or renamed) replacement is mounted beside
    it. Scoped to ``/v2/`` so the marking tracks this migration — deprecating
    v1 is its own DEPR decision. Paths appear here after
    SCHEMA_PATH_PREFIX_TRIM strips /api/enrollment.
    """
    for path, path_item in result.get("paths", {}).items():
        if not path.startswith("/v2/") or path.endswith("/"):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
