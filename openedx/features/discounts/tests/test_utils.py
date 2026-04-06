"""
Tests of the openedx.features.discounts.utils module.
"""
from unittest.mock import Mock, patch

import ddt
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.utils.translation import override as override_lang
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.discounts.applicability import (
    DISCOUNT_APPLICABILITY_FLAG,
    FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG,
    get_discount_expiration_date,
)

from .. import utils


@ddt.ddt
class TestStrikeoutPrice(TestCase):
    """
    Tests of the strike-out pricing for discounts.
    """
    def test_not_eligible(self):
        with patch.multiple(
            utils,
            can_receive_discount=Mock(return_value=False),
            get_course_prices=Mock(return_value=(100, None))
        ):
            content, has_discount = utils.format_strikeout_price(Mock(name='user'), Mock(name='course'))

        assert str(content) == "<span class='price'>$100</span>"
        assert not has_discount

    @ddt.data((15, 100, "$100", "$85",), (50, 50, "$50", "$25"), (10, 99, "$99", "$89.10"))
    @ddt.unpack
    def test_eligible_eligible(self, discount_percentage, base_price, formatted_base_price, final_price):
        with patch.multiple(
            utils,
            can_receive_discount=Mock(return_value=True),
            get_course_prices=Mock(return_value=(base_price, None)),
            discount_percentage=Mock(return_value=discount_percentage)
        ):
            content, has_discount = utils.format_strikeout_price(Mock(name='user'), Mock(name='course'))

        assert str(content) == (  # noqa: UP032
            "<span class='sr-only'>"
            "Original price: <span class='price original'>{original_price}</span>, discount price: "
            "</span>"
            "<span class='price discount'>{discount_price}</span> "
            "<del aria-hidden='true'><span class='price original'>{original_price}</span></del>"
        ).format(original_price=formatted_base_price, discount_price=final_price)
        assert has_discount


@override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
class TestOfferData(TestCase):
    """
    Tests of the generate_offer_data call.
    """
    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.overview = CourseOverviewFactory()
        CourseModeFactory(course_id=self.overview.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory(course_id=self.overview.id, mode_slug=CourseMode.VERIFIED, min_price=149)
        CourseEnrollment.enroll(self.user, self.overview.id, CourseMode.AUDIT)

    def test_happy_path(self):
        assert utils.generate_offer_data(self.user, self.overview) == {
            'code': 'EDXWELCOME',
            'expiration_date': get_discount_expiration_date(self.user, self.overview),
            'original_price': '$149',
            'discounted_price': '$126.65',
            'percentage': 15,
            'upgrade_url': '/dashboard'
        }

    def test_spanish_code(self):
        with override_lang('es-419'):
            assert utils.generate_offer_data(self.user, self.overview)['code'] == 'BIENVENIDOAEDX'

    def test_override(self):
        with override_settings(FIRST_PURCHASE_DISCOUNT_OVERRIDE_CODE='NOTEDXWELCOME'):
            with override_settings(FIRST_PURCHASE_DISCOUNT_OVERRIDE_PERCENTAGE=30):
                with override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True):
                    with override_waffle_flag(FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG, active=True):
                        assert utils.generate_offer_data(self.user, self.overview)['code'] == 'NOTEDXWELCOME'
                        assert utils.generate_offer_data(self.user, self.overview)['percentage'] == 30

    def test_anonymous(self):
        assert utils.generate_offer_data(AnonymousUser(), self.overview) is None

    @patch('openedx.features.discounts.utils.can_receive_discount', return_value=False)
    def test_no_discount(self, _mock):  # noqa: PT019
        assert utils.generate_offer_data(self.user, self.overview) is None
