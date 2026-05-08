# pylint: disable=missing-module-docstring

import base64
from datetime import datetime, timedelta
from unittest import mock
from unittest.mock import patch

import ddt
import pytest
import requests.exceptions
import simplejson as json
from django.conf import settings
from django.utils.timezone import now
from freezegun import freeze_time

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import MockS3Boto3Mixin
from lms.djangoapps.verify_student.models import (
    ManualVerification,
    PhotoVerification,
    SoftwareSecurePhotoVerification,
    SSOVerification,
    VerificationAttempt,
    VerificationException,
)
from lms.djangoapps.verify_student.tests import TestVerificationBase
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)

FAKE_SETTINGS = {
    "SOFTWARE_SECURE": {
        "FACE_IMAGE_AES_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "API_ACCESS_KEY": "BBBBBBBBBBBBBBBBBBBB",
        "API_SECRET_KEY": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        "RSA_PUBLIC_KEY": """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu2fUn20ZQtDpa1TKeCA/
rDA2cEeFARjEr41AP6jqP/k3O7TeqFX6DgCBkxcjojRCs5IfE8TimBHtv/bcSx9o
7PANTq/62ZLM9xAMpfCcU6aAd4+CVqQkXSYjj5TUqamzDFBkp67US8IPmw7I2Gaa
tX8ErZ9D7ieOJ8/0hEiphHpCZh4TTgGuHgjon6vMV8THtq3AQMaAQ/y5R3V7Lezw
dyZCM9pBcvcH+60ma+nNg8GVGBAW/oLxILBtg+T3PuXSUvcu/r6lUFMHk55pU94d
9A/T8ySJm379qU24ligMEetPk1o9CUasdaI96xfXVDyFhrzrntAmdD+HYCSPOQHz
iwIDAQAB
-----END PUBLIC KEY-----""",
        "API_URL": "http://localhost/verify_student/fake_endpoint",
        "AWS_ACCESS_KEY": "FAKEACCESSKEY",
        "AWS_SECRET_KEY": "FAKESECRETKEY",
        "S3_BUCKET": "fake-bucket",
        "CERT_VERIFICATION_PATH": False,
        "STORAGE_CLASS": "storages.backends.s3boto3.S3Boto3Storage"
    },
    "DAYS_GOOD_FOR": 10
}


def mock_software_secure_post(url, headers=None, data=None, **kwargs):  # pylint: disable=unused-argument
    """
    Mocks our interface when we post to Software Secure. Does basic assertions
    on the fields we send over to make sure we're not missing headers or giving
    total garbage.
    """
    data_dict = json.loads(data)

    # Basic sanity checking on the keys
    EXPECTED_KEYS = [
        "EdX-ID", "ExpectedName", "PhotoID", "PhotoIDKey", "SendResponseTo",
        "UserPhoto", "UserPhotoKey",
    ]
    for key in EXPECTED_KEYS:
        assert data_dict.get(key)

    # The keys should be stored as Base64 strings, i.e. this should not explode
    data_dict["PhotoIDKey"] = base64.b64decode(data_dict["PhotoIDKey"])
    data_dict["UserPhotoKey"] = base64.b64decode(data_dict["UserPhotoKey"])

    response = requests.Response()
    response.status_code = 200

    return response


def mock_software_secure_post_error(url, headers=None, data=None, **kwargs):  # pylint: disable=unused-argument
    """
    Simulates what happens if our post to Software Secure is rejected, for
    whatever reason.
    """
    response = requests.Response()
    response.status_code = 400
    return response


def mock_software_secure_post_unavailable(url, headers=None, data=None, **kwargs):
    """Simulates a connection failure when we try to submit to Software Secure."""
    raise requests.exceptions.ConnectionError


# Lots of patching to stub in our own settings, and HTTP posting
@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post)
@ddt.ddt
class TestPhotoVerification(TestVerificationBase, MockS3Boto3Mixin, ModuleStoreTestCase):  # pylint: disable=missing-class-docstring

    def test_state_transitions(self):
        """
        Make sure we can't make unexpected status transitions.

        The status transitions we expect are::

                        → → → must_retry
                        ↑        ↑ ↓
            created → ready → submitted → approved
                                    ↓        ↑ ↓
                                    ↓ → →  denied
        """
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        assert attempt.status == PhotoVerification.STATUS.created

        # These should all fail because we're in the wrong starting state.
        pytest.raises(VerificationException, attempt.submit)
        pytest.raises(VerificationException, attempt.approve)
        pytest.raises(VerificationException, attempt.deny)
        pytest.raises(VerificationException, attempt.mark_must_retry)
        pytest.raises(VerificationException, attempt.mark_submit)

        # Now let's fill in some values so that we can pass the mark_ready() call
        attempt.mark_ready()
        assert attempt.status == PhotoVerification.STATUS.ready

        # ready (can't approve or deny unless it's "submitted")
        pytest.raises(VerificationException, attempt.approve)
        pytest.raises(VerificationException, attempt.deny)
        attempt.mark_must_retry()
        attempt.mark_submit()

        DENY_ERROR_MSG = '[{"photoIdReasons": ["Not provided"]}]'

        # must_retry
        attempt.status = PhotoVerification.STATUS.must_retry
        attempt.system_error("System error")
        attempt.mark_must_retry()  # no-op
        attempt.mark_submit()
        attempt.approve()

        attempt.status = PhotoVerification.STATUS.must_retry
        attempt.deny(DENY_ERROR_MSG)

        # submitted
        attempt.status = PhotoVerification.STATUS.submitted
        attempt.deny(DENY_ERROR_MSG)

        attempt.status = PhotoVerification.STATUS.submitted
        attempt.mark_must_retry()

        attempt.status = PhotoVerification.STATUS.submitted
        attempt.approve()

        # approved
        pytest.raises(VerificationException, attempt.submit)
        pytest.raises(VerificationException, attempt.mark_must_retry)
        pytest.raises(VerificationException, attempt.mark_submit)
        attempt.approve()  # no-op
        attempt.system_error("System error")  # no-op, something processed it without error
        attempt.deny(DENY_ERROR_MSG)

        # denied
        pytest.raises(VerificationException, attempt.submit)
        pytest.raises(VerificationException, attempt.mark_must_retry)
        pytest.raises(VerificationException, attempt.mark_submit)
        attempt.deny(DENY_ERROR_MSG)  # no-op
        attempt.system_error("System error")  # no-op, something processed it without error
        attempt.approve()

    def test_name_freezing(self):
        """
        You can change your name prior to marking a verification attempt ready,
        but changing your name afterwards should not affect the value in the
        in the attempt record. Basically, we want to always know what your name
        was when you submitted it.
        """
        user = UserFactory.create()
        user.profile.name = "Jack \u01B4"  # gratuious non-ASCII char to test encodings

        attempt = SoftwareSecurePhotoVerification(user=user)
        user.profile.name = "Clyde \u01B4"
        attempt.mark_ready()

        user.profile.name = "Rusty \u01B4"

        assert 'Clyde ƴ' == attempt.name

    def test_name_preset(self):
        """
        If a name was set when creating the photo verification
        (from name affirmation / verified name flow) it should not
        be overwritten by the profile name
        """
        user = UserFactory.create()
        user.profile.name = "Profile"

        preset_attempt = SoftwareSecurePhotoVerification(user=user)
        preset_attempt.name = "Preset"
        preset_attempt.mark_ready()
        assert "Preset" == preset_attempt.name

    def test_submissions(self):
        """Test that we set our status correctly after a submission."""
        # Basic case, things go well.
        attempt = self.create_upload_and_submit_attempt_for_user()
        assert attempt.status == PhotoVerification.STATUS.submitted

        # We post, but Software Secure doesn't like what we send for some reason
        with patch('lms.djangoapps.verify_student.tasks.requests.post', new=mock_software_secure_post_error):
            attempt = self.create_upload_and_submit_attempt_for_user()
            assert attempt.status == PhotoVerification.STATUS.must_retry

        # We try to post, but run into an error (in this case a network connection error)
        with patch('lms.djangoapps.verify_student.tasks.requests.post', new=mock_software_secure_post_unavailable):
            attempt = self.create_upload_and_submit_attempt_for_user()
            assert attempt.status == PhotoVerification.STATUS.must_retry

    @mock.patch.dict(settings.FEATURES, {'AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING': True})
    def test_submission_while_testing_flag_is_true(self):
        """ Test that a fake value is set for field 'photo_id_key' of user's
        initial verification when the feature flag 'AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING'
        is enabled.
        """
        attempt = self.create_upload_and_submit_attempt_for_user()
        assert attempt.photo_id_key == 'fake-photo-id-key'

    # pylint: disable=line-too-long
    def test_parse_error_msg_success(self):
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.status = PhotoVerification.STATUS.denied
        attempt.error_msg = '[{"userPhotoReasons": ["Face out of view"]}, {"photoIdReasons": ["Photo hidden/No photo", "ID name not provided"]}]'
        parsed_error_msg = attempt.parsed_error_msg()
        assert sorted(parsed_error_msg) == sorted(['id_image_missing_name', 'user_image_not_clear', 'id_image_not_clear'])

    @ddt.data(
        'Not Provided',
        '{"IdReasons": ["Not provided"]}',
        '[{"ïḋṚëäṡöṅṡ": ["Ⓝⓞⓣ ⓟⓡⓞⓥⓘⓓⓔⓓ "]}]',
    )
    def test_parse_error_msg_failure(self, msg):
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification.objects.create(user=user, status='denied', error_msg=msg)
        assert attempt.parsed_error_msg() == []

    def test_active_at_datetime(self):
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification.objects.create(user=user)
        self.verification_active_at_datetime(attempt)

    def test_initial_verification_for_user(self):
        """Test that method 'get_initial_verification' of model
        'SoftwareSecurePhotoVerification' always returns the initial
        verification with field 'photo_id_key' set against a user.
        """
        user = UserFactory.create()

        # No initial verification for the user
        result = SoftwareSecurePhotoVerification.get_initial_verification(user=user)
        assert result is None

        # Make an initial verification with 'photo_id_key'
        attempt = SoftwareSecurePhotoVerification(user=user, photo_id_key="dummy_photo_id_key")
        attempt.status = PhotoVerification.STATUS.approved
        attempt.save()

        # Check that method 'get_initial_verification' returns the correct
        # initial verification attempt
        first_result = SoftwareSecurePhotoVerification.get_initial_verification(user=user)
        assert first_result is not None

        # Now create a second verification without 'photo_id_key'
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.status = PhotoVerification.STATUS.submitted
        attempt.save()

        # Test method 'get_initial_verification' still returns the correct
        # initial verification attempt which have 'photo_id_key' set
        second_result = SoftwareSecurePhotoVerification.get_initial_verification(user=user)
        assert second_result is not None
        assert second_result == first_result

        # Test method 'get_initial_verification' returns None after expiration
        expired_future = now() + timedelta(days=(FAKE_SETTINGS['DAYS_GOOD_FOR'] + 1))
        with freeze_time(expired_future):
            third_result = SoftwareSecurePhotoVerification.get_initial_verification(user)
            assert third_result is None

        # Test method 'get_initial_verification' returns correct attempt after system expiration,
        # but within earliest allowed override.
        expired_future = now() + timedelta(days=(FAKE_SETTINGS['DAYS_GOOD_FOR'] + 1))
        earliest_allowed = now() - timedelta(days=1)
        with freeze_time(expired_future):
            fourth_result = SoftwareSecurePhotoVerification.get_initial_verification(user, earliest_allowed)
            assert fourth_result is not None
            assert fourth_result == first_result

    def test_retire_user(self):
        """
        Retire user with record(s) in table
        """
        user = UserFactory.create()
        user.profile.name = "Enrique"
        attempt = SoftwareSecurePhotoVerification(user=user)

        # Populate Record
        attempt.mark_ready()
        attempt.status = PhotoVerification.STATUS.submitted
        attempt.photo_id_image_url = "https://example.com/test/image/img.jpg"
        attempt.face_image_url = "https://example.com/test/face/img.jpg"
        attempt.photo_id_key = 'there_was_an_attempt'
        attempt.approve()

        # Validate data before retirement
        assert attempt.name == user.profile.name
        assert attempt.photo_id_image_url == 'https://example.com/test/image/img.jpg'
        assert attempt.face_image_url == 'https://example.com/test/face/img.jpg'
        assert attempt.photo_id_key == 'there_was_an_attempt'

        # Retire User
        attempt_again = SoftwareSecurePhotoVerification(user=user)
        assert attempt_again.retire_user(user_id=user.id)

        # Validate data after retirement
        assert attempt_again.name == ''
        assert attempt_again.face_image_url == ''
        assert attempt_again.photo_id_image_url == ''
        assert attempt_again.photo_id_key == ''

    def test_retire_nonuser(self):
        """
        Attempt to Retire User with no records in table
        """
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)

        # User with no records in table
        assert not attempt.retire_user(user_id=user.id)

        # No user
        assert not attempt.retire_user(user_id=47)

    def test_get_recent_verification(self):
        """Test that method 'get_recent_verification' of model
        'SoftwareSecurePhotoVerification' always returns the most
        recent 'approved' verification based on updated_at set
        against a user.
        """
        user = UserFactory.create()
        attempt = None

        for _ in range(2):
            # Make an approved verification
            attempt = SoftwareSecurePhotoVerification(user=user)
            attempt.status = PhotoVerification.STATUS.approved
            attempt.expiration_date = datetime.now()
            attempt.save()

        # Test method 'get_recent_verification' returns the most recent
        # verification attempt based on updated_at
        recent_verification = SoftwareSecurePhotoVerification.get_recent_verification(user=user)
        assert recent_verification is not None
        assert recent_verification.id == attempt.id

    def test_no_approved_verification(self):
        """Test that method 'get_recent_verification' of model
        'SoftwareSecurePhotoVerification' returns None if no
        'approved' verification are found
        """
        user = UserFactory.create()
        SoftwareSecurePhotoVerification(user=user)

        result = SoftwareSecurePhotoVerification.get_recent_verification(user=user)
        assert result is None

    def test_update_expiry_email_date_for_user(self):
        """Test that method update_expiry_email_date_for_user of
        model 'SoftwareSecurePhotoVerification' set expiry_email_date
        if the most recent approved verification is expired.
        """
        email_config = getattr(settings, 'VERIFICATION_EXPIRY_EMAIL', {'DAYS_RANGE': 1, 'RESEND_DAYS': 15})
        user = UserFactory.create()
        verification = SoftwareSecurePhotoVerification(user=user)
        verification.expiration_date = now() - timedelta(days=FAKE_SETTINGS['DAYS_GOOD_FOR'])
        verification.status = PhotoVerification.STATUS.approved
        verification.save()

        assert verification.expiry_email_date is None

        SoftwareSecurePhotoVerification.update_expiry_email_date_for_user(user, email_config)
        result = SoftwareSecurePhotoVerification.get_recent_verification(user=user)

        assert result.expiry_email_date is not None

    def test_expiration_date_null(self):
        """
        Test if the `expiration_date` field is null, `expiration_datetime` returns a
        default expiration date based on the time the entry was created.
        """
        user = UserFactory.create()
        verification = SoftwareSecurePhotoVerification(user=user)
        verification.expiration_date = None
        verification.save()

        assert verification.expiration_datetime == (verification.created_at + timedelta(days=FAKE_SETTINGS['DAYS_GOOD_FOR']))

    def test_get_verification_from_receipt(self):
        result = SoftwareSecurePhotoVerification.get_verification_from_receipt('')
        assert result is None

        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.status = PhotoVerification.STATUS.submitted
        attempt.save()
        receipt_id = attempt.receipt_id
        result = SoftwareSecurePhotoVerification.get_verification_from_receipt(receipt_id)
        assert result is not None


class SSOVerificationTest(TestVerificationBase):
    """
    Tests for the SSOVerification model
    """

    def test_active_at_datetime(self):
        user = UserFactory.create()
        attempt = SSOVerification.objects.create(user=user)
        self.verification_active_at_datetime(attempt)


class ManualVerificationTest(TestVerificationBase):
    """
    Tests for the ManualVerification model
    """

    def test_active_at_datetime(self):
        user = UserFactory.create()
        verification = ManualVerification.objects.create(user=user)
        self.verification_active_at_datetime(verification)


class VerificationAttemptTest(TestVerificationBase):
    """
    Tests for the VerificationAttempt model
    """

    def test_active_at_datetime(self):
        user = UserFactory.create()
        attempt = VerificationAttempt.objects.create(user=user)
        self.verification_active_at_datetime(attempt)
