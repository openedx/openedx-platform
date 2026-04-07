"""
Tests for the cert_generation command
"""

from unittest import mock

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

ID_VERIFIED_METHOD = 'lms.djangoapps.verify_student.services.IDVerificationService.user_is_verified'
PASSING_GRADE_METHOD = 'lms.djangoapps.certificates.generation_handler._is_passing_grade'
WEB_CERTS_METHOD = 'lms.djangoapps.certificates.generation_handler.has_html_certificates_enabled'


# base setup is unverified users, Enable certificates IDV requirements turned off,
# and normal passing grade certificates for convenience
@mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=False)
@mock.patch(ID_VERIFIED_METHOD, mock.Mock(return_value=False))
@mock.patch(PASSING_GRADE_METHOD, mock.Mock(return_value=True))
@mock.patch(WEB_CERTS_METHOD, mock.Mock(return_value=True))
class RegenerateNoIDVCertTests(ModuleStoreTestCase):
    """
    Tests for the regenerate_noidv_cert management command
    """

    def test_command_with_course_key_and_excluded_keys(self):
        """
        Verify command with a missing param -- course key.
        """
        with pytest.raises(CommandError, match="You may not specify both course keys and excluded course keys."):
            call_command("regenerate_noidv_cert", "-c", "blah", "--excluded-keys", "bleh")

    def test_command_with_invalid_key(self):
        """
        Verify command with an invalid course run key
        """
        with pytest.raises(CommandError, match=" is not a valid course-key"):
            call_command("regenerate_noidv_cert", "-c", "blah")

    def test_command_with_invalid_key_to_exclude(self):
        """
        Verify command with an invalid course run key as an excluded key
        """
        with pytest.raises(CommandError, match=" is not a valid course-key"):
            call_command("regenerate_noidv_cert", "--excluded-keys", "blah")

    def test_regeneration(self):
        """
        Single regeneration base case
        """
        course_run = CourseFactory()
        course_run_key = course_run.id

        user = UserFactory()
        CourseEnrollmentFactory(
            user=user,
            course_id=course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user,
            course_id=course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.unverified
        )

        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key)
        self.assertEqual('1', regenerated)

    def test_regeneration_verified(self):
        """
        Only unverified certificates should get regenerated
        """
        course_run = CourseFactory()
        course_run_key = course_run.id

        user = UserFactory()
        CourseEnrollmentFactory(
            user=user,
            course_id=course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user,
            course_id=course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )

        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key)
        self.assertEqual('0', regenerated)

    def _multisetup(self):
        """
        setup certs across two course runs
        """
        course_run = CourseFactory()
        course_run_key = course_run.id
        course_run2 = CourseFactory()
        course_run_key2 = course_run2.id

        user = UserFactory()
        CourseEnrollmentFactory(
            user=user,
            course_id=course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user,
            course_id=course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.unverified
        )

        user1 = UserFactory()
        CourseEnrollmentFactory(
            user=user1,
            course_id=course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user1,
            course_id=course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.unverified
        )

        user2 = UserFactory()
        CourseEnrollmentFactory(
            user=user2,
            course_id=course_run_key2,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user2,
            course_id=course_run_key2,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.unverified
        )

        user_verified = UserFactory()
        CourseEnrollmentFactory(
            user=user_verified,
            course_id=course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user_verified,
            course_id=course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )

        user_failing = UserFactory()
        CourseEnrollmentFactory(
            user=user_failing,
            course_id=course_run_key2,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=user_failing,
            course_id=course_run_key2,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.notpassing
        )
        return (course_run_key, course_run_key2)

    def test_multiple_regeneration(self):
        """
        Verify regeneration across multiple courses and users
        """
        course_run_key, course_run_key2 = self._multisetup()

        #3/5 in unverified status
        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key, course_run_key2)
        self.assertEqual('3', regenerated)

        #nothing left unverified for another run
        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key, course_run_key2)
        self.assertEqual('0', regenerated)

    def test_course_at_a_time(self):
        """
        Verify course regeneration separated
        """
        course_run_key, course_run_key2 = self._multisetup()

        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key)
        self.assertEqual('2', regenerated)

        regenerated = call_command("regenerate_noidv_cert", "-c", course_run_key2)
        self.assertEqual('1', regenerated)

    def test_regenerate_all(self):
        """
        Verify that all unverified certs are regenerated
        """
        self._multisetup()
        regenerated = call_command("regenerate_noidv_cert")
        self.assertEqual('3', regenerated)

    def test_regenerate_all_with_excluded_keys(self):
        """
        Verify that all unverified certs are regenerated except for those from courses in the excluded keys
        """
        course_run_key, course_run_key2 = self._multisetup()

        regenerated = call_command("regenerate_noidv_cert", "--excluded-keys", course_run_key)
        self.assertEqual('1', regenerated)
