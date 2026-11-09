"""Tests for the shared OpenAPI schema helpers."""

from pathlib import Path

from django.conf import settings
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

    # The other environments inherit the hooks from these two modules.
    MARKING_HOOKS = {
        "cms/envs/common.py": "'cms.lib.spectacular.cms_mark_migrated_paths',",
        "lms/envs/common.py": "'lms.lib.spectacular.lms_mark_legacy_paths_deprecated',",
    }

    def test_the_hook_runs_last(self):
        for module, marking_hook in self.MARKING_HOOKS.items():
            source = (REPO_ROOT / module).read_text()
            assert HOOK in source, module
            assert source.index(marking_hook) < source.index(HOOK), module


class SchemaSettingsLayeringTest(SimpleTestCase):
    """SPECTACULAR_SETTINGS is the whole service; SDK_SPECTACULAR_SETTINGS layers a subset over it."""

    # Settings modules are data: they may import other settings modules, never
    # application code. Reading django.conf.settings while one is still being
    # imported configures Django from the half-loaded module.
    SETTINGS_MODULES = ("openedx/envs/common.py", "lms/envs/common.py", "cms/envs/common.py", "docs/docs_settings.py")

    def test_the_service_wide_schema_documents_every_endpoint(self):
        assert settings.SPECTACULAR_SETTINGS["TITLE"] == "Open edX API"
        assert "PREPROCESSING_HOOKS" not in settings.SPECTACULAR_SETTINGS
        assert "SCHEMA_PATH_PREFIX" not in settings.SPECTACULAR_SETTINGS
        assert HOOK.strip(",") in repr(settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"])

    def test_the_sdk_subset_holds_only_overridable_keys(self):
        """drf-spectacular refuses ``SERVE_*`` keys in ``custom_settings``."""
        assert not any(key.startswith("SERVE_") for key in settings.SDK_SPECTACULAR_SETTINGS)

    def test_the_contact_comes_from_the_api_access_manager_email(self):
        assert settings.SPECTACULAR_SETTINGS["CONTACT"] == {"email": settings.API_ACCESS_MANAGER_EMAIL}

    def test_settings_modules_do_not_import_this_module(self):
        for module in self.SETTINGS_MODULES:
            assert "openedx.core.apidocs import" not in (REPO_ROOT / module).read_text(), module
