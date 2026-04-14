"""
Tests for the get_storage utility function.
"""

from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings

from openedx.core.storage import get_storage


class TestGetStorage(TestCase):
    """
    Tests of the get_storage function
    """

    def setUp(self):
        super().setUp()
        get_storage.cache_clear()

    def tearDown(self):
        get_storage.cache_clear()

    @override_settings(
        STORAGES={
            'default': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage'
            }
        }
    )
    def test_get_storage_returns_default_storage_when_no_class_specified(self):
        """Test that get_storage returns the default storage when no storage_class is provided."""
        storage = get_storage()
        self.assertIsInstance(storage, FileSystemStorage)  # noqa: PT009

    def test_get_storage_returns_custom_storage_when_class_specified(self):
        """Test that get_storage returns the specified storage class."""
        storage_class = 'django.core.files.storage.FileSystemStorage'
        storage = get_storage(storage_class=storage_class)
        self.assertIsInstance(storage, FileSystemStorage)  # noqa: PT009

    def test_get_storage_caching_behavior(self):
        """Test that get_storage caches instances with identical arguments."""
        storage_class = 'django.core.files.storage.FileSystemStorage'
        kwargs = {'location': '/test/path'}
        # First Call
        storage1 = get_storage(storage_class=storage_class, **kwargs)
        # Second Call
        storage2 = get_storage(storage_class=storage_class, **kwargs)
        self.assertIs(storage1, storage2)  # noqa: PT009

    def test_get_storage_handles_invalid_storage_class(self):
        """Test that get_storage raises appropriate error for invalid storage class."""
        with self.assertRaises(ImportError):  # noqa: PT027
            get_storage(storage_class='nonexistent.storage.InvalidStorage')
