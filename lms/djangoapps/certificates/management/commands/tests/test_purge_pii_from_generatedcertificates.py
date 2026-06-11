"""
Tests for the `purge_pii_from_generatedcertificates` management command.
"""

import ddt
from django.core.management import call_command
from django.test import override_settings
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.user_api.models import RetirementState
from openedx.core.djangoapps.user_api.tests.factories import (
    RetirementStateFactory,
    UserRetirementRequestFactory,
    UserRetirementStatusFactory,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class PurgePiiFromCertificatesTests(ModuleStoreTestCase):
    """
    Tests for the `purge_pii_from_generatedcertificates` management command.
    """
    @classmethod
    def setUpClass(cls):
        """
        The retirement pipeline is not fully enabled by default. In order to properly test the management command, we
        must ensure that at least one of the required RetirementState states (`COMPLETE`) exists.
        """
        super().setUpClass()
        cls.complete = RetirementStateFactory(state_name="COMPLETE")

    @classmethod
    def tearDownClass(cls):
        # Remove any retirement state objects that we created during this test suite run. We don't want to poison other
        # test suites.
        RetirementState.objects.all().delete()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.course_run = CourseFactory()
        # create an "active" learner that is not associated with any retirement requests, used to verify that the
        # management command doesn't purge any info for active users.
        self.user_active = UserFactory()
        self.user_active_name = "Teysa Karlov"
        GeneratedCertificateFactory(
            status=CertificateStatuses.downloadable,
            course_id=self.course_run.id,
            user=self.user_active,
            name=self.user_active_name,
            grade=1.00,
        )
        # create a second learner that is associated with a retirement request, used to verify that the management
        # command purges info successfully from a GeneratedCertificate instance associated with a retired learner
        self.user_retired = UserFactory()
        self.user_retired_name = "Nicol Bolas"
        GeneratedCertificateFactory(
            status=CertificateStatuses.downloadable,
            course_id=self.course_run.id,
            user=self.user_retired,
            name=self.user_retired_name,
            grade=0.99,
        )
        UserRetirementStatusFactory(
            user=self.user_retired,
            current_state=self.complete,
            last_state=self.complete,
        )
        UserRetirementRequestFactory(user=self.user_retired)

    @ddt.data(True, False)
    def test_management_command(self, redact_history_toggle_enabled):
        """
        Verify the management command purges expected data from a GeneratedCertificate instance if a learner has
        successfully had their account retired.
        """
        cert_for_active_user = GeneratedCertificate.objects.get(user_id=self.user_active)
        assert cert_for_active_user.name == self.user_active_name
        cert_for_retired_user = GeneratedCertificate.objects.get(user_id=self.user_retired)
        assert cert_for_retired_user.name == self.user_retired_name

        with override_settings(REDACT_CERTIFICATES_HISTORICAL_PII=redact_history_toggle_enabled):
            call_command("purge_pii_from_generatedcertificates")

        cert_for_active_user = GeneratedCertificate.objects.get(user_id=self.user_active)
        assert cert_for_active_user.name == self.user_active_name
        cert_for_retired_user = GeneratedCertificate.objects.get(user_id=self.user_retired)
        assert cert_for_retired_user.name == ""

        active_history_names = list(
            GeneratedCertificate.history.filter(user=self.user_active).values_list("name", flat=True)
        )
        assert len(active_history_names) > 0
        assert all(n == self.user_active_name for n in active_history_names)

        retired_history_names = list(
            GeneratedCertificate.history.filter(user=self.user_retired).values_list("name", flat=True)
        )
        assert len(retired_history_names) > 0
        if redact_history_toggle_enabled:
            assert all(n == "" for n in retired_history_names), "Names in the history table should have been redacted."
        else:
            assert all(n == self.user_retired_name for n in retired_history_names), (
                "Names in the history table should not have been redacted."
            )

    def test_management_command_dry_run(self):
        """
        Verify that the management command does not purge any data when invoked with the `--dry-run` flag
        """
        expected_log_msg = (
            "DRY RUN: running this management command would purge `name` data from the following users: "
            f"[{self.user_retired.id}]"
        )

        cert_for_active_user = GeneratedCertificate.objects.get(user_id=self.user_active)
        assert cert_for_active_user.name == self.user_active_name
        cert_for_retired_user = GeneratedCertificate.objects.get(user_id=self.user_retired)
        assert cert_for_retired_user.name == self.user_retired_name

        with LogCapture() as logger:
            call_command("purge_pii_from_generatedcertificates", "--dry-run")

        cert_for_active_user = GeneratedCertificate.objects.get(user_id=self.user_active)
        assert cert_for_active_user.name == self.user_active_name
        cert_for_retired_user = GeneratedCertificate.objects.get(user_id=self.user_retired)
        assert cert_for_retired_user.name == self.user_retired_name

        retired_history_names = list(
            GeneratedCertificate.history.filter(user=self.user_retired).values_list("name", flat=True)
        )
        assert len(retired_history_names) > 0
        assert all(n == self.user_retired_name for n in retired_history_names)

        assert logger.records[0].msg == expected_log_msg
