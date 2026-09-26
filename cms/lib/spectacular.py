"""Helper functions for drf-spectacular"""

import re

CMS_PATH_PATTERN = re.compile(r"^/api/(contentstore|authoring)/v\d+/")

# Path prefixes of operations that have a conforming successor and are kept only
# for their deprecation window. The mounted prefix is listed next to the
# shortened form, so the hook still matches where a document is published
# without the service prefix.
SUPERSEDED_PATH_PREFIXES = (
    "/api/contentstore/v0/videos/uploads/",
    "/v0/videos/uploads/",
)

_OPERATION_KEYS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def cms_api_filter(endpoints):
    """
    Pre-processing hook: keep the versioned authoring and contentstore
    endpoints, and the course-level endpoints named below.
    """
    filtered = []

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
    Post-processing hook: mark every operation of a superseded path deprecated.
    """
    for path, path_item in (result.get("paths") or {}).items():
        if not path.startswith(SUPERSEDED_PATH_PREFIXES):
            continue
        for key, operation in path_item.items():
            if key in _OPERATION_KEYS and isinstance(operation, dict):
                operation["deprecated"] = True

    return result
