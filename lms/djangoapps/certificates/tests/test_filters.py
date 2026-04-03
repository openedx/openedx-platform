"""
Test that various filters are fired for models in the certificates app.
"""
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CertificateCreationRequested
from rest_framework import status as status_code

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.roles import SupportStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.generation_handler import (
    CertificateGenerationNotAllowed,
    generate_allowlist_certificate_task,
    generate_certificate_task
)
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.signals import (
    _handle_id_verification_approved,
    _listen_for_enrollment_mode_change,
    listen_for_passing_grade
)
from lms.djangoapps.certificates.tests.factories import CertificateAllowlistFactory
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestCertificatePipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user, course_key, mode, status, grade, generation_mode):  # pylint: disable=arguments-differ
        """Pipeline steps that changes certificate mode from honor to no-id-professional."""
        if mode == 'honor':
            return {
                'mode': 'no-id-professional',
            }
        return {}


class TestStopCertificateGenerationStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user, course_key, mode, status, grade, generation_mode):  # pylint: disable=arguments-differ
        """Pipeline step that stops the certificate generation process."""
        raise CertificateCreationRequested.PreventCertificateCreation(
            "You can't generate a certificate from this site."
        )


@mock.patch(
    'lms.djangoapps.certificates.generation_handler.has_html_certificates_enabled', mock.Mock(return_value=True),
)
@mock.patch('lms.djangoapps.certificates.generation_handler._is_passing_grade', mock.Mock(return_value=True))
@skip_unless_lms
class CertificateFiltersTest(SharedModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the certificate generation process.

    This class guarantees that the following filters are triggered during the user's certificate generation:

    - CertificateCreationRequested
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course_run = CourseFactory()
        self.user = UserFactory.create(
            username="somestudent",
            first_name="Student",
            last_name="Person",
            email="robot@robot.org",
            is_active=True,
            password="password",
        )
        self.grade = CourseGradeFactory().read(self.user, self.course_run)
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course_run.id,
            is_active=True,
            mode=CourseMode.HONOR,
        )
        self.client.login(username=self.user.username, password="password")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestCertificatePipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_certificate_creation_filter_executed(self):
        """
        Test whether the student certificate filter is triggered before the user's
        certificate creation process.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestCertificatePipelineStep.
            - The certificate generates with no-id-professional mode instead of honor mode.
        """
        cert_gen_task_created = generate_certificate_task(
            self.user, self.course_run.id, generation_mode=CourseMode.HONOR,
        )

        certificate = GeneratedCertificate.objects.get(
            user=self.user,
            course_id=self.course_run.id,
        )

        self.assertTrue(cert_gen_task_created)
        self.assertEqual(CourseMode.NO_ID_PROFESSIONAL_MODE, certificate.mode)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_certificate_creation_filter_prevent_generation(self):
        """
        Test prevent the user's certificate generation through a pipeline step.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        with self.assertRaises(CertificateGenerationNotAllowed):
            generate_certificate_task(
                self.user, self.course_run.id, generation_mode=CourseMode.HONOR,
            )

        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_certificate_generation_without_filter_configuration(self):
        """
        Test usual certificate process, without filter's intervention.

        Expected result:
            - CertificateCreationRequested does not have any effect on the certificate generation process.
            - The certificate generation process ends successfully.
        """
        cert_gen_task_created = generate_certificate_task(
            self.user, self.course_run.id, generation_mode=CourseMode.HONOR,
        )

        certificate = GeneratedCertificate.objects.get(
            user=self.user,
            course_id=self.course_run.id,
        )

        self.assertTrue(cert_gen_task_created)
        self.assertEqual(CourseMode.HONOR, certificate.mode)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_generate_allowlist_certificate_fail(self):
        """
        Test stop certificate process by raising a filter exception when the user is in the
        allow list.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        CertificateAllowlistFactory.create(course_id=self.course_run.id, user=self.user)

        certificate_generated = generate_allowlist_certificate_task(self.user, self.course_run.id)

        self.assertFalse(certificate_generated)
        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_generate_certificate_command(self):
        """
        Test stop certificate process through the Django command by raising a filter exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        with self.assertLogs(level="ERROR"):
            call_command("cert_generation", "--u", self.user.id, "--c", self.course_run.id)

        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch("lms.djangoapps.certificates.api.auto_certificate_generation_enabled", mock.Mock(return_value=True))
    def test_listen_for_passing_grade(self):
        """
        Test stop automatic certificate generation process by raising a filters exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        signal_result = listen_for_passing_grade(None, self.user, self.course_run.id)

        self.assertFalse(signal_result)
        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch(
        'lms.djangoapps.verify_student.services.IDVerificationService.user_status',
        mock.Mock(return_value={"status": "approved"})
    )
    @mock.patch("lms.djangoapps.certificates.api.auto_certificate_generation_enabled", mock.Mock(return_value=True))
    def test_handle_id_verification_approved(self):
        """
        Test stop certificate generation process after the verification status changed by raising a filters exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        _handle_id_verification_approved(self.user)

        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_listen_for_enrollment_mode_change(self):
        """
        Test stop automatic certificate generation process by raising a filters exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The certificate is not generated.
        """
        signal_result = _listen_for_enrollment_mode_change(None, self.user, self.course_run.id, CourseMode.HONOR)

        self.assertFalse(signal_result)
        self.assertFalse(
            GeneratedCertificate.objects.filter(
                user=self.user, course_id=self.course_run.id, mode=CourseMode.HONOR,
            )
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch(
        "lms.djangoapps.certificates.generation_handler._can_generate_regular_certificate",
        mock.Mock(return_value=True),
    )
    def test_generate_cert_support_view(self):
        """
        Test stop automatic certificate generation process by raising a filters exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The view returns HTTP_400_BAD_REQUEST.
        """
        SupportStaffRole().add_users(self.user)
        url = reverse(
            "certificates:regenerate_certificate_for_user",
        )
        body = {
            "course_key": str(self.course_run.id),
            "username": self.user.username,
        }

        response = self.client.post(url, body)

        self.assertEqual(status_code.HTTP_400_BAD_REQUEST, response.status_code)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.creation.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.tests.test_filters.TestStopCertificateGenerationStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_generate_cert_progress_view(self):
        """
        Test stop certificate generation from the progress view by raising a filters exception.

        Expected result:
            - CertificateCreationRequested is triggered and executes TestStopCertificateGenerationStep.
            - The view returns HTTP_400_BAD_REQUEST.
        """
        url = reverse("generate_user_cert", kwargs={"course_id": str(self.course_run.id)})

        response = self.client.post(url)

        self.assertContains(
            response,
            "You can't generate a certificate from this site.",
            status_code=status_code.HTTP_400_BAD_REQUEST,
        )
