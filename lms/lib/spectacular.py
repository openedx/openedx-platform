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
