# lint-amnesty, pylint: disable=missing-module-docstring
# Lots of patching to stub in our own settings, and HTTP posting
from unittest import mock
from unittest.mock import patch

import ddt
from django.conf import settings

from common.test.utils import MockS3Boto3Mixin
from lms.djangoapps.verify_student.tests import TestVerificationBase
from lms.djangoapps.verify_student.tests.test_models import FAKE_SETTINGS, mock_software_secure_post_unavailable
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)

LOGGER_NAME = 'lms.djangoapps.verify_student.tasks'


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@ddt.ddt
class TestPhotoVerificationTasks(TestVerificationBase, MockS3Boto3Mixin, ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    @mock.patch('lms.djangoapps.verify_student.tasks.log')
    def test_logs_for_retry_until_failure(self, mock_log):
        retry_max_attempts = settings.SOFTWARE_SECURE_RETRY_MAX_ATTEMPTS
        with mock.patch('lms.djangoapps.verify_student.tasks.requests.post', new=mock_software_secure_post_unavailable):
            attempt = self.create_and_submit_attempt_for_user()
            username = attempt.user.username
            mock_log.error.assert_called_with(
                'Software Secure submission failed for user %r, setting status to must_retry',
                username,
                exc_info=True
            )
            for current_attempt in range(retry_max_attempts):
                mock_log.error.assert_any_call(
                    ('Retrying sending request to Software Secure for user: %r, Receipt ID: %r '
                     'attempt#: %s of %s'),
                    username,
                    attempt.receipt_id,
                    current_attempt,
                    settings.SOFTWARE_SECURE_RETRY_MAX_ATTEMPTS,
                )
