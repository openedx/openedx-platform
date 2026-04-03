"""
Tests for certificate generation handler
"""
import logging
from unittest import mock

import ddt
from django.conf import settings
from django.test import override_settings

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.generation_handler import (
    _can_generate_allowlist_certificate,
    _can_generate_certificate_for_status,
    _can_generate_regular_certificate,
    _generate_regular_certificate_task,
    _set_allowlist_cert_status,
    _set_regular_cert_status,
    generate_allowlist_certificate_task,
    generate_certificate_task,
    is_on_certificate_allowlist,
)
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import (
    CertificateAllowlistFactory,
    CertificateInvalidationFactory,
    GeneratedCertificateFactory,
)
from lms.djangoapps.grades.api import CourseGradeFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

BETA_TESTER_METHOD = 'lms.djangoapps.certificates.generation_handler.is_beta_tester'
COURSE_OVERVIEW_METHOD = 'lms.djangoapps.certificates.generation_handler.get_course_overview_or_none'
CCX_COURSE_METHOD = 'lms.djangoapps.certificates.generation_handler._is_ccx_course'
GET_GRADE_METHOD = 'lms.djangoapps.certificates.generation_handler._get_course_grade'
ID_VERIFIED_METHOD = 'lms.djangoapps.verify_student.services.IDVerificationService.user_is_verified'
PASSING_GRADE_METHOD = 'lms.djangoapps.certificates.generation_handler._is_passing_grade'
WEB_CERTS_METHOD = 'lms.djangoapps.certificates.generation_handler.has_html_certificates_enabled'


@mock.patch(ID_VERIFIED_METHOD, mock.Mock(return_value=True))
@mock.patch(WEB_CERTS_METHOD, mock.Mock(return_value=True))
@ddt.ddt
class AllowlistTests(ModuleStoreTestCase):
    """
    Tests for handling allowlist certificates
    """

    def setUp(self):
        super().setUp()

        # Create user, a course run, and an enrollment
        self.user = UserFactory()
        self.course_run = CourseFactory()
        self.course_run_key = self.course_run.id  # pylint: disable=no-member
        self.enrollment_mode = CourseMode.VERIFIED
        self.grade = CourseGradeFactory().read(self.user, self.course_run)
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course_run_key,
            is_active=True,
            mode=self.enrollment_mode,
        )

        # Add user to the allowlist
        CertificateAllowlistFactory.create(course_id=self.course_run_key, user=self.user)

    def test_is_on_allowlist(self):
        """
        Test the presence of the user on the allowlist
        """
        assert is_on_certificate_allowlist(self.user, self.course_run_key)

    def test_is_on_allowlist_false(self):
        """
        Test the absence of the user on the allowlist
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        CertificateAllowlistFactory.create(course_id=self.course_run_key, user=u, allowlist=False)
        assert not is_on_certificate_allowlist(u, self.course_run_key)

    @ddt.data(
        (CertificateStatuses.deleted, True),
        (CertificateStatuses.deleting, True),
        (CertificateStatuses.downloadable, False),
        (CertificateStatuses.error, True),
        (CertificateStatuses.generating, True),
        (CertificateStatuses.notpassing, True),
        (CertificateStatuses.restricted, True),
        (CertificateStatuses.unavailable, True),
        (CertificateStatuses.audit_passing, True),
        (CertificateStatuses.audit_notpassing, True),
        (CertificateStatuses.honor_passing, True),
        (CertificateStatuses.unverified, True),
        (CertificateStatuses.invalidated, True),
        (CertificateStatuses.requesting, True))
    @ddt.unpack
    def test_generation_status(self, status, expected_response):
        """
        Test handling of certificate statuses
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=status,
        )

        assert _can_generate_certificate_for_status(u, key, CourseMode.VERIFIED) == expected_response

    def test_generation_status_mode_changed_from_verified(self):
        """
        Test handling of certificate statuses when the mode has changed from verified to audit
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        assert not _can_generate_certificate_for_status(u, key, CourseMode.AUDIT)

    def test_generation_status_mode_changed_from_audit(self):
        """
        Test handling of certificate statuses when the mode has changed from audit to verified
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.audit,
            status=CertificateStatuses.downloadable,
        )

        assert _can_generate_certificate_for_status(u, key, CourseMode.VERIFIED)

    def test_generation_status_mode_changed_from_audit_not_downloadable(self):
        """
        Test handling of certificate statuses when the mode has changed from audit to verified but the cert is not
        downloadable
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.audit,
            status=CertificateStatuses.unverified,
        )

        assert _can_generate_certificate_for_status(u, key, CourseMode.VERIFIED)

    def test_generation_status_for_none(self):
        """
        Test handling of certificate statuses for a non-existent cert
        """
        assert _can_generate_certificate_for_status(None, None, None)

    def test_handle_invalid(self):
        """
        Test handling of an invalid user/course run combo
        """
        u = UserFactory()

        assert not _can_generate_allowlist_certificate(u, self.course_run_key, self.enrollment_mode)
        assert not generate_allowlist_certificate_task(u, self.course_run_key)
        assert not generate_certificate_task(u, self.course_run_key)
        assert _set_allowlist_cert_status(u, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_handle_valid(self):
        """
        Test handling of a valid user/course run combo
        """
        assert _can_generate_allowlist_certificate(self.user, self.course_run_key, self.enrollment_mode)
        assert generate_allowlist_certificate_task(self.user, self.course_run_key)

    def test_handle_valid_general_methods(self):
        """
        Test handling of a valid user/course run combo for the general (non-allowlist) generation methods
        """
        assert generate_certificate_task(self.user, self.course_run_key)

    @ddt.data(False, True)
    def test_can_generate_not_verified(self, enable_idv_requirement):
        """
        Test handling when the user's id is not verified
        """
        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_idv_requirement):
            self.assertNotEqual(
                enable_idv_requirement,
                _can_generate_allowlist_certificate(self.user, self.course_run_key, self.enrollment_mode))
            self.assertIs(
                enable_idv_requirement,
                _set_allowlist_cert_status(
                    self.user, self.course_run_key,
                    self.enrollment_mode, self.grade) == CertificateStatuses.unverified)

    def test_can_generate_not_enrolled(self):
        """
        Test handling when user is not enrolled
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        mode = None
        grade = None
        CertificateAllowlistFactory.create(course_id=key, user=u)
        assert not _can_generate_allowlist_certificate(u, key, mode)
        assert _set_allowlist_cert_status(u, key, mode, grade) is None

    def test_can_generate_audit(self):
        """
        Test handling when user is enrolled in audit mode
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        mode = CourseMode.AUDIT
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=mode,
        )
        CertificateAllowlistFactory.create(course_id=key, user=u)

        assert not _can_generate_allowlist_certificate(u, key, mode)
        assert _set_allowlist_cert_status(u, key, mode, self.grade) is None

    def test_can_generate_not_allowlisted(self):
        """
        Test handling when user is not on the certificate allowlist.
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        assert not _can_generate_allowlist_certificate(u, key, self.enrollment_mode)
        assert _set_allowlist_cert_status(u, key, self.enrollment_mode, self.grade) is None

    def test_can_generate_invalidated(self):
        """
        Test handling when user is on the invalidate list
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        cert = GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )
        CertificateAllowlistFactory.create(course_id=key, user=u)
        CertificateInvalidationFactory.create(
            generated_certificate=cert,
            invalidated_by=self.user,
            active=True
        )

        assert not _can_generate_allowlist_certificate(u, key, self.enrollment_mode)
        assert _set_allowlist_cert_status(u, key, self.enrollment_mode, self.grade) == CertificateStatuses.unavailable

    def test_can_generate_web_cert_disabled(self):
        """
        Test handling when web certs are not enabled
        """
        with mock.patch(WEB_CERTS_METHOD, return_value=False):
            assert not _can_generate_allowlist_certificate(self.user, self.course_run_key, self.enrollment_mode)
            assert _set_allowlist_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_can_generate_no_overview(self):
        """
        Test handling when the course overview is missing
        """
        with mock.patch(COURSE_OVERVIEW_METHOD, return_value=None):
            assert not _can_generate_allowlist_certificate(self.user, self.course_run_key, self.enrollment_mode)
            assert _set_allowlist_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_cert_status_downloadable(self):
        """
        Test cert status when status is already downloadable
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )

        assert _set_allowlist_cert_status(u, key, self.enrollment_mode, self.grade) is None

    def test_generate_allowlist_honor_cert(self):
        """
        Test that verifies we can generate an Honor cert for an Open edX installation configured to support Honor
        certificates.
        """
        course_run = CourseFactory()
        course_run_key = course_run.id  # pylint: disable=no-member
        enrollment_mode = CourseMode.HONOR
        CourseEnrollmentFactory(
            user=self.user,
            course_id=course_run_key,
            is_active=True,
            mode=enrollment_mode,
        )

        CertificateAllowlistFactory.create(course_id=course_run_key, user=self.user)

        # Enable Honor Certificates and verify we can generate an AllowList certificate
        with override_settings(FEATURES={**settings.FEATURES, 'DISABLE_HONOR_CERTIFICATES': False}):
            assert _can_generate_allowlist_certificate(self.user, course_run_key, enrollment_mode)

        # Disable Honor Certificates and verify we cannot generate an AllowList certificate
        with override_settings(FEATURES={**settings.FEATURES, 'DISABLE_HONOR_CERTIFICATES': True}):
            assert not _can_generate_allowlist_certificate(self.user, course_run_key, enrollment_mode)


@mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=False)
@mock.patch(ID_VERIFIED_METHOD, mock.Mock(return_value=True))
@mock.patch(CCX_COURSE_METHOD, mock.Mock(return_value=False))
@mock.patch(PASSING_GRADE_METHOD, mock.Mock(return_value=True))
@mock.patch(WEB_CERTS_METHOD, mock.Mock(return_value=True))
@ddt.ddt
class CertificateTests(ModuleStoreTestCase):
    """
    Tests for handling course certificates
    """

    def setUp(self):
        super().setUp()

        # Create user, a course run, and an enrollment
        self.user = UserFactory()
        self.course_run = CourseFactory()
        self.course_run_key = self.course_run.id  # pylint: disable=no-member
        self.enrollment_mode = CourseMode.VERIFIED
        self.grade = CourseGradeFactory().read(self.user, self.course_run)
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course_run_key,
            is_active=True,
            mode=self.enrollment_mode,
        )

    def test_handle_valid(self):
        """
        Test handling of a valid user/course run combo.
        """
        assert _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode, self.grade)
        assert generate_certificate_task(self.user, self.course_run_key)

    def test_handle_valid_task(self):
        """
        Test handling of a valid user/course run combo.

        We test generate_certificate_task() and _generate_regular_certificate_task() separately since they both
        generate a cert.
        """
        assert _generate_regular_certificate_task(self.user, self.course_run_key) is True

    def test_handle_invalid(self):
        """
        Test handling of an invalid user/course run combo
        """
        other_user = UserFactory()
        mode = None
        grade = None
        assert not _can_generate_regular_certificate(other_user, self.course_run_key, mode, grade)
        assert not generate_certificate_task(other_user, self.course_run_key)
        assert not _generate_regular_certificate_task(other_user, self.course_run_key)

    def test_handle_no_grade(self):
        """
        Test handling when the grade is none
        """
        with mock.patch(GET_GRADE_METHOD, return_value=None):
            assert generate_certificate_task(self.user, self.course_run_key)

    def test_handle_audit_status(self):
        """
        Test handling of a user who is not passing and is enrolled in audit mode
        """
        different_user = UserFactory()
        mode = CourseMode.AUDIT
        CourseEnrollmentFactory(
            user=different_user,
            course_id=self.course_run_key,
            is_active=True,
            mode=mode,
        )

        assert _set_regular_cert_status(different_user, self.course_run_key, mode, self.grade) is None
        assert not _generate_regular_certificate_task(different_user, self.course_run_key)

    def test_handle_not_passing_id_verified_no_cert(self):
        """
        Test handling of a user who is not passing and is id verified and has no cert
        """
        different_user = UserFactory()
        CourseEnrollmentFactory(
            user=different_user,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )

        with mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert _set_regular_cert_status(different_user, self.course_run_key, self.enrollment_mode,
                                            self.grade) is None
            assert not _generate_regular_certificate_task(different_user, self.course_run_key)

    def test_handle_not_passing_id_verified_cert(self):
        """
        Test handling of a user who is not passing and is id verified and has a cert
        """
        different_user = UserFactory()
        CourseEnrollmentFactory(
            user=different_user,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=different_user,
            course_id=self.course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.generating,
        )

        with mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert _set_regular_cert_status(different_user, self.course_run_key, self.enrollment_mode, self.grade) == \
                   CertificateStatuses.notpassing
            assert _generate_regular_certificate_task(different_user, self.course_run_key) is True
            assert not _can_generate_regular_certificate(different_user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)

    @ddt.data(
        (CertificateStatuses.deleted, True),
        (CertificateStatuses.deleting, True),
        (CertificateStatuses.downloadable, False),
        (CertificateStatuses.error, True),
        (CertificateStatuses.generating, True),
        (CertificateStatuses.notpassing, True),
        (CertificateStatuses.restricted, True),
        (CertificateStatuses.unavailable, True),
        (CertificateStatuses.audit_passing, True),
        (CertificateStatuses.audit_notpassing, True),
        (CertificateStatuses.honor_passing, True),
        (CertificateStatuses.unverified, True),
        (CertificateStatuses.invalidated, True),
        (CertificateStatuses.requesting, True))
    @ddt.unpack
    def test_generation_status(self, status, expected_response):
        """
        Test handling of certificate statuses
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=status,
        )

        assert _can_generate_certificate_for_status(u, key, CourseMode.VERIFIED) == expected_response

    def test_generation_status_for_none(self):
        """
        Test handling of certificate statuses for a non-existent cert
        """
        assert _can_generate_certificate_for_status(None, None, None)

    @ddt.data(False, True)
    def test_can_generate_not_verified_cert(self, enable_idv_requirement):
        """
        Test handling when the user's id is not verified and they have a cert
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=self.course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.generating
        )

        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_idv_requirement):
            self.assertNotEqual(
                enable_idv_requirement,
                _can_generate_regular_certificate(u, self.course_run_key, self.enrollment_mode, self.grade)
            )
            regular_cert_status = _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode, self.grade)
            self.assertIs(enable_idv_requirement, regular_cert_status == CertificateStatuses.unverified)

    @ddt.data(False, True)
    def test_can_generate_not_verified_no_cert(self, enable_idv_requirement):
        """
        Test handling when the user's id is not verified and they don't have a cert
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )

        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_idv_requirement):
            self.assertNotEqual(
                enable_idv_requirement,
                _can_generate_regular_certificate(u, self.course_run_key, self.enrollment_mode, self.grade)
            )
            regular_cert_status = _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode, self.grade)
            self.assertIs(enable_idv_requirement, regular_cert_status == CertificateStatuses.unverified)

    @ddt.data(False, True)
    def test_can_generate_not_verified_not_passing(self, enable_idv_requirement):
        """
        Test handling when the user's id is not verified and the user is not passing
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=self.course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.generating
        )

        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_idv_requirement), \
                mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert not _can_generate_regular_certificate(u, self.course_run_key, self.enrollment_mode, self.grade)
            if enable_idv_requirement:
                assert _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode, self.grade) is None
            else:
                assert _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode, self.grade) \
                       == CertificateStatuses.notpassing

    @ddt.data(False, True)
    def test_can_generate_not_verified_not_passing_allowlist(self, enable_idv_requirement):
        """
        Test handling when the user's id is not verified and the user is not passing but is on the allowlist
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=self.course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.generating
        )
        CertificateAllowlistFactory(course_id=self.course_run_key, user=u)

        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_idv_requirement), \
                mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert not _can_generate_regular_certificate(u, self.course_run_key, self.enrollment_mode, self.grade)
            if enable_idv_requirement:
                assert _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode,
                                                self.grade) == CertificateStatuses.unverified
            else:
                assert _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode,
                                                self.grade) == CertificateStatuses.notpassing

    def test_can_generate_ccx(self):
        """
        Test handling when the course is a CCX (custom edX) course
        """
        with mock.patch(CCX_COURSE_METHOD, return_value=True):
            assert not _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)
            assert _set_regular_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_can_generate_beta_tester(self):
        """
        Test handling when the user is a beta tester
        """
        with mock.patch(BETA_TESTER_METHOD, return_value=True):
            assert not _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)
            assert _set_regular_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_can_generate_not_passing_no_cert(self):
        """
        Test handling when the user does not have a passing grade and no cert exists
        """
        with mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert not _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)
            assert _set_regular_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_can_generate_not_passing_cert(self):
        """
        Test handling when the user does not have a passing grade and a cert exists
        """
        u = UserFactory()
        CourseEnrollmentFactory(
            user=u,
            course_id=self.course_run_key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=self.course_run_key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.generating
        )

        with mock.patch(PASSING_GRADE_METHOD, return_value=False):
            assert not _can_generate_regular_certificate(u, self.course_run_key, self.enrollment_mode, self.grade)
            assert _set_regular_cert_status(u, self.course_run_key, self.enrollment_mode,
                                            self.grade) == CertificateStatuses.notpassing

    def test_can_generate_not_enrolled(self):
        """
        Test handling when user is not enrolled
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        mode = None
        grade = None
        assert not _can_generate_regular_certificate(u, key, mode, grade)
        assert _set_regular_cert_status(u, key, mode, grade) is None

    def test_can_generate_audit(self):
        """
        Test handling when user is enrolled in audit mode
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        mode = CourseMode.AUDIT
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=mode,
        )

        assert not _can_generate_regular_certificate(u, key, mode, self.grade)
        assert _set_regular_cert_status(u, key, mode, self.grade) is None

    def test_can_generate_invalidated(self):
        """
        Test handling when user is on the invalidate list
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        cert = GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )
        CertificateInvalidationFactory.create(
            generated_certificate=cert,
            invalidated_by=self.user,
            active=True
        )

        assert not _can_generate_regular_certificate(u, key, self.enrollment_mode, self.grade)
        assert _set_regular_cert_status(u, key, self.enrollment_mode, self.grade) == CertificateStatuses.unavailable

    def test_can_generate_web_cert_disabled(self):
        """
        Test handling when web certs are not enabled
        """
        with mock.patch(WEB_CERTS_METHOD, return_value=False):
            assert not _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)
            assert _set_regular_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_can_generate_no_overview(self):
        """
        Test handling when the course overview is missing
        """
        with mock.patch(COURSE_OVERVIEW_METHOD, return_value=None):
            assert not _can_generate_regular_certificate(self.user, self.course_run_key, self.enrollment_mode,
                                                         self.grade)
            assert _set_regular_cert_status(self.user, self.course_run_key, self.enrollment_mode, self.grade) is None

    def test_cert_status_downloadable(self):
        """
        Test cert status when status is already downloadable
        """
        u = UserFactory()
        cr = CourseFactory()
        key = cr.id  # pylint: disable=no-member
        CourseEnrollmentFactory(
            user=u,
            course_id=key,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=u,
            course_id=key,
            mode=GeneratedCertificate.MODES.verified,
            status=CertificateStatuses.downloadable
        )

        assert _set_regular_cert_status(u, key, self.enrollment_mode, self.grade) is None

    def test_can_generate_honor_cert(self):
        """
        Test that verifies we can generate an Honor cert for an Open edX installation configured to support Honor
        certificates.
        """
        course_run = CourseFactory()
        course_run_key = course_run.id  # pylint: disable=no-member
        enrollment_mode = CourseMode.HONOR
        grade = CourseGradeFactory().read(self.user, course_run)
        CourseEnrollmentFactory(
            user=self.user,
            course_id=course_run_key,
            is_active=True,
            mode=enrollment_mode,
        )

        # Enable Honor Certificates and verify we can generate a certificate
        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch(PASSING_GRADE_METHOD, return_value=True), \
                override_settings(FEATURES={**settings.FEATURES, 'DISABLE_HONOR_CERTIFICATES': False}):
            assert _can_generate_regular_certificate(self.user, course_run_key, enrollment_mode, grade)

        # Disable Honor Certificates and verify we cannot generate a certificate
        with mock.patch(ID_VERIFIED_METHOD, return_value=False), \
                mock.patch(PASSING_GRADE_METHOD, return_value=True), \
                override_settings(FEATURES={**settings.FEATURES, 'DISABLE_HONOR_CERTIFICATES': True}):
            assert not _can_generate_regular_certificate(self.user, course_run_key, enrollment_mode, grade)
