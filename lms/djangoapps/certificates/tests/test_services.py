"""
Unit Tests for the Certificate service
"""


from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.services import CertificateService
from lms.djangoapps.certificates.tests.factories import CertificateAllowlistFactory, GeneratedCertificateFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class CertificateServiceTests(ModuleStoreTestCase):
    """
    Tests for the Certificate service
    """

    def setUp(self):
        super().setUp()
        self.service = CertificateService()
        self.course = CourseFactory()
        self.course_overview = CourseOverviewFactory.create(
            id=self.course.id
        )
        self.user = UserFactory()
        self.user_id = self.user.id
        self.course_id = self.course.id  # pylint: disable=no-member
        GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course.id,  # pylint: disable=no-member
            grade=1.0
        )

    def generated_certificate_to_dict(self, generated_certificate):
        """
        Converts a Generated Certificate instance to a Python dictionary
        """
        return {
            'verify_uuid': generated_certificate.verify_uuid,
            'download_uuid': generated_certificate.download_uuid,
            'download_url': generated_certificate.download_url,
            'grade': generated_certificate.grade,
            'status': generated_certificate.status
        }

    def test_invalidate_certificate(self):
        """
        Verify that CertificateService invalidates the user certificate
        """
        success = self.service.invalidate_certificate(self.user_id, self.course_id)
        assert success

        invalid_generated_certificate = GeneratedCertificate.objects.get(
            user=self.user_id,
            course_id=self.course_id
        )
        self.assertDictEqual(
            self.generated_certificate_to_dict(invalid_generated_certificate),
            {
                'verify_uuid': invalid_generated_certificate.verify_uuid,
                'download_uuid': '',
                'download_url': '',
                'grade': '',
                'status': CertificateStatuses.unavailable
            }
        )

    def test_invalidate_certificate_allowlist(self):
        """
        Verify that CertificateService does not invalidate the certificate if it is allowlisted
        """
        u = UserFactory.create()
        c = CourseFactory()
        course_key = c.id  # pylint: disable=no-member
        GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=u,
            course_id=course_key,
            grade=1.0
        )
        CertificateAllowlistFactory(
            user=u,
            course_id=course_key
        )
        success = self.service.invalidate_certificate(u.id, course_key)
        assert not success

        cert = GeneratedCertificate.objects.get(user=u.id, course_id=course_key)
        assert cert.status == CertificateStatuses.downloadable
