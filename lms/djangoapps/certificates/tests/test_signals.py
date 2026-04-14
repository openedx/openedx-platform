"""
Unit tests for enabling self-generated certificates for self-paced courses
and disabling for instructor-paced courses.
"""
from datetime import datetime, timezone
from unittest import mock
from uuid import uuid4

import ddt
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_events.data import EventsMetadata
from openedx_events.learning.data import ExamAttemptData, UserData, UserPersonalData
from openedx_events.learning.signals import EXAM_ATTEMPT_REJECTED
from openedx_events.testing import OpenEdxEventsTestMixin

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.api import has_self_generated_certificates_enabled
from lms.djangoapps.certificates.config import AUTO_CERTIFICATE_GENERATION
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import CertificateGenerationConfiguration, GeneratedCertificate
from lms.djangoapps.certificates.signals import handle_exam_attempt_rejected_event
from lms.djangoapps.certificates.tests.factories import CertificateAllowlistFactory, GeneratedCertificateFactory
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.tests.utils import mock_passing_grade
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class SelfGeneratedCertsSignalTest(ModuleStoreTestCase):
    """
    Tests for enabling/disabling self-generated certificates according to course-pacing.
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        CertificateGenerationConfiguration.objects.create(enabled=True)

    def test_cert_generation_flag_on_pacing_toggle(self):
        """
        Verify that signal enables or disables self-generated certificates
        according to course-pacing.
        """
        course = CourseFactory.create(self_paced=False, emit_signals=True)
        assert not has_self_generated_certificates_enabled(course.id)

        course.self_paced = True
        self.update_course(course, self.user.id)
        assert has_self_generated_certificates_enabled(course.id)

        course.self_paced = False
        self.update_course(course, self.user.id)
        assert not has_self_generated_certificates_enabled(course.id)


class AllowlistGeneratedCertificatesTest(ModuleStoreTestCase):
    """
    Tests for allowlisted student auto-certificate generation
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        # Instructor paced course
        self.ip_course = CourseFactory.create(self_paced=False)
        CourseEnrollmentFactory(
            user=self.user,
            course_id=self.ip_course.id,
            is_active=True,
            mode="verified",
        )

    def test_fire_task_allowlist_auto_enabled(self):
        """
        Test that the allowlist generation is invoked if automatic generation is enabled
        """
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_allowlist_certificate_task',
            return_value=None
        ) as mock_generate_allowlist_task:
            with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
                CertificateAllowlistFactory(
                    user=self.user,
                    course_id=self.ip_course.id
                )
                mock_generate_allowlist_task.assert_called_with(self.user, self.ip_course.id)

    def test_fire_task_allowlist_auto_disabled(self):
        """
        Test that the allowlist generation is not invoked if automatic generation is disabled
        """
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_allowlist_certificate_task',
            return_value=None
        ) as mock_generate_allowlist_task:
            with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=False):
                CertificateAllowlistFactory(
                    user=self.user,
                    course_id=self.ip_course.id
                )
                mock_generate_allowlist_task.assert_not_called()


class PassingGradeCertsTest(ModuleStoreTestCase):
    """
    Tests for certificate generation task firing on passing grade receipt
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            self_paced=True,
        )
        self.course_key = self.course.id
        self.user = UserFactory.create()
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            is_active=True,
            mode="verified",
        )
        self.ip_course = CourseFactory.create(self_paced=False)
        self.ip_enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.ip_course.id,
            is_active=True,
            mode="verified",
        )
        attempt = SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            status='submitted'
        )
        attempt.approve()

    def test_passing_grade_allowlist(self):
        with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
            # User who is not on the allowlist
            GeneratedCertificateFactory(
                user=self.user,
                course_id=self.course.id,
                status=CertificateStatuses.error
            )
            with mock_passing_grade():
                with mock.patch(
                    'lms.djangoapps.certificates.signals.generate_certificate_task',
                    return_value=None
                ) as mock_cert_task:
                    CourseGradeFactory().update(self.user, self.course)
                    mock_cert_task.assert_called_with(self.user, self.course.id)

            # User who is on the allowlist
            u = UserFactory.create()
            c = CourseFactory()
            course_key = c.id  # pylint: disable=no-member
            CertificateAllowlistFactory(
                user=u,
                course_id=course_key
            )
            GeneratedCertificateFactory(
                user=u,
                course_id=course_key,
                status=CertificateStatuses.error
            )
            with mock_passing_grade():
                with mock.patch(
                    'lms.djangoapps.certificates.signals.generate_certificate_task',
                    return_value=None
                ) as mock_cert_task:
                    CourseGradeFactory().update(u, c)
                    mock_cert_task.assert_called_with(u, course_key)

    def test_cert_already_generated_downloadable(self):
        with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
            GeneratedCertificateFactory(
                user=self.user,
                course_id=self.course.id,
                status=CertificateStatuses.downloadable
            )

            with mock.patch(
                'lms.djangoapps.certificates.signals.generate_certificate_task',
                return_value=None
            ) as mock_cert_task:
                grade_factory = CourseGradeFactory()
                with mock_passing_grade():
                    grade_factory.update(self.user, self.course)
                    mock_cert_task.assert_not_called()

    def test_cert_already_generated_unverified(self):
        with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
            GeneratedCertificateFactory(
                user=self.user,
                course_id=self.course.id,
                status=CertificateStatuses.unverified
            )

            with mock.patch(
                'lms.djangoapps.certificates.signals.generate_certificate_task',
                return_value=None
            ) as mock_cert_task:
                grade_factory = CourseGradeFactory()
                with mock_passing_grade():
                    grade_factory.update(self.user, self.course)
                    mock_cert_task.assert_called_with(self.user, self.course_key)

    def test_without_cert(self):
        with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
            with mock.patch(
                'lms.djangoapps.certificates.signals.generate_certificate_task',
                return_value=None
            ) as mock_cert_task:
                grade_factory = CourseGradeFactory()
                with mock_passing_grade():
                    grade_factory.update(self.user, self.course)
                    mock_cert_task.assert_called_with(self.user, self.course_key)


@ddt.ddt
class FailingGradeCertsTest(ModuleStoreTestCase):
    """
    Tests for marking certificate notpassing when grade goes from passing to failing,
    and that the signal has no effect on the cert status if the cert has a non-passing
    status
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            self_paced=True,
        )
        self.user = UserFactory.create()
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            is_active=True,
            mode="verified",
        )
        attempt = SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            status='submitted'
        )
        attempt.approve()

    @ddt.data(
        CertificateStatuses.deleted,
        CertificateStatuses.deleting,
        CertificateStatuses.downloadable,
        CertificateStatuses.error,
        CertificateStatuses.generating,
        CertificateStatuses.notpassing,
        CertificateStatuses.restricted,
        CertificateStatuses.unavailable,
        CertificateStatuses.auditing,
        CertificateStatuses.audit_passing,
        CertificateStatuses.audit_notpassing,
        CertificateStatuses.unverified,
        CertificateStatuses.invalidated,
        CertificateStatuses.requesting,
    )
    def test_cert_failure(self, status):
        if CertificateStatuses.is_passing_status(status):
            expected_status = CertificateStatuses.notpassing
        else:
            expected_status = status
        GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course.id,
            status=status
        )
        CourseGradeFactory().update(self.user, self.course)
        cert = GeneratedCertificate.certificate_for_student(self.user, self.course.id)
        assert cert.status == expected_status

    def test_failing_grade_allowlist(self):
        # User who is not on the allowlist
        GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable
        )
        CourseGradeFactory().update(self.user, self.course)
        cert = GeneratedCertificate.certificate_for_student(self.user, self.course.id)
        assert cert.status == CertificateStatuses.notpassing

        # User who is on the allowlist
        u = UserFactory.create()
        c = CourseFactory()
        course_key = c.id  # pylint: disable=no-member
        CertificateAllowlistFactory(
            user=u,
            course_id=course_key
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=course_key,
            status=CertificateStatuses.downloadable
        )
        CourseGradeFactory().update(u, c)
        cert = GeneratedCertificate.certificate_for_student(u, course_key)
        assert cert.status == CertificateStatuses.downloadable


class LearnerIdVerificationTest(OpenEdxEventsTestMixin, ModuleStoreTestCase):
    """
    Tests for certificate generation task firing on learner id verification
    """
    ENABLED_OPENEDX_EVENTS = ['org.openedx.learning.idv_attempt.approved.v1']

    def setUp(self):
        super().setUp()
        self.course_one = CourseFactory.create(self_paced=True)
        self.user_one = UserFactory.create()
        self.enrollment_one = CourseEnrollmentFactory(
            user=self.user_one,
            course_id=self.course_one.id,
            is_active=True,
            mode='verified',
        )
        self.user_two = UserFactory.create()
        self.course_two = CourseFactory.create(self_paced=False)
        self.enrollment_two = CourseEnrollmentFactory(
            user=self.user_two,
            course_id=self.course_two.id,
            is_active=True,
            mode='verified'
        )
        with mock_passing_grade():
            grade_factory = CourseGradeFactory()
            grade_factory.update(self.user_one, self.course_one)
            grade_factory.update(self.user_two, self.course_two)

    def test_cert_generation_on_photo_verification(self):
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_certificate_task',
            return_value=None
        ) as mock_cert_task:
            with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
                attempt = SoftwareSecurePhotoVerification.objects.create(
                    user=self.user_two,
                    status='submitted'
                )
                attempt.approve()
                mock_cert_task.assert_called_with(self.user_two, self.course_two.id)

    def test_id_verification_allowlist(self):
        # User is not on the allowlist
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_allowlist_certificate_task',
            return_value=None
        ) as mock_allowlist_task:
            with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
                attempt = SoftwareSecurePhotoVerification.objects.create(
                    user=self.user_two,
                    status='submitted'
                )
                attempt.approve()
                mock_allowlist_task.assert_not_called()

        # User is on the allowlist
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_allowlist_certificate_task',
            return_value=None
        ) as mock_allowlist_task:
            with override_waffle_switch(AUTO_CERTIFICATE_GENERATION, active=True):
                u = UserFactory.create()
                c = CourseFactory()
                course_key = c.id  # pylint: disable=no-member
                CourseEnrollmentFactory(
                    user=u,
                    course_id=course_key,
                    is_active=True,
                    mode='verified'
                )
                CertificateAllowlistFactory(
                    user=u,
                    course_id=course_key
                )
                attempt = SoftwareSecurePhotoVerification.objects.create(
                    user=u,
                    status='submitted'
                )
                attempt.approve()
                mock_allowlist_task.assert_called_with(u, course_key)


@override_waffle_flag(AUTO_CERTIFICATE_GENERATION, active=True)
class EnrollmentModeChangeCertsTest(ModuleStoreTestCase):
    """
    Tests for certificate generation task firing when the user's enrollment mode changes
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.verified_course = CourseFactory.create(
            self_paced=True,
        )
        self.verified_course_key = self.verified_course.id  # pylint: disable=no-member
        self.verified_enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.verified_course_key,
            is_active=True,
            mode='verified',
        )
        CertificateAllowlistFactory(
            user=self.user,
            course_id=self.verified_course_key
        )

        self.audit_course = CourseFactory.create(self_paced=False)
        self.audit_course_key = self.audit_course.id  # pylint: disable=no-member
        self.audit_enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.audit_course_key,
            is_active=True,
            mode='audit',
        )
        CertificateAllowlistFactory(
            user=self.user,
            course_id=self.audit_course_key
        )

    def test_audit_to_verified(self):
        """
        Test that we try to generate a certificate when the user switches from audit to verified
        """
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_certificate_task',
            return_value=None
        ) as mock_cert_task:
            self.audit_enrollment.change_mode('verified')
            mock_cert_task.assert_called_with(self.user, self.audit_course_key)

    def test_verified_to_audit(self):
        """
        Test that we do not try to generate a certificate when the user switches from verified to audit
        """
        with mock.patch(
            'lms.djangoapps.certificates.signals.generate_allowlist_certificate_task',
            return_value=None
        ) as mock_allowlist_task:
            self.verified_enrollment.change_mode('audit')
            mock_allowlist_task.assert_not_called()


class ExamCompletionEventBusTests(TestCase):
    """
    Tests completion events from the event bus.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = CourseKey.from_string('course-v1:edX+TestX+Test_Course')
        cls.subsection_id = 'block-v1:edX+TestX+Test_Course+type@sequential+block@subsection'
        cls.usage_key = UsageKey.from_string(cls.subsection_id)
        cls.student_user = UserFactory(
            username='student_user',
        )

    @staticmethod
    def _get_exam_event_data(student_user, course_key, usage_key, exam_type, requesting_user=None):
        """ create ExamAttemptData object for exam based event """
        if requesting_user:
            requesting_user_data = UserData(
                id=requesting_user.id,
                is_active=True,
                pii=None
            )
        else:
            requesting_user_data = None

        return ExamAttemptData(
            student_user=UserData(
                id=student_user.id,
                is_active=True,
                pii=UserPersonalData(
                    username=student_user.username,
                    email=student_user.email,
                ),
            ),
            course_key=course_key,
            usage_key=usage_key,
            requesting_user=requesting_user_data,
            exam_type=exam_type,
        )

    @staticmethod
    def _get_exam_event_metadata(event_signal):
        """ create metadata object for event """
        return EventsMetadata(
            event_type=event_signal.event_type,
            id=uuid4(),
            minorversion=0,
            source='openedx/lms/web',
            sourcehost='lms.test',
            time=datetime.now(timezone.utc)  # noqa: UP017
        )

    @mock.patch('lms.djangoapps.certificates.signals.invalidate_certificate')
    def test_exam_attempt_rejected_event(self, mock_api_function):
        """
        Assert that CertificateService api's invalidate_certificate is called upon consuming the event
        """
        exam_event_data = self._get_exam_event_data(self.student_user,
                                                    self.course_key,
                                                    self.usage_key,
                                                    exam_type='proctored')
        event_metadata = self._get_exam_event_metadata(EXAM_ATTEMPT_REJECTED)

        event_kwargs = {
            'exam_attempt': exam_event_data,
            'metadata': event_metadata
        }
        handle_exam_attempt_rejected_event(None, EXAM_ATTEMPT_REJECTED, **event_kwargs)
        mock_api_function.assert_called_once_with(self.student_user.id, self.course_key, source='exam_event')
