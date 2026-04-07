"""
Tests for signals.py
"""

from unittest.mock import patch

from django.conf import settings
from django.core.files.storage import storages
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from storages.backends.s3boto3 import S3Boto3Storage

from common.djangoapps.util.storage import resolve_storage_backend
from xmodule.modulestore.django import SignalHandler
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .signals import export_course_metadata
from .toggles import EXPORT_COURSE_METADATA_FLAG


@override_waffle_flag(EXPORT_COURSE_METADATA_FLAG, True)
class TestExportCourseMetadata(SharedModuleStoreTestCase):
    """
    Tests for the export_course_metadata function
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        SignalHandler.course_published.disconnect(export_course_metadata)
        self.course = CourseFactory.create(highlights_enabled_for_messaging=True)
        self.course_key = self.course.id

    def tearDown(self):
        super().tearDown()
        SignalHandler.course_published.disconnect(export_course_metadata)

    def _create_chapter(self, **kwargs):
        BlockFactory.create(
            parent=self.course,
            category='chapter',
            **kwargs
        )

    @patch('cms.djangoapps.export_course_metadata.tasks.course_metadata_export_storage')
    @patch('cms.djangoapps.export_course_metadata.tasks.ContentFile')
    def test_happy_path(self, patched_content, patched_storage):
        """ Ensure we call the storage class with the correct parameters and course metadata """
        all_highlights = [["week1highlight1", "week1highlight2"], ["week1highlight1", "week1highlight2"], [], []]
        with self.store.bulk_operations(self.course_key):
            for week_highlights in all_highlights:
                self._create_chapter(highlights=week_highlights)
        SignalHandler.course_published.connect(export_course_metadata)
        SignalHandler.course_published.send(sender=None, course_key=self.course_key)
        patched_content.assert_called_once_with(
            b'{"highlights": [["week1highlight1", "week1highlight2"], ["week1highlight1", "week1highlight2"], [], []]}'
        )
        patched_storage.save.assert_called_once_with(
            f'course_metadata_export/{self.course_key}.json', patched_content.return_value
        )

    @override_settings(
        COURSE_METADATA_EXPORT_STORAGE="cms.djangoapps.export_course_metadata.storage.CourseMetadataExportS3Storage",
        STORAGES={
            'default': {
                'BACKEND': "django.core.files.storage.FileSystemStorage"
            }
        }
    )
    def test_resolve_default_storage(self):
        """ Ensure the default storage is invoked, even if course export storage is configured """
        storage = storages["default"]
        self.assertEqual(storage.__class__.__name__, "FileSystemStorage")

    @override_settings(
        COURSE_METADATA_EXPORT_STORAGE="cms.djangoapps.export_course_metadata.storage.CourseMetadataExportS3Storage",
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage"
            }
        },
        COURSE_METADATA_EXPORT_BUCKET="bucket_name_test"
    )
    def test_resolve_happy_path_storage(self):
        """ Make sure that the correct course export storage is being used """
        storage = resolve_storage_backend(
            storage_key="course_metadata_export_storage",
            legacy_setting_key="COURSE_METADATA_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "CourseMetadataExportS3Storage")
        self.assertEqual(storage.bucket_name, "bucket_name_test")

    @override_settings()
    def test_resolve_storage_with_no_config(self):
        """ If no storage setup is defined, we get FileSystemStorage by default """
        del settings.COURSE_METADATA_EXPORT_STORAGE
        del settings.COURSE_METADATA_EXPORT_BUCKET
        storage = resolve_storage_backend(
            storage_key="course_metadata_export_storage",
            legacy_setting_key="COURSE_METADATA_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "FileSystemStorage")

    @override_settings(
        COURSE_METADATA_EXPORT_STORAGE=None,
        COURSE_METADATA_EXPORT_BUCKET="bucket_name_test",
        STORAGES={
            'course_metadata_export_storage': {
                'BACKEND': 'cms.djangoapps.export_course_metadata.storage.CourseMetadataExportS3Storage',
                'OPTIONS': {}
            }
        }
    )
    def test_resolve_storage_using_django5_settings(self):
        """ Simulating a Django 4 environment using Django 5 Storages configuration """
        storage = resolve_storage_backend(
            storage_key="course_metadata_export_storage",
            legacy_setting_key="COURSE_METADATA_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "CourseMetadataExportS3Storage")
        self.assertEqual(storage.bucket_name, "bucket_name_test")

    @override_settings(
        STORAGES={
            'course_metadata_export_storage': {
                'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
                'OPTIONS': {
                    'bucket_name': 'bucket_name_test'
                }
            }
        }
    )
    def test_resolve_storage_using_django5_settings_with_options(self):
        """ Ensure we call the storage class with the correct parameters and Django 5 setup """
        del settings.COURSE_METADATA_EXPORT_STORAGE
        del settings.COURSE_METADATA_EXPORT_BUCKET
        storage = resolve_storage_backend(
            storage_key="course_metadata_export_storage",
            legacy_setting_key="COURSE_METADATA_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, S3Boto3Storage.__name__)
        self.assertEqual(storage.bucket_name, "bucket_name_test")
