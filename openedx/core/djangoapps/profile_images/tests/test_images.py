"""
Test cases for image processing functions in the profile image package.
"""
import os  # lint-amnesty, pylint: disable=wrong-import-order
from contextlib import closing
from itertools import product  # lint-amnesty, pylint: disable=wrong-import-order
from tempfile import NamedTemporaryFile  # lint-amnesty, pylint: disable=wrong-import-order
from unittest import mock

import ddt
import piexif
import pytest
from django.core.files.uploadedfile import UploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from PIL import Image

from openedx.core.djangolib.testing.utils import skip_unless_lms

from ..exceptions import ImageValidationError
from ..images import (
    _get_exif_orientation,
    _get_valid_file_types,
    _update_exif_orientation,
    create_profile_images,
    remove_profile_images,
    validate_uploaded_image,
)
from .helpers import make_image_file, make_uploaded_file


@ddt.ddt
@skip_unless_lms
class TestValidateUploadedImage(TestCase):
    """
    Test validate_uploaded_image
    """
    FILE_UPLOAD_BAD_TYPE = (
        'The file must be one of the following types: {valid_file_types}.'.format(
            valid_file_types=_get_valid_file_types()
        )
    )

    def check_validation_result(self, uploaded_file, expected_failure_message):
        """
        Internal DRY helper.
        """
        if expected_failure_message is not None:
            with pytest.raises(ImageValidationError) as ctx:
                validate_uploaded_image(uploaded_file)
            assert str(ctx.value) == expected_failure_message
        else:
            validate_uploaded_image(uploaded_file)
            assert uploaded_file.tell() == 0

    @ddt.data(
        (99, "The file must be at least 100 bytes in size."),
        (100, ),
        (1024, ),
        (1025, "The file must be smaller than 1 KB in size."),
    )
    @ddt.unpack
    @override_settings(PROFILE_IMAGE_MIN_BYTES=100, PROFILE_IMAGE_MAX_BYTES=1024)
    def test_file_size(self, upload_size, expected_failure_message=None):
        """
        Ensure that files outside the accepted size range fail validation.
        """
        with make_uploaded_file(
            dimensions=(1, 1), extension=".png", content_type="image/png", force_size=upload_size
        ) as uploaded_file:
            self.check_validation_result(uploaded_file, expected_failure_message)

    @ddt.data(
        (".gif", "image/gif"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".bmp", "image/bmp", FILE_UPLOAD_BAD_TYPE),
        (".tif", "image/tiff", FILE_UPLOAD_BAD_TYPE),
    )
    @ddt.unpack
    def test_extension(self, extension, content_type, expected_failure_message=None):
        """
        Ensure that files whose extension is not supported fail validation.
        """
        with make_uploaded_file(extension=extension, content_type=content_type) as uploaded_file:
            self.check_validation_result(uploaded_file, expected_failure_message)

    def test_extension_mismatch(self):
        """
        Ensure that validation fails when the file extension does not match the
        file data.
        """
        file_upload_bad_ext = (
            'The file name extension for this file does not match '
            'the file data. The file may be corrupted.'
        )
        # make a bmp, try to fool the function into thinking it's a jpeg
        with make_image_file(extension=".bmp") as bmp_file:
            with closing(NamedTemporaryFile(suffix=".jpeg")) as fake_jpeg_file:
                fake_jpeg_file.write(bmp_file.read())
                fake_jpeg_file.seek(0)
                uploaded_file = UploadedFile(
                    fake_jpeg_file,
                    content_type="image/jpeg",
                    size=os.path.getsize(fake_jpeg_file.name)
                )
                with pytest.raises(ImageValidationError) as ctx:
                    validate_uploaded_image(uploaded_file)
                assert str(ctx.value) == file_upload_bad_ext

    def test_content_type(self):
        """
        Ensure that validation fails when the content_type header and file
        extension do not match
        """
        file_upload_bad_mimetype = (
            'The Content-Type header for this file does not match '
            'the file data. The file may be corrupted.'
        )
        with make_uploaded_file(extension=".jpeg", content_type="image/gif") as uploaded_file:
            with pytest.raises(ImageValidationError) as ctx:
                validate_uploaded_image(uploaded_file)
            assert str(ctx.value) == file_upload_bad_mimetype


@ddt.ddt
@skip_unless_lms
class TestGenerateProfileImages(TestCase):
    """
    Test create_profile_images
    """

    def check_exif_orientation(self, image, expected_orientation):
        """
        Check that the created object is a JPEG and that it has the expected
        """
        assert image.format == 'JPEG'
        if expected_orientation is not None:
            assert 'exif' in image.info
            assert _get_exif_orientation(image.info['exif']) == expected_orientation
        else:
            assert _get_exif_orientation(image.info.get('exif', piexif.dump({}))) is None

    @ddt.data(
        *product(
            ["gif", "jpg", "png"],
            [(1, 1), (10, 10), (100, 100), (1000, 1000), (1, 10), (10, 100), (100, 1000), (1000, 999)],
        )
    )
    @ddt.unpack
    def test_generation(self, image_type, dimensions):
        """
        Ensure that regardless of the input format or dimensions, the outcome
        of calling the function is square jpeg files with explicitly-requested
        dimensions being saved to the profile image storage backend.
        """
        extension = "." + image_type
        content_type = "image/" + image_type
        requested_sizes = {
            10: "ten.jpg",
            100: "hundred.jpg",
            1000: "thousand.jpg",
        }
        with make_uploaded_file(dimensions=dimensions, extension=extension, content_type=content_type) as uploaded_file:
            names_and_images = self._create_mocked_profile_images(uploaded_file, requested_sizes)
            actual_sizes = {}
            for name, image_obj in names_and_images:
                # get the size of the image file and ensure it's square jpeg
                width, height = image_obj.size
                assert width == height
                actual_sizes[width] = name
            assert requested_sizes == actual_sizes

    def test_jpeg_with_exif_orientation(self):
        requested_images = {10: "ten.jpg", 100: "hunnert.jpg"}
        rotate_90_clockwise = 8  # Value used in EXIF Orientation field.
        with make_image_file(orientation=rotate_90_clockwise, extension='.jpg') as imfile:
            for _, image in self._create_mocked_profile_images(imfile, requested_images):
                self.check_exif_orientation(image, rotate_90_clockwise)

    def test_jpeg_without_exif_orientation(self):
        requested_images = {10: "ten.jpg", 100: "hunnert.jpg"}
        with make_image_file(extension='.jpg') as imfile:
            for _, image in self._create_mocked_profile_images(imfile, requested_images):
                self.check_exif_orientation(image, None)

    def test_update_exif_orientation_without_orientation(self):
        """
        Test the update_exif_orientation without orientation will not throw exception.
        """
        requested_images = {10: "ten.jpg"}
        with make_image_file(extension='.jpg') as imfile:
            for _, image in self._create_mocked_profile_images(imfile, requested_images):
                self.check_exif_orientation(image, None)
                exif = image.info.get('exif', piexif.dump({}))
                assert _update_exif_orientation(exif, None) == image.info.get('exif', piexif.dump({}))

    def _create_mocked_profile_images(self, image_file, requested_images):
        """
        Create image files with mocked-out storage.

        Verifies that an image was created for each element in
        requested_images, and returns an iterator of 2-tuples representing
        those imageswhere each tuple consists of a filename and a PIL.Image
        object.
        """
        mock_storage = mock.Mock()
        with mock.patch(
            "openedx.core.djangoapps.profile_images.images.get_profile_image_storage",
            return_value=mock_storage,
        ):
            create_profile_images(image_file, requested_images)
        names_and_files = [v[0] for v in mock_storage.save.call_args_list]
        assert len(names_and_files) == len(requested_images)
        for name, file_ in names_and_files:
            with closing(Image.open(file_)) as image:
                yield name, image


@skip_unless_lms
class TestRemoveProfileImages(TestCase):
    """
    Test remove_profile_images
    """

    def test_remove(self):
        """
        Ensure that the outcome of calling the function is that the named images
        are deleted from the profile image storage backend.
        """
        requested_sizes = {
            10: "ten.jpg",
            100: "hundred.jpg",
            1000: "thousand.jpg",
        }
        mock_storage = mock.Mock()
        with mock.patch(
            "openedx.core.djangoapps.profile_images.images.get_profile_image_storage",
            return_value=mock_storage,
        ):
            remove_profile_images(requested_sizes)
            deleted_names = [v[0][0] for v in mock_storage.delete.call_args_list]
            assert list(requested_sizes.values()) == deleted_names
            mock_storage.save.reset_mock()
