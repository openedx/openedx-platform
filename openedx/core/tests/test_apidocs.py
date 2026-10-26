"""Tests for the shared OpenAPI schema helpers."""

from pathlib import Path

from django.test import SimpleTestCase

from openedx.core.apidocs import SDK_TAG, untag_deprecated_operations

HOOK = "'openedx.core.apidocs.untag_deprecated_operations',"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _schema(**operations):
    return {"paths": {"/api/x/": operations}}


class UntagDeprecatedOperationsTest(SimpleTestCase):
    """A deprecated operation is never offered to the SDK."""

    def test_the_sdk_tag_is_dropped_from_a_deprecated_operation(self):
        result = untag_deprecated_operations(
            _schema(get={"deprecated": True, "tags": [SDK_TAG, "v3"]}), None, None, True,
        )
        assert result["paths"]["/api/x/"]["get"]["tags"] == ["v3"]

    def test_an_operation_left_without_tags_has_no_tags_key(self):
        result = untag_deprecated_operations(
            _schema(get={"deprecated": True, "tags": [SDK_TAG]}), None, None, True,
        )
        assert "tags" not in result["paths"]["/api/x/"]["get"]

    def test_a_current_operation_keeps_its_sdk_tag(self):
        result = untag_deprecated_operations(_schema(get={"tags": [SDK_TAG]}), None, None, True)
        assert result["paths"]["/api/x/"]["get"]["tags"] == [SDK_TAG]

    def test_non_operation_entries_are_left_alone(self):
        result = untag_deprecated_operations(
            _schema(parameters=[{"name": "id"}], get={"deprecated": True}), None, None, True,
        )
        assert result["paths"]["/api/x/"]["parameters"] == [{"name": "id"}]


class UntagDeprecatedOperationsRegistrationTest(SimpleTestCase):
    """Every schema configuration runs the hook after the hooks that mark operations deprecated."""

    MARKING_HOOKS = {
        "cms/envs/common.py": "'cms.lib.spectacular.cms_mark_migrated_paths',",
        "cms/envs/devstack.py": "'cms.lib.spectacular.cms_mark_migrated_paths',",
        "cms/envs/production.py": "'cms.lib.spectacular.cms_mark_migrated_paths',",
        "lms/envs/common.py": "'lms.lib.spectacular.lms_mark_legacy_paths_deprecated',",
    }

    def test_the_hook_runs_last(self):
        for module, marking_hook in self.MARKING_HOOKS.items():
            source = (REPO_ROOT / module).read_text()
            assert HOOK in source, module
            assert source.index(marking_hook) < source.index(HOOK), module
