"""Helper functions for drf-spectacular (LMS schema)."""

import re

# Mounts published in the LMS schema. Anything else is left out.
SCHEMA_PATH_PATTERNS = (
    re.compile(r"^/api/enrollment/v\d+/"),
    re.compile(r"^/api/cohorts/v2/courses/[^/]+/(cohorts|cohort_settings)/"),
)


def lms_api_filter(endpoints):
    """
    Pre-processing hook: keep only the endpoints under the mounts the LMS schema publishes.
    """
    return [
        (path, path_regex, method, callback)
        for path, path_regex, method, callback in endpoints
        if any(pattern.match(path) for pattern in SCHEMA_PATH_PATTERNS)
    ]
