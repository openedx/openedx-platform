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
    Mark the legacy slashless Enrollment v2 addresses ``deprecated: true``.

    Conforming routes always end in a slash, so a slashless /v2/ path is by
    construction a legacy address. Paths are post-SCHEMA_PATH_PREFIX_TRIM.
    """
    for path, path_item in result.get("paths", {}).items():
        if not path.startswith("/v2/") or path.endswith("/"):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
