"""
Image file manipulation functions related to profile images.
"""


import binascii
from collections import namedtuple
from contextlib import closing
from io import BytesIO

import piexif
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image

from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_storage

from .exceptions import ImageValidationError

ImageType = namedtuple('ImageType', ('extensions', 'mimetypes', 'magic'))

IMAGE_TYPES = {
    'jpeg': ImageType(
        extensions=['.jpeg', '.jpg'],
        mimetypes=['image/jpeg', 'image/pjpeg'],
        magic=['ffd8'],
    ),
    'png': ImageType(
        extensions=[".png"],
        mimetypes=['image/png'],
        magic=["89504e470d0a1a0a"],
    ),
    'gif': ImageType(
        extensions=[".gif"],
        mimetypes=['image/gif'],
        magic=["474946383961", "474946383761"],
    ),
}


def create_profile_images(image_file, profile_image_names):
    """
    Generates a set of image files based on image_file and stores them
    according to the sizes and filenames specified in `profile_image_names`.

    Arguments:

        image_file (file):
            The uploaded image file to be cropped and scaled to use as a
            profile image.  The image is cropped to the largest possible square,
            and centered on this image.

        profile_image_names (dict):
            A dictionary that maps image sizes to file names.  The image size
            is an integer representing one side of the equilateral image to be
            created.

    Returns:

        None
    """
    storage = get_profile_image_storage()

    original = Image.open(image_file)
    image = _set_color_mode_to_rgb(original)
    image = _crop_image_to_square(image)

    for size, name in profile_image_names.items():
        scaled = _scale_image(image, size)
        exif = _get_corrected_exif(scaled, original)
        with closing(_create_image_file(scaled, exif)) as scaled_image_file:
            storage.save(name, scaled_image_file)


def remove_profile_images(profile_image_names):
    """
    Physically remove the image files specified in `profile_image_names`
    """
    storage = get_profile_image_storage()
    for name in profile_image_names.values():
        storage.delete(name)


def validate_uploaded_image(uploaded_file):
    """
    Raises ImageValidationError if the server should refuse to use this
    uploaded file as the source image for a user's profile image.  Otherwise,
    returns nothing.
    """

    # validation code by @pmitros,
    # adapted from https://github.com/pmitros/ProfileXBlock
    # see also: http://en.wikipedia.org/wiki/Magic_number_%28programming%29

    if uploaded_file.size > settings.PROFILE_IMAGE_MAX_BYTES:  # pylint: disable=no-else-raise
        file_upload_too_large = _(
            'The file must be smaller than {image_max_size} in size.'
        ).format(
            image_max_size=_user_friendly_size(settings.PROFILE_IMAGE_MAX_BYTES)
        )
        raise ImageValidationError(file_upload_too_large)
    elif uploaded_file.size < settings.PROFILE_IMAGE_MIN_BYTES:
        file_upload_too_small = _(
            'The file must be at least {image_min_size} in size.'
        ).format(
            image_min_size=_user_friendly_size(settings.PROFILE_IMAGE_MIN_BYTES)
        )
        raise ImageValidationError(file_upload_too_small)

    # check the file extension looks acceptable
    filename = str(uploaded_file.name).lower()
    filetype = [ft for ft in IMAGE_TYPES if any(filename.endswith(ext) for ext in IMAGE_TYPES[ft].extensions)]
    if not filetype:
        file_upload_bad_type = _(
            'The file must be one of the following types: {valid_file_types}.'
        ).format(valid_file_types=_get_valid_file_types())
        raise ImageValidationError(file_upload_bad_type)
    filetype = filetype[0]

    # check mimetype matches expected file type
    if uploaded_file.content_type not in IMAGE_TYPES[filetype].mimetypes:
        file_upload_bad_mimetype = _(
            'The Content-Type header for this file does not match '
            'the file data. The file may be corrupted.'
        )
        raise ImageValidationError(file_upload_bad_mimetype)

    # check magic number matches expected file type
    headers = IMAGE_TYPES[filetype].magic
    if binascii.hexlify(uploaded_file.read(len(headers[0]) // 2)).decode('utf-8') not in headers:
        file_upload_bad_ext = _(
            'The file name extension for this file does not match '
            'the file data. The file may be corrupted.'
        )
        raise ImageValidationError(file_upload_bad_ext)
    # avoid unexpected errors from subsequent modules expecting the fp to be at 0
    uploaded_file.seek(0)


def _crop_image_to_square(image):
    """
    Given a PIL.Image object, return a copy cropped to a square around the
    center point with each side set to the size of the smaller dimension.
    """
    width, height = image.size
    if width != height:
        side = width if width < height else height
        left = (width - side) // 2
        top = (height - side) // 2
        right = (width + side) // 2
        bottom = (height + side) // 2
        image = image.crop((left, top, right, bottom))
    return image


def _set_color_mode_to_rgb(image):
    """
    Given a PIL.Image object, return a copy with the color mode set to RGB.
    """
    return image.convert('RGB')


def _scale_image(image, side_length):
    """
    Given a PIL.Image object, get a resized copy with each side being
    `side_length` pixels long.  The scaled image will always be square.
    """
    return image.resize((side_length, side_length), Image.LANCZOS)


def _create_image_file(image, exif):
    """
    Given a PIL.Image object, create and return a file-like object containing
    the data saved as a JPEG.

    Note that the file object returned is a django ContentFile which holds data
    in memory (not on disk).
    """
    string_io = BytesIO()

    # The if/else dance below is required, because PIL raises an exception if
    # you pass None as the value of the exif kwarg.
    if exif is None:
        image.save(string_io, format='JPEG')
    else:
        image.save(string_io, format='JPEG', exif=exif)

    image_file = ContentFile(string_io.getvalue())
    return image_file


def _get_corrected_exif(image, original):
    """
    If the original image contains exif data, use that data to
    preserve image orientation in the new image.
    """
    if 'exif' in original.info:
        image_exif = image.info.get('exif', piexif.dump({}))
        original_exif = original.info['exif']
        image_exif = _update_exif_orientation(image_exif, _get_exif_orientation(original_exif))
        return image_exif


def _update_exif_orientation(exif, orientation):
    """
    Given an exif value and an integer value 1-8, reflecting a valid value for
    the exif orientation, return a new exif with the orientation set.
    """
    exif_dict = piexif.load(exif)
    if orientation:
        exif_dict['0th'][piexif.ImageIFD.Orientation] = orientation
    return piexif.dump(exif_dict)


def _get_exif_orientation(exif):
    """
    Return the orientation value for the given Image object, or None if the
    value is not set.
    """
    exif_dict = piexif.load(exif)
    return exif_dict['0th'].get(piexif.ImageIFD.Orientation)


def _get_valid_file_types():
    """
    Return comma separated string of valid file types.
    """
    return ', '.join([', '.join(IMAGE_TYPES[ft].extensions) for ft in IMAGE_TYPES.keys()])  # pylint: disable=consider-iterating-dictionary


def _user_friendly_size(size):
    """
    Convert size in bytes to user friendly size.

    Arguments:
        size (int): size in bytes

    Returns:
        user friendly size
    """
    units = [_('bytes'), _('KB'), _('MB')]
    i = 0
    while size >= 1024 and i < len(units):
        size //= 1024
        i += 1
    return f'{size} {units[i]}'
