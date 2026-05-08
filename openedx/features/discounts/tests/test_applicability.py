"""Tests of openedx.features.discounts.applicability"""


from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import ddt
import pytest
from django.contrib.sites.models import Site
from django.utils.timezone import now
from edx_toggles.toggles.testutils import override_waffle_flag
from enterprise.models import EnterpriseCustomer, EnterpriseCustomerUser

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.experiments.models import ExperimentData
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.discounts.models import DiscountRestrictionConfig
from openedx.features.discounts.utils import REV1008_EXPERIMENT_ID
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

from ..applicability import DISCOUNT_APPLICABILITY_FLAG, _is_in_holdback_and_bucket, can_receive_discount


@ddt.ddt
class TestApplicability(ModuleStoreTestCase):
    """
    Applicability determines if this combination of user and course can receive a discount. Make
    sure that all of the business conditions work.
    """

    def setUp(self):
        super().setUp()
        self.site, _ = Site.objects.get_or_create(domain='example.com')
        self.user = UserFactory.create()
        self.course = CourseFactory.create(run='test', display_name='test')
        CourseModeFactory.create(course_id=self.course.id, mode_slug='verified')
        now_time = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S%z")
        ExperimentData.objects.create(
            user=self.user, experiment_id=REV1008_EXPERIMENT_ID, key=str(self.course.id), value=now_time
        )

        holdback_patcher = patch(
            'openedx.features.discounts.applicability._is_in_holdback_and_bucket', return_value=False
        )
        self.mock_holdback = holdback_patcher.start()
        self.addCleanup(holdback_patcher.stop)

    def test_can_receive_discount(self):
        # Right now, no one should be able to receive the discount
        applicability = can_receive_discount(user=self.user, course=self.course)
        assert applicability is False

    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_can_receive_discount_course_requirements(self):
        """
        Ensure first purchase offer banner only displays for courses with a non-expired verified mode
        """
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        applicability = can_receive_discount(user=self.user, course=self.course)
        assert applicability is True

        no_verified_mode_course = CourseFactory(end=now() + timedelta(days=30))
        applicability = can_receive_discount(user=self.user, course=no_verified_mode_course)
        assert applicability is False

        course_that_has_ended = CourseFactory(end=now() - timedelta(days=30))
        applicability = can_receive_discount(user=self.user, course=course_that_has_ended)
        assert applicability is False

        disabled_course = CourseFactory()
        CourseModeFactory.create(course_id=disabled_course.id, mode_slug='verified')  # pylint: disable=no-member
        disabled_course_overview = CourseOverview.get_from_id(disabled_course.id)  # pylint: disable=no-member
        DiscountRestrictionConfig.objects.create(disabled=True, course=disabled_course_overview)
        applicability = can_receive_discount(user=self.user, course=disabled_course)
        assert applicability is False

    @ddt.data(*(
        [[]] +
        [[mode] for mode in CourseMode.ALL_MODES] +
        [
            [mode1, mode2]
            for mode1 in CourseMode.ALL_MODES
            for mode2 in CourseMode.ALL_MODES
            if mode1 != mode2
        ]
    ))
    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_can_receive_discount_previous_verified_enrollment(self, existing_enrollments):
        """
        Ensure that only users who have not already purchased courses receive the discount.
        """
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        for mode in existing_enrollments:
            CourseEnrollmentFactory.create(mode=mode, user=self.user)

        applicability = can_receive_discount(user=self.user, course=self.course)
        assert applicability == all(mode in CourseMode.UPSELL_TO_VERIFIED_MODES for mode in existing_enrollments)

    @ddt.data(
        None,
        CourseMode.VERIFIED,
        CourseMode.PROFESSIONAL,
    )
    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_can_receive_discount_entitlement(self, entitlement_mode):
        """
        Ensure that only users who have not already purchased courses receive the discount.
        """
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        if entitlement_mode is not None:
            CourseEntitlementFactory.create(mode=entitlement_mode, user=self.user)

        applicability = can_receive_discount(user=self.user, course=self.course)
        assert applicability == (entitlement_mode is None)

    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_can_receive_discount_false_enterprise(self):
        """
        Ensure that enterprise users do not receive the discount.
        """
        enterprise_customer = EnterpriseCustomer.objects.create(
            name='Test EnterpriseCustomer',
            site=self.site
        )
        EnterpriseCustomerUser.objects.create(
            user_id=self.user.id,
            enterprise_customer=enterprise_customer
        )

        applicability = can_receive_discount(user=self.user, course=self.course)
        assert applicability is False

    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_holdback_denies_discount(self):
        """
        Ensure that users in the holdback do not receive the discount.
        """
        self.mock_holdback.return_value = True

        applicability = can_receive_discount(user=self.user, course=self.course)
        assert not applicability

    @ddt.data(
        (0, True),
        (1, False),
    )
    @ddt.unpack
    @pytest.mark.skip(reason="fix under work by revenue team")
    def test_holdback_group_ids(self, group_number, in_holdback):
        with patch('openedx.features.discounts.applicability.stable_bucketing_hash_group', return_value=group_number):
            assert _is_in_holdback_and_bucket(self.user) == in_holdback

    @pytest.mark.skip(reason="fix under work by revenue team")
    def test_holdback_expiry(self):
        with patch('openedx.features.discounts.applicability.stable_bucketing_hash_group', return_value=0):
            with patch(
                'openedx.features.discounts.applicability.datetime',
                Mock(now=Mock(return_value=datetime(2020, 8, 1, 0, 1, tzinfo=ZoneInfo("UTC"))), wraps=datetime),
            ):
                assert not _is_in_holdback_and_bucket(self.user)
