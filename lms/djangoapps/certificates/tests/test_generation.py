"""
Tests for certificate generation
"""
import logging  # lint-amnesty, pylint: disable=wrong-import-order
from unittest import mock, skipUnless  # lint-amnesty, pylint: disable=wrong-import-order

import ddt

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import EventTestMixin
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.generation import generate_course_certificate
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

PROFILE_NAME_METHOD = 'common.djangoapps.student.models_api.get_name'
name_affirmation_service = get_name_affirmation_service()


@ddt.ddt
class CertificateTests(EventTestMixin, ModuleStoreTestCase):
    """
    Tests for certificate generation
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.certificates.utils.tracker')

        # Create user, a course run, and an enrollment
        self.u = UserFactory()
        self.profile = UserProfile.objects.get(user_id=self.u.id)
        self.name = self.profile.name
        self.cr = CourseFactory()
        self.key = self.cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=self.u,
            course_id=self.key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        self.gen_mode = 'batch'
        self.grade = '.85'
        self.enrollment_mode = CourseMode.VERIFIED

    def test_generation(self):
        """
        Test certificate generation
        """
        certs = GeneratedCertificate.objects.filter(user=self.u, course_id=self.key)
        assert len(certs) == 0

        generated_cert = generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable,
                                                     self.enrollment_mode, self.grade, self.gen_mode)

        self.assert_event_emitted(
            'edx.certificate.created',
            user_id=self.u.id,
            course_id=str(self.key),
            certificate_id=generated_cert.verify_uuid,
            enrollment_mode=generated_cert.mode,
            certificate_url='',
            generation_mode=self.gen_mode
        )

        certs = GeneratedCertificate.objects.filter(user=self.u, course_id=self.key)
        assert len(certs) == 1

        cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)
        assert cert.status == CertificateStatuses.downloadable
        assert cert.mode == self.enrollment_mode
        assert cert.grade == self.grade
        assert cert.name == self.name

    def test_generation_existing_unverified(self):
        """
        Test certificate generation when a certificate already exists and we want to mark it as unverified
        """
        error_reason = 'Some PDF error'
        GeneratedCertificateFactory(
            user=self.u,
            course_id=self.key,
            mode=CourseMode.AUDIT,
            status=CertificateStatuses.error,
            error_reason=error_reason
        )

        cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)
        assert cert.error_reason == error_reason
        assert cert.mode == CourseMode.AUDIT
        assert cert.status == CertificateStatuses.error

        generate_course_certificate(self.u, self.key, CertificateStatuses.unverified, self.enrollment_mode, self.grade,
                                    self.gen_mode)

        cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)
        assert cert.error_reason == ''
        assert cert.status == CertificateStatuses.unverified
        assert cert.mode == self.enrollment_mode
        assert cert.grade == ''

    def test_generation_existing_downloadable(self):
        """
        Test certificate generation when a certificate already exists and we want to mark it as downloadable
        """
        error_reason = 'Some PDF error'
        GeneratedCertificateFactory(
            user=self.u,
            course_id=self.key,
            mode=CourseMode.AUDIT,
            status=CertificateStatuses.error,
            error_reason=error_reason
        )

        generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable, self.enrollment_mode,
                                    self.grade, self.gen_mode)

        cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)
        assert cert.error_reason == ''
        assert cert.status == CertificateStatuses.downloadable
        assert cert.mode == self.enrollment_mode
        assert cert.grade == self.grade

    def test_generation_uuid_persists_through_revocation(self):
        """
        Test that the `verify_uuid` value of a certificate does not change when it is revoked and re-awarded.
        """
        generated_cert = generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable,
                                                     self.enrollment_mode, self.grade, self.gen_mode)
        assert generated_cert.status, CertificateStatuses.downloadable

        verify_uuid = generated_cert.verify_uuid

        generated_cert.invalidate()
        assert generated_cert.status, CertificateStatuses.unavailable
        assert generated_cert.verify_uuid, verify_uuid

        generated_cert = generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable,
                                                     self.enrollment_mode, self.grade, self.gen_mode)
        assert generated_cert.status, CertificateStatuses.downloadable
        assert generated_cert.verify_uuid, verify_uuid

        generated_cert.mark_notpassing(mode=generated_cert.mode, grade=50.00)
        assert generated_cert.status, CertificateStatuses.notpassing
        assert generated_cert.verify_uuid, verify_uuid

        generated_cert = generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable,
                                                     self.enrollment_mode, self.grade, self.gen_mode)
        assert generated_cert.status, CertificateStatuses.downloadable
        assert generated_cert.verify_uuid, verify_uuid

    def test_generation_creates_verify_uuid_when_needed(self):
        """
        Test that ensures we will create a verify_uuid when needed.
        """
        GeneratedCertificateFactory(
            user=self.u,
            course_id=self.key,
            mode=CourseMode.VERIFIED,
            status=CertificateStatuses.unverified,
            verify_uuid=''
        )

        generated_cert = generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable,
                                                     self.enrollment_mode, self.grade, self.gen_mode)
        assert generated_cert.status, CertificateStatuses.downloadable
        assert generated_cert.verify_uuid != ''

    def test_generation_missing_profile(self):
        """
        Test certificate generation when the user profile is missing
        """
        GeneratedCertificateFactory(
            user=self.u,
            course_id=self.key,
            mode=CourseMode.AUDIT,
            status=CertificateStatuses.unverified
        )

        with mock.patch(PROFILE_NAME_METHOD, return_value=None):
            generate_course_certificate(self.u, self.key, CertificateStatuses.downloadable, self.enrollment_mode,
                                        self.grade, self.gen_mode)

            cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)
            assert cert.status == CertificateStatuses.downloadable
            assert cert.mode == self.enrollment_mode
            assert cert.grade == self.grade
            assert cert.name == ''

    @skipUnless(name_affirmation_service is not None, 'Requires Name Affirmation')
    @ddt.data((True, 'approved'),
              (True, 'denied'),
              (False, 'pending'))
    @ddt.unpack
    def test_generation_verified_name(self, should_use_verified_name_for_certs, status):
        """
        Test that if the user has their preference set to use
        verified name for certificates, their verified name will appear on the certificate rather than
        their profile name.
        """
        verified_name = 'Jonathan Doe'
        name_affirmation_service.create_verified_name(self.u, verified_name, self.name, status=status)
        name_affirmation_service.create_verified_name_config(
            self.u,
            use_verified_name_for_certs=should_use_verified_name_for_certs
        )

        GeneratedCertificateFactory(
            user=self.u,
            course_id=self.key,
            mode=CourseMode.AUDIT,
            status=CertificateStatuses.unverified
        )

        generate_course_certificate(
            self.u, self.key, CertificateStatuses.downloadable, self.enrollment_mode, self.grade, self.gen_mode,
        )

        cert = GeneratedCertificate.objects.get(user=self.u, course_id=self.key)

        if should_use_verified_name_for_certs and status == 'approved':
            assert cert.name == verified_name
        else:
            assert cert.name == self.name
