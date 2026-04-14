"""
Tests for the resolve_storage_backend function in common.djangoapps.util.storage.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.test.utils import override_settings

from common.djangoapps.util.storage import resolve_storage_backend

DEFAULT_STORAGE_CLASS_NAME = "FileSystemStorage"


class ResolveStorageTest(TestCase):
    """
    Tests for the resolve_storage_backend function.
    """

    @override_settings(
        BLOCK_STRUCTURES_SETTINGS="cms.djangoapps.contentstore.storage.ImportExportS3Storage"
    )
    def test_legacy_settings(self):
        """Test legacy string-based storage settings."""
        storage = resolve_storage_backend(
            storage_key="block_structures_settings",
            legacy_setting_key="BLOCK_STRUCTURES_SETTINGS",
            options={}
        )
        assert storage.__class__.__name__ == "ImportExportS3Storage"

    @override_settings(
        BLOCK_STRUCTURES_SETTINGS={
            "STORAGE_CLASS": "cms.djangoapps.contentstore.storage.ImportExportS3Storage"
        }
    )
    def test_nested_legacy_settings(self):
        """Test legacy nested dictionary."""
        storage = resolve_storage_backend(
            storage_key="block_structures_settings",
            legacy_setting_key="BLOCK_STRUCTURES_SETTINGS",
            legacy_sec_setting_keys=["STORAGE_CLASS"],
            options={}
        )
        assert storage.__class__.__name__ == "ImportExportS3Storage"

    @override_settings(
        BLOCK_STRUCTURES_SETTINGS={
            "OTHER_KEY": "cms.djangoapps.contentstore.storage.ImportExportS3Storage"
        }
    )
    def test_nested_legacy_settings_failed(self):
        """Test legacy nested dictionary settings with missing key falls back to default."""
        storage = resolve_storage_backend(
            storage_key="block_structures_settings",
            legacy_setting_key="BLOCK_STRUCTURES_SETTINGS",
            legacy_sec_setting_keys=["STORAGE_CLASS"],
            options={}
        )
        assert storage.__class__.__name__ == DEFAULT_STORAGE_CLASS_NAME

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {}
            }
        },
        LEGACY_SETTING="cms.djangoapps.contentstore.storage.ImportExportS3Storage"
    )
    def test_missing_storage_key_fallback_to_legacy(self):
        """Test fallback to legacy settings when storage key not found in STORAGES."""
        storage = resolve_storage_backend(
            storage_key="nonexistent_storage",
            legacy_setting_key="LEGACY_SETTING",
            options={}
        )
        assert storage.__class__.__name__ == "ImportExportS3Storage"

    def test_no_storages_no_legacy_setting(self):
        """Test fallback to default storage when neither STORAGES nor legacy setting exists."""
        storage = resolve_storage_backend(
            storage_key="nonexistent_storage",
            legacy_setting_key="NONEXISTENT_LEGACY_SETTING",
            options={}
        )
        assert storage.__class__.__name__ == DEFAULT_STORAGE_CLASS_NAME

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "cms.djangoapps.contentstore.storage.ImportExportS3Storage",
                "OPTIONS": {}
            }
        }
    )
    def test_fallback_to_custom_default_backend(self):
        """Test fallback uses custom default backend from STORAGES config."""
        storage = resolve_storage_backend(
            storage_key="nonexistent_storage",
            legacy_setting_key="NONEXISTENT_LEGACY_SETTING",
            options={}
        )
        assert storage.__class__.__name__ == "ImportExportS3Storage"

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {}
            },
            "custom_storage_key": {
                "BACKEND": "cms.djangoapps.contentstore.storage.ImportExportS3Storage",
                "OPTIONS": {}
            }
        }
    )
    @patch('common.djangoapps.util.storage.storages')
    def test_modern_storages_config(self, mock_storages):
        """Test modern Django STORAGES configuration that takes precedence."""
        mock_storage_instance = MagicMock()
        mock_storage_instance.__class__.__name__ = "ImportExportS3Storage"
        mock_storages.__getitem__.return_value = mock_storage_instance

        storage = resolve_storage_backend(
            storage_key="custom_storage_key",
            legacy_setting_key="SOME_LEGACY_SETTING",
            options={}
        )

        mock_storages.__getitem__.assert_called_once_with("custom_storage_key")
        assert storage.__class__.__name__ == "ImportExportS3Storage"
