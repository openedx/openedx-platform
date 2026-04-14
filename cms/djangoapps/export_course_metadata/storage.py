"""
Storage backend for course metadata export.
"""


from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage

from common.djangoapps.util.storage import resolve_storage_backend


class CourseMetadataExportS3Storage(S3Boto3Storage):  # pylint: disable=abstract-method
    """
    S3 backend for course metadata export
    """

    def __init__(self):
        bucket = settings.COURSE_METADATA_EXPORT_BUCKET
        super().__init__(bucket_name=bucket, custom_domain=None, querystring_auth=True)

course_metadata_export_storage = resolve_storage_backend(
    storage_key="course_metadata_export",
    legacy_setting_key="COURSE_METADATA_EXPORT_STORAGE"
)
