"""Helper functions for drf-spectacular"""

import re

# Legacy operations superseded by a conforming address under /api/authoring/.
# Each is listed in the truncated spelling as well as the full one: the routes
# make the final character optional, so both addresses resolve, and which one
# the generator emits depends on how it renders that regex.
SUPERSEDED_PATHS = (
    "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
)


def cms_api_filter(endpoints):
    """
    Pre-processing hook: keep only contentstore versioned endpoints and select
    course-level endpoints.
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
