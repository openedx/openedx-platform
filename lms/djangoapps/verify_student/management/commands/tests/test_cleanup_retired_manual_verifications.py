"""
Tests for django admin command `cleanup_retired_manual_verifications` in the verify_student module
"""

import logging

from django.core.management import call_command
from django.test import TestCase, override_settings
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.core.djangoapps.user_api.tests.factories import UserRetirementRequestFactory

LOGGER_NAME = 'lms.djangoapps.verify_student.management.commands.cleanup_retired_manual_verifications'


class TestCleanupRetiredManualVerificationsCommand(TestCase):
    """ Tests for django admin command `cleanup_retired_manual_verifications` in the verify_student module """

    def test_skips_when_redaction_setting_disabled(self):
        """
        Test that the command logs a warning and skips cleanup when redaction setting is disabled.
        """
        user = UserFactory.create()
        ManualVerification.objects.create(
            user=user,
            name='Retired User Name',
            status='approved',
        )
        UserRetirementRequestFactory(user=user)

        with override_settings(REDACT_MANUAL_VERIFICATION_HISTORICAL_PII=False):
            with LogCapture(LOGGER_NAME, level=logging.WARNING) as logger:
                call_command('cleanup_retired_manual_verifications')

        logger.check(
            (
                LOGGER_NAME,
                'WARNING',
                'Skipping. REDACT_MANUAL_VERIFICATION_HISTORICAL_PII must first be enabled.',
            ),
        )
        assert ManualVerification.objects.filter(user=user, name='Retired User Name').exists()

    def test_redacts_and_deletes_retired_records(self):
        """
        Test that the command redacts and deletes retired users' records but leaves active users untouched.
        """
        retired_user = UserFactory.create()
        active_user = UserFactory.create()

        ManualVerification.objects.create(
            user=retired_user,
            name='Retired User Name',
            status='approved',
        )
        ManualVerification.objects.create(
            user=active_user,
            name='Active User Name',
            status='approved',
        )
        UserRetirementRequestFactory(user=retired_user)

        with override_settings(REDACT_MANUAL_VERIFICATION_HISTORICAL_PII=True):
            call_command('cleanup_retired_manual_verifications')

        assert not ManualVerification.objects.filter(user=retired_user).exists()
        assert ManualVerification.objects.filter(user=active_user, name='Active User Name').exists()
