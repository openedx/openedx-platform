# pylint: disable=missing-module-docstring
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.conf import settings  # noqa: F401
from django.db import DEFAULT_DB_ALIAS
from django.test import TestCase
from django.utils.timezone import now

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, VerificationAttempt


class TestVerificationBase(TestCase):
    """
    Common tests across all types of Verifications (e.g., SoftwareSecurePhotoVerification, SSOVerification)
    """

    @contextmanager
    def immediate_on_commit(self, using=None):
        """
        Context manager executing transaction.on_commit() hooks immediately as
        if the connection was in auto-commit mode. This is required when
        using a subclass of django.test.TestCase as all tests are wrapped in
        a transaction that never gets committed.

        TODO: Remove when immediate_on_commit function is actually implemented
        Django Ticket #: 30456, Link: https://code.djangoproject.com/ticket/30457#no1
        """
        immediate_using = DEFAULT_DB_ALIAS if using is None else using

        def on_commit(func, using=None):
            using = DEFAULT_DB_ALIAS if using is None else using
            if using == immediate_using:
                func()

        with mock.patch('django.db.transaction.on_commit', side_effect=on_commit) as patch:
            yield patch

    def verification_active_at_datetime(self, attempt):
        """
        Tests to ensure the Verification is active or inactive at the appropriate datetimes.
        """
        # Not active before the created date
        before = attempt.created_at - timedelta(minutes=1)
        assert not attempt.active_at_datetime(before)

        # Active immediately after created date
        after_created = attempt.created_at + timedelta(seconds=1)
        assert attempt.active_at_datetime(after_created)

        # Active immediately before expiration date
        expiration = attempt.expiration_datetime
        if expiration:
            before_expiration = expiration - timedelta(seconds=1)
            assert attempt.active_at_datetime(before_expiration)

        # Not active after the expiration date
        field = 'expiration_datetime' if isinstance(attempt, VerificationAttempt) else 'expiration_date'
        setattr(attempt, field, now() - timedelta(days=1))
        # attempt.expiration_date = now() - timedelta(days=1)
        attempt.save()
        assert not attempt.active_at_datetime(now())

    def submit_attempt(self, attempt):
        with self.immediate_on_commit():
            attempt.submit()
            attempt.refresh_from_db()
        return attempt

    def create_and_submit_attempt_for_user(self, user=None):
        """
        Create photo verification attempt without uploading photos
        for a user.
        """
        if not user:
            user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification.objects.create(user=user)
        attempt.mark_ready()
        return self.submit_attempt(attempt)

    def create_upload_and_submit_attempt_for_user(self, user=None):
        """
        Helper method to create a generic submission with photos for
        a user and send it.
        """
        if not user:
            user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        user.profile.name = "Rust\u01B4"

        attempt.upload_face_image("Just pretend this is image data")
        attempt.upload_photo_id_image("Hey, we're a photo ID")
        attempt.mark_ready()
        return self.submit_attempt(attempt)
