"""
Extracted file utilities used for adding files to courses. These are likely to
need removal once learning core asset handling is completed:

https://github.com/openedx/openedx-core/issues/890
https://github.com/openedx/openedx-core/pull/880
"""

import logging
from functools import partial

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.utils.translation import gettext as _

from openedx.core.djangoapps.contentserver.caching import del_cached_content
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError


def update_course_run_asset(course_key, upload_file):
    """returns contents of the uploaded file"""
    course_exists_response = _get_error_if_course_does_not_exist(course_key)

    if course_exists_response is not None:
        return course_exists_response

    file_metadata = _get_file_metadata_as_dictionary(upload_file)

    is_file_too_large = _check_file_size_is_too_large(file_metadata)
    if is_file_too_large:
        error_message = _get_file_too_large_error_message(file_metadata["filename"])
        raise AssetSizeTooLargeException(error_message)

    content, temporary_file_path = _get_file_content_and_path(file_metadata, course_key)

    (thumbnail_content, thumbnail_location) = contentstore().generate_thumbnail(
        content, tempfile_path=temporary_file_path
    )

    # delete cached thumbnail even if one couldn't be created this time (else the old thumbnail will continue to show)
    del_cached_content(thumbnail_location)

    if _check_thumbnail_uploaded(thumbnail_content):
        content.thumbnail_location = thumbnail_location

    contentstore().save(content)
    del_cached_content(content.location)

    return content


class AssetNotFoundException(Exception):
    """
    Raised when asset not found
    """


class AssetSizeTooLargeException(Exception):
    """
    Raised when the size of an uploaded asset exceeds the maximum size limit.
    """


def _check_thumbnail_uploaded(thumbnail_content):
    """returns whether thumbnail is None"""
    return thumbnail_content is not None


def _get_file_content_and_path(file_metadata, course_key):
    """returns contents of the uploaded file and path for temporary uploaded file"""
    content_location = StaticContent.compute_location(course_key, file_metadata["filename"])
    upload_file_size = str(file_metadata["upload_file_size"])
    upload_file = file_metadata["upload_file"]

    file_can_be_chunked = upload_file.multiple_chunks()

    static_content_partial = partial(
        StaticContent, content_location, file_metadata["filename"], file_metadata["mime_type"], length=upload_file_size
    )

    if file_can_be_chunked:
        content = static_content_partial(upload_file.chunks())
        temporary_file_path = upload_file.temporary_file_path()
    else:
        content = static_content_partial(upload_file.read())
        temporary_file_path = None
    return content, temporary_file_path


def _get_file_too_large_error_message(filename):
    """returns formatted error message for large files"""

    return _("File {filename} exceeds maximum size of {maximum_size_in_megabytes} MB.").format(
        filename=filename,
        maximum_size_in_megabytes=settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB,
    )


def _get_error_if_course_does_not_exist(course_key):  # pylint: disable=missing-function-docstring
    try:
        modulestore().get_course(course_key)
    except ItemNotFoundError:
        logging.error("Could not find course: %s", course_key)
        return HttpResponseBadRequest()


def _get_file_metadata_as_dictionary(upload_file):  # pylint: disable=missing-function-docstring
    # compute a 'filename' which is similar to the location formatting; we're
    # using the 'filename' nomenclature since we're using a FileSystem paradigm
    # here; we're just imposing the Location string formatting expectations to
    # keep things a bit more consistent
    return {
        "upload_file": upload_file,
        "filename": upload_file.name,
        "mime_type": upload_file.content_type,
        "upload_file_size": get_file_size(upload_file),
    }


def _check_file_size_is_too_large(file_metadata):
    """verifies whether file size is greater than allowed file size"""
    upload_file_size = file_metadata["upload_file_size"]
    maximum_file_size_in_megabytes = settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB
    maximum_file_size_in_bytes = maximum_file_size_in_megabytes * 1000**2

    return upload_file_size > maximum_file_size_in_bytes


def get_file_size(upload_file):
    """returns the size of the uploaded file"""
    # can be used for mocking test file sizes.
    return upload_file.size
