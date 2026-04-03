"""
Storage backend for course import and export.
"""


from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage
from storages.utils import setting

from common.djangoapps.util.storage import resolve_storage_backend


class ImportExportS3Storage(S3Boto3Storage):  # pylint: disable=abstract-method
    """
    S3 backend for course import and export OLX files.
    """

    def __init__(self):
        bucket = setting('COURSE_IMPORT_EXPORT_BUCKET', settings.AWS_STORAGE_BUCKET_NAME)
        super().__init__(bucket_name=bucket, custom_domain=None, querystring_auth=True)

# pylint: disable=invalid-name
course_import_export_storage = resolve_storage_backend(
    storage_key="course_import_export",
    legacy_setting_key="COURSE_IMPORT_EXPORT_STORAGE"
)
