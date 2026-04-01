"""
    Tests for enrollment refund capabilities.
"""

import json
import logging
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ddt
import httpretty
# Explicitly import the cache from ConfigurationModel so we can reset it after each test
from config_models.models import cache
from django.test.client import Client
from django.test.utils import override_settings
from django.urls import reverse
from edx_django_utils.cache import TieredCache, get_cache_key

# These imports refer to lms djangoapps.
# Their testcases are only run under lms.
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAttribute, EnrollmentRefundConfiguration
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.commerce.utils import ECOMMERCE_DATE_FORMAT
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)
TEST_API_URL = 'http://www-internal.example.com/api'
JSON = 'application/json'


@ddt.ddt
@skip_unless_lms
class RefundableTest(SharedModuleStoreTestCase):
    """
    Tests for dashboard utility functions
    """
    USER_PASSWORD = 'test'
    ORDER_NUMBER = 'EDX-100000'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        """ Setup components used by each refund test."""
        super().setUp()
        self.user = UserFactory.create(password=self.USER_PASSWORD)
        self.verified_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='Verified',
            expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        )

        self.enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode='verified')

        self.client = Client()
        cache.clear()

    @patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.refund_cutoff_date')
    def test_refundable(self, cutoff_date):
        """ Assert base case is refundable"""
        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        assert self.enrollment.refundable()

    @patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.refund_cutoff_date')
    def test_refundable_expired_verification(self, cutoff_date):
        """ Assert that enrollment is refundable if course mode has expired."""
        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        self.verified_mode.expiration_datetime = datetime.now(ZoneInfo("UTC")) - timedelta(days=1)
        self.verified_mode.save()
        assert self.enrollment.refundable()

    @patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.refund_cutoff_date')
    def test_refundable_when_certificate_exists(self, cutoff_date):
        """ Assert that enrollment is not refundable once a certificat has been generated."""

        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)

        assert self.enrollment.refundable()

        certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified'
        )

        assert not self.enrollment.refundable()

        # Certificates that are not in the PASSED_STATUSES should allow a refund
        certificate.status = CertificateStatuses.notpassing
        certificate.save()

        assert self.enrollment.refundable()

        # Assert that can_refund overrides this and allows refund
        certificate.status = CertificateStatuses.downloadable
        certificate.save()
        self.enrollment.can_refund = True
        assert self.enrollment.refundable()

    @patch('common.djangoapps.student.models.course_enrollment.CourseEnrollment.refund_cutoff_date')
    def test_refundable_with_cutoff_date(self, cutoff_date):
        """ Assert enrollment is refundable before cutoff and not refundable after."""
        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        assert self.enrollment.refundable()

        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=5)
        assert not self.enrollment.refundable()

        cutoff_date.return_value = datetime.now(ZoneInfo("UTC")) + timedelta(minutes=5)
        assert self.enrollment.refundable()

    @ddt.data(
        (timedelta(days=1), timedelta(days=2), timedelta(days=2), 14),
        (timedelta(days=2), timedelta(days=1), timedelta(days=2), 14),
        (timedelta(days=1), timedelta(days=2), timedelta(days=2), 1),
        (timedelta(days=2), timedelta(days=1), timedelta(days=2), 1),
    )
    @ddt.unpack
    @httpretty.activate
    @override_settings(ECOMMERCE_API_URL=TEST_API_URL)
    def test_refund_cutoff_date(self, order_date_delta, course_start_delta, expected_date_delta, days):
        """
        Assert that the later date is used with the configurable refund period in calculating the returned cutoff date.
        """
        now = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
        order_date = now + order_date_delta
        course_start = now + course_start_delta
        expected_date = now + expected_date_delta
        refund_period = timedelta(days=days)
        date_placed = order_date.strftime(ECOMMERCE_DATE_FORMAT)
        expected_content = f'{{"date_placed": "{date_placed}"}}'

        httpretty.register_uri(
            httpretty.GET,
            f'{TEST_API_URL}/orders/{self.ORDER_NUMBER}/',
            status=200, body=expected_content,
            adding_headers={'Content-Type': JSON}
        )

        self.enrollment.course_overview.start = course_start
        self.enrollment.attributes.create(
            enrollment=self.enrollment,
            namespace='order',
            name='order_number',
            value=self.ORDER_NUMBER
        )

        CONFIG_METHOD_NAME = 'common.djangoapps.student.models.course_enrollment.EnrollmentRefundConfiguration.current'
        with patch(CONFIG_METHOD_NAME) as config:
            instance = config.return_value
            instance.refund_window = refund_period
            assert self.enrollment.refund_cutoff_date() == (expected_date + refund_period)

            expected_date_placed_attr = {
                "namespace": "order",
                "name": "date_placed",
                "value": date_placed,
            }

            assert expected_date_placed_attr in CourseEnrollmentAttribute.get_enrollment_attributes(self.enrollment)

    @ddt.data(
        (datetime.now(ZoneInfo("UTC")) + timedelta(days=1), True),
        (datetime.now(ZoneInfo("UTC")) - timedelta(days=1), False),
        (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=5), False),
    )
    @ddt.unpack
    @httpretty.activate
    @override_settings(ECOMMERCE_API_URL=TEST_API_URL)
    def test_is_order_voucher_refundable(self, voucher_expiration_date, expected):
        """
        Assert that the correct value is returned based on voucher expiration date.
        """
        voucher_expiration_date_str = voucher_expiration_date.strftime(ECOMMERCE_DATE_FORMAT)
        response = json.dumps({"vouchers": [{"end_datetime": voucher_expiration_date_str}]})
        httpretty.register_uri(
            httpretty.GET,
            f'{TEST_API_URL}/orders/{self.ORDER_NUMBER}/',
            status=200, body=response,
            adding_headers={'Content-Type': JSON}
        )

        self.enrollment.attributes.create(
            enrollment=self.enrollment,
            namespace='order',
            name='order_number',
            value=self.ORDER_NUMBER
        )
        assert self.enrollment.is_order_voucher_refundable() == expected

    def test_refund_cutoff_date_no_attributes(self):
        """ Assert that the None is returned when no order number attribute is found."""
        assert self.enrollment.refund_cutoff_date() is None

    @httpretty.activate
    @override_settings(ECOMMERCE_API_URL=TEST_API_URL)
    def test_is_order_voucher_refundable_no_attributes(self, ):
        """ Assert that False is returned when no order number or vouchers attribute is found in response."""
        # no order number attribute
        assert self.enrollment.is_order_voucher_refundable() is False

        # no voucher information in orders api response
        response = json.dumps({"vouchers": []})
        httpretty.register_uri(
            httpretty.GET,
            f'{TEST_API_URL}/orders/{self.ORDER_NUMBER}/',
            status=200, body=response,
            adding_headers={'Content-Type': JSON}
        )

        self.enrollment.attributes.create(
            enrollment=self.enrollment,
            namespace='order',
            name='order_number',
            value=self.ORDER_NUMBER
        )
        assert self.enrollment.is_order_voucher_refundable() is False

        response = json.dumps({"vouchers": None})
        httpretty.register_uri(
            httpretty.GET,
            f'{TEST_API_URL}/orders/{self.ORDER_NUMBER}/',
            status=200, body=response,
            adding_headers={'Content-Type': JSON}
        )
        assert self.enrollment.is_order_voucher_refundable() is False

    @patch('openedx.core.djangoapps.commerce.utils.get_ecommerce_api_client')
    def test_get_order_attribute_from_ecommerce(self, mock_ecommerce_api_client):
        """
        Assert that the get_order_attribute_from_ecommerce method returns order details if it's already cached,
        without calling ecommerce.
        """
        order_details = {"number": self.ORDER_NUMBER, "vouchers": [{"end_datetime": '2025-09-25T00:00:00Z'}]}
        cache_key = get_cache_key(user_id=self.user.id, order_number=self.ORDER_NUMBER)
        TieredCache.set_all_tiers(cache_key, order_details, 60)

        self.enrollment.attributes.create(
            enrollment=self.enrollment,
            namespace='order',
            name='order_number',
            value=self.ORDER_NUMBER
        )
        assert self.enrollment.get_order_attribute_from_ecommerce("vouchers") == order_details["vouchers"]
        mock_ecommerce_api_client.assert_not_called()

    @patch('openedx.core.djangoapps.commerce.utils.get_ecommerce_api_client')
    def test_refund_cutoff_date_with_date_placed_attr(self, mock_ecommerce_api_client):
        """
        Assert that the refund_cutoff_date returns order placement date if order:date_placed
        attribute exist without calling ecommerce.
        """
        now = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
        order_date = now + timedelta(days=2)
        course_start = now + timedelta(days=1)

        self.enrollment.course_overview.start = course_start
        self.enrollment.attributes.create(
            enrollment=self.enrollment,
            namespace='order',
            name='date_placed',
            value=order_date.strftime(ECOMMERCE_DATE_FORMAT)
        )

        refund_config = EnrollmentRefundConfiguration.current()
        assert self.enrollment.refund_cutoff_date() == (order_date + refund_config.refund_window)
        mock_ecommerce_api_client.assert_not_called()

    @httpretty.activate
    @override_settings(ECOMMERCE_API_URL=TEST_API_URL)
    def test_multiple_refunds_dashbaord_page_error(self):
        """ Order with mutiple refunds will not throw 500 error when dashboard page will access."""
        now = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
        order_date = now + timedelta(days=1)
        expected_content = f'{{"date_placed": "{order_date.strftime(ECOMMERCE_DATE_FORMAT)}"}}'

        httpretty.register_uri(
            httpretty.GET,
            f'{TEST_API_URL}/orders/{self.ORDER_NUMBER}/',
            status=200, body=expected_content,
            adding_headers={'Content-Type': JSON}
        )

        # creating multiple attributes for same order.
        for attribute_count in range(2):  # pylint: disable=unused-variable
            self.enrollment.attributes.create(
                enrollment=self.enrollment,
                namespace='order',
                name='order_number',
                value=self.ORDER_NUMBER
            )

        self.client.login(username=self.user.username, password=self.USER_PASSWORD)
        resp = self.client.post(reverse('dashboard', args=[]))
        assert resp.status_code == 200
