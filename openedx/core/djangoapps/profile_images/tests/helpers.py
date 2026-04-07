"""
Helper methods for use in profile image tests.
"""

import os
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

import piexif
from django.core.files.uploadedfile import UploadedFile
from PIL import Image


@contextmanager
def make_image_file(dimensions=(320, 240), prefix='tmp', extension='.jpeg', force_size=None, orientation=None):
    """
    Yields a named temporary file created with the specified image type and
    options.

    Note the default dimensions are unequal (not a square) ensuring that center-square
    cropping logic will be exercised during tests.

    The temporary file will be closed and deleted automatically upon exiting
    the `with` block.

    prefix - To add prefix to random image file name, after adding will be like <custom-prefix><random-name>.png
            otherwise by default `tmp` is added making file name tmp<random-name>.png.

    """
    image = Image.new('RGB', dimensions, "green")
    image_file = NamedTemporaryFile(prefix=prefix, suffix=extension)  # lint-amnesty, pylint: disable=consider-using-with
    try:
        if orientation and orientation in range(1, 9):
            exif_bytes = piexif.dump({'0th': {piexif.ImageIFD.Orientation: orientation}})
            image.save(image_file, exif=exif_bytes)
        else:
            image.save(image_file)
        if force_size is not None:
            image_file.seek(0, os.SEEK_END)
            bytes_to_pad = force_size - image_file.tell()
            # write in hunks of 256 bytes
            hunk, byte_ = bytearray([0] * 256), bytearray([0])
            num_hunks, remainder = divmod(bytes_to_pad, 256)
            for _ in range(num_hunks):
                image_file.write(hunk)
            for _ in range(remainder):
                image_file.write(byte_)
            image_file.flush()
        image_file.seek(0)
        yield image_file
    finally:
        image_file.close()


@contextmanager
def make_uploaded_file(content_type, *a, **kw):
    """
    Wrap the result of make_image_file in a django UploadedFile.
    """
    with make_image_file(*a, **kw) as image_file:
        yield UploadedFile(
            image_file,
            content_type=content_type,
            size=os.path.getsize(image_file.name),
        )
