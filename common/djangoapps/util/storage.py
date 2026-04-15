""" Utility functions related to django storages """
import logging
from typing import List, Optional  # noqa: UP035

from django.conf import settings
from django.core.files.storage import storages
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def resolve_storage_backend(
        storage_key: str,
        legacy_setting_key: str,
        legacy_sec_setting_keys: List[str] = None,  # noqa: UP006
        options: Optional[dict] = None):  # noqa: UP045
    """
    Configures and returns a Django `Storage` instance, compatible with both Django 4 and Django 5.
    Params:
        storage_key = The key name saved in Django storages settings.
        legacy_setting_key = The key name saved in Django settings.
        legacy_sec_setting_keys = List of keys to get the storage class.
            For legacy dict settings like settings.BLOCK_STRUCTURES_SETTINGS.get('STORAGE_CLASS'),
            it is necessary to access a second-level key or above to retrieve the class path.
        options = Kwargs for the storage class.
    Returns:
        An instance of the configured storage backend.
    Raises:
        ImportError: If the specified storage class cannot be imported.
        KeyError: If the specified key is not found in the legacy settings.
    Main goal:
        Deprecate the use of `django.core.files.storage.get_storage_class`.
    How:
        Replace `get_storage_class` with direct configuration logic,
        ensuring backward compatibility with both Django 4 and Django 5 storage settings.
    """

    storage_path = getattr(settings, legacy_setting_key, None)
    storages_config = getattr(settings, 'STORAGES', {})
    options = options or {}

    if storage_key in storages_config:
        # Use case 1: STORAGES is defined
        # If STORAGES is present, we retrieve it through the storages API
        # settings.py must define STORAGES like:
        # STORAGES = {
        #     "default": {"BACKEND": "...", "OPTIONS": {...}},
        #     "custom": {"BACKEND": "...", "OPTIONS": {...}},
        # }
        # See: https://docs.djangoproject.com/en/5.2/ref/settings/#std-setting-STORAGES
        return storages[storage_key]

    # Use case 2: Legacy settings
    # Fallback to import the storage_path (Obtained from django settings) manually
    if isinstance(storage_path, dict) and legacy_sec_setting_keys:
        # If storage_path is a dict, we need to access the second-level keys
        # to retrieve the storage class path.
        # This is useful for legacy settings that store storage paths in a nested structure.
        for deep_setting_key in legacy_sec_setting_keys:
            # For legacy dict settings like settings.CUSTOM_STORAGE = {"BACKEND": "cms.custom.."}
            if deep_setting_key not in storage_path:
                logger.warning(
                    f"Key {legacy_setting_key} '{deep_setting_key}' not found in storage settings {storage_path}."
                    "Using default storage path."
                )
                storage_path = None  # We set it to None to use the default storage later
                break
            storage_path = storage_path.get(deep_setting_key)

    StorageClass = import_string(storage_path or storages_config["default"]["BACKEND"])
    return StorageClass(**options)
