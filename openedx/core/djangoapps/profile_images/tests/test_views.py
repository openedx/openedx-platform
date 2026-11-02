"""
Test cases for the HTTP endpoints of the profile image api.
"""
from contextlib import closing
from unittest import mock
from unittest.mock import patch

import pytest
import datetime  # lint-amnesty, pylint: disable=wrong-import-order
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.http import HttpResponse

import ddt
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.student.tests.tests import UserSettingsEventTestMixin
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    set_has_profile_image,
    get_profile_image_names,
    get_profile_image_storage,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ..images import create_profile_images, ImageValidationError
from ..views import LOG_MESSAGE_CREATE, LOG_MESSAGE_DELETE
from .helpers import make_image_file

TEST_PASSWORD = "test"
TEST_UPLOAD_DT = datetime.datetime(2002, 1, 9, 15, 43, 1, tzinfo=ZoneInfo("UTC"))
TEST_UPLOAD_DT2 = datetime.datetime(2003, 1, 9, 15, 43, 1, tzinfo=ZoneInfo("UTC"))


class ProfileImageEndpointMixin(UserSettingsEventTestMixin):
    """
    Base class / shared infrastructure for tests of profile_image "upload" and
    "remove" endpoints.
    """
    # subclasses should override this with the name of the view under test, as
    # per the urls.py configuration.
    _view_name = None

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password=TEST_PASSWORD)
        # Ensure that parental controls don't apply to this user
        self.user.profile.year_of_birth = 1980
        self.user.profile.save()
        self.url = reverse(self._view_name, kwargs={'username': self.user.username})
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.storage = get_profile_image_storage()
        self.table = 'auth_userprofile'
        # this assertion is made here as a sanity check because all tests
        # assume user.profile.has_profile_image is False by default
        assert not self.user.profile.has_profile_image

        # Reset the mock event tracker so that we're not considering the
        # initial profile creation events.
        self.reset_tracker()

    def tearDown(self):
        super().tearDown()
        for name in get_profile_image_names(self.user.username).values():
            self.storage.delete(name)

    def check_images(self, exist=True):
        """
        If exist is True, make sure the images physically exist in storage
        with correct sizes and formats.

        If exist is False, make sure none of the images exist.
        """
        for size, name in get_profile_image_names(self.user.username).items():
            if exist:
                assert self.storage.exists(name)
                with closing(Image.open(self.storage.path(name))) as img:
                    assert img.size == (size, size)
                    assert img.format == 'JPEG'
            else:
                assert not self.storage.exists(name)

    def check_response(self, response, expected_code, expected_developer_message=None, expected_user_message=None):
        """
        Make sure the response has the expected code, and if that isn't 204,
        optionally check the correctness of a developer-facing message.
        """
        assert expected_code == response.status_code
        if expected_code == 204:
            assert response.data is None
        else:
            if expected_developer_message is not None:
                assert response.data.get('developer_message') == expected_developer_message
            if expected_user_message is not None:
                assert response.data.get('user_message') == expected_user_message

    def check_has_profile_image(self, has_profile_image=True):
        """
        Make sure the value of self.user.profile.has_profile_image is what we
        expect.
        """
        # it's necessary to reload this model from the database since save()
        # would have been called on another instance.
        profile = self.user.profile.__class__.objects.get(user=self.user)
        assert profile.has_profile_image == has_profile_image

    def check_anonymous_request_rejected(self, method):
        """
        Make sure that the specified method rejects access by unauthorized users.
        """
        anonymous_client = APIClient()
        request_method = getattr(anonymous_client, method)
        response = request_method(self.url)
        self.check_response(response, 401)
        self.assert_no_events_were_emitted()


@skip_unless_lms
@mock.patch('openedx.core.djangoapps.profile_images.views.log')
class ProfileImageViewGeneralTestCase(ProfileImageEndpointMixin, APITestCase):
    """
    Tests for the profile image endpoint
    """
    _view_name = "accounts_profile_image_api"

    def test_unsupported_methods(self, mock_log):
        """
        Test that GET, PUT, and PATCH are not supported.
        """
        assert 405 == self.client.get(self.url).status_code
        assert 405 == self.client.put(self.url).status_code
        assert 405 == self.client.patch(self.url).status_code
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()


@ddt.ddt
@skip_unless_lms
@mock.patch('openedx.core.djangoapps.profile_images.views.log')
class ProfileImageViewPostTestCase(ProfileImageEndpointMixin, APITestCase):
    """
    Tests for the POST method of the profile_image api endpoint.
    """
    _view_name = "accounts_profile_image_api"

    # Use the patched version of the API client to workaround a unicode issue
    # with DRF 3.1 and Django 1.4.  Remove this after we upgrade Django past 1.4!

    def check_upload_event_emitted(self, old=None, new=TEST_UPLOAD_DT):
        """
        Make sure we emit a UserProfile event corresponding to the
        profile_image_uploaded_at field changing.
        """
        self.assert_user_setting_event_emitted(
            setting='profile_image_uploaded_at', old=old, new=new
        )

    def test_anonymous_access(self, mock_log):
        """
        Test that an anonymous client (not logged in) cannot call POST.
        """
        self.check_anonymous_request_rejected('post')
        assert not mock_log.info.called

    @ddt.data('.jpg', '.jpeg', '.jpg', '.jpeg', '.png', '.gif', '.GIF')
    @patch(
        'openedx.core.djangoapps.profile_images.views._make_upload_dt',
        side_effect=[TEST_UPLOAD_DT, TEST_UPLOAD_DT2],
    )
    def test_upload_self(self, extension, _mock_make_image_version, mock_log):
        """
        Test that an authenticated user can POST to their own upload endpoint.
        """
        with make_image_file(extension=extension) as image_file:
            response = self.client.post(self.url, {'file': image_file}, format='multipart')
            self.check_response(response, 204)
            self.check_images()
            self.check_has_profile_image()
        mock_log.info.assert_called_once_with(
            LOG_MESSAGE_CREATE,
            {'image_names': list(get_profile_image_names(self.user.username).values()), 'user_id': self.user.id}
        )
        self.check_upload_event_emitted()

        # Try another upload and make sure that a second event is emitted.
        with make_image_file() as image_file:
            response = self.client.post(self.url, {'file': image_file}, format='multipart')
            self.check_response(response, 204)

        self.check_upload_event_emitted(old=TEST_UPLOAD_DT, new=TEST_UPLOAD_DT2)

    @ddt.data(
        ('image/jpeg', '.jpg'),
        ('image/jpeg', '.jpeg'),
        ('image/pjpeg', '.jpg'),
        ('image/pjpeg', '.jpeg'),
        ('image/png', '.png'),
        ('image/gif', '.gif'),
        ('image/gif', '.GIF'),
    )
    @ddt.unpack
    @patch('openedx.core.djangoapps.profile_images.views._make_upload_dt', return_value=TEST_UPLOAD_DT)
    def test_upload_by_mimetype(self, content_type, extension, _mock_make_image_version, mock_log):
        """
        Test that a user can upload raw content with the appropriate mimetype
        """
        with make_image_file(extension=extension) as image_file:
            data = image_file.read()
            response = self.client.post(
                self.url,
                data,
                content_type=content_type,
                HTTP_CONTENT_DISPOSITION=f'attachment;filename=filename{extension}',
            )
            self.check_response(response, 204)
            self.check_images()
            self.check_has_profile_image()
        mock_log.info.assert_called_once_with(
            LOG_MESSAGE_CREATE,
            {'image_names': list(get_profile_image_names(self.user.username).values()), 'user_id': self.user.id}
        )
        self.check_upload_event_emitted()

    def test_upload_unsupported_mimetype(self, mock_log):
        """
        Test that uploading an unsupported image as raw content fails with an
        HTTP 415 Error.
        """
        with make_image_file() as image_file:
            data = image_file.read()
            response = self.client.post(
                self.url,
                data,
                content_type='image/tiff',
                HTTP_CONTENT_DISPOSITION='attachment;filename=filename.tiff',
            )
            self.check_response(response, 415)
            self.check_images(False)
            self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_upload_nonexistent_user(self, mock_log):
        """
        Test that an authenticated user who POSTs to a non-existent user's upload
        endpoint gets an indistinguishable 403.
        """
        nonexistent_user_url = reverse(self._view_name, kwargs={'username': 'nonexistent'})

        with make_image_file() as image_file:
            response = self.client.post(nonexistent_user_url, {'file': image_file}, format='multipart')
            self.check_response(response, 403)
        assert not mock_log.info.called

    def test_upload_other(self, mock_log):
        """
        Test that an authenticated user cannot POST to another user's upload
        endpoint.
        """
        different_user = UserFactory.create(password=TEST_PASSWORD)
        # Ignore UserProfileFactory creation events.
        self.reset_tracker()
        different_client = APIClient()
        different_client.login(username=different_user.username, password=TEST_PASSWORD)
        with make_image_file() as image_file:
            response = different_client.post(self.url, {'file': image_file}, format='multipart')
            self.check_response(response, 403)
            self.check_images(False)
            self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_upload_staff(self, mock_log):
        """
        Test that an authenticated staff cannot POST to another user's upload
        endpoint.
        """
        staff_user = UserFactory(is_staff=True, password=TEST_PASSWORD)
        # Ignore UserProfileFactory creation events.
        self.reset_tracker()
        staff_client = APIClient()
        staff_client.login(username=staff_user.username, password=TEST_PASSWORD)
        with make_image_file() as image_file:
            response = staff_client.post(self.url, {'file': image_file}, format='multipart')
            self.check_response(response, 403)
            self.check_images(False)
            self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_upload_missing_file(self, mock_log):
        """
        Test that omitting the file entirely from the POST results in HTTP 400.
        """
        response = self.client.post(self.url, {}, format='multipart')
        self.check_response(
            response, 400,
            expected_developer_message="No file provided for profile image",
            expected_user_message="No file provided for profile image",
        )
        self.check_images(False)
        self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_upload_not_a_file(self, mock_log):
        """
        Test that sending unexpected data that isn't a file results in HTTP
        400.
        """
        response = self.client.post(self.url, {'file': 'not a file'}, format='multipart')
        self.check_response(
            response, 400,
            expected_developer_message="No file provided for profile image",
            expected_user_message="No file provided for profile image",
        )
        self.check_images(False)
        self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_upload_validation(self, mock_log):
        """
        Test that when upload validation fails, the proper HTTP response and
        messages are returned.
        """
        with make_image_file() as image_file:
            with mock.patch(
                'openedx.core.djangoapps.profile_images.views.validate_uploaded_image',
                side_effect=ImageValidationError("test error message")
            ):
                response = self.client.post(self.url, {'file': image_file}, format='multipart')
                self.check_response(
                    response, 400,
                    expected_developer_message="test error message",
                    expected_user_message="test error message",
                )
                self.check_images(False)
                self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    @patch('PIL.Image.open')
    def test_upload_failure(self, image_open, mock_log):
        """
        Test that when upload validation fails, the proper HTTP response and
        messages are returned.
        """
        image_open.side_effect = [Exception("whoops"), None]
        with make_image_file() as image_file:
            with pytest.raises(Exception):
                self.client.post(self.url, {'file': image_file}, format='multipart')
            self.check_images(False)
            self.check_has_profile_image(False)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()


@skip_unless_lms
@mock.patch('openedx.core.djangoapps.profile_images.views.log')
class ProfileImageViewDeleteTestCase(ProfileImageEndpointMixin, APITestCase):
    """
    Tests for the DELETE method of the profile_image endpoint.
    """
    _view_name = "accounts_profile_image_api"

    def setUp(self):
        super().setUp()
        with make_image_file() as image_file:
            create_profile_images(image_file, get_profile_image_names(self.user.username))
            self.check_images()
            set_has_profile_image(self.user.username, True, TEST_UPLOAD_DT)
            # Ignore previous event
            self.reset_tracker()

    def check_remove_event_emitted(self):
        """
        Make sure we emit a UserProfile event corresponding to the
        profile_image_uploaded_at field changing.
        """
        self.assert_user_setting_event_emitted(
            setting='profile_image_uploaded_at', old=TEST_UPLOAD_DT, new=None
        )

    def test_anonymous_access(self, mock_log):
        """
        Test that an anonymous client (not logged in) cannot call DELETE.
        """
        self.check_anonymous_request_rejected('delete')
        assert not mock_log.info.called

    def test_remove_self(self, mock_log):
        """
        Test that an authenticated user can DELETE to remove their own profile
        images.
        """
        response = self.client.delete(self.url)
        self.check_response(response, 204)
        self.check_images(False)
        self.check_has_profile_image(False)
        mock_log.info.assert_called_once_with(
            LOG_MESSAGE_DELETE,
            {'image_names': list(get_profile_image_names(self.user.username).values()), 'user_id': self.user.id}
        )
        self.check_remove_event_emitted()

    def test_remove_other(self, mock_log):
        """
        Test that an authenticated user cannot DELETE to remove another user's
        profile images.
        """
        different_user = UserFactory.create(password=TEST_PASSWORD)
        # Ignore UserProfileFactory creation events.
        self.reset_tracker()
        different_client = APIClient()
        different_client.login(username=different_user.username, password=TEST_PASSWORD)
        response = different_client.delete(self.url)
        self.check_response(response, 403)
        self.check_images(True)  # thumbnails should remain intact.
        self.check_has_profile_image(True)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_remove_staff(self, mock_log):
        """
        Test that an authenticated staff user can DELETE to remove another user's
        profile images.
        """
        staff_user = UserFactory(is_staff=True, password=TEST_PASSWORD)
        staff_client = APIClient()
        staff_client.login(username=staff_user.username, password=TEST_PASSWORD)
        response = self.client.delete(self.url)
        self.check_response(response, 204)
        self.check_images(False)
        self.check_has_profile_image(False)
        mock_log.info.assert_called_once_with(
            LOG_MESSAGE_DELETE,
            {'image_names': list(get_profile_image_names(self.user.username).values()), 'user_id': self.user.id}
        )
        self.check_remove_event_emitted()

    @patch('common.djangoapps.student.models.user.UserProfile.save')
    def test_remove_failure(self, user_profile_save, mock_log):
        """
        Test that when remove validation fails, the proper HTTP response and
        messages are returned.
        """
        user_profile_save.side_effect = [Exception("whoops"), None]
        with pytest.raises(Exception):
            self.client.delete(self.url)
        self.check_images(True)  # thumbnails should remain intact.
        self.check_has_profile_image(True)
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()


class DeprecatedProfileImageTestMixin(ProfileImageEndpointMixin):
    """
    Actual tests for DeprecatedProfileImage.*TestCase classes defined here.

    Requires:
        self._view_name
        self._replacement_method
    """

    def test_unsupported_methods(self, mock_log):
        """
        Test that GET, PUT, PATCH, and DELETE are not supported.
        """
        assert 405 == self.client.get(self.url).status_code
        assert 405 == self.client.put(self.url).status_code
        assert 405 == self.client.patch(self.url).status_code
        assert 405 == self.client.delete(self.url).status_code
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()

    def test_post_calls_replacement_view_method(self, mock_log):
        """
        Test that calls to this view pass through the the new view.
        """
        with patch(self._replacement_method) as mock_method:
            mock_method.return_value = HttpResponse()
            self.client.post(self.url)
            assert mock_method.called
        assert not mock_log.info.called
        self.assert_no_events_were_emitted()


@skip_unless_lms
@mock.patch('openedx.core.djangoapps.profile_images.views.log')
class DeprecatedProfileImageUploadTestCase(DeprecatedProfileImageTestMixin, APITestCase):
    """
    Tests for the deprecated profile_image upload endpoint.

    Actual tests defined on DeprecatedProfileImageTestMixin
    """
    _view_name = 'profile_image_upload'
    _replacement_method = 'openedx.core.djangoapps.profile_images.views.ProfileImageView.post'


@skip_unless_lms
@mock.patch('openedx.core.djangoapps.profile_images.views.log')
class DeprecatedProfileImageRemoveTestCase(DeprecatedProfileImageTestMixin, APITestCase):
    """
    Tests for the deprecated profile_image remove endpoint.

    Actual tests defined on DeprecatedProfileImageTestMixin
    """
    _view_name = "profile_image_remove"
    _replacement_method = 'openedx.core.djangoapps.profile_images.views.ProfileImageView.delete'
