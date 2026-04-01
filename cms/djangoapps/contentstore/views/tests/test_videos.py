"""
Unit tests for video-related REST APIs.
"""


import csv
import json
import re
from contextlib import contextmanager
from datetime import datetime
from io import StringIO
from unittest.mock import Mock, patch

import dateutil.parser
from common.djangoapps.student.tests.factories import UserFactory
import ddt
import pytz
from django.test import TestCase
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from edxval.api import (
    create_or_update_transcript_preferences,
    create_or_update_video_transcript,
    create_profile,
    create_video,
    get_course_video_image_url,
    get_transcript_preferences,
    get_video_info
)
from cms.djangoapps.contentstore.models import VideoUploadConfig
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.video_pipeline.config.waffle import (
    DEPRECATE_YOUTUBE,
    ENABLE_DEVSTACK_VIDEO_UPLOADS,
)
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..videos import (
    KEY_EXPIRATION_IN_SECONDS,
    VIDEO_IMAGE_UPLOAD_ENABLED,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    TranscriptProvider,
    StatusDisplayStrings,
    convert_video_status,
    storage_service_bucket,
    storage_service_key,
    PUBLIC_VIDEO_SHARE
)


def setup_s3_mocks(mock_boto3_resource, bucket_name='test-bucket'):
    """
    Helper function to set up consistent boto3 S3 mocks.

    Args:
        mock_boto3_resource: The patched boto3.resource mock
        bucket_name: Name for the mock bucket (default: 'test-bucket')

    Returns:
        tuple: (mock_s3_client, mock_bucket, mock_s3_resource)
    """
    mock_s3_client = Mock()
    mock_bucket = Mock()
    mock_bucket.name = bucket_name
    mock_bucket.meta.client = mock_s3_client
    mock_s3_resource = Mock()
    mock_s3_resource.Bucket.return_value = mock_bucket
    mock_boto3_resource.return_value = mock_s3_resource
    return mock_s3_client, mock_bucket, mock_s3_resource


class VideoUploadTestBase:
    """
    Test cases for the video upload feature
    """

    def get_url_for_course_key(self, course_key, kwargs=None):
        """Return video handler URL for the given course"""
        return reverse_course_url(self.VIEW_NAME, course_key, kwargs)  # lint-amnesty, pylint: disable=no-member

    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=no-member
        self.url = self.get_url_for_course_key(self.course.id)
        self.test_token = "test_token"
        self.course.video_upload_pipeline = {
            "course_video_upload_token": self.test_token,
        }
        self.save_course()  # lint-amnesty, pylint: disable=no-member

        # create another course for videos belonging to multiple courses
        self.course2 = CourseFactory.create()
        self.course2.video_upload_pipeline = {
            "course_video_upload_token": self.test_token,
        }
        self.course2.save()
        self.store.update_item(self.course2, self.user.id)  # lint-amnesty, pylint: disable=no-member

        # course ids for videos
        course_ids = [str(self.course.id), str(self.course2.id)]
        created = datetime.now(pytz.utc)

        self.profiles = ["profile1", "profile2"]
        self.previous_uploads = [
            {
                "edx_video_id": "test1",
                "client_video_id": "test1.mp4",
                "duration": 42.0,
                "status": "upload",
                "courses": course_ids,
                "encoded_videos": [],
                "created": created
            },
            {
                "edx_video_id": "test2",
                "client_video_id": "test2.mp4",
                "duration": 128.0,
                "status": "file_complete",
                "courses": course_ids,
                "created": created,
                "encoded_videos": [
                    {
                        "profile": "profile1",
                        "url": "http://example.com/profile1/test2.mp4",
                        "file_size": 1600,
                        "bitrate": 100,
                    },
                    {
                        "profile": "profile2",
                        "url": "http://example.com/profile2/test2.mov",
                        "file_size": 16000,
                        "bitrate": 1000,
                    },
                ],
            },
            {
                "edx_video_id": "non-ascii",
                "client_video_id": "nón-ascii-näme.mp4",
                "duration": 256.0,
                "status": "transcode_active",
                "courses": course_ids,
                "created": created,
                "encoded_videos": [
                    {
                        "profile": "profile1",
                        "url": "http://example.com/profile1/nón-ascii-näme.mp4",
                        "file_size": 3200,
                        "bitrate": 100,
                    },
                ]
            },
        ]
        # Ensure every status string is tested
        self.previous_uploads += [
            {
                "edx_video_id": f"status_test_{status}",
                "client_video_id": "status_test.mp4",
                "duration": 3.14,
                "status": status,
                "courses": course_ids,
                "created": created,
                "encoded_videos": [],
            }
            for status in (
                list(StatusDisplayStrings._STATUS_MAP.keys()) +  # pylint:disable=protected-access
                ["non_existent_status"]
            )
        ]
        for profile in self.profiles:
            create_profile(profile)
        for video in self.previous_uploads:
            create_video(video)

    def _get_previous_upload(self, edx_video_id):
        """Returns the previous upload with the given video id."""
        return next(
            video
            for video in self.previous_uploads
            if video["edx_video_id"] == edx_video_id
        )


class VideoStudioAccessTestsMixin:
    """
    Base Access tests for studio video views
    """
    def test_anon_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_put(self):
        response = self.client.put(self.url)
        self.assertEqual(response.status_code, 405)

    def test_invalid_course_key(self):
        response = self.client.get(
            self.get_url_for_course_key("Non/Existent/Course")
        )
        self.assertEqual(response.status_code, 404)

    def test_non_staff_user(self):
        client, __ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 403)


class VideoPipelineStudioAccessTestsMixin:
    """
    Access tests for video views that rely on the video pipeline
    """
    def test_video_pipeline_not_enabled(self):
        settings.FEATURES["ENABLE_VIDEO_UPLOAD_PIPELINE"] = False
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_video_pipeline_not_configured(self):
        settings.VIDEO_UPLOAD_PIPELINE = None
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_course_not_configured(self):
        self.course.video_upload_pipeline = {}
        self.save_course()
        self.assertEqual(self.client.get(self.url).status_code, 404)


class VideoUploadPostTestsMixin:
    """
    Shared test cases for video post tests.
    """
    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    def test_post_success(self, mock_boto3_resource):
        files = [
            {
                'file_name': 'first.mp4',
                'content_type': 'video/mp4',
            },
            {
                'file_name': 'second.mp4',
                'content_type': 'video/mp4',
            },
            {
                'file_name': 'third.mov',
                'content_type': 'video/quicktime',
            },
            {
                'file_name': 'fourth.mp4',
                'content_type': 'video/mp4',
            },
        ]

        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Mock generate_presigned_url to return different URLs for each file
        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        response = self.client.post(
            self.url,
            json.dumps({'files': files}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        response_obj = json.loads(response.content.decode('utf-8'))

        # Verify boto3 resource was called correctly
        mock_boto3_resource.assert_called_once_with(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )

        self.assertEqual(len(response_obj['files']), len(files))
        self.assertEqual(mock_s3_client.generate_presigned_url.call_count, len(files))

        for i, file_info in enumerate(files):
            # Get the call args for this file's presigned URL generation
            call_args = mock_s3_client.generate_presigned_url.call_args_list[i]
            args, kwargs = call_args

            # Verify the operation and params
            self.assertEqual(args[0], 'put_object')
            self.assertEqual(kwargs['Params']['Bucket'], 'test-bucket')
            self.assertEqual(kwargs['Params']['ContentType'], file_info['content_type'])
            self.assertEqual(kwargs['ExpiresIn'], KEY_EXPIRATION_IN_SECONDS)

            # Extract video_id from the key
            key_name = kwargs['Params']['Key']
            path_match = re.match(
                (
                    settings.VIDEO_UPLOAD_PIPELINE['ROOT_PATH'] +
                    '/([a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12})$'
                ),
                key_name
            )
            self.assertIsNotNone(path_match)
            video_id = path_match.group(1)

            # Verify metadata
            metadata = kwargs['Params']['Metadata']
            self.assertEqual(metadata['course_video_upload_token'], self.test_token)
            self.assertEqual(metadata['client_video_id'], file_info['file_name'])
            self.assertEqual(metadata['course_key'], str(self.course.id))

            # Ensure VAL was updated
            val_info = get_video_info(video_id)
            self.assertEqual(val_info['status'], 'upload')
            self.assertEqual(val_info['client_video_id'], file_info['file_name'])
            self.assertEqual(val_info['duration'], 0)
            self.assertEqual(val_info['courses'], [{str(self.course.id): None}])

            # Ensure response is correct
            response_file = response_obj['files'][i]
            self.assertEqual(response_file['file_name'], file_info['file_name'])
            self.assertEqual(response_file['upload_url'], f'http://example.com/url_{file_info["file_name"]}')

    def test_post_non_json(self):
        response = self.client.post(self.url, {"files": []})
        self.assertEqual(response.status_code, 400)

    def test_post_malformed_json(self):
        response = self.client.post(self.url, "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_post_invalid_json(self):
        def assert_bad(content):
            """Make request with content and assert that response is 400"""
            response = self.client.post(
                self.url,
                json.dumps(content),
                content_type="application/json"
            )
            self.assertEqual(response.status_code, 400)

        # Top level missing files key
        assert_bad({})

        # Entry missing file_name
        assert_bad({"files": [{"content_type": "video/mp4"}]})

        # Entry missing content_type
        assert_bad({"files": [{"file_name": "test.mp4"}]})


@ddt.ddt
@patch.dict("django.conf.settings.FEATURES", {"ENABLE_VIDEO_UPLOAD_PIPELINE": True})
@override_settings(VIDEO_UPLOAD_PIPELINE={
    "VEM_S3_BUCKET": "vem_test_bucket", "BUCKET": "test_bucket", "ROOT_PATH": "test_root"
})
class VideosHandlerTestCase(
    VideoUploadTestBase,
    VideoStudioAccessTestsMixin,
    VideoPipelineStudioAccessTestsMixin,
    VideoUploadPostTestsMixin,
    CourseTestCase
):
    """Test cases for the main video upload endpoint"""

    VIEW_NAME = 'videos_handler'

    def test_get_json(self):
        response = self.client.get_json(self.url)
        self.assertEqual(response.status_code, 200)
        response_videos = json.loads(response.content.decode('utf-8'))['videos']
        self.assertEqual(len(response_videos), len(self.previous_uploads))
        for i, response_video in enumerate(response_videos):
            # Videos should be returned by creation date descending
            original_video = self.previous_uploads[-(i + 1)]
            print(response_video.keys())
            self.assertEqual(
                set(response_video.keys()),
                {
                    'edx_video_id',
                    'client_video_id',
                    'created',
                    'duration',
                    'status',
                    'status_nontranslated',
                    'course_video_image_url',
                    'file_size',
                    'download_link',
                    'transcripts',
                    'transcription_status',
                    'transcript_urls',
                    'error_description',
                }
            )
            dateutil.parser.parse(response_video['created'])
            for field in ['edx_video_id', 'client_video_id', 'duration']:
                self.assertEqual(response_video[field], original_video[field])
            self.assertEqual(
                response_video['status'],
                convert_video_status(original_video)
            )

    @ddt.data(
        (
            [
                'edx_video_id', 'client_video_id', 'created', 'duration',
                'status', 'status_nontranslated', 'course_video_image_url', 'file_size',
                'download_link', 'transcripts', 'transcription_status', 'transcript_urls',
                'error_description'
            ],
            [
                {
                    'video_id': 'test1',
                    'language_code': 'en',
                    'file_name': 'edx101.srt',
                    'file_format': 'srt',
                    'provider': 'Cielo24'
                }
            ],
            ['en']
        ),
        (
            [
                'edx_video_id', 'client_video_id', 'created', 'duration',
                'status', 'status_nontranslated', 'course_video_image_url', 'file_size',
                'download_link', 'transcripts', 'transcription_status', 'transcript_urls',
                'error_description'
            ],
            [
                {
                    'video_id': 'test1',
                    'language_code': 'en',
                    'file_name': 'edx101_en.srt',
                    'file_format': 'srt',
                    'provider': 'Cielo24'
                },
                {
                    'video_id': 'test1',
                    'language_code': 'es',
                    'file_name': 'edx101_es.srt',
                    'file_format': 'srt',
                    'provider': 'Cielo24'
                }
            ],
            ['en', 'es']
        )
    )
    @ddt.unpack
    def test_get_json_transcripts(self, expected_video_keys, uploaded_transcripts, expected_transcripts):
        """
        Test that transcripts are attached based on whether the video transcript feature is enabled.
        """
        for transcript in uploaded_transcripts:
            create_or_update_video_transcript(
                transcript['video_id'],
                transcript['language_code'],
                metadata={
                    'file_name': transcript['file_name'],
                    'file_format': transcript['file_format'],
                    'provider': transcript['provider']
                }
            )

        response = self.client.get_json(self.url)
        self.assertEqual(response.status_code, 200)
        response_videos = json.loads(response.content.decode('utf-8'))['videos']
        self.assertEqual(len(response_videos), len(self.previous_uploads))
        for response_video in response_videos:
            print(response_video)

            self.assertEqual(set(response_video.keys()), set(expected_video_keys))
            if response_video['edx_video_id'] == self.previous_uploads[0]['edx_video_id']:
                self.assertEqual(response_video.get('transcripts', []), expected_transcripts)

    def test_get_redirects_to_video_uploads_url(self):
        """
        Test that GET requests redirect to the MFE video uploads page.
        """
        from cms.djangoapps.contentstore.utils import get_video_uploads_url
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, get_video_uploads_url(self.course.id))

    @override_settings(AWS_ACCESS_KEY_ID="test_key_id", AWS_SECRET_ACCESS_KEY="test_secret")
    @patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
    @ddt.data(
        (
            [
                {
                    "file_name": "supported-1.mp4",
                    "content_type": "video/mp4",
                },
                {
                    "file_name": "supported-2.mov",
                    "content_type": "video/quicktime",
                },
            ],
            200
        ),
        (
            [
                {
                    "file_name": "unsupported-1.txt",
                    "content_type": "text/plain",
                },
                {
                    "file_name": "unsupported-2.png",
                    "content_type": "image/png",
                },
            ],
            400
        )
    )
    @ddt.unpack
    def test_video_supported_file_formats(self, files, expected_status, mock_boto3_resource):
        """
        Test that video upload works correctly against supported and unsupported file formats.
        """
        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Mock generate_presigned_url to return different URLs for each file
        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        # Check supported formats
        response = self.client.post(
            self.url,
            json.dumps({"files": files}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, expected_status)
        response = json.loads(response.content.decode('utf-8'))

        if expected_status == 200:
            self.assertNotIn('error', response)
        else:
            self.assertIn('error', response)
            self.assertEqual(response['error'], "Request 'files' entry contain unsupported content_type")

    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    def test_upload_with_non_ascii_charaters(self, mock_boto3_resource):
        """
        Test that video uploads throws error message when file name contains special characters.
        """
        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        file_name = 'test\u2019_file.mp4'
        files = [{'file_name': file_name, 'content_type': 'video/mp4'}]

        response = self.client.post(
            self.url,
            json.dumps({'files': files}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        response = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response['error'], 'The file name for %s must contain only ASCII characters.' % file_name)

    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret', AWS_SECURITY_TOKEN='token')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    @override_waffle_flag(ENABLE_DEVSTACK_VIDEO_UPLOADS, active=True)
    def test_devstack_upload_connection(self, mock_boto3_resource):
        files = [{'file_name': 'first.mp4', 'content_type': 'video/mp4'}]

        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Mock generate_presigned_url to return different URLs for each file
        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        response = self.client.post(
            self.url,
            json.dumps({'files': files}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        mock_boto3_resource.assert_called_once_with(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            security_token=settings.AWS_SECURITY_TOKEN
        )

    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    def test_send_course_to_vem_pipeline(self, mock_boto3_resource):
        """
        Test that uploads always go to VEM S3 bucket by default.
        """
        files = [{'file_name': 'first.mp4', 'content_type': 'video/mp4'}]

        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Mock generate_presigned_url to return different URLs for each file
        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        response = self.client.post(
            self.url,
            json.dumps({'files': files}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        mock_s3_resource.Bucket.assert_called_once_with(
            settings.VIDEO_UPLOAD_PIPELINE['VEM_S3_BUCKET']  # pylint: disable=unsubscriptable-object
        )

    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    @ddt.data(
        {
            'global_waffle': True,
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.off,
            'expect_token': True
        },
        {
            'global_waffle': False,
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            'expect_token': False
        },
        {
            'global_waffle': False,
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.off,
            'expect_token': True
        }
    )
    def test_video_upload_token_in_meta(self, data, mock_boto3_resource):
        """
        Test video upload token in s3 metadata.
        """
        @contextmanager
        def proxy_manager(manager, ignore_manager):
            """
            This acts as proxy to the original manager in the arguments given
            the original manager is not set to be ignored.
            """
            if ignore_manager:
                yield
            else:
                with manager:
                    yield

        file_data = {
            'file_name': 'first.mp4',
            'content_type': 'video/mp4',
        }

        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Track generate_presigned_url calls to inspect metadata
        presigned_url_calls = []

        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            presigned_url_calls.append((operation, Params, ExpiresIn))
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        with patch.object(WaffleFlagCourseOverrideModel, 'override_value', return_value=data['course_override']):
            with override_waffle_flag(DEPRECATE_YOUTUBE, active=data['global_waffle']):
                response = self.client.post(
                    self.url,
                    json.dumps({'files': [file_data]}),
                    content_type='application/json'
                )
                self.assertEqual(response.status_code, 200)

                # Check if course_video_upload_token is in metadata based on expectation
                if data['expect_token']:
                    # We should find the token in the metadata
                    self.assertEqual(len(presigned_url_calls), 1)
                    metadata = presigned_url_calls[0][1]['Metadata']
                    self.assertIn('course_video_upload_token', metadata)
                    self.assertEqual(metadata['course_video_upload_token'], self.test_token)
                else:
                    # If we don't expect a token, verify it's not in metadata
                    if presigned_url_calls:
                        metadata = presigned_url_calls[0][1]['Metadata']
                        self.assertNotIn('course_video_upload_token', metadata)

    def _assert_video_removal(self, url, edx_video_id, deleted_videos):
        """
        Verify that if correct video is removed from a particular course.

        Arguments:
            url (str): URL to get uploaded videos
            edx_video_id (str): video id
            deleted_videos (int): how many videos are deleted
        """
        response = self.client.get_json(url)
        self.assertEqual(response.status_code, 200)
        response_videos = json.loads(response.content.decode('utf-8'))["videos"]
        self.assertEqual(len(response_videos), len(self.previous_uploads) - deleted_videos)

        if deleted_videos:
            self.assertNotIn(edx_video_id, [video.get('edx_video_id') for video in response_videos])
        else:
            self.assertIn(edx_video_id, [video.get('edx_video_id') for video in response_videos])

    def test_video_removal(self):
        """
        Verifies that video removal is working as expected.
        """
        edx_video_id = 'test1'
        remove_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': edx_video_id})
        response = self.client.delete(remove_url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 204)

        self._assert_video_removal(self.url, edx_video_id, 1)

    def test_video_removal_multiple_courses(self):
        """
        Verifies that video removal is working as expected for multiple courses.

        If a video is used by multiple courses then removal from one course shouldn't effect the other course.
        """
        # remove video from course1
        edx_video_id = 'test1'
        remove_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': edx_video_id})
        response = self.client.delete(remove_url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 204)

        # verify that video is only deleted from course1 only
        self._assert_video_removal(self.url, edx_video_id, 1)
        self._assert_video_removal(self.get_url_for_course_key(self.course2.id), edx_video_id, 0)

    def test_convert_video_status(self):
        """
        Verifies that convert_video_status works as expected.
        """
        video = self.previous_uploads[0]

        # video status should be failed if it's in upload state for more than 24 hours
        video['created'] = datetime(2016, 1, 1, 10, 10, 10, 0, pytz.UTC)
        status = convert_video_status(video)
        self.assertEqual(status, StatusDisplayStrings.get('upload_failed'))

        # `invalid_token` should be converted to `youtube_duplicate`
        video['created'] = datetime.now(pytz.UTC)
        video['status'] = 'invalid_token'
        status = convert_video_status(video)
        self.assertEqual(status, StatusDisplayStrings.get('youtube_duplicate'))

        # The "encode status" should be converted to `file_complete` if video encodes are complete
        video['status'] = 'transcription_in_progress'
        status = convert_video_status(video, is_video_encodes_ready=True)
        self.assertEqual(status, StatusDisplayStrings.get('file_complete'))

        # If encoding is not complete return the status as it is
        video['status'] = 's3_upload_failed'
        status = convert_video_status(video)
        self.assertEqual(status, StatusDisplayStrings.get('s3_upload_failed'))

        # for all other status, there should not be any conversion
        statuses = list(StatusDisplayStrings._STATUS_MAP.keys())  # pylint: disable=protected-access
        statuses.remove('invalid_token')
        for status in statuses:
            video['status'] = status
            new_status = convert_video_status(video)
            self.assertEqual(new_status, StatusDisplayStrings.get(status))

    def assert_video_status(self, url, edx_video_id, status):
        """
        Verifies that video with `edx_video_id` has `status`
        """
        response = self.client.get_json(url)
        self.assertEqual(response.status_code, 200)
        videos = json.loads(response.content.decode('utf-8'))["videos"]
        for video in videos:
            if video['edx_video_id'] == edx_video_id:
                return self.assertEqual(video['status'], status)

        # Test should fail if video not found
        self.assertEqual(True, False, 'Invalid edx_video_id')

    @patch('cms.djangoapps.contentstore.video_storage_handlers.LOGGER')
    def test_video_status_update_request(self, mock_logger):
        """
        Verifies that video status update request works as expected.
        """
        url = self.get_url_for_course_key(self.course.id)
        edx_video_id = 'test1'
        self.assert_video_status(url, edx_video_id, 'Uploading')

        response = self.client.post(
            url,
            json.dumps([{
                'edxVideoId': edx_video_id,
                'status': 'upload_failed',
                'message': 'server down'
            }]),
            content_type="application/json"
        )

        mock_logger.info.assert_called_with(
            'VIDEOS: Video status update with id [%s], status [%s] and message [%s]',
            edx_video_id,
            'upload_failed',
            'server down'
        )

        self.assertEqual(response.status_code, 204)

        self.assert_video_status(url, edx_video_id, 'Failed')

    @ddt.data(
        ('test_video_token', "Transcription in Progress"),
        ('', "Ready"),
    )
    @ddt.unpack
    def test_video_transcript_status_conversion(self, course_video_upload_token, expected_video_status_text):
        """
        Verifies that video status `transcription_in_progress` gets converted
        correctly into the `file_complete` for the new video workflow and
        stays as it is, for the old video workflow.
        """
        self.course.video_upload_pipeline = {
            'course_video_upload_token': course_video_upload_token
        }
        self.save_course()

        url = self.get_url_for_course_key(self.course.id)
        edx_video_id = 'test1'
        self.assert_video_status(url, edx_video_id, 'Uploading')

        response = self.client.post(
            url,
            json.dumps([{
                'edxVideoId': edx_video_id,
                'status': 'transcription_in_progress',
                'message': 'Transcription is in progress'
            }]),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 204)

        self.assert_video_status(url, edx_video_id, expected_video_status_text)


@ddt.ddt
@patch.dict("django.conf.settings.FEATURES", {"ENABLE_VIDEO_UPLOAD_PIPELINE": True})
@override_settings(VIDEO_UPLOAD_PIPELINE={
    "VEM_S3_BUCKET": "vem_test_bucket", "BUCKET": "test_bucket", "ROOT_PATH": "test_root"
})
class GenerateVideoUploadLinkTestCase(
    VideoUploadTestBase,
    VideoUploadPostTestsMixin,
    CourseTestCase
):
    """
    Test cases for the main video upload endpoint
    """

    VIEW_NAME = 'generate_video_upload_link'

    def test_unsupported_requests_fail(self):
        """
        The API only supports post, make sure other requests fail
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

        response = self.client.put(self.url)
        self.assertEqual(response.status_code, 405)

        response = self.client.patch(self.url)
        self.assertEqual(response.status_code, 405)


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_VIDEO_UPLOAD_PIPELINE': True})
@override_settings(VIDEO_UPLOAD_PIPELINE={'BUCKET': 'test_bucket', 'ROOT_PATH': 'test_root'})
class VideoImageTestCase(VideoUploadTestBase, CourseTestCase):
    """
    Tests for video image.
    """

    VIEW_NAME = "video_images_handler"

    def verify_image_upload_reponse(self, course_id, edx_video_id, upload_response):
        """
        Verify that image is uploaded successfully.

        Arguments:
            course_id: ID of course
            edx_video_id: ID of video
            upload_response: Upload response object

        Returns:
            uploaded image url
        """
        self.assertEqual(upload_response.status_code, 200)
        response = json.loads(upload_response.content.decode('utf-8'))
        val_image_url = get_course_video_image_url(course_id=course_id, edx_video_id=edx_video_id)
        self.assertEqual(response['image_url'], val_image_url)

        return val_image_url

    def verify_error_message(self, response, error_message):
        """
        Verify that image upload failure gets proper error message.

        Arguments:
            response: Response object.
            error_message: Expected error message.
        """
        self.assertEqual(response.status_code, 400)
        response = json.loads(response.content.decode('utf-8'))
        self.assertIn('error', response)
        self.assertEqual(response['error'], error_message)

    @override_waffle_switch(VIDEO_IMAGE_UPLOAD_ENABLED, False)
    def test_video_image_upload_disabled(self):
        """
        Tests the video image upload when the feature is disabled.
        """
        video_image_upload_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': 'test_vid_id'})
        response = self.client.post(video_image_upload_url, {'file': 'dummy_file'}, format='multipart')
        self.assertEqual(response.status_code, 404)

    @override_waffle_switch(VIDEO_IMAGE_UPLOAD_ENABLED, True)
    def test_video_image(self):
        """
        Test video image is saved.
        """
        edx_video_id = 'test1'
        video_image_upload_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': edx_video_id})
        with make_image_file(
            dimensions=(settings.VIDEO_IMAGE_MIN_WIDTH, settings.VIDEO_IMAGE_MIN_HEIGHT),
        ) as image_file:
            response = self.client.post(video_image_upload_url, {'file': image_file}, format='multipart')
            image_url1 = self.verify_image_upload_reponse(self.course.id, edx_video_id, response)

        # upload again to verify that new image is uploaded successfully
        with make_image_file(
            dimensions=(settings.VIDEO_IMAGE_MIN_WIDTH, settings.VIDEO_IMAGE_MIN_HEIGHT),
        ) as image_file:
            response = self.client.post(video_image_upload_url, {'file': image_file}, format='multipart')
            image_url2 = self.verify_image_upload_reponse(self.course.id, edx_video_id, response)

        self.assertNotEqual(image_url1, image_url2)

    @override_waffle_switch(VIDEO_IMAGE_UPLOAD_ENABLED, True)
    def test_video_image_no_file(self):
        """
        Test that an error error message is returned if upload request is incorrect.
        """
        video_image_upload_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': 'test1'})
        response = self.client.post(video_image_upload_url, {})
        self.verify_error_message(response, 'An image file is required.')

    @override_waffle_switch(VIDEO_IMAGE_UPLOAD_ENABLED, True)
    def test_no_video_image(self):
        """
        Test image url is set to None if no video image.
        """
        edx_video_id = 'test1'
        get_videos_url = reverse_course_url('videos_handler', self.course.id)
        video_image_upload_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': edx_video_id})
        with make_image_file(
            dimensions=(settings.VIDEO_IMAGE_MIN_WIDTH, settings.VIDEO_IMAGE_MIN_HEIGHT),
        ) as image_file:
            self.client.post(video_image_upload_url, {'file': image_file}, format='multipart')

        val_image_url = get_course_video_image_url(course_id=self.course.id, edx_video_id=edx_video_id)

        response = self.client.get_json(get_videos_url)
        self.assertEqual(response.status_code, 200)
        response_videos = json.loads(response.content.decode('utf-8'))["videos"]
        for response_video in response_videos:
            if response_video['edx_video_id'] == edx_video_id:
                self.assertEqual(response_video['course_video_image_url'], val_image_url)
            else:
                self.assertEqual(response_video['course_video_image_url'], None)

    @ddt.data(
        # Image file type validation
        (
            {
                'extension': '.png'
            },
            None
        ),
        (
            {
                'extension': '.gif'
            },
            None
        ),
        (
            {
                'extension': '.bmp'
            },
            None
        ),
        (
            {
                'extension': '.jpg'
            },
            None
        ),
        (
            {
                'extension': '.jpeg'
            },
            None
        ),
        (
            {
                'extension': '.PNG'
            },
            None
        ),
        (
            {
                'extension': '.tiff'
            },
            'This image file type is not supported. Supported file types are {supported_file_formats}.'.format(
                supported_file_formats=list(settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS.keys())
            )
        ),
        # Image file size validation
        (
            {
                'size': settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MAX_BYTES'] + 10
            },
            'This image file must be smaller than {image_max_size}.'.format(
                image_max_size=settings.VIDEO_IMAGE_MAX_FILE_SIZE_MB
            )
        ),
        (
            {
                'size': settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MIN_BYTES'] - 10
            },
            'This image file must be larger than {image_min_size}.'.format(
                image_min_size=settings.VIDEO_IMAGE_MIN_FILE_SIZE_KB
            )
        ),
        # Image file minimum width / height
        (
            {
                'width': 16,  # 16x9
                'height': 9
            },
            'Recommended image resolution is {image_file_max_width}x{image_file_max_height}. The minimum resolution is {image_file_min_width}x{image_file_min_height}.'.format(  # lint-amnesty, pylint: disable=line-too-long
                image_file_max_width=settings.VIDEO_IMAGE_MAX_WIDTH,
                image_file_max_height=settings.VIDEO_IMAGE_MAX_HEIGHT,
                image_file_min_width=settings.VIDEO_IMAGE_MIN_WIDTH,
                image_file_min_height=settings.VIDEO_IMAGE_MIN_HEIGHT
            )
        ),
        (
            {
                'width': settings.VIDEO_IMAGE_MIN_WIDTH - 10,
                'height': settings.VIDEO_IMAGE_MIN_HEIGHT
            },
            'Recommended image resolution is {image_file_max_width}x{image_file_max_height}. The minimum resolution is {image_file_min_width}x{image_file_min_height}.'.format(  # lint-amnesty, pylint: disable=line-too-long
                image_file_max_width=settings.VIDEO_IMAGE_MAX_WIDTH,
                image_file_max_height=settings.VIDEO_IMAGE_MAX_HEIGHT,
                image_file_min_width=settings.VIDEO_IMAGE_MIN_WIDTH,
                image_file_min_height=settings.VIDEO_IMAGE_MIN_HEIGHT
            )
        ),
        (
            {
                'width': settings.VIDEO_IMAGE_MIN_WIDTH,
                'height': settings.VIDEO_IMAGE_MIN_HEIGHT - 10
            },
            (
                'Recommended image resolution is {image_file_max_width}x{image_file_max_height}. '
                'The minimum resolution is {image_file_min_width}x{image_file_min_height}.'
            ).format(
                image_file_max_width=settings.VIDEO_IMAGE_MAX_WIDTH,
                image_file_max_height=settings.VIDEO_IMAGE_MAX_HEIGHT,
                image_file_min_width=settings.VIDEO_IMAGE_MIN_WIDTH,
                image_file_min_height=settings.VIDEO_IMAGE_MIN_HEIGHT
            )
        ),
        (
            {
                'width': 1200,  # not 16:9, but width/height check first.
                'height': 100
            },
            (
                'Recommended image resolution is {image_file_max_width}x{image_file_max_height}. '
                'The minimum resolution is {image_file_min_width}x{image_file_min_height}.'
            ).format(
                image_file_max_width=settings.VIDEO_IMAGE_MAX_WIDTH,
                image_file_max_height=settings.VIDEO_IMAGE_MAX_HEIGHT,
                image_file_min_width=settings.VIDEO_IMAGE_MIN_WIDTH,
                image_file_min_height=settings.VIDEO_IMAGE_MIN_HEIGHT
            )
        ),
        # Image file aspect ratio validation
        (
            {
                'width': settings.VIDEO_IMAGE_MAX_WIDTH,  # 1280x720
                'height': settings.VIDEO_IMAGE_MAX_HEIGHT
            },
            None
        ),
        (
            {
                'width': 850,  # 16:9
                'height': 478
            },
            None
        ),
        (
            {
                'width': 940,  # 1.67 ratio, applicable aspect ratio margin of .01
                'height': 560
            },
            None
        ),
        (
            {
                'width': settings.VIDEO_IMAGE_MIN_WIDTH + 100,
                'height': settings.VIDEO_IMAGE_MIN_HEIGHT + 200
            },
            'This image file must have an aspect ratio of {video_image_aspect_ratio_text}.'.format(
                video_image_aspect_ratio_text=settings.VIDEO_IMAGE_ASPECT_RATIO_TEXT
            )
        ),
        # Image file name validation
        (
            {
                'prefix': 'nøn-åßç¡¡'
            },
            'The image file name can only contain letters, numbers, hyphens (-), and underscores (_).'
        )
    )
    @ddt.unpack
    @override_waffle_switch(VIDEO_IMAGE_UPLOAD_ENABLED, True)
    def test_video_image_validation_message(self, image_data, error_message):
        """
        Test video image validation gives proper error message.

        Arguments:
            image_data (Dict): Specific data to create image file.
            error_message (String): Error message
        """
        edx_video_id = 'test1'
        video_image_upload_url = self.get_url_for_course_key(self.course.id, {'edx_video_id': edx_video_id})
        with make_image_file(
            dimensions=(
                image_data.get('width', settings.VIDEO_IMAGE_MIN_WIDTH),
                image_data.get('height', settings.VIDEO_IMAGE_MIN_HEIGHT)
            ),
            prefix=image_data.get('prefix', 'videoimage'),
            extension=image_data.get('extension', '.png'),
            force_size=image_data.get('size', settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MIN_BYTES'])
        ) as image_file:
            response = self.client.post(video_image_upload_url, {'file': image_file}, format='multipart')
            if error_message:
                self.verify_error_message(response, error_message)
            else:
                self.verify_image_upload_reponse(self.course.id, edx_video_id, response)


@ddt.ddt
@patch(
    'openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled',
    Mock(return_value=True)
)
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_VIDEO_UPLOAD_PIPELINE': True})
class TranscriptPreferencesTestCase(VideoUploadTestBase, CourseTestCase):
    """
    Tests for video transcripts preferences.
    """

    VIEW_NAME = 'transcript_preferences_handler'

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods are POST and DELETE.
        """
        video_transcript_url = self.get_url_for_course_key(self.course.id)
        response = self.client.get(
            video_transcript_url,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 405)

    @ddt.data(
        # Video transcript feature disabled
        (
            {},
            False,
            '',
            404,
        ),
        # Error cases
        (
            {},
            True,
            "Invalid provider None.",
            400
        ),
        (
            {
                'provider': ''
            },
            True,
            "Invalid provider .",
            400
        ),
        (
            {
                'provider': 'dummy-provider'
            },
            True,
            "Invalid provider dummy-provider.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24
            },
            True,
            "Invalid cielo24 fidelity None.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
            },
            True,
            "Invalid cielo24 turnaround None.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
                'cielo24_turnaround': 'STANDARD',
                'video_source_language': 'en'
            },
            True,
            "Invalid languages [].",
            400
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PREMIUM',
                'cielo24_turnaround': 'STANDARD',
                'video_source_language': 'es'
            },
            True,
            "Unsupported source language es.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
                'cielo24_turnaround': 'STANDARD',
                'video_source_language': 'en',
                'preferred_languages': ['es', 'ur']
            },
            True,
            "Invalid languages ['es', 'ur'].",
            400
        ),
        (
            {
                'provider': TranscriptProvider.THREE_PLAY_MEDIA
            },
            True,
            "Invalid 3play turnaround None.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.THREE_PLAY_MEDIA,
                'three_play_turnaround': 'standard',
                'video_source_language': 'zh',
            },
            True,
            "Unsupported source language zh.",
            400
        ),
        (
            {
                'provider': TranscriptProvider.THREE_PLAY_MEDIA,
                'three_play_turnaround': 'standard',
                'video_source_language': 'es',
                'preferred_languages': ['es', 'ur']
            },
            True,
            "Invalid languages ['es', 'ur'].",
            400
        ),
        (
            {
                'provider': TranscriptProvider.THREE_PLAY_MEDIA,
                'three_play_turnaround': 'standard',
                'video_source_language': 'en',
                'preferred_languages': ['es', 'ur']
            },
            True,
            "Invalid languages ['es', 'ur'].",
            400
        ),
        # Success
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
                'cielo24_turnaround': 'STANDARD',
                'video_source_language': 'es',
                'preferred_languages': ['en']
            },
            True,
            '',
            200
        ),
        (
            {
                'provider': TranscriptProvider.THREE_PLAY_MEDIA,
                'three_play_turnaround': 'standard',
                'preferred_languages': ['en'],
                'video_source_language': 'en',
            },
            True,
            '',
            200
        )
    )
    @ddt.unpack
    def test_video_transcript(self, preferences, is_video_transcript_enabled, error_message, expected_status_code):
        """
        Tests that transcript handler works correctly.
        """
        video_transcript_url = self.get_url_for_course_key(self.course.id)
        preferences_data = {
            'provider': preferences.get('provider'),
            'cielo24_fidelity': preferences.get('cielo24_fidelity'),
            'cielo24_turnaround': preferences.get('cielo24_turnaround'),
            'three_play_turnaround': preferences.get('three_play_turnaround'),
            'preferred_languages': preferences.get('preferred_languages', []),
            'video_source_language': preferences.get('video_source_language'),
        }

        with patch(
            'openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled'
        ) as video_transcript_feature:
            video_transcript_feature.return_value = is_video_transcript_enabled
            response = self.client.post(
                video_transcript_url,
                json.dumps(preferences_data),
                content_type='application/json'
            )
        status_code = response.status_code
        response = json.loads(response.content.decode('utf-8')) if is_video_transcript_enabled else response

        self.assertEqual(status_code, expected_status_code)
        self.assertEqual(response.get('error', ''), error_message)

        # Remove modified and course_id fields from the response so as to check the expected transcript preferences.
        response.get('transcript_preferences', {}).pop('modified', None)
        response.get('transcript_preferences', {}).pop('course_id', None)
        expected_preferences = preferences_data if is_video_transcript_enabled and not error_message else {}
        self.assertDictEqual(response.get('transcript_preferences', {}), expected_preferences)

    def test_remove_transcript_preferences(self):
        """
        Test that transcript handler removes transcript preferences correctly.
        """
        # First add course wide transcript preferences.
        preferences = create_or_update_transcript_preferences(str(self.course.id))

        # Verify transcript preferences exist
        self.assertIsNotNone(preferences)

        response = self.client.delete(
            self.get_url_for_course_key(self.course.id),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 204)

        # Verify transcript preferences no loger exist
        preferences = get_transcript_preferences(str(self.course.id))
        self.assertIsNone(preferences)

    def test_remove_transcript_preferences_not_found(self):
        """
        Test that transcript handler works correctly even when no preferences are found.
        """
        course_id = 'course-v1:dummy+course+id'
        # Verify transcript preferences do not exist
        preferences = get_transcript_preferences(course_id)
        self.assertIsNone(preferences)

        response = self.client.delete(
            self.get_url_for_course_key(course_id),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 204)

        # Verify transcript preferences do not exist
        preferences = get_transcript_preferences(course_id)
        self.assertIsNone(preferences)

    @ddt.data(
        (
            None,
            False
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
                'cielo24_turnaround': 'STANDARD',
                'preferred_languages': ['en']
            },
            False
        ),
        (
            {
                'provider': TranscriptProvider.CIELO24,
                'cielo24_fidelity': 'PROFESSIONAL',
                'cielo24_turnaround': 'STANDARD',
                'preferred_languages': ['en']
            },
            True
        )
    )
    @ddt.unpack
    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.get_transcript_preferences')
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    def test_transcript_preferences_metadata(self, transcript_preferences, is_video_transcript_enabled,
                                             mock_boto3_resource, mock_transcript_preferences):
        """
        Tests that transcript preference metadata is only set if it is video transcript feature is enabled and
        transcript preferences are already stored in the system.
        """
        file_name = 'test-video.mp4'
        request_data = {'files': [{'file_name': file_name, 'content_type': 'video/mp4'}]}

        mock_transcript_preferences.return_value = transcript_preferences

        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource)

        # Track generate_presigned_url calls to inspect metadata
        presigned_url_calls = []

        def mock_generate_presigned_url(operation, Params=None, ExpiresIn=None):
            presigned_url_calls.append((operation, Params, ExpiresIn))
            file_name = Params['Metadata']['client_video_id']
            return f'http://example.com/url_{file_name}'

        mock_s3_client.generate_presigned_url.side_effect = mock_generate_presigned_url

        videos_handler_url = reverse_course_url('videos_handler', self.course.id)
        with patch(
            'openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled'
        ) as video_transcript_feature:
            video_transcript_feature.return_value = is_video_transcript_enabled
            response = self.client.post(videos_handler_url, json.dumps(request_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        # Ensure `transcript_preferences` was set up in metadata correctly if sent through request.
        if is_video_transcript_enabled and transcript_preferences:
            self.assertEqual(len(presigned_url_calls), 1)
            metadata = presigned_url_calls[0][1]['Metadata']
            self.assertIn('transcript_preferences', metadata)
            self.assertEqual(metadata['transcript_preferences'], json.dumps(transcript_preferences))
        else:
            # If conditions aren't met, verify transcript_preferences is not in metadata
            if presigned_url_calls:
                metadata = presigned_url_calls[0][1]['Metadata']
                self.assertNotIn('transcript_preferences', metadata)


@patch.dict("django.conf.settings.FEATURES", {"ENABLE_VIDEO_UPLOAD_PIPELINE": True})
@override_settings(VIDEO_UPLOAD_PIPELINE={"BUCKET": "test_bucket", "ROOT_PATH": "test_root"})
class VideoUrlsCsvTestCase(
    VideoUploadTestBase,
    VideoStudioAccessTestsMixin,
    VideoPipelineStudioAccessTestsMixin,
    CourseTestCase
):
    """Test cases for the CSV download endpoint for video uploads"""

    VIEW_NAME = "video_encodings_download"

    def setUp(self):
        super().setUp()
        VideoUploadConfig(profile_whitelist="profile1").save()

    def _check_csv_response(self, expected_profiles):
        """
        Check that the response is a valid CSV response containing rows
        corresponding to previous_uploads and including the expected profiles.
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            f"attachment; filename=\"{self.course.id.course}_video_urls.csv\""
        )
        response_content = b"".join(response.streaming_content)
        response_reader = StringIO(response_content.decode())
        reader = csv.DictReader(response_reader, dialect=csv.excel)
        self.assertEqual(
            reader.fieldnames,
            (
                ["Name", "Duration", "Date Added", "Video ID", "Status"] +
                [f"{profile} URL" for profile in expected_profiles]
            )
        )
        rows = list(reader)
        self.assertEqual(len(rows), len(self.previous_uploads))
        for i, row in enumerate(rows):
            response_video = dict(row.items())
            # Videos should be returned by creation date descending
            original_video = self.previous_uploads[-(i + 1)]
            client_video_id = original_video["client_video_id"]
            self.assertEqual(response_video["Name"], client_video_id)
            self.assertEqual(response_video["Duration"], str(original_video["duration"]))
            dateutil.parser.parse(response_video["Date Added"])
            self.assertEqual(response_video["Video ID"], original_video["edx_video_id"])
            self.assertEqual(response_video["Status"], convert_video_status(original_video))
            for profile in expected_profiles:
                response_profile_url = response_video[f"{profile} URL"]
                original_encoded_for_profile = next(
                    (
                        original_encoded
                        for original_encoded in original_video["encoded_videos"]
                        if original_encoded["profile"] == profile
                    ),
                    None
                )
                if original_encoded_for_profile:
                    original_encoded_for_profile_url = original_encoded_for_profile["url"]
                    self.assertEqual(response_profile_url, original_encoded_for_profile_url)
                else:
                    self.assertEqual(response_profile_url, "")

    def test_basic(self):
        self._check_csv_response(["profile1"])

    def test_profile_whitelist(self):
        VideoUploadConfig(profile_whitelist="profile1,profile2").save()
        self._check_csv_response(["profile1", "profile2"])

    def test_non_ascii_course(self):
        course = CourseFactory.create(
            number="nón-äscii",
            video_upload_pipeline={
                "course_video_upload_token": self.test_token,
            }
        )
        response = self.client.get(self.get_url_for_course_key(course.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            "attachment; filename*=utf-8''n%C3%B3n-%C3%A4scii_video_urls.csv"
        )


@ddt.ddt
class GetVideoFeaturesTestCase(
    CourseTestCase
):
    """Test cases for the get_video_features endpoint """
    def setUp(self):
        super().setUp()
        self.url = self.get_url_for_course_key()

    def get_url_for_course_key(self):
        """ Helper to generate a url for a course key """
        return reverse("video_features")

    def test_basic(self):
        """ Test for expected return keys """
        response = self.client.get(self.get_url_for_course_key())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json().keys()),
            {
                'videoSharingEnabled',
                'allowThumbnailUpload',
            }
        )

    @ddt.data(True, False)
    def test_video_share_enabled(self, is_enabled):
        """ Test the public video share flag """
        self._test_video_feature(
            PUBLIC_VIDEO_SHARE,
            'videoSharingEnabled',
            override_waffle_flag,
            is_enabled,
        )

    @ddt.data(True, False)
    def test_video_image_upload_enabled(self, is_enabled):
        """ Test the video image upload switch """
        self._test_video_feature(
            VIDEO_IMAGE_UPLOAD_ENABLED,
            'allowThumbnailUpload',
            override_waffle_switch,
            is_enabled,
        )

    def _test_video_feature(self, flag, key, override_fn, is_enabled):
        """ Test that setting a waffle flag or switch on or off will cause the expected result """
        with override_fn(flag, is_enabled):
            response = self.client.get(self.get_url_for_course_key())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[key], is_enabled)


class GetStorageBucketTestCase(TestCase):
    """ This test just check that connection works and returns the bucket.
    It does not involve any mocking and triggers errors if has any import issue.
    """
    @override_settings(AWS_ACCESS_KEY_ID='test_key_id', AWS_SECRET_ACCESS_KEY='test_secret')
    @override_settings(VIDEO_UPLOAD_PIPELINE={
        "VEM_S3_BUCKET": "vem_test_bucket", "BUCKET": "test_bucket", "ROOT_PATH": "test_root"
    })
    @patch('cms.djangoapps.contentstore.video_storage_handlers.boto3.resource')
    def test_storage_bucket(self, mock_boto3_resource):
        """ Test that storage service functions work correctly with boto3."""
        # Setup boto3 mocks
        mock_s3_client, mock_bucket, mock_s3_resource = setup_s3_mocks(mock_boto3_resource, 'vem_test_bucket')

        # Test storage_service_bucket function
        bucket = storage_service_bucket()
        self.assertEqual(bucket.name, 'vem_test_bucket')
        mock_s3_resource.Bucket.assert_called_once_with('vem_test_bucket')

        # Test storage_service_key function
        edx_video_id = 'dummy_video'
        key_name = storage_service_key(bucket, file_name=edx_video_id)
        expected_key = 'test_root/dummy_video'
        self.assertEqual(key_name, expected_key)

        # Test that we can generate presigned URL using the bucket's client
        mock_s3_client.generate_presigned_url.return_value = (
            'https://vem_test_bucket.s3.amazonaws.com:443/test_root/dummy_video?signature=test'
        )
        upload_url = mock_s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket.name,
                'Key': key_name,
                'ContentType': 'video/mp4'
            },
            ExpiresIn=KEY_EXPIRATION_IN_SECONDS
        )

        self.assertIn("vem_test_bucket.s3.amazonaws.com", upload_url)
        self.assertIn("test_root/dummy_video", upload_url)


class CourseYoutubeEdxVideoIds(ModuleStoreTestCase):
    """
    This test checks youtube videos in a course
    """
    VIEW_NAME = 'youtube_edx_video_ids'

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.course_with_no_youtube_videos = CourseFactory.create()
        self.store = modulestore()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password='Password1234')

    def get_url_for_course_key(self, course_key, kwargs=None):
        """Return video handler URL for the given course"""
        return reverse_course_url(self.VIEW_NAME, course_key, kwargs)  # lint-amnesty, pylint: disable=no-member

    def test_course_with_youtube_videos(self):
        course_key = self.course.id

        with self.store.bulk_operations(course_key):
            chapter_loc = self.store.create_child(
                self.user.id, self.course.location, 'chapter', 'test_chapter'
            ).location
            seq_loc = self.store.create_child(
                self.user.id, chapter_loc, 'sequential', 'test_seq'
            ).location
            vert_loc = self.store.create_child(self.user.id, seq_loc, 'vertical', 'test_vert').location
            self.store.create_child(
                self.user.id,
                vert_loc,
                'problem',
                'test_problem',
                fields={"data": "<problem>Test</problem>"}
            )
            self.store.create_child(
                self.user.id, vert_loc, 'video', fields={
                    "youtube_is_available": False,
                    "name": "sample_video",
                    "edx_video_id": "youtube_193_84709099",
                }
            )

            response = self.client.get(self.get_url_for_course_key(course_key))
            self.assertEqual(response.status_code, 200)

            edx_video_ids = json.loads(response.content.decode('utf-8'))['edx_video_ids']
            self.assertEqual(len(edx_video_ids), 1)

    def test_course_with_no_youtube_videos(self):
        course_key = self.course_with_no_youtube_videos.id

        with self.store.bulk_operations(course_key):
            chapter_loc = self.store.create_child(
                self.user.id, self.course_with_no_youtube_videos.location, 'chapter', 'test_chapter'
            ).location
            seq_loc = self.store.create_child(
                self.user.id, chapter_loc, 'sequential', 'test_seq'
            ).location
            vert_loc = self.store.create_child(self.user.id, seq_loc, 'vertical', 'test_vert').location
            self.store.create_child(
                self.user.id, vert_loc, 'problem', 'test_problem', fields={"data": "<problem>Test</problem>"}
            )
            self.store.create_child(
                self.user.id, vert_loc, 'video', fields={
                    "youtube_id_1_0": None,
                    "name": "sample_video",
                    "edx_video_id": "no_youtube_193_84709099",
                }
            )

            response = self.client.get(self.get_url_for_course_key(course_key))

            edx_video_ids = json.loads(response.content.decode('utf-8'))['edx_video_ids']
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(edx_video_ids), 0)
