"""Tests for the CMS drf-spectacular hooks and schema class."""

from importlib import import_module
from pathlib import Path
from unittest import TestCase, mock

from django.conf import settings
from django.test import SimpleTestCase
from django.test import TestCase as DjangoTestCase
from django.urls import include
from django.urls import path as url_path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings

from cms.lib.spectacular import (
    ANY_OF_COMPONENTS,
    SUPERSEDED_PATHS,
    CmsAutoSchema,
    cms_api_filter,
    cms_mark_migrated_paths,
    cms_mark_superseded_paths,
)


def _endpoint(path):
    """Return an endpoint tuple shaped as the pre-processing hook receives it."""
    return (path, path, "GET", object())


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class CmsApiFilterTest(TestCase):
    """Which mounts reach the generated schema."""

    @staticmethod
    def _admitted(path):
        """Return whether the pre-processing hook keeps path."""
        return bool(cms_api_filter([_endpoint(path)]))

    def test_the_authoring_mount_is_admitted(self):
        assert self._admitted("/api/authoring/v1/courses/course-v1:a+b+c/youtube_transcript_checks/")

    def test_the_contentstore_mount_is_still_admitted(self):
        assert self._admitted("/api/contentstore/v0/youtube_transcripts/course-v1:a+b+c/check")
        assert self._admitted("/api/contentstore/v4/home/courses/")

    def test_the_hand_listed_discussions_path_is_still_admitted(self):
        assert self._admitted("/api/courses/course-v1:a+b+c/bulk_enable_disable_discussions")

    def test_an_unrelated_mount_is_refused(self):
        assert not self._admitted("/api/user/v1/accounts/")
        assert not self._admitted("/api/authoring/courses/")
        assert not self._admitted("/authoring/v1/courses/")

    def test_the_filter_preserves_the_endpoint_tuples_it_keeps(self):
        kept = _endpoint("/api/authoring/v1/courses/")
        dropped = _endpoint("/api/user/v1/accounts/")
        assert cms_api_filter([kept, dropped]) == [kept]


class CmsMarkSupersededPathsTest(TestCase):
    """Which operations the post-processing hook marks deprecated."""

    def _run(self, schema):
        return cms_mark_superseded_paths(schema, generator=None, request=None, public=True)

    def test_every_operation_of_a_superseded_path_is_marked(self):
        schema = {"paths": {path: {"get": {}} for path in SUPERSEDED_PATHS}}
        result = self._run(schema)
        for path in SUPERSEDED_PATHS:
            assert result["paths"][path]["get"]["deprecated"] is True

    def test_both_spellings_of_each_superseded_path_are_listed(self):
        assert set(SUPERSEDED_PATHS) == {
            "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
            "/api/contentstore/v0/grading/{course_id}",
            "/api/contentstore/v1/course_grading/{course_id}",
        }

    def test_the_conforming_paths_are_left_unmarked(self):
        conforming = "/api/authoring/v1/courses/{course_key}/youtube_transcript_checks/"
        result = self._run({"paths": {conforming: {"get": {"operationId": "x"}}}})
        assert result["paths"][conforming]["get"] == {"operationId": "x"}

    def test_an_unrelated_contentstore_path_is_left_unmarked(self):
        other = "/api/contentstore/v0/advanced_settings/{course_id}"
        result = self._run({"paths": {other: {"get": {}}}})
        assert "deprecated" not in result["paths"][other]["get"]

    def test_path_level_keys_are_not_treated_as_operations(self):
        path = SUPERSEDED_PATHS[0]
        result = self._run({"paths": {path: {"parameters": [], "get": {}}}})
        assert result["paths"][path]["parameters"] == []
        assert result["paths"][path]["get"]["deprecated"] is True

    def test_a_schema_without_the_superseded_paths_is_returned_unchanged(self):
        schema = {"paths": {}}
        assert self._run(schema) == {"paths": {}}


class CmsMarkMigratedPathsTest(SimpleTestCase):
    """The post-processing hook works on full paths, so both prefixes coexist in one schema."""

    def test_legacy_paths_deprecated_conforming_paths_not(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v1/xblock/{usage_key_string}/",
            "/api/authoring/v1/xblocks/{usage_key_string}/",
            "/api/contentstore/v3/course_details/{course_id}/",
            "/api/authoring/v3/courses/{course_key}/details/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v1/xblock/{usage_key_string}/"]["get"]["deprecated"] is True
        assert paths["/api/contentstore/v3/course_details/{course_id}/"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/authoring/v1/xblocks/{usage_key_string}/"]["get"]
        assert "deprecated" not in paths["/api/authoring/v3/courses/{course_key}/details/"]["get"]

    def test_bff_surfaces_internal_on_both_mounts(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v3/home/",
            "/api/authoring/v3/home/",
            "/api/authoring/v4/courses/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v3/home/"]["get"]["x-internal"] is True
        assert paths["/api/contentstore/v3/home/"]["get"]["deprecated"] is True
        assert paths["/api/authoring/v3/home/"]["get"]["x-internal"] is True
        assert "deprecated" not in paths["/api/authoring/v3/home/"]["get"]
        assert "x-internal" not in paths["/api/authoring/v4/courses/"]["get"]

    def test_paths_are_left_as_full_urls(self):
        """Paths are not trimmed, so every key resolves against a service-root server."""
        result = cms_mark_migrated_paths(_schema("/api/contentstore/v1/xblock/"), None, None, False)
        assert list(result["paths"]) == ["/api/contentstore/v1/xblock/"]


def _alternatives(*names):
    return [{"$ref": f"#/components/schemas/{name}"} for name in names]


class OverlappingShapesTest(SimpleTestCase):
    """The post-processing hook publishes the listed overlapping response shapes as anyOf, and nothing else."""

    READ = "authoring_grading.CourseGradingRead.v3"
    SHAPES = ("authoring_grading.CourseGradingFull.v3", "authoring_grading.CourseGrading.v3")

    def _run(self, schemas):
        result = {"paths": {}, "components": {"schemas": schemas}}
        return cms_mark_migrated_paths(result, None, None, True)["components"]["schemas"]

    def test_only_the_grading_read_is_listed(self):
        assert ANY_OF_COMPONENTS == {self.READ: self.SHAPES}

    def test_the_grading_read_becomes_any_of(self):
        schemas = self._run({self.READ: {"oneOf": _alternatives(*self.SHAPES)}})
        assert schemas[self.READ] == {"anyOf": _alternatives(*self.SHAPES)}

    def test_other_one_of_components_are_left_alone(self):
        other = {"oneOf": _alternatives("A", "B")}
        schemas = self._run({"Other": other, self.READ: {"oneOf": _alternatives(*self.SHAPES)}})
        assert schemas["Other"] == {"oneOf": _alternatives("A", "B")}

    def test_the_grading_read_with_other_shapes_is_left_alone(self):
        """A change to the shapes the read offers needs a fresh look at whether they overlap."""
        for shapes in (self.SHAPES[::-1], self.SHAPES[:1], (*self.SHAPES, "authoring_grading.Other.v3")):
            schemas = self._run({self.READ: {"oneOf": _alternatives(*shapes)}})
            assert schemas[self.READ] == {"oneOf": _alternatives(*shapes)}, shapes

    def test_a_schema_without_components_is_accepted(self):
        assert cms_mark_migrated_paths({"paths": {}}, None, None, True) == {"paths": {}}


class SpectacularSettingsTest(TestCase):
    """
    The service settings the hooks depend on.

    Read from the environment modules that define them, because the test
    settings module does not configure the schema at all.
    """

    SETTINGS_MODULES = ("cms/envs/devstack.py", "cms/envs/production.py")

    def _source(self, module):
        return (Path(__file__).resolve().parents[3] / module).read_text()

    def test_both_hooks_are_configured_in_every_environment(self):
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'PREPROCESSING_HOOKS': ['cms.lib.spectacular.cms_api_filter']" in source, module
            assert "'cms.lib.spectacular.cms_mark_superseded_paths'," in source, module
            assert "'cms.lib.spectacular.cms_mark_migrated_paths'," in source, module

    def test_the_default_enum_hook_is_kept_alongside_the_new_one(self):
        """Setting the key replaces drf-spectacular's default list, so it is restated."""
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'drf_spectacular.hooks.postprocess_schema_enums'," in source, module

    def test_no_prefix_is_trimmed_so_paths_are_emitted_in_full(self):
        for module in self.SETTINGS_MODULES:
            assert "SCHEMA_PATH_PREFIX_TRIM" not in self._source(module), module

    def test_the_tag_prefix_covers_both_mounts(self):
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'SCHEMA_PATH_PREFIX': r'/api/(contentstore|authoring)'," in source, module

    def test_no_server_advertises_a_single_mount_as_its_root(self):
        for module in self.SETTINGS_MODULES:
            assert "CMS-contentstore" not in self._source(module), module


class _EmptySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    pass


class _HomeViewSet(viewsets.GenericViewSet):
    """Stand-in for a viewset served on both a legacy and a conforming mount."""

    schema = CmsAutoSchema()
    serializer_class = _EmptySerializer

    def list(self, request):
        return Response([])


class CmsAutoSchemaTest(SimpleTestCase):
    """Dual mounts get deterministic operationIds instead of registration-order numeral suffixes."""

    def test_is_the_default_schema_class(self):
        """Views without their own ``schema`` pick this up via REST_FRAMEWORK settings."""
        assert api_settings.DEFAULT_SCHEMA_CLASS is CmsAutoSchema

    def test_only_the_colliding_legacy_address_is_suffixed(self):
        patterns = [
            url_path("api/contentstore/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/contentstore/v4/home/courses/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v4/courses/", _HomeViewSet.as_view({"get": "list"})),
        ]
        with mock.patch.object(spectacular_settings, "SCHEMA_PATH_PREFIX", r"/api/(contentstore|authoring)"):
            schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
        ids = {p: op["operationId"] for p, item in schema["paths"].items() for op in item.values()}
        assert ids["/api/authoring/v3/home/"] == "v3_home_list"
        assert ids["/api/contentstore/v3/home/"] == "v3_home_list_legacy"
        # Already distinct from its conforming twin's id, so it keeps its name.
        assert ids["/api/authoring/v4/courses/"] == "v4_courses_list"
        assert ids["/api/contentstore/v4/home/courses/"] == "v4_home_courses_list"


class GradingSchemaTest(DjangoTestCase):
    """The grading operations as published, generated from the real contentstore and authoring routes."""

    CONFORMING = "/api/authoring/v3/courses/{course_key}/grading/"
    LEGACY_V3 = "/api/contentstore/v3/authoring_grading/{course_key}/"
    V1 = "/api/contentstore/v1/course_grading/{course_id}"
    V0 = "/api/contentstore/v0/grading/{course_id}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Importing the service URLconf registers the course key converter the authoring routes use.
        import_module(settings.ROOT_URLCONF)
        patterns = [
            url_path("api/contentstore/", include("cms.djangoapps.contentstore.rest_api.urls")),
            url_path("api/authoring/v3/", include("cms.djangoapps.contentstore.rest_api.v3.authoring_urls")),
        ]
        cls.schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)

    def _operation(self, path, method):
        return self.schema["paths"][path][method]

    @staticmethod
    def _ref(response_or_body):
        return response_or_body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def test_the_superseded_operations_are_deprecated(self):
        assert self._operation(self.V1, "get")["deprecated"] is True
        assert self._operation(self.V1, "post")["deprecated"] is True
        assert self._operation(self.V0, "post")["deprecated"] is True

    def test_the_read_on_the_conforming_address_is_published_for_the_sdk(self):
        operation = self._operation(self.CONFORMING, "get")
        assert "deprecated" not in operation
        assert operation["operationId"] == "v3_courses_grading_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        view = [p for p in operation["parameters"] if p["name"] == "view"]
        assert len(view) == 1
        assert view[0]["in"] == "query"
        assert "required" not in view[0]
        assert view[0]["schema"]["enum"] == ["full"]
        assert self._ref(operation["responses"]["200"]) == "authoring_grading.CourseGradingRead.v3"
        for status in ("400", "401", "403", "404"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_read_on_the_legacy_address_is_deprecated(self):
        operation = self._operation(self.LEGACY_V3, "get")
        assert operation["deprecated"] is True
        assert operation["operationId"] == "v3_authoring_grading_retrieve"

    def test_the_update_takes_the_update_body_and_returns_the_policy(self):
        for path in (self.CONFORMING, self.LEGACY_V3):
            operation = self._operation(path, "patch")
            assert self._ref(operation["requestBody"]) == "Patchedauthoring_grading.CourseGradingUpdate.v3", path
            assert self._ref(operation["responses"]["200"]) == "authoring_grading.CourseGrading.v3", path
            for status in ("400", "401", "403", "404"):
                assert self._ref(operation["responses"][status]) == "ErrorResponse", (path, status)
        assert "deprecated" not in self._operation(self.CONFORMING, "patch")
        assert self._operation(self.LEGACY_V3, "patch")["deprecated"] is True

    def test_the_update_body_requires_nothing_but_a_type_per_grader(self):
        components = self.schema["components"]["schemas"]
        assert "required" not in components["Patchedauthoring_grading.CourseGradingUpdate.v3"]
        assert components["authoring_grading.GraderUpdate.v3"]["required"] == ["type"]
        assert "required" not in components["authoring_grading.GracePeriodUpdate.v3"]

    def test_the_update_body_is_json_only(self):
        for path in (self.CONFORMING, self.LEGACY_V3):
            assert list(self._operation(path, "patch")["requestBody"]["content"]) == ["application/json"], path

    def test_the_update_body_types(self):
        components = self.schema["components"]["schemas"]
        update = components["Patchedauthoring_grading.CourseGradingUpdate.v3"]["properties"]
        assert update["grade_cutoffs"]["additionalProperties"] == {"type": "number", "format": "double"}
        assert update["grade_cutoffs"]["minProperties"] == 1
        assert "nullable" not in update["grade_cutoffs"]
        assert {key: update["minimum_grade_credit"][key] for key in ("type", "format", "nullable")} == {
            "type": "number", "format": "double", "nullable": True,
        }
        assert "nullable" not in update["graders"]
        assert update["grace_period"]["allOf"] == [
            {"$ref": "#/components/schemas/authoring_grading.GracePeriodUpdate.v3"},
        ]
        assert update["grace_period"]["nullable"] is True
        grace = components["authoring_grading.GracePeriodUpdate.v3"]["properties"]
        assert {unit: (grace[unit]["type"], grace[unit]["default"]) for unit in grace} == {
            "hours": ("integer", 0), "minutes": ("integer", 0), "seconds": ("integer", 0),
        }

    def test_the_read_documents_the_full_and_the_default_shape(self):
        components = self.schema["components"]["schemas"]
        # anyOf, as a full response also matches the default shape. Full first, so a
        # client trying each shape in order reads a full response as full.
        assert components["authoring_grading.CourseGradingRead.v3"] == {"anyOf": [
            {"$ref": "#/components/schemas/authoring_grading.CourseGradingFull.v3"},
            {"$ref": "#/components/schemas/authoring_grading.CourseGrading.v3"},
        ]}
        for path in (self.CONFORMING, self.LEGACY_V3):
            response = self._operation(path, "get")["responses"]["200"]
            assert self._ref(response) == "authoring_grading.CourseGradingRead.v3", path
        policy = {"graders", "grade_cutoffs", "grace_period", "minimum_grade_credit"}
        extras = {
            "course_assignment_lists", "default_grade_designations", "is_credit_course",
            "show_credit_eligibility", "mfe_proctored_exam_settings_url",
        }
        default = components["authoring_grading.CourseGrading.v3"]
        full = components["authoring_grading.CourseGradingFull.v3"]
        assert set(default["properties"]) == set(default["required"]) == policy
        assert set(full["properties"]) == set(full["required"]) == policy | extras

    def test_the_policy_response_marks_what_is_always_present_and_what_can_be_null(self):
        components = self.schema["components"]["schemas"]

        def nullable(component):
            properties = components[component]["properties"]
            return {name for name, schema in properties.items() if schema.get("nullable")}

        for component in ("authoring_grading.CourseGrading.v3", "authoring_grading.CourseGradingFull.v3"):
            assert nullable(component) == {"grade_cutoffs", "grace_period", "minimum_grade_credit"}, component
            cutoffs = components[component]["properties"]["grade_cutoffs"]
            assert cutoffs["additionalProperties"] == {"type": "number", "format": "double", "nullable": True}
        grader = components["authoring_grading.Grader.v3"]
        assert set(grader["required"]) == set(grader["properties"]) == {
            "type", "min_count", "drop_count", "short_label", "weight", "id",
        }
        assert nullable("authoring_grading.Grader.v3") == {"type", "short_label"}
        grace = components["authoring_grading.GracePeriod.v3"]
        assert grace["required"] == ["hours", "minutes", "seconds"]
        assert nullable("authoring_grading.GracePeriod.v3") == set()

    def test_only_get_and_patch_are_published_on_the_grading_addresses(self):
        for path in (self.CONFORMING, self.LEGACY_V3):
            methods = {key for key in self.schema["paths"][path] if key != "parameters"}
            assert methods == {"get", "patch"}, path
