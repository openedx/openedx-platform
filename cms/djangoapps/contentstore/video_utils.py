"""
Utils related to the videos.
"""


import logging
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.files.images import get_image_dimensions
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.translation import gettext as _
from edxval.api import get_course_video_image_url, update_video_image

# Youtube thumbnail sizes.
# https://img.youtube.com/vi/{youtube_id}/{thumbnail_quality}.jpg
# High Quality Thumbnail - hqdefault (480x360 pixels)
# Medium Quality Thumbnail - mqdefault (320x180 pixels)
# Normal Quality Thumbnail - default (120x90 pixels)
# And additionally, the next two thumbnails may or may not exist. For HQ videos they exist.
# Standard Definition Thumbnail - sddefault (640x480 pixels)
# Maximum Resolution Thumbnail - maxresdefault (1920x1080 pixels)
YOUTUBE_THUMBNAIL_SIZES = ['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault', 'default']

LOGGER = logging.getLogger(__name__)


def validate_video_image(image_file, skip_aspect_ratio=False):
    """
    Validates video image file.

    Arguments:
        image_file: The selected image file.

    Returns:
        error (String or None): If there is error returns error message otherwise None.
    """
    error = None

    if not all(hasattr(image_file, attr) for attr in ['name', 'content_type', 'size']):
        error = _('The image must have name, content type, and size information.')
    elif image_file.content_type not in list(settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.values()):
        error = _('This image file type is not supported. Supported file types are {supported_file_formats}.').format(
            supported_file_formats=list(settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.keys())
        )
    elif image_file.size > settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MAX_BYTES']:
        error = _('This image file must be smaller than {image_max_size}.').format(
            image_max_size=settings.VIDEO_IMAGE_MAX_FILE_SIZE_MB
        )
    elif image_file.size < settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MIN_BYTES']:
        error = _('This image file must be larger than {image_min_size}.').format(
            image_min_size=settings.VIDEO_IMAGE_MIN_FILE_SIZE_KB
        )
    else:
        try:
            image_file_width, image_file_height = get_image_dimensions(image_file)
        except TypeError:
            return _('There is a problem with this image file. Try to upload a different file.')
        if image_file_width is None or image_file_height is None:
            return _('There is a problem with this image file. Try to upload a different file.')
        image_file_aspect_ratio = abs(image_file_width / float(image_file_height) - settings.VIDEO_IMAGE_ASPECT_RATIO)
        if image_file_width < settings.VIDEO_IMAGE_MIN_WIDTH or image_file_height < settings.VIDEO_IMAGE_MIN_HEIGHT:
            error = _('Recommended image resolution is {image_file_max_width}x{image_file_max_height}. '
                      'The minimum resolution is {image_file_min_width}x{image_file_min_height}.').format(
                image_file_max_width=settings.VIDEO_IMAGE_MAX_WIDTH,
                image_file_max_height=settings.VIDEO_IMAGE_MAX_HEIGHT,
                image_file_min_width=settings.VIDEO_IMAGE_MIN_WIDTH,
                image_file_min_height=settings.VIDEO_IMAGE_MIN_HEIGHT
            )
        elif not skip_aspect_ratio and image_file_aspect_ratio > settings.VIDEO_IMAGE_ASPECT_RATIO_ERROR_MARGIN:
            error = _('This image file must have an aspect ratio of {video_image_aspect_ratio_text}.').format(
                video_image_aspect_ratio_text=settings.VIDEO_IMAGE_ASPECT_RATIO_TEXT
            )
        else:
            try:
                image_file.name.encode('ascii')
            except UnicodeEncodeError:
                error = _('The image file name can only contain letters, numbers, hyphens (-), and underscores (_).')
    return error


def download_youtube_video_thumbnail(youtube_id):
    """
    Download highest resoultion video thumbnail available from youtube.
    """
    thumbnail_content = thumbnail_content_type = None
    # Download highest resolution thumbnail available.
    for thumbnail_quality in YOUTUBE_THUMBNAIL_SIZES:
        thumbnail_url = urljoin('https://img.youtube.com', '/vi/{youtube_id}/{thumbnail_quality}.jpg'.format(  # noqa: UP032  # pylint: disable=line-too-long
            youtube_id=youtube_id, thumbnail_quality=thumbnail_quality
        ))
        response = requests.get(thumbnail_url)
        if response.status_code == requests.codes.ok:   # pylint: disable=no-member
            thumbnail_content = response.content
            thumbnail_content_type = response.headers['content-type']
            # If best available resolution is found, don't look for lower resolutions.
            break
    return thumbnail_content, thumbnail_content_type


def validate_and_update_video_image(course_key_string, edx_video_id, image_file, image_filename):
    """
    Validates image content and updates video image.
    """
    error = validate_video_image(image_file, skip_aspect_ratio=True)
    if error:
        LOGGER.info(
            'VIDEOS: Scraping youtube video thumbnail failed for edx_video_id [%s] in course [%s] with error: %s',
            edx_video_id,
            course_key_string,
            error
        )
        return

    update_video_image(edx_video_id, course_key_string, image_file, image_filename)
    LOGGER.info(
        'VIDEOS: Scraping youtube video thumbnail for edx_video_id [%s] in course [%s]', edx_video_id, course_key_string  # pylint: disable=line-too-long
    )


def scrape_youtube_thumbnail(course_id, edx_video_id, youtube_id):
    """
    Scrapes youtube thumbnail for a given video.
    """
    # Scrape when course video image does not exist for edx_video_id.
    if not get_course_video_image_url(course_id, edx_video_id):
        thumbnail_content, thumbnail_content_type = download_youtube_video_thumbnail(youtube_id)
        supported_content_types = {v: k for k, v in settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.items()}
        image_filename = '{youtube_id}{image_extention}'.format(
            youtube_id=youtube_id,
            image_extention=supported_content_types.get(
                thumbnail_content_type, supported_content_types['image/jpeg']
            )
        )
        image_file = SimpleUploadedFile(image_filename, thumbnail_content, thumbnail_content_type)
        validate_and_update_video_image(course_id, edx_video_id, image_file, image_filename)
