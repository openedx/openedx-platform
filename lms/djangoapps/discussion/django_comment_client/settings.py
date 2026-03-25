# pylint: disable=missing-module-docstring


from django.conf import settings  # noqa: I001

MAX_COMMENT_DEPTH = None
MAX_UPLOAD_FILE_SIZE = 1024 * 1024   # result in bytes
ALLOWED_UPLOAD_FILE_TYPES = ('.jpg', '.jpeg', '.gif', '.bmp', '.png', '.tiff')

if hasattr(settings, 'DISCUSSION_SETTINGS'):
    MAX_COMMENT_DEPTH = settings.DISCUSSION_SETTINGS.get('MAX_COMMENT_DEPTH')
    MAX_UPLOAD_FILE_SIZE = settings.DISCUSSION_SETTINGS.get('MAX_UPLOAD_FILE_SIZE') or MAX_UPLOAD_FILE_SIZE
    ALLOWED_UPLOAD_FILE_TYPES = (
        settings.DISCUSSION_SETTINGS.get('ALLOWED_UPLOAD_FILE_TYPES') or
        ALLOWED_UPLOAD_FILE_TYPES
    )
