"""Helper functions for drf-spectacular"""

import re

from drf_spectacular.openapi import AutoSchema

# Legacy operations superseded by a conforming address under /api/authoring/.
# The YouTube transcript routes make their final character optional, so both
# addresses resolve and each is listed in the truncated spelling as well as the
# full one: which one the generator emits depends on how it renders that regex.
SUPERSEDED_PATHS = (
    "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
    "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
    "/api/contentstore/v0/grading/{course_id}",         # → /api/authoring/v3/courses/{course_key}/grading/
    "/api/contentstore/v1/course_grading/{course_id}",  # → /api/authoring/v3/courses/{course_key}/grading/
)

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

# Response components whose alternative shapes overlap, published as anyOf
# instead of drf-spectacular's oneOf. The default grading shape allows extra
# keys, so a full read matches both shapes and a strict oneOf validator would
# reject it.
ANY_OF_COMPONENTS = {
    "authoring_grading.CourseGradingRead.v3": (
        "authoring_grading.CourseGradingFull.v3",
        "authoring_grading.CourseGrading.v3",
    ),
}


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


def cms_mark_superseded_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: mark the operations of superseded paths deprecated.
    """
    for path in SUPERSEDED_PATHS:
        for operation in result.get("paths", {}).get(path, {}).values():
            if isinstance(operation, dict):
                operation["deprecated"] = True
    return result


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


def _publish_overlapping_shapes_as_any_of(result):
    """Turn the oneOf of each component in ANY_OF_COMPONENTS into anyOf, if it lists exactly the named shapes."""
    schemas = result.get("components", {}).get("schemas", {})
    for name, shapes in ANY_OF_COMPONENTS.items():
        component = schemas.get(name)
        if not component or "oneOf" not in component:
            continue
        refs = [item.get("$ref") for item in component["oneOf"]]
        if refs == [f"#/components/schemas/{shape}" for shape in shapes]:
            component["anyOf"] = component.pop("oneOf")


def cms_mark_migrated_paths(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: mark the legacy addresses of
    migrated APIs ``deprecated: true`` and BFF surfaces ``x-internal``,
    and publish the components in ANY_OF_COMPONENTS as anyOf.
    """
    _publish_overlapping_shapes_as_any_of(result)
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
