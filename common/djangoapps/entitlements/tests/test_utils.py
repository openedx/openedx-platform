"""
Test entitlements utilities
"""


from datetime import timedelta

from django.conf import settings
from django.utils.timezone import now
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import (  # lint-amnesty, pylint: disable=line-too-long
    TEST_PASSWORD,
    CourseEnrollmentFactory,
    CourseOverviewFactory,
    UserFactory,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)

# Entitlements is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
    from common.djangoapps.entitlements.utils import is_course_run_entitlement_fulfillable


@skip_unless_lms
class TestCourseRunFulfillableForEntitlement(ModuleStoreTestCase):
    """
    Tests for the utility function is_course_run_entitlement_fulfillable
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory(is_staff=True)
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    def create_course(  # lint-amnesty, pylint: disable=missing-function-docstring
            self,
            start_from_now,
            end_from_now,
            enrollment_start_from_now,
            enrollment_end_from_now,
            upgraded_ended_from_now=1
    ):
        course_overview = CourseOverviewFactory.create(
            start=now() + timedelta(days=start_from_now),
            end=now() + timedelta(days=end_from_now),
            enrollment_start=now() + timedelta(days=enrollment_start_from_now),
            enrollment_end=now() + timedelta(days=enrollment_end_from_now)
        )

        CourseModeFactory(
            course_id=course_overview.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=upgraded_ended_from_now)
        )
        return course_overview

    def test_course_run_fulfillable(self):
        course_overview = self.create_course(
            start_from_now=-2,
            end_from_now=2,
            enrollment_start_from_now=-1,
            enrollment_end_from_now=1
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_missing_overview_not_fulfillable(self):
        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert not is_course_run_entitlement_fulfillable(
            CourseKey.from_string('course-v1:edx+FakeCourse+3T2017'),
            entitlement
        )

    def test_course_run_not_fulfillable_no_start_date(self):
        course_overview = self.create_course(
            start_from_now=-2,
            end_from_now=2,
            enrollment_start_from_now=-1,
            enrollment_end_from_now=1
        )
        course_overview.start = None
        course_overview.save()

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert not is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_not_fulfillable_run_ended(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=-1,
            enrollment_start_from_now=-3,
            enrollment_end_from_now=-2
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert not is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_not_fulfillable_enrollment_start_in_future(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=2,
            enrollment_start_from_now=2,
            enrollment_end_from_now=4
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert not is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_fulfillable_user_enrolled(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=2,
            enrollment_start_from_now=-2,
            enrollment_end_from_now=1
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        # Enroll User in the Course, but do not update the entitlement
        CourseEnrollmentFactory.create(user=entitlement.user, course_id=course_overview.id)

        assert is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_fulfillable_enrollment_ended_already_enrolled(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=2,
            enrollment_start_from_now=-2,
            enrollment_end_from_now=-1,
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        # Enroll User in the Course, but do not update the entitlement
        CourseEnrollmentFactory.create(user=entitlement.user, course_id=course_overview.id)

        assert is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_not_fulfillable_upgrade_ended(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=2,
            enrollment_start_from_now=-2,
            enrollment_end_from_now=1,
            upgraded_ended_from_now=-1
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)

        assert not is_course_run_entitlement_fulfillable(course_overview.id, entitlement)

    def test_course_run_fulfillable_already_enrolled_course_ended(self):
        course_overview = self.create_course(
            start_from_now=-3,
            end_from_now=-1,
            enrollment_start_from_now=-2,
            enrollment_end_from_now=-1,
        )

        entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        CourseEnrollmentFactory.create(user=entitlement.user, course_id=course_overview.id)

        assert is_course_run_entitlement_fulfillable(course_overview.id, entitlement)
