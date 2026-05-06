"""Tests of commerce utilities."""


import json
from unittest.mock import patch
from urllib.parse import urlencode

import ddt
import httpretty
from django.conf import settings
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator
from waffle.testutils import override_switch

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.commerce.utils import EcommerceService, refund_entitlement, refund_seat
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.log_utils import audit_log
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

# Entitlements is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory


def update_commerce_config(enabled=False, checkout_page='/test_basket/add/'):
    """ Enable / Disable CommerceConfiguration model """
    CommerceConfiguration.objects.create(
        checkout_on_ecommerce_service=enabled,
        basket_checkout_page=checkout_page,
    )


class AuditLogTests(TestCase):
    """Tests of the commerce audit logging helper."""

    @patch('openedx.core.lib.log_utils.log')
    def test_log_message(self, mock_log):
        """Verify that log messages are constructed correctly."""
        audit_log('foo', qux='quux', bar='baz')

        # Verify that the logged message contains comma-separated
        # key-value pairs ordered alphabetically by key.
        message = 'foo: bar="baz", qux="quux"'
        mock_log.info.assert_called_with(message)


@ddt.ddt
class EcommerceServiceTests(TestCase):
    """Tests for the EcommerceService helper class."""

    def setUp(self):
        self.request_factory = RequestFactory()
        self.user = UserFactory.create()
        self.request = self.request_factory.get("foo")
        update_commerce_config(enabled=True)
        super().setUp()

    def test_is_enabled(self):
        """Verify that is_enabled() returns True when ecomm checkout is enabled. """
        is_enabled = EcommerceService().is_enabled(self.user)
        assert is_enabled

        config = CommerceConfiguration.current()
        config.checkout_on_ecommerce_service = False
        config.save()
        is_not_enabled = EcommerceService().is_enabled(self.user)
        assert not is_not_enabled

    # TODO: replace override_switch with override_waffle_switch when the
    # DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH will be defined as actual WaffleSwitch. Now
    # we have only switch name defined in the settings
    @override_switch(settings.DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH, active=True)
    def test_is_enabled_activation_requirement_disabled(self):
        """Verify that is_enabled() returns True when ecomm checkout is enabled. """
        self.user.is_active = False
        self.user.save()
        is_enabled = EcommerceService().is_enabled(self.user)
        assert is_enabled

    @patch('openedx.core.djangoapps.theming.helpers.is_request_in_themed_site')
    def test_is_enabled_for_sites(self, is_site):
        """Verify that is_enabled() returns True if used for a site."""
        is_site.return_value = True
        is_enabled = EcommerceService().is_enabled(self.user)
        assert is_enabled

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    def test_ecommerce_url_root(self):
        """Verify that the proper root URL is returned."""
        assert EcommerceService().ecommerce_url_root == 'http://ecommerce_url'

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    def test_get_absolute_ecommerce_url(self):
        """Verify that the proper URL is returned."""
        url = EcommerceService().get_absolute_ecommerce_url('/test_basket/')
        assert url == 'http://ecommerce_url/test_basket/'

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    def test_get_receipt_page_url(self):
        """Verify that the proper Receipt page URL is returned."""
        order_number = 'ORDER1'
        url = EcommerceService().get_receipt_page_url(order_number)
        expected_url = f'http://ecommerce_url/checkout/receipt/?order_number={order_number}'
        assert url == expected_url

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    def test_get_order_dashboard_url(self):
        """Verify that the proper order dashboard url is returned."""
        url = EcommerceService().get_order_dashboard_url()
        expected_url = 'http://ecommerce_url/dashboard/orders/'
        assert url == expected_url

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    @ddt.data(
        {
            'skus': ['TESTSKU']
        },
        {
            'skus': ['TESTSKU1', 'TESTSKU2', 'TESTSKU3']
        },
        {
            'skus': ['TESTSKU'],
            'program_uuid': '12345678-9012-3456-7890-123456789012'
        },
        {
            'skus': ['TESTSKU1', 'TESTSKU2', 'TESTSKU3'],
            'program_uuid': '12345678-9012-3456-7890-123456789012'
        }
    )
    def test_get_checkout_page_url(self, skus, program_uuid=None):
        """ Verify the checkout page URL is properly constructed and returned. """
        url = EcommerceService().get_checkout_page_url(*skus, program_uuid=program_uuid)
        config = CommerceConfiguration.current()
        expected_url = '{root}{basket_url}?{skus}'.format(
            basket_url=config.basket_checkout_page,
            root=settings.ECOMMERCE_PUBLIC_URL_ROOT,
            skus=urlencode({'sku': skus}, doseq=True),
        )
        if program_uuid:
            expected_url = '{expected_url}&basket={program_uuid}'.format(  # noqa: UP032
                expected_url=expected_url,
                program_uuid=program_uuid
            )
        assert url == expected_url

    @override_settings(ECOMMERCE_PUBLIC_URL_ROOT='http://ecommerce_url')
    @ddt.data(
        {
            'skus': ['TESTSKU'],
            'enterprise_catalog_uuid': None
        },
        {
            'skus': ['TESTSKU'],
            'enterprise_catalog_uuid': '6eca3efb-f3a0-4c08-806f-c6e6b65d61cb'
        },
    )
    @ddt.unpack
    def test_get_checkout_page_url_with_enterprise_catalog_uuid(self, skus, enterprise_catalog_uuid):
        """ Verify the checkout page URL is properly constructed and returned. """
        url = EcommerceService().get_checkout_page_url(
            *skus,
            catalog=enterprise_catalog_uuid
        )
        config = CommerceConfiguration.current()

        query = {'sku': skus}
        if enterprise_catalog_uuid:
            query.update({'catalog': enterprise_catalog_uuid})

        expected_url = '{root}{basket_url}?{skus}'.format(  # noqa: UP032
            basket_url=config.basket_checkout_page,
            root=settings.ECOMMERCE_PUBLIC_URL_ROOT,
            skus=urlencode(query, doseq=True),
        )

        assert url == expected_url


@ddt.ddt
@skip_unless_lms
class RefundUtilMethodTests(ModuleStoreTestCase):
    """Tests for Refund Utilities"""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        UserFactory(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME, is_staff=True)

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.course = CourseFactory.create(org='edX', number='DemoX', display_name='Demo_Course')
        self.course2 = CourseFactory.create(org='edX', number='DemoX2', display_name='Demo_Course 2')

    @patch('lms.djangoapps.commerce.utils.is_commerce_service_configured', return_value=False)
    def test_ecommerce_service_not_configured(self, mock_commerce_configured):
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        refund_success = refund_entitlement(course_entitlement)
        assert mock_commerce_configured.is_called
        assert not refund_success

    @httpretty.activate
    def test_no_ecommerce_connection_and_failure(self):
        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=404,
            body='{}',
            content_type='application/json'
        )
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        refund_success = refund_entitlement(course_entitlement)
        assert not refund_success

    @httpretty.activate
    def test_ecommerce_successful_refund(self):
        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=201,
            body='[1]',
            content_type='application/json'
        )
        httpretty.register_uri(
            httpretty.PUT,
            settings.ECOMMERCE_API_URL + 'refunds/1/process/',
            status=200,
            body=json.dumps({
                "id": 9,
                "created": "2017-12-21T18:23:49.468298Z",
                "modified": "2017-12-21T18:24:02.741426Z",
                "total_credit_excl_tax": "100.00",
                "currency": "USD",
                "status": "Complete",
                "order": 15,
                "user": 5
            }),
            content_type='application/json'
        )
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        refund_success = refund_entitlement(course_entitlement)
        assert refund_success

    @httpretty.activate
    @patch('lms.djangoapps.commerce.utils._send_refund_notification', return_value=True)
    def test_ecommerce_refund_failed_process_notification_sent(self, mock_send_notification):
        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=201,
            body='[1]',
            content_type='application/json'
        )
        httpretty.register_uri(
            httpretty.PUT,
            settings.ECOMMERCE_API_URL + 'refunds/1/process/',
            status=400,
            body='{}',
            content_type='application/json'
        )
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        refund_success = refund_entitlement(course_entitlement)
        assert mock_send_notification.is_called
        call_args = list(mock_send_notification.call_args)
        assert call_args[0] == (course_entitlement.user, [1])
        assert refund_success

    @httpretty.activate
    @patch('lms.djangoapps.commerce.utils._send_refund_notification', return_value=True)
    def test_ecommerce_refund_not_verified_notification_for_entitlement(self, mock_send_notification):
        """
        Note that we are currently notifying Support whenever a refund require approval for entitlements as
        Entitlements are only available in paid modes. This test should be updated if this logic changes
        in the future.

        PROFESSIONAL mode is used here although we never auto approve PROFESSIONAL refunds right now
        """
        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=201,
            body='[1]',
            content_type='application/json'
        )
        httpretty.register_uri(
            httpretty.PUT,
            settings.ECOMMERCE_API_URL + 'refunds/1/process/',
            status=400,
            body='{}',
            content_type='application/json'
        )
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.PROFESSIONAL)
        refund_success = refund_entitlement(course_entitlement)
        assert mock_send_notification.is_called
        call_args = list(mock_send_notification.call_args)
        assert call_args[0] == (course_entitlement.user, [1])
        assert refund_success

    @httpretty.activate
    @patch('lms.djangoapps.commerce.utils._send_refund_notification', return_value=True)
    def test_ecommerce_refund_send_notification_failed(self, mock_send_notification):
        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=201,
            body='[1]',
            content_type='application/json'
        )
        httpretty.register_uri(
            httpretty.PUT,
            settings.ECOMMERCE_API_URL + 'refunds/1/process/',
            status=400,
            body='{}',
            content_type='application/json'
        )
        mock_send_notification.side_effect = NotImplementedError
        course_entitlement = CourseEntitlementFactory.create(mode=CourseMode.VERIFIED)
        refund_success = refund_entitlement(course_entitlement)

        assert mock_send_notification.is_called
        call_args = list(mock_send_notification.call_args)
        assert call_args[0] == (course_entitlement.user, [1])
        assert not refund_success

    @httpretty.activate
    @ddt.data(
        (["verified", "audit"], "audit"),
        (["professional"], "professional"),
    )
    @ddt.unpack
    def test_mode_change_after_refund_seat(self, course_modes, new_mode):
        """
        Test if a course seat is refunded student is enrolled into default course mode
        unless no default mode available.
        """
        course_id = CourseLocator('test_org', 'test_course_number', 'test_run')
        CourseMode.objects.all().delete()
        for course_mode in course_modes:
            CourseModeFactory.create(
                course_id=course_id,
                mode_slug=course_mode,
                mode_display_name=course_mode,
            )

        httpretty.register_uri(
            httpretty.POST,
            settings.ECOMMERCE_API_URL + 'refunds/',
            status=201,
            body='[1]',
            content_type='application/json'
        )
        httpretty.register_uri(
            httpretty.PUT,
            settings.ECOMMERCE_API_URL + 'refunds/1/process/',
            status=200,
            body=json.dumps({
                "id": 9,
                "created": "2017-12-21T18:23:49.468298Z",
                "modified": "2017-12-21T18:24:02.741426Z",
                "total_credit_excl_tax": "100.00",
                "currency": "USD",
                "status": "Complete",
                "order": 15,
                "user": 5
            }),
            content_type='application/json'
        )
        enrollment = CourseEnrollment.enroll(self.user, course_id, mode=course_modes[0])

        refund_success = refund_seat(enrollment, True)

        enrollment = CourseEnrollment.get_or_create_enrollment(self.user, course_id)

        assert refund_success
        assert enrollment.mode == new_mode
