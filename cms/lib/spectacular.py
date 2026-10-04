"""Helper functions for drf-spectacular"""

import re

# Path prefixes of operations superseded by a newer version, kept for their
# deprecation window.
SUPERSEDED_PATH_PREFIXES = (
    # → /api/authoring/v2/courses/{course_key}/video_usages/{edx_video_id}/
    "/api/contentstore/v1/videos/{course_id}/{edx_video_id}/usage",
    # → /api/authoring/v2/courses/{course_key}/video_archives/
    "/api/contentstore/v1/videos/{course_id}/download",
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
    Post-processing hook: mark the operations that have a conforming successor
    ``deprecated: true``.
    """
    for path, path_item in result.get("paths", {}).items():
        if not path.startswith(SUPERSEDED_PATH_PREFIXES):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
