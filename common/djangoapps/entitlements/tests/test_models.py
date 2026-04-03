"""Test Entitlements models"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase
from django.utils.timezone import now

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import TEST_PASSWORD, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.api import MODES
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

# Entitlements is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from common.djangoapps.entitlements.models import CourseEntitlement
    from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory


@skip_unless_lms
class TestCourseEntitlementModelHelpers(ModuleStoreTestCase):
    """
    Series of tests for the helper methods in the CourseEntitlement Model Class.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    def test_check_for_existing_entitlement_and_enroll(self, mock_get_course_uuid):
        course = CourseFactory()
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )
        entitlement = CourseEntitlementFactory.create(
            mode=CourseMode.VERIFIED,
            user=self.user,
        )
        mock_get_course_uuid.return_value = entitlement.course_uuid

        assert not CourseEnrollment.is_enrolled(user=self.user, course_key=course.id)

        CourseEntitlement.check_for_existing_entitlement_and_enroll(
            user=self.user,
            course_run_key=course.id,
        )

        assert CourseEnrollment.is_enrolled(user=self.user, course_key=course.id)

        entitlement.refresh_from_db()
        assert entitlement.enrollment_course_run

    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    def test_check_for_no_entitlement_and_do_not_enroll(self, mock_get_course_uuid):
        course = CourseFactory()
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )
        entitlement = CourseEntitlementFactory.create(
            mode=CourseMode.VERIFIED,
            user=self.user,
        )
        mock_get_course_uuid.return_value = None

        assert not CourseEnrollment.is_enrolled(user=self.user, course_key=course.id)

        CourseEntitlement.check_for_existing_entitlement_and_enroll(
            user=self.user,
            course_run_key=course.id,
        )

        assert not CourseEnrollment.is_enrolled(user=self.user, course_key=course.id)

        entitlement.refresh_from_db()
        assert entitlement.enrollment_course_run is None

        new_course = CourseFactory()
        CourseModeFactory(
            course_id=new_course.id,  # lint-amnesty, pylint: disable=no-member
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )

        # Return invalid uuid so that no entitlement returned for this new course
        mock_get_course_uuid.return_value = uuid4().hex

        try:
            CourseEntitlement.check_for_existing_entitlement_and_enroll(
                user=self.user,
                course_run_key=new_course.id,
            )
            assert not CourseEnrollment.is_enrolled(user=self.user, course_key=new_course.id)
        except AttributeError as error:
            self.fail(error.message)  # lint-amnesty, pylint: disable=no-member


@skip_unless_lms
class TestModels(TestCase):
    """Test entitlement with policy model functions."""

    def setUp(self):
        super().setUp()
        self.course = CourseOverviewFactory.create(
            start=now()
        )
        self.enrollment = CourseEnrollmentFactory.create(course_id=self.course.id)
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    def test_is_entitlement_redeemable(self):
        """
        Test that the entitlement is not expired when created now, and is expired when created 2 years
        ago with a policy that sets the expiration period to 450 days
        """

        entitlement = CourseEntitlementFactory.create()

        assert entitlement.is_entitlement_redeemable() is True

        # Create a date 2 years in the past (greater than the policy expire period of 450 days)
        past_datetime = now() - timedelta(days=365 * 2)
        entitlement.created = past_datetime
        entitlement.save()

        assert entitlement.is_entitlement_redeemable() is False

        entitlement = CourseEntitlementFactory.create(expired_at=now())

        assert entitlement.is_entitlement_refundable() is False

    def test_is_entitlement_refundable(self):
        """
        Test that the entitlement is refundable when created now, and is not refundable when created 70 days
        ago with a policy that sets the expiration period to 60 days. Also test that if the entitlement is spent
        and greater than 14 days it is no longer refundable.
        """
        entitlement = CourseEntitlementFactory.create()
        assert entitlement.is_entitlement_refundable() is True

        # If there is no order_number make sure the entitlement is not refundable
        entitlement.order_number = None
        assert entitlement.is_entitlement_refundable() is False

        # Create a date 70 days in the past (greater than the policy refund expire period of 60 days)
        past_datetime = now() - timedelta(days=70)
        entitlement = CourseEntitlementFactory.create(created=past_datetime)

        assert entitlement.is_entitlement_refundable() is False

        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        # Create a date 20 days in the past (less than the policy refund expire period of 60 days)
        # but more than the policy regain period of 14 days and also the course start
        past_datetime = now() - timedelta(days=20)
        entitlement.created = past_datetime
        self.enrollment.created = past_datetime
        self.course.start = past_datetime
        entitlement.save()
        self.course.save()
        self.enrollment.save()

        assert entitlement.is_entitlement_refundable() is False

        # Removing the entitlement being redeemed, make sure that the entitlement is refundable
        entitlement.enrollment_course_run = None

        assert entitlement.is_entitlement_refundable() is True

        entitlement = CourseEntitlementFactory.create(expired_at=now())

        assert entitlement.is_entitlement_refundable() is False

    def test_is_entitlement_regainable(self):
        """
        Test that the entitlement is not expired when created now, and is expired when created 20 days
        ago with a policy that sets the expiration period to 14 days
        """
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        assert entitlement.is_entitlement_regainable() is True

        # Create and associate a GeneratedCertificate for a user and course and make sure it isn't regainable
        certificate = GeneratedCertificateFactory(
            user=entitlement.user,
            course_id=entitlement.enrollment_course_run.course_id,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        assert entitlement.is_entitlement_regainable() is False

        certificate.status = CertificateStatuses.notpassing
        certificate.save()

        assert entitlement.is_entitlement_regainable() is True

        # Create a date 20 days in the past (greater than the policy expire period of 14 days)
        # and apply it to both the entitlement and the course
        past_datetime = now() - timedelta(days=20)
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment, created=past_datetime)
        self.enrollment.created = past_datetime
        self.course.start = past_datetime

        self.course.save()
        self.enrollment.save()

        assert entitlement.is_entitlement_regainable() is False

        entitlement = CourseEntitlementFactory.create(expired_at=now())

        assert entitlement.is_entitlement_regainable

    def test_get_days_until_expiration(self):
        """
        Test that the expiration period is always less than or equal to the policy expiration
        """
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        # This will always either be 1 less than the expiration_period_days because the get_days_until_expiration
        # method will have had at least some time pass between object creation in setUp and this method execution,
        # or the exact same as the original expiration_period_days if somehow no time has passed
        assert entitlement.get_days_until_expiration() <= entitlement.policy.expiration_period.days

    def test_expired_at_datetime(self):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Tests that using the getter method properly updates the expired_at field for an entitlement
        """

        # Verify a brand new entitlement isn't expired and the db row isn't updated
        entitlement = CourseEntitlementFactory.create()
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime is None
        assert entitlement.expired_at is None

        # Verify an entitlement from three years ago day is expired and the db row is updated
        past_datetime = now() - timedelta(days=365 * 3)
        entitlement.created = past_datetime
        entitlement.save()
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime
        assert entitlement.expired_at

        # Verify that a brand new entitlement that has been redeemed is not expired
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        assert entitlement.enrollment_course_run
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime is None
        assert entitlement.expired_at is None

        # Verify that an entitlement that has been redeemed but not within 14 days
        # and the course started more than two weeks ago is expired
        past_datetime = now() - timedelta(days=20)
        entitlement.created = past_datetime
        self.enrollment.created = past_datetime
        self.course.start = past_datetime
        entitlement.save()
        self.course.save()
        self.enrollment.save()
        assert entitlement.enrollment_course_run
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime
        assert entitlement.expired_at

        # Verify that an entitlement that has just been created, but the user has been enrolled in the course for
        # greater than 14 days, and the course started more than 14 days ago is not expired
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        past_datetime = now() - timedelta(days=20)
        entitlement.created = now()
        self.enrollment.created = past_datetime
        self.course.start = past_datetime
        entitlement.save()
        self.enrollment.save()
        self.course.save()
        assert entitlement.enrollment_course_run
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime is None
        assert entitlement.expired_at is None

        # Verify a date 731 days in the past (1 days after the policy expiration)
        # That is enrolled and started in within the regain period is still expired
        entitlement = CourseEntitlementFactory.create(enrollment_course_run=self.enrollment)
        expired_datetime = now() - timedelta(days=731)
        entitlement.created = expired_datetime
        start = now()
        self.enrollment.created = start
        self.course.start = start
        entitlement.save()
        self.course.save()
        self.enrollment.save()
        assert entitlement.enrollment_course_run
        expired_at_datetime = entitlement.expired_at_datetime
        assert expired_at_datetime
        assert entitlement.expired_at

    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    @patch("common.djangoapps.entitlements.models.CourseEntitlement.refund")
    def test_unenroll_entitlement_with_audit_course_enrollment(self, mock_refund, mock_get_course_uuid):
        """
        Test that entitlement is not refunded if un-enroll is called on audit course un-enroll.
        """
        self.enrollment.mode = CourseMode.AUDIT
        self.enrollment.user = self.user
        self.enrollment.save()
        entitlement = CourseEntitlementFactory.create(user=self.user)
        mock_get_course_uuid.return_value = entitlement.course_uuid
        CourseEnrollment.unenroll(self.user, self.course.id)

        assert not mock_refund.called
        entitlement.refresh_from_db()
        assert entitlement.expired_at is None

        self.enrollment.mode = CourseMode.VERIFIED
        self.enrollment.is_active = True
        self.enrollment.save()
        entitlement.enrollment_course_run = self.enrollment
        entitlement.save()
        CourseEnrollment.unenroll(self.user, self.course.id)

        assert mock_refund.called
        entitlement.refresh_from_db()
        assert entitlement.expired_at < now()
