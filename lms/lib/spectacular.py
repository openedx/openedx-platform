"""Helper functions for drf-spectacular (LMS schema)."""

import re

from drf_spectacular.openapi import AutoSchema

LEGACY_V2_PREFIX = "/api/enrollment/v2/"


def _is_legacy_path(path):
    """Conforming routes always end in a slash, so a slashless v2 path is a legacy address."""
    return path.startswith(LEGACY_V2_PREFIX) and not path.endswith("/")


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


class LmsAutoSchema(AutoSchema):
    """
    Give the legacy slashless Enrollment v2 addresses a distinct operationId.

    A legacy mount and its conforming mount serve the same view and tokenize to
    the same operationId, so drf-spectacular would otherwise break the tie with
    a numeral suffix in registration order. The conforming address keeps the
    clean id; the deprecated one is suffixed ``_legacy``.
    """

    def get_operation_id(self):
        """Suffix the legacy address so the pair never shares an operationId."""
        operation_id = super().get_operation_id()
        if _is_legacy_path(self.path):
            return f"{operation_id}_legacy"
        return operation_id


def lms_mark_legacy_paths_deprecated(result, generator, request, public):  # pylint: disable=unused-argument
    """Mark the legacy slashless Enrollment v2 addresses ``deprecated: true``."""
    for path, path_item in result.get("paths", {}).items():
        if not _is_legacy_path(path):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result
