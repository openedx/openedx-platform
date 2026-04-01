"""
Utility methods related to file handling.
"""


import os
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.storage import DefaultStorage
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from storages.backends.s3boto3 import S3Boto3Storage


class FileValidationException(Exception):
    """
    An exception thrown during file validation.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def store_uploaded_file(
        request, file_key, allowed_file_types, base_storage_filename, max_file_size, validator=None, is_private=False,
):
    """
    Stores an uploaded file to django file storage.

    Args:
        request (HttpRequest): A request object from which a file will be retrieved.
        file_key (str): The key for retrieving the file from `request.FILES`. If no entry exists with this
            key, a `ValueError` will be thrown.
        allowed_file_types (list): a list of allowable file type extensions. These should start with a period
            and be specified in lower-case. For example, ['.txt', '.csv']. If the uploaded file does not end
            with one of these extensions, a `PermissionDenied` exception will be thrown. Note that the uploaded file
            extension does not need to be lower-case.
        base_storage_filename (str): the filename to be used for the stored file, not including the extension.
            The same extension as the uploaded file will be appended to this value.
        max_file_size (int): the maximum file size in bytes that the uploaded file can be. If the uploaded file
            is larger than this size, a `PermissionDenied` exception will be thrown.
        validator (function): an optional validation method that, if defined, will be passed the stored file (which
            is copied from the uploaded file). This method can do validation on the contents of the file and throw
            a `FileValidationException` if the file is not properly formatted. If any exception is thrown, the stored
            file will be deleted before the exception is re-raised. Note that the implementor of the validator function
            should take care to close the stored file if they open it for reading.
        is_private (Boolean): an optional boolean which if True and the storage backend is S3,
            sets the ACL for the file object to be private.

    Returns:
        Storage: the file storage object where the file can be retrieved from
        str: stored_file_name: the name of the stored file (including extension)

    """

    if file_key not in request.FILES:
        raise ValueError("No file uploaded with key '" + file_key + "'.")

    uploaded_file = request.FILES[file_key]
    try:
        file_extension = os.path.splitext(uploaded_file.name)[1].lower()
        if file_extension not in allowed_file_types:
            file_types = "', '".join(allowed_file_types)
            msg = ngettext(
                "The file must end with the extension '{file_types}'.",
                "The file must end with one of the following extensions: '{file_types}'.",
                len(allowed_file_types)).format(file_types=file_types)
            raise PermissionDenied(msg)

        if uploaded_file.size > max_file_size:
            msg = _("Maximum upload file size is {file_size} bytes.").format(file_size=max_file_size)
            raise PermissionDenied(msg)

        stored_file_name = base_storage_filename + file_extension

        file_storage = DefaultStorage()
        # If a file already exists with the supplied name, file_storage will make the filename unique.
        stored_file_name = file_storage.save(stored_file_name, uploaded_file)
        if is_private and settings.STORAGES['default']['BACKEND'] == 'storages.backends.s3boto3.S3Boto3Storage':
            S3Boto3Storage().connection.meta.client.put_object_acl(
                ACL='private',
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=stored_file_name,
            )

        if validator:
            try:
                validator(file_storage, stored_file_name)
            except:
                file_storage.delete(stored_file_name)
                raise

    finally:
        uploaded_file.close()

    return file_storage, stored_file_name


def course_filename_prefix_generator(course_id, separator='_'):
    """
    Generates a course-identifying unicode string for use in a file name.

    Args:
        course_id (object): A course identification object.
        separator (str): The character or chain of characters used for separating course details in
            the filename.
    Returns:
        str: A unicode string which can safely be inserted into a filename.
    """
    filename = str(separator).join([
        course_id.org,
        course_id.course,
        course_id.run
    ])

    enable_course_filename_ccx_suffix = settings.FEATURES.get(
        'ENABLE_COURSE_FILENAME_CCX_SUFFIX',
        False
    )

    if enable_course_filename_ccx_suffix and getattr(course_id, 'ccx', None):
        filename = separator.join([filename, 'ccx', course_id.ccx])

    return get_valid_filename(filename)


def course_and_time_based_filename_generator(course_id, base_name):
    """
    Generates a filename (without extension) based on the current time and the supplied filename.

    Args:
        course_id (object): A course identification object (must have org, course, and run).
        base_name (str): A name describing what type of file this is. Any characters that are not safe for
            filenames will be converted per django.core.files.storage.get_valid_filename (Specifically,
            leading and trailing spaces are removed; other  spaces are converted to underscores; and anything
            that is not a unicode alphanumeric, dash, underscore, or dot, is removed).

    Returns:
        str: a concatenation of the org, course and run from the input course_id, the input base_name,
            and the current time. Note that there will be no extension.

    """
    return "{course_prefix}_{base_name}_{timestamp_str}".format(
        course_prefix=course_filename_prefix_generator(course_id),
        base_name=get_valid_filename(base_name),
        timestamp_str=datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d-%H%M%S")
    )


class UniversalNewlineIterator:
    """
    This iterable class can be used as a wrapper around a file-like
    object which does not inherently support being read in
    universal-newline mode.  It returns a line at a time.
    """
    def __init__(self, original_file, buffer_size=4096):
        self.original_file = original_file
        self.buffer_size = buffer_size

    def __iter__(self):
        return self.generate_lines()

    @staticmethod
    def sanitize(string):
        """
        Replace CR and CRLF with LF within `string`.
        """
        return string.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')

    def generate_lines(self):
        """
        Return data from `self.original_file` a line at a time,
        replacing CR and CRLF with LF.
        """
        buf = self.original_file.read(self.buffer_size)
        line = ''
        while buf:
            for char in buf:
                if line.endswith('\r') and char == '\n':
                    last_line = line
                    line = ''
                    yield self.sanitize(last_line)
                elif line.endswith('\r') or line.endswith('\n'):
                    last_line = line
                    line = char
                    yield self.sanitize(last_line)
                else:
                    line += str(char) if isinstance(char, int) else char
            buf = self.original_file.read(self.buffer_size)
            if not buf and line:
                yield self.sanitize(line)
