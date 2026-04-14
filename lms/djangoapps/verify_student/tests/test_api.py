"""
Tests of API module.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import ddt
from django.conf import settings
from django.core import mail
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.api import (
    create_verification_attempt,
    send_approval_email,
    update_verification_attempt,
)
from lms.djangoapps.verify_student.exceptions import VerificationAttemptInvalidStatus
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, VerificationAttempt
from lms.djangoapps.verify_student.statuses import VerificationAttemptStatus


@ddt.ddt
class TestSendApprovalEmail(TestCase):
    """
    Test cases for the send_approval_email API method.
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.attempt = SoftwareSecurePhotoVerification(
            status="submitted",
            user=self.user
        )
        self.attempt.save()

    def _assert_verification_approved_email(self, expiration_date):
        """Check that a verification approved email was sent."""
        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.subject == 'Your édX ID verification was approved!'
        assert 'Your édX ID verification photos have been approved' in email.body
        assert expiration_date.strftime("%m/%d/%Y") in email.body

    @ddt.data(True, False)
    def test_send_approval(self, use_ace):
        with patch.dict(settings.VERIFY_STUDENT, {'USE_DJANGO_MAIL': use_ace}):
            send_approval_email(self.attempt)
            self._assert_verification_approved_email(self.attempt.expiration_datetime)


@ddt.ddt
class CreateVerificationAttempt(TestCase):
    """
    Test cases for the create_verification_attempt API method.
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.attempt = VerificationAttempt(
            user=self.user,
            name='Tester McTest',
            status=VerificationAttemptStatus.CREATED,
            expiration_datetime=datetime(2024, 12, 31, tzinfo=timezone.utc)  # noqa: UP017
        )
        self.attempt.save()

    @patch('lms.djangoapps.verify_student.api.emit_idv_attempt_created_event')
    def test_create_verification_attempt(self, mock_created_event):
        expected_id = 2
        self.assertEqual(  # noqa: PT009
            create_verification_attempt(
                user=self.user,
                name='Tester McTest',
                status=VerificationAttemptStatus.CREATED,
                expiration_datetime=datetime(2024, 12, 31, tzinfo=timezone.utc)  # noqa: UP017
            ),
            expected_id
        )
        verification_attempt = VerificationAttempt.objects.get(id=expected_id)

        self.assertEqual(verification_attempt.user, self.user)  # noqa: PT009
        self.assertEqual(verification_attempt.name, 'Tester McTest')  # noqa: PT009
        self.assertEqual(verification_attempt.status, VerificationAttemptStatus.CREATED)  # noqa: PT009
        self.assertEqual(verification_attempt.expiration_datetime, datetime(2024, 12, 31, tzinfo=timezone.utc))  # noqa: PT009, UP017  # pylint: disable=line-too-long
        mock_created_event.assert_called_with(
            attempt_id=verification_attempt.id,
            user=self.user,
            status=VerificationAttemptStatus.CREATED,
            name='Tester McTest',
            expiration_date=datetime(2024, 12, 31, tzinfo=timezone.utc),  # noqa: UP017
        )

    def test_create_verification_attempt_no_expiration_datetime(self):
        expected_id = 2
        self.assertEqual(  # noqa: PT009
            create_verification_attempt(
                user=self.user,
                name='Tester McTest',
                status=VerificationAttemptStatus.CREATED,
            ),
            expected_id
        )
        verification_attempt = VerificationAttempt.objects.get(id=expected_id)

        self.assertEqual(verification_attempt.user, self.user)  # noqa: PT009
        self.assertEqual(verification_attempt.name, 'Tester McTest')  # noqa: PT009
        self.assertEqual(verification_attempt.status, VerificationAttemptStatus.CREATED)  # noqa: PT009
        self.assertEqual(verification_attempt.expiration_datetime, None)  # noqa: PT009


@ddt.ddt
class UpdateVerificationAttempt(TestCase):
    """
    Test cases for the update_verification_attempt API method.
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.attempt = VerificationAttempt(
            user=self.user,
            name='Tester McTest',
            status=VerificationAttemptStatus.CREATED,
            expiration_datetime=datetime(2024, 12, 31, tzinfo=timezone.utc)  # noqa: UP017
        )
        self.attempt.save()

    @ddt.data(
        ('Tester McTest', VerificationAttemptStatus.PENDING, datetime(2024, 12, 31, tzinfo=timezone.utc)),  # noqa: UP017  # pylint: disable=line-too-long
        ('Tester McTest2', VerificationAttemptStatus.APPROVED, datetime(2025, 12, 31, tzinfo=timezone.utc)),  # noqa: UP017  # pylint: disable=line-too-long
        ('Tester McTest3', VerificationAttemptStatus.DENIED, datetime(2026, 12, 31, tzinfo=timezone.utc)),  # noqa: UP017  # pylint: disable=line-too-long
    )
    @ddt.unpack
    @patch('lms.djangoapps.verify_student.api.emit_idv_attempt_pending_event')
    @patch('lms.djangoapps.verify_student.api.emit_idv_attempt_approved_event')
    @patch('lms.djangoapps.verify_student.api.emit_idv_attempt_denied_event')
    def test_update_verification_attempt(
        self,
        name,
        status,
        expiration_datetime,
        mock_denied_event,
        mock_approved_event,
        mock_pending_event,
    ):
        update_verification_attempt(
            attempt_id=self.attempt.id,
            name=name,
            status=status,
            expiration_datetime=expiration_datetime,
        )

        verification_attempt = VerificationAttempt.objects.get(id=self.attempt.id)

        # Values should change as a result of this update.
        self.assertEqual(verification_attempt.user, self.user)  # noqa: PT009
        self.assertEqual(verification_attempt.name, name)  # noqa: PT009
        self.assertEqual(verification_attempt.status, status)  # noqa: PT009
        self.assertEqual(verification_attempt.expiration_datetime, expiration_datetime)  # noqa: PT009

        if status == VerificationAttemptStatus.PENDING:
            mock_pending_event.assert_called_with(
                attempt_id=verification_attempt.id,
                user=self.user,
                status=status,
                name=name,
                expiration_date=expiration_datetime,
            )
        elif status == VerificationAttemptStatus.APPROVED:
            mock_approved_event.assert_called_with(
                attempt_id=verification_attempt.id,
                user=self.user,
                status=status,
                name=name,
                expiration_date=expiration_datetime,
            )
        elif status == VerificationAttemptStatus.DENIED:
            mock_denied_event.assert_called_with(
                attempt_id=verification_attempt.id,
                user=self.user,
                status=status,
                name=name,
                expiration_date=expiration_datetime,
            )

    def test_update_verification_attempt_none_values(self):
        update_verification_attempt(
            attempt_id=self.attempt.id,
            name=None,
            status=None,
            expiration_datetime=None,
        )

        verification_attempt = VerificationAttempt.objects.get(id=self.attempt.id)

        # Values should not change as a result of the values passed in being None, except for expiration_datetime.
        self.assertEqual(verification_attempt.user, self.user)  # noqa: PT009
        self.assertEqual(verification_attempt.name, self.attempt.name)  # noqa: PT009
        self.assertEqual(verification_attempt.status, self.attempt.status)  # noqa: PT009
        self.assertEqual(verification_attempt.expiration_datetime, None)  # noqa: PT009

    def test_update_verification_attempt_not_found(self):
        self.assertRaises(  # noqa: PT027
            VerificationAttempt.DoesNotExist,
            update_verification_attempt,
            attempt_id=999999,
            name=None,
            status=VerificationAttemptStatus.APPROVED,
        )

    @ddt.data(
        'completed',
        'failed',
        'submitted',
        'expired',
    )
    def test_update_verification_attempt_invalid(self, status):
        self.assertRaises(  # noqa: PT027
            VerificationAttemptInvalidStatus,
            update_verification_attempt,
            attempt_id=self.attempt.id,
            name=None,
            status=status,
            expiration_datetime=None,
        )
