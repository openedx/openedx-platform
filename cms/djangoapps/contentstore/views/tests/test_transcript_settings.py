# lint-amnesty, pylint: disable=missing-module-docstring


import json
from io import StringIO
from unittest.mock import ANY, Mock, patch

import ddt
from django.test.client import Client
from django.test.testcases import TestCase
from django.urls import reverse
from edxval import api

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.transcript_storage_handlers import (
    TranscriptionProviderErrorType,
    validate_transcript_credentials,
)
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.student.roles import CourseStaffRole
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file


@ddt.ddt
@patch(
    'openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled',
    Mock(return_value=True)
)
class TranscriptCredentialsTest(CourseTestCase):
    """
    Tests for transcript credentials handler.
    """
    VIEW_NAME = 'transcript_credentials_handler'

    def get_url_for_course_key(self, course_id):
        return reverse_course_url(self.VIEW_NAME, course_id)

    def test_302_with_anonymous_user(self):
        """
        Verify that redirection happens in case of unauthorized request.
        """
        self.client.logout()
        transcript_credentials_url = self.get_url_for_course_key(self.course.id)
        response = self.client.post(transcript_credentials_url, content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods include POST.
        """
        transcript_credentials_url = self.get_url_for_course_key(self.course.id)
        response = self.client.get(transcript_credentials_url, content_type='application/json')
        self.assertEqual(response.status_code, 405)

    def test_404_with_feature_disabled(self):
        """
        Verify that 404 is returned if the corresponding feature is disabled.
        """
        transcript_credentials_url = self.get_url_for_course_key(self.course.id)
        with patch('openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled') as feature:
            feature.return_value = False
            response = self.client.post(transcript_credentials_url, content_type='application/json')
            self.assertEqual(response.status_code, 404)

    @ddt.data(
        (
            {
                'provider': 'abc_provider',
                'api_key': '1234'
            },
            ({}, None),
            400,
            '{\n  "error": "Invalid Provider abc_provider."\n}'
        ),
        (
            {
                'provider': '3PlayMedia',
                'api_key': '11111',
                'api_secret_key': '44444'
            },
            ({'error_type': TranscriptionProviderErrorType.INVALID_CREDENTIALS}, False),
            400,
            '{\n  "error": "The information you entered is incorrect."\n}'
        ),
        (
            {
                'provider': 'Cielo24',
                'api_key': '12345',
                'username': 'test_user'
            },
            ({}, True),
            200,
            ''
        )
    )
    @ddt.unpack
    @patch('cms.djangoapps.contentstore.transcript_storage_handlers.update_3rd_party_transcription_service_credentials')
    def test_transcript_credentials_handler(self, request_payload, update_credentials_response, expected_status_code,
                                            expected_response, mock_update_credentials):
        """
        Tests that transcript credentials handler works as expected.
        """
        mock_update_credentials.return_value = update_credentials_response
        transcript_credentials_url = self.get_url_for_course_key(self.course.id)
        response = self.client.post(
            transcript_credentials_url,
            data=json.dumps(request_payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, expected_status_code)
        self.assertEqual(response.content.decode('utf-8'), expected_response)


@ddt.ddt
class TranscriptCredentialsValidationTest(TestCase):
    """
    Tests for credentials validations.
    """

    @ddt.data(
        (
            'ABC',
            {
                'username': 'test_user',
                'password': 'test_pass'
            },
            'Invalid Provider ABC.',
            {}
        ),
        (
            'Cielo24',
            {
                'username': 'test_user'
            },
            'api_key must be specified.',
            {}
        ),
        (
            'Cielo24',
            {
                'username': 'test_user',
                'api_key': 'test_api_key',
                'extra_param': 'extra_value'
            },
            '',
            {
                'username': 'test_user',
                'api_key': 'test_api_key'
            }
        ),
        (
            '3PlayMedia',
            {
                'username': 'test_user'
            },
            'api_key and api_secret_key must be specified.',
            {}
        ),
        (
            '3PlayMedia',
            {
                'api_key': 'test_key',
                'api_secret_key': 'test_secret',
                'extra_param': 'extra_value'
            },
            '',
            {
                'api_key': 'test_key',
                'api_secret_key': 'test_secret'
            }
        ),

    )
    @ddt.unpack
    def test_invalid_credentials(self, provider, credentials, expected_error_message, expected_validated_credentials):
        """
        Test validation with invalid transcript credentials.
        """
        error_message, validated_credentials = validate_transcript_credentials(provider, **credentials)
        # Assert the results.
        self.assertEqual(error_message, expected_error_message)
        self.assertDictEqual(validated_credentials, expected_validated_credentials)


@ddt.ddt
class TranscriptDownloadTest(CourseTestCase):
    """
    Tests for transcript download handler.
    """

    @property
    def view_url(self):
        """
        Returns url for this view
        """
        return reverse('transcript_download_handler')

    def test_302_with_anonymous_user(self):
        """
        Verify that redirection happens in case of unauthorized request.
        """
        self.client.logout()
        response = self.client.get(self.view_url, content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods include GET.
        """
        response = self.client.post(self.view_url, content_type='application/json')
        self.assertEqual(response.status_code, 405)

    @patch('cms.djangoapps.contentstore.transcript_storage_handlers.get_video_transcript_data')
    def test_transcript_download_handler(self, mock_get_video_transcript_data):
        """
        Tests that transcript download handler works as expected.
        """
        mock_get_video_transcript_data.return_value = {
            'content': json.dumps({
                "start": [10],
                "end": [100],
                "text": ["Hi, welcome to Edx."],
            }),
            'file_name': 'edx.sjson'
        }

        # Make request to transcript download handler
        response = self.client.get(
            self.view_url,
            data={
                'edx_video_id': '123',
                'language_code': 'en'
            },
            content_type='application/json'
        )

        # Expected response
        expected_content = '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
        expected_headers = {
            'Content-Disposition': 'attachment; filename="edx.srt"',
            'Content-Language': 'en',
            'Content-Type': 'application/x-subrip; charset=utf-8'
        }

        # Assert the actual response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), expected_content)
        for attribute, value in expected_headers.items():
            self.assertEqual(response.get(attribute), value)

    @ddt.data(
        (
            {},
            'The following parameters are required: edx_video_id, language_code.'
        ),
        (
            {'edx_video_id': '123'},
            'The following parameters are required: language_code.'
        ),
        (
            {'language_code': 'en'},
            'The following parameters are required: edx_video_id.'
        ),
    )
    @ddt.unpack
    def test_transcript_download_handler_missing_attrs(self, request_payload, expected_error_message):
        """
        Tests that transcript download handler with missing attributes.
        """
        # Make request to transcript download handler
        response = self.client.get(self.view_url, data=request_payload)
        # Assert the response
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content.decode('utf-8'))['error'], expected_error_message)


@ddt.ddt
class TranscriptUploadTest(CourseTestCase):
    """
    Tests for transcript upload handler.
    """
    @property
    def view_url(self):
        """
        Returns url for this view
        """
        return reverse('transcript_upload_handler')

    def test_302_with_anonymous_user(self):
        """
        Verify that redirection happens in case of unauthorized request.
        """
        self.client.logout()
        response = self.client.post(self.view_url, content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods include POST.
        """
        response = self.client.get(self.view_url, content_type='application/json')
        self.assertEqual(response.status_code, 405)

    @patch('cms.djangoapps.contentstore.transcript_storage_handlers.create_or_update_video_transcript')
    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler(self, mock_create_or_update_video_transcript):
        """
        Tests that transcript upload handler works as expected.
        """
        transcript_file_stream = StringIO('0\n00:00:00,010 --> 00:00:00,100\nПривіт, edX вітає вас.\n\n')
        # Make request to transcript upload handler
        response = self.client.post(
            self.view_url,
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'es',
                'file': transcript_file_stream,
            },
            format='multipart'
        )

        self.assertEqual(response.status_code, 201)
        mock_create_or_update_video_transcript.assert_called_with(
            video_id='123',
            language_code='en',
            metadata={
                'language_code': 'es',
                'file_format': 'sjson',
                'provider': 'Custom'
            },
            file_data=ANY,
        )

    @ddt.data(
        (
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'en',
            },
            'A transcript file is required.'
        ),
        (
            {
                'language_code': 'en',
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id, new_language_code.'
        ),
        (
            {
                'language_code': 'en',
                'new_language_code': 'en',
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id.'
        ),
        (
            {
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id, language_code, new_language_code.'
        )
    )
    @ddt.unpack
    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_missing_attrs(self, request_payload, expected_error_message):
        """
        Tests the transcript upload handler when the required attributes are missing.
        """
        # Make request to transcript upload handler
        response = self.client.post(self.view_url, request_payload, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content.decode('utf-8'))['error'], expected_error_message)

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en', 'es'])
    )
    def test_transcript_upload_handler_existing_transcript(self):
        """
        Tests that upload handler do not update transcript's language if a transcript
        with the same language already present for an edx_video_id.
        """
        # Make request to transcript upload handler
        request_payload = {
            'edx_video_id': '1234',
            'language_code': 'en',
            'new_language_code': 'es'
        }
        response = self.client.post(self.view_url, request_payload, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode('utf-8'))['error'],
            'A transcript with the "es" language code already exists.'
        )

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_with_image(self):
        """
        Tests the transcript upload handler with an image file.
        """
        with make_image_file() as image_file:
            # Make request to transcript upload handler
            response = self.client.post(
                self.view_url,
                {
                    'edx_video_id': '123',
                    'language_code': 'en',
                    'new_language_code': 'es',
                    'file': image_file,
                },
                format='multipart'
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                json.loads(response.content.decode('utf-8'))['error'],
                'There is a problem with this transcript file. Try to upload a different file.'
            )

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_with_invalid_transcript(self):
        """
        Tests the transcript upload handler with an invalid transcript file.
        """
        transcript_file_stream = StringIO('An invalid transcript SubRip file content')
        # Make request to transcript upload handler
        response = self.client.post(
            self.view_url,
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'es',
                'file': transcript_file_stream,
            },
            format='multipart'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode('utf-8'))['error'],
            'There is a problem with this transcript file. Try to upload a different file.'
        )


@ddt.ddt
class TranscriptDeleteTest(CourseTestCase):
    """
    Tests for transcript deletion handler.
    """
    VIEW_NAME = 'transcript_delete_handler'

    def get_url_for_course_key(self, course_id, **kwargs):
        return reverse_course_url(self.VIEW_NAME, course_id, kwargs)

    def test_302_with_anonymous_user(self):
        """
        Verify that redirection happens in case of unauthorized request.
        """
        self.client.logout()
        transcript_delete_url = self.get_url_for_course_key(self.course.id, edx_video_id='test_id', language_code='en')
        response = self.client.delete(transcript_delete_url)
        self.assertEqual(response.status_code, 302)

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods include DELETE.
        """
        transcript_delete_url = self.get_url_for_course_key(self.course.id, edx_video_id='test_id', language_code='en')
        response = self.client.post(transcript_delete_url)
        self.assertEqual(response.status_code, 405)

    def test_404_with_non_staff_user(self):
        """
        Verify that 404 is returned if the user doesn't have studio write access.
        """
        # Making sure that user is not a staff / course's staff.
        self.user.is_staff = False
        self.user.save()

        # Assert the user's role
        self.assertFalse(self.user.is_staff)
        self.assertFalse(CourseStaffRole(self.course.id).has_user(self.user))

        # Now, Make request to deletion handler
        transcript_delete_url = self.get_url_for_course_key(self.course.id, edx_video_id='test_id', language_code='en')
        response = self.client.delete(transcript_delete_url)
        self.assertEqual(response.status_code, 404)

    @ddt.data(
        {
            'is_staff': True,
            'is_course_staff': True
        },
        {
            'is_staff': False,
            'is_course_staff': True
        },
        {
            'is_staff': True,
            'is_course_staff': False
        },
    )
    @ddt.unpack
    def test_transcript_delete_handler(self, is_staff, is_course_staff):
        """
        Tests that transcript delete handler works as expected with combinations of staff and course's staff.
        """
        # Setup user's roles
        self.user.is_staff = is_staff
        self.user.save()
        course_staff_role = CourseStaffRole(self.course.id)
        if is_course_staff:
            course_staff_role.add_users(self.user)
        else:
            course_staff_role.remove_users(self.user)

        # Assert the user role
        self.assertEqual(self.user.is_staff, is_staff)
        self.assertEqual(CourseStaffRole(self.course.id).has_user(self.user), is_course_staff)

        video_id, language_code = '1234', 'en'
        # Create a real transcript in VAL.
        api.create_or_update_video_transcript(
            video_id=video_id,
            language_code=language_code,
            metadata={'file_format': 'srt'}
        )

        # Make request to transcript deletion handler
        response = self.client.delete(self.get_url_for_course_key(
            self.course.id,
            edx_video_id=video_id,
            language_code=language_code
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(api.get_video_transcript_data(video_id=video_id, language_code=language_code))


@ddt.ddt
class TranscriptUploadApiTest(CourseTestCase):
    """
    Tests for transcript upload handler.
    """
    def setUp(self):
        super().setUp()
        jwt_headers = {
            'HTTP_AUTHORIZATION': 'JWT ' + create_jwt_for_user(self.user)
        }
        self.client = Client(**jwt_headers)

    @property
    def view_url(self):
        """
        Returns url for this view
        """
        return reverse('transcript_upload_api')

    def test_401_without_authentication(self):
        """
        Verify that redirection happens in case of an unauthenticated request.
        """
        response = self.client.post(self.view_url, content_type='application/json', HTTP_AUTHORIZATION='')
        self.assertEqual(response.status_code, 401)

    def test_405_with_not_allowed_request_method(self):
        """
        Verify that 405 is returned in case of not-allowed request methods.
        Allowed request methods include POST.
        """
        response = self.client.get(self.view_url, content_type='application/json')
        self.assertEqual(response.status_code, 405)

    @patch('cms.djangoapps.contentstore.transcript_storage_handlers.create_or_update_video_transcript')
    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler(self, mock_create_or_update_video_transcript):
        """
        Tests that transcript upload handler works as expected.
        """
        transcript_file_stream = StringIO('0\n00:00:00,010 --> 00:00:00,100\nПривіт, edX вітає вас.\n\n')
        # Make request to transcript upload handler
        response = self.client.post(
            self.view_url,
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'es',
                'file': transcript_file_stream,
            },
            format='multipart'
        )

        self.assertEqual(response.status_code, 201)
        mock_create_or_update_video_transcript.assert_called_with(
            video_id='123',
            language_code='en',
            metadata={
                'language_code': 'es',
                'file_format': 'sjson',
                'provider': 'Custom'
            },
            file_data=ANY,
        )

    @ddt.data(
        (
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'en',
            },
            'A transcript file is required.'
        ),
        (
            {
                'language_code': 'en',
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id, new_language_code.'
        ),
        (
            {
                'language_code': 'en',
                'new_language_code': 'en',
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id.'
        ),
        (
            {
                'file': '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
            },
            'The following parameters are required: edx_video_id, language_code, new_language_code.'
        )
    )
    @ddt.unpack
    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_missing_attrs(self, request_payload, expected_error_message):
        """
        Tests the transcript upload handler when the required attributes are missing.
        """
        # Make request to transcript upload handler
        response = self.client.post(self.view_url, request_payload, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content.decode('utf-8'))['error'], expected_error_message)

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en', 'es'])
    )
    def test_transcript_upload_handler_existing_transcript(self):
        """
        Tests that upload handler do not update transcript's language if a transcript
        with the same language already present for an edx_video_id.
        """
        # Make request to transcript upload handler
        request_payload = {
            'edx_video_id': '1234',
            'language_code': 'en',
            'new_language_code': 'es'
        }
        response = self.client.post(self.view_url, request_payload, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode('utf-8'))['error'],
            'A transcript with the "es" language code already exists.'
        )

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_with_image(self):
        """
        Tests the transcript upload handler with an image file.
        """
        with make_image_file() as image_file:
            # Make request to transcript upload handler
            response = self.client.post(
                self.view_url,
                {
                    'edx_video_id': '123',
                    'language_code': 'en',
                    'new_language_code': 'es',
                    'file': image_file,
                },
                format='multipart'
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                json.loads(response.content.decode('utf-8'))['error'],
                'There is a problem with this transcript file. Try to upload a different file.'
            )

    @patch(
        'cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages',
        Mock(return_value=['en']),
    )
    def test_transcript_upload_handler_with_invalid_transcript(self):
        """
        Tests the transcript upload handler with an invalid transcript file.
        """
        transcript_file_stream = StringIO('An invalid transcript SubRip file content')
        # Make request to transcript upload handler
        response = self.client.post(
            self.view_url,
            {
                'edx_video_id': '123',
                'language_code': 'en',
                'new_language_code': 'es',
                'file': transcript_file_stream,
            },
            format='multipart'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode('utf-8'))['error'],
            'There is a problem with this transcript file. Try to upload a different file.'
        )
