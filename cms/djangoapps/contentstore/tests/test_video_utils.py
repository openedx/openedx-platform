"""
Unit tests for video utils.
"""


from datetime import datetime
from unittest import TestCase, mock

import ddt
import pytz
import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.test.utils import override_settings
from django.utils.module_loading import import_string
from edxval.api import create_profile, create_video, get_course_video_image_url, update_video_image
from storages.backends.s3boto3 import S3Boto3Storage

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.video_utils import (
    YOUTUBE_THUMBNAIL_SIZES,
    download_youtube_video_thumbnail,
    scrape_youtube_thumbnail,
    validate_video_image,
)
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file


class ValidateVideoImageTestCase(TestCase):
    """
    Tests for `validate_video_image` method.
    """
    def test_invalid_image_file_info(self):
        """
        Test that when no file information is provided to validate_video_image, it gives proper error message.
        """
        error = validate_video_image({})
        self.assertEqual(error, 'The image must have name, content type, and size information.')  # noqa: PT009

    def test_corrupt_image_file(self):
        """
        Test that when corrupt file is provided to validate_video_image, it gives proper error message.
        """
        with open(settings.MEDIA_ROOT + '/test-corrupt-image.png', 'w+') as image_file:
            uploaded_image_file = UploadedFile(
                image_file,
                content_type='image/png',
                size=settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MIN_BYTES']
            )
            error = validate_video_image(uploaded_image_file)
            self.assertEqual(error, 'There is a problem with this image file. Try to upload a different file.')  # noqa: PT009  # pylint: disable=line-too-long


@ddt.ddt
class ScrapeVideoThumbnailsTestCase(CourseTestCase):
    """
    Test cases for scraping video thumbnails from youtube.
    """

    def setUp(self):
        super().setUp()
        course_ids = [str(self.course.id)]
        profiles = ['youtube']
        created = datetime.now(pytz.utc)
        previous_uploads = [
            {
                'edx_video_id': 'test1',
                'client_video_id': 'test1.mp4',
                'duration': 42.0,
                'status': 'upload',
                'courses': course_ids,
                'encoded_videos': [],
                'created': created
            },
            {
                'edx_video_id': 'test-youtube-video-1',
                'client_video_id': 'test-youtube-id.mp4',
                'duration': 128.0,
                'status': 'file_complete',
                'courses': course_ids,
                'created': created,
                'encoded_videos': [
                    {
                        'profile': 'youtube',
                        'url': '3_yD_cEKoCk',
                        'file_size': 1600,
                        'bitrate': 100,
                    }
                ],
            },
            {
                'edx_video_id': 'test-youtube-video-2',
                'client_video_id': 'test-youtube-id.mp4',
                'image': 'image2.jpg',
                'duration': 128.0,
                'status': 'file_complete',
                'courses': course_ids,
                'created': created,
                'encoded_videos': [
                    {
                        'profile': 'youtube',
                        'url': '3_yD_cEKoCk',
                        'file_size': 1600,
                        'bitrate': 100,
                    }
                ],
            },
        ]
        for profile in profiles:
            create_profile(profile)

        for video in previous_uploads:
            create_video(video)

        # Create video images.
        with make_image_file() as image_file:
            update_video_image(
                'test-youtube-video-2', str(self.course.id), image_file, 'image.jpg'
            )

    def mocked_youtube_thumbnail_response(
        self,
        mocked_content=None,
        error_response=False,
        image_width=settings.VIDEO_IMAGE_MIN_WIDTH,
        image_height=settings.VIDEO_IMAGE_MIN_HEIGHT
    ):
        """
        Returns a mocked youtube thumbnail response.
        """
        image_content = ''
        with make_image_file(dimensions=(image_width, image_height), ) as image_file:
            image_content = image_file.read()

        if mocked_content or error_response:
            image_content = mocked_content

        mocked_response = requests.Response()
        mocked_response.status_code = requests.codes.ok if image_content else requests.codes.not_found  # pylint: disable=no-member
        mocked_response._content = image_content    # pylint: disable=protected-access
        mocked_response.headers = {'content-type': 'image/jpeg'}
        return mocked_response

    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @mock.patch('requests.get')
    @ddt.data(
        (
            {
                'maxresdefault': 'maxresdefault-result-image-content',
                'sddefault': 'sddefault-result-image-content',
                'hqdefault': 'hqdefault-result-image-content',
                'mqdefault': 'mqdefault-result-image-content',
                'default': 'default-result-image-content'
            },
            'maxresdefault-result-image-content'
        ),
        (
            {
                'maxresdefault': '',
                'sddefault': 'sddefault-result-image-content',
                'hqdefault': 'hqdefault-result-image-content',
                'mqdefault': 'mqdefault-result-image-content',
                'default': 'default-result-image-content'
            },
            'sddefault-result-image-content'
        ),
        (
            {
                'maxresdefault': '',
                'sddefault': '',
                'hqdefault': 'hqdefault-result-image-content',
                'mqdefault': 'mqdefault-result-image-content',
                'default': 'default-result-image-content'
            },
            'hqdefault-result-image-content'
        ),
        (
            {
                'maxresdefault': '',
                'sddefault': '',
                'hqdefault': '',
                'mqdefault': 'mqdefault-result-image-content',
                'default': 'default-result-image-content'
            },
            'mqdefault-result-image-content'
        ),
        (
            {
                'maxresdefault': '',
                'sddefault': '',
                'hqdefault': '',
                'mqdefault': '',
                'default': 'default-result-image-content'
            },
            'default-result-image-content'
        ),
    )
    @ddt.unpack
    def test_youtube_video_thumbnail_download(
        self,
        thumbnail_content_data,
        expected_thumbnail_content,
        mocked_request
    ):
        """
        Test that we get highest resolution video thumbnail available from youtube.
        """
        # Mock get youtube thumbnail responses.
        def mocked_youtube_thumbnail_responses(resolutions):
            """
            Returns a list of mocked responses containing youtube thumbnails.
            """
            mocked_responses = []
            for resolution in YOUTUBE_THUMBNAIL_SIZES:
                mocked_content = resolutions.get(resolution, '')
                error_response = False if mocked_content else True  # pylint: disable=simplifiable-if-expression
                mocked_responses.append(self.mocked_youtube_thumbnail_response(mocked_content, error_response))
            return mocked_responses

        mocked_request.side_effect = mocked_youtube_thumbnail_responses(thumbnail_content_data)

        thumbnail_content, thumbnail_content_type = download_youtube_video_thumbnail('test-yt-id')

        # Verify that we get the expected thumbnail content.
        self.assertEqual(thumbnail_content, expected_thumbnail_content)  # noqa: PT009
        self.assertEqual(thumbnail_content_type, 'image/jpeg')  # noqa: PT009

    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @mock.patch('requests.get')
    def test_scrape_youtube_thumbnail(self, mocked_request):
        """
        Test that youtube thumbnails are correctly scrapped.
        """
        course_id = str(self.course.id)
        video1_edx_video_id = 'test-youtube-video-1'
        video2_edx_video_id = 'test-youtube-video-2'

        # Mock get youtube thumbnail responses.
        mocked_request.side_effect = [self.mocked_youtube_thumbnail_response()]

        # Verify that video1 has no image attached.
        video1_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=video1_edx_video_id)
        self.assertIsNone(video1_image_url)  # noqa: PT009

        # Verify that video2 has already image attached.
        video2_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=video2_edx_video_id)
        self.assertIsNotNone(video2_image_url)  # noqa: PT009

        # Scrape video thumbnails.
        scrape_youtube_thumbnail(course_id, video1_edx_video_id, 'test-yt-id')
        scrape_youtube_thumbnail(course_id, video2_edx_video_id, 'test-yt-id2')

        # Verify that now video1 image is attached.
        video1_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=video1_edx_video_id)
        self.assertIsNotNone(video1_image_url)  # noqa: PT009

        # Also verify that video2's image is not updated.
        video2_image_url_latest = get_course_video_image_url(course_id=course_id, edx_video_id=video2_edx_video_id)
        self.assertEqual(video2_image_url, video2_image_url_latest)  # noqa: PT009

    @ddt.data(
        (
            100,
            100,
            False
        ),
        (
            640,
            360,
            True
        )
    )
    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @mock.patch('cms.djangoapps.contentstore.video_utils.LOGGER')
    @mock.patch('requests.get')
    @ddt.unpack
    def test_scrape_youtube_thumbnail_logging(
        self,
        image_width,
        image_height,
        is_success,
        mocked_request,
        mock_logger
    ):
        """
        Test that we get correct logs in case of failure as well as success.
        """
        course_id = str(self.course.id)
        video1_edx_video_id = 'test-youtube-video-1'
        mocked_request.side_effect = [
            self.mocked_youtube_thumbnail_response(
                image_width=image_width,
                image_height=image_height
            )
        ]
        scrape_youtube_thumbnail(course_id, video1_edx_video_id, 'test-yt-id')
        if is_success:
            mock_logger.info.assert_called_with(
                'VIDEOS: Scraping youtube video thumbnail for edx_video_id [%s] in course [%s]',
                video1_edx_video_id,
                course_id
            )
        else:
            mock_logger.info.assert_called_with(
                'VIDEOS: Scraping youtube video thumbnail failed for edx_video_id [%s] in course [%s] with error: %s',
                video1_edx_video_id,
                course_id,
                'This image file must be larger than 2 KB.'
            )

    @ddt.data(
        (
            None,
            'image/jpeg',
            'This image file must be larger than {image_min_size}.'.format(  # noqa: UP032
                image_min_size=settings.VIDEO_IMAGE_MIN_FILE_SIZE_KB
            )
        ),
        (
            b'dummy-content',
            None,
            'This image file type is not supported. Supported file types are {supported_file_formats}.'.format(  # noqa: UP032  # pylint: disable=line-too-long
                supported_file_formats=list(settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.keys())
            )
        ),
        (
            None,
            None,
            'This image file type is not supported. Supported file types are {supported_file_formats}.'.format(  # noqa: UP032  # pylint: disable=line-too-long
                supported_file_formats=list(settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.keys())
            )
        ),
    )
    @mock.patch('cms.djangoapps.contentstore.video_utils.LOGGER')
    @mock.patch('cms.djangoapps.contentstore.video_utils.download_youtube_video_thumbnail')
    @ddt.unpack
    def test_no_video_thumbnail_downloaded(
        self,
        image_content,
        image_content_type,
        error_message,
        mock_download_youtube_thumbnail,
        mock_logger
    ):
        """
        Test that when no thumbnail is downloaded, video image is not updated.
        """
        mock_download_youtube_thumbnail.return_value = image_content, image_content_type
        course_id = str(self.course.id)
        video1_edx_video_id = 'test-youtube-video-1'

        # Verify that video1 has no image attached.
        video1_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=video1_edx_video_id)
        self.assertIsNone(video1_image_url)  # noqa: PT009

        # Scrape video thumbnail.
        scrape_youtube_thumbnail(course_id, video1_edx_video_id, 'test-yt-id')

        mock_logger.info.assert_called_with(
            'VIDEOS: Scraping youtube video thumbnail failed for edx_video_id [%s] in course [%s] with error: %s',
            video1_edx_video_id,
            course_id,
            error_message
        )

        # Verify that no image is attached to video1.
        video1_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=video1_edx_video_id)
        self.assertIsNone(video1_image_url)  # noqa: PT009


@ddt.ddt
class S3Boto3TestCase(TestCase):
    """ verify s3boto3 returns valid backend."""

    def setUp(self):
        self.storage = S3Boto3Storage()
        self.storage._connections.connection = mock.MagicMock()  # pylint: disable=protected-access

    def order_dict(self, dictionary):
        """
        sorting dict key:values for tests cases.
        """
        sorted_key_values = sorted(dictionary.items())
        dictionary.clear()
        dictionary.update(sorted_key_values)
        return dictionary

    def test_video_backend(self):
        self.assertEqual(  # noqa: PT009
            S3Boto3Storage,
            import_string(
                'storages.backends.s3boto3.S3Boto3Storage',
            )(**settings.VIDEO_IMAGE_SETTINGS.get('STORAGE_KWARGS', {})).__class__
        )

    @override_settings(VIDEO_IMAGE_SETTINGS={
        'STORAGE_CLASS': 'storages.backends.s3boto3.S3Boto3Storage',
        'STORAGE_KWARGS':
            {'bucket_name': 'test', 'default_acl': None, 'location': 'abc/def'}}
    )
    def test_boto3_backend_with_params(self):
        storage = import_string(
            settings.VIDEO_IMAGE_SETTINGS.get('STORAGE_CLASS', {})
        )(**settings.VIDEO_IMAGE_SETTINGS.get('STORAGE_KWARGS', {}))

        self.assertEqual(S3Boto3Storage, storage.__class__)  # noqa: PT009

    def test_storage_without_global_default_acl_setting(self):
        """
        In 1.9.1 package provides the default-acl=`public-read`.
        AWS_DEFAULT_ACL is not defined but package will send public-read.
        In 1.10.1 this test will fail because that version has no default value.
        """
        name = 'test_storage_save231.txt'
        content = ContentFile('new content')

        storage = S3Boto3Storage(**{'bucket_name': 'test'})
        storage._connections.connection = mock.MagicMock()  # pylint: disable=protected-access

        storage.save(name, content)
        storage.bucket.Object.assert_called_once_with(name)

        obj = storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            mock.ANY,
            ExtraArgs=self.order_dict({
                'ContentType': 'text/plain',
            }),
            Config=storage.transfer_config  # pylint: disable=protected-access
        )

    @override_settings(AWS_DEFAULT_ACL='public-read')
    @ddt.data(
        ('public-read', 'public-read'),
        ('private', 'private'),
        (None, None)
    )
    @ddt.unpack
    def test_storage_without_global_default_acl_setting_and_bucket_acls(self, default_acl, output_acl):
        """
        AWS_DEFAULT_ACL set to private and let bucket level acl overrides it behaviour.
        """
        name = 'test_storage_save.txt'
        content = ContentFile('new content')
        storage = S3Boto3Storage(**{'bucket_name': 'test', 'default_acl': default_acl})
        storage._connections.connection = mock.MagicMock()  # pylint: disable=protected-access

        storage.save(name, content)
        storage.bucket.Object.assert_called_once_with(name)

        obj = storage.bucket.Object.return_value

        ExtraArgs = {
            'ACL': output_acl,
            'ContentType': 'text/plain',
        }

        if default_acl is None:
            del ExtraArgs['ACL']

        obj.upload_fileobj.assert_called_with(
            mock.ANY,
            ExtraArgs=self.order_dict(ExtraArgs),
            Config=storage.transfer_config  # pylint: disable=protected-access
        )

    @ddt.data('public-read', 'private')
    def test_storage_passing_default_acl_as_none(self, input_acl):
        """
        check bucket-level None behaviour with different AWS_DEFAULT_ACL
        """
        with override_settings(AWS_DEFAULT_ACL=input_acl):
            name = 'test_storage_save231.txt'
            content = ContentFile('new content')

            storage = S3Boto3Storage(**{'bucket_name': 'test', 'default_acl': None})
            storage._connections.connection = mock.MagicMock()   # pylint: disable=protected-access

            storage.save(name, content)
            storage.bucket.Object.assert_called_once_with(name)

            obj = storage.bucket.Object.return_value
            obj.upload_fileobj.assert_called_with(
                mock.ANY,
                Config=storage.transfer_config,  # pylint: disable=protected-access
                ExtraArgs={
                    'ContentType': 'text/plain',
                },
            )
