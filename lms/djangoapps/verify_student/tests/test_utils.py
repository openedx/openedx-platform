"""
Tests for verify_student utility functions.
"""


import unittest
from datetime import timedelta
from unittest import mock
from unittest.mock import patch

import ddt
from django.conf import settings
from django.utils import timezone
from pytest import mark

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification, SSOVerification
from lms.djangoapps.verify_student.utils import (
    most_recent_verification,
    submit_request_to_ss,
    verification_for_datetime,
)

FAKE_SETTINGS = {
    "DAYS_GOOD_FOR": 10,
}


@ddt.ddt
@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@mark.django_db
class TestVerifyStudentUtils(unittest.TestCase):
    """
    Tests for utility functions in verify_student.
    """

    def test_verification_for_datetime(self):
        user = UserFactory.create()
        now = timezone.now()

        # No attempts in the query set, so should return None
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(now, query)
        assert result is None

        # Should also return None if no deadline specified
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(None, query)
        assert result is None

        # Make an attempt
        attempt = SoftwareSecurePhotoVerification.objects.create(user=user)

        # Before the created date, should get no results
        before = attempt.created_at - timedelta(seconds=1)
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(before, query)
        assert result is None

        # Immediately after the created date, should get the attempt
        after_created = attempt.created_at + timedelta(seconds=1)
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(after_created, query)
        assert result == attempt

        # If no deadline specified, should return first available
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(None, query)
        assert result == attempt

        # Immediately before the expiration date, should get the attempt
        expiration = attempt.expiration_datetime + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])
        before_expiration = expiration - timedelta(seconds=1)
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(before_expiration, query)
        assert result == attempt

        # Immediately after the expiration date, should not get the attempt
        attempt.expiration_date = now - timedelta(seconds=1)
        attempt.save()
        after = now + timedelta(days=1)
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(after, query)
        assert result is None

        # Create a second attempt in the same window
        second_attempt = SoftwareSecurePhotoVerification.objects.create(user=user)

        # Now we should get the newer attempt
        deadline = second_attempt.created_at + timedelta(days=1)
        query = SoftwareSecurePhotoVerification.objects.filter(user=user)
        result = verification_for_datetime(deadline, query)
        assert result == second_attempt

    @ddt.data(
        (False, False, False, None, None),
        (True, False, False, None, 'photo'),
        (False, True, False, None, 'sso'),
        (False, False, True, None, 'manual'),
        (True, True, True, 'photo', 'sso'),
        (True, True, True, 'sso', 'photo'),
        (True, True, True, 'manual', 'photo')
    )
    @ddt.unpack
    def test_most_recent_verification(
            self,
            create_photo_verification,
            create_sso_verification,
            create_manual_verification,
            first_verification,
            expected_verification):

        user = UserFactory.create()
        photo_verification = None
        sso_verification = None
        manual_verification = None

        if not first_verification:
            if create_photo_verification:
                photo_verification = SoftwareSecurePhotoVerification.objects.create(user=user)
            if create_sso_verification:
                sso_verification = SSOVerification.objects.create(user=user)
            if create_manual_verification:
                manual_verification = ManualVerification.objects.create(user=user)
        elif first_verification == 'photo':
            photo_verification = SoftwareSecurePhotoVerification.objects.create(user=user)
            sso_verification = SSOVerification.objects.create(user=user)
        elif first_verification == 'sso':
            sso_verification = SSOVerification.objects.create(user=user)
            photo_verification = SoftwareSecurePhotoVerification.objects.create(user=user)
        else:
            #last first = manual expected = photo case adds some duplicates for complexity
            ManualVerification.objects.create(user=user)
            SoftwareSecurePhotoVerification.objects.create(user=user)
            manual_verification = ManualVerification.objects.create(user=user)
            photo_verification = SoftwareSecurePhotoVerification.objects.create(user=user)

        most_recent = most_recent_verification(
            (SoftwareSecurePhotoVerification.objects.all(),
             SSOVerification.objects.all(),
             ManualVerification.objects.all())
        )

        if not expected_verification:
            assert most_recent is None
        elif expected_verification == 'photo':
            assert most_recent == photo_verification
        elif expected_verification == 'sso':
            assert most_recent == sso_verification
        else:
            assert most_recent == manual_verification

    @mock.patch('lms.djangoapps.verify_student.utils.log')
    @mock.patch(
        'lms.djangoapps.verify_student.tasks.send_request_to_ss_for_user.delay', mock.Mock(side_effect=Exception('error'))  # lint-amnesty, pylint: disable=line-too-long
    )
    def test_submit_request_to_ss(self, mock_log):
        """Tests that we log appropriate information when celery task creation fails."""
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification.objects.create(user=user)
        attempt.mark_ready()
        submit_request_to_ss(user_verification=attempt, copy_id_photo_from=None)

        mock_log.error.assert_called_with(
            "Software Secure submit request %r failed, result: %s",
            user.username,
            'error'
        )
        assert attempt.status, SoftwareSecurePhotoVerification.STATUS.must_retry
