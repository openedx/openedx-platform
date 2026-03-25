"""
User facing error messages during course import and export
"""
from django.utils.translation import gettext as _  # noqa: I001

COURSE_ALREADY_EXIST = _('Aborting import because a course with this id: {} already exists.')
COURSE_PERMISSION_DENIED = _('Permission denied. You do not have write access to this course.')
FILE_MISSING = _('Could not find the {0} file in the package.')
FILE_NOT_FOUND = _('Uploaded Tar file not found. Try again.')
INVALID_FILE_TYPE = _('We only support uploading a .tar.gz file.')
LIBRARY_ALREADY_EXIST = _('Aborting import since a library with this id already exists.')
OLX_VALIDATION_FAILED = _('Course olx validation failed. Please check your email.')
PERMISSION_DENIED = _('Permission denied')
UNKNOWN_ERROR_IN_IMPORT = _('Unknown error while importing course.')
UNKNOWN_ERROR_IN_UNPACKING = _('An Unknown error occurred during the unpacking step.')
UNKNOWN_USER_ID = _('Unknown User ID: {0}')
UNSAFE_ARCHIVE_FILE = _('Unsafe archive file. Aborting import.')
USER_PERMISSION_DENIED = _('User permission denied.')
