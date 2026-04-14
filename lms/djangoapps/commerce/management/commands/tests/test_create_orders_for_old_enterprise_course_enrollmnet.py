"""
Test the create_orders_for_old_enterprise_course_enrollment management command
"""

import re
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.credit.tests.test_api import TEST_ECOMMERCE_WORKER
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerUserFactory,
)


@skip_unless_lms
@override_settings(ECOMMERCE_SERVICE_WORKER_USERNAME=TEST_ECOMMERCE_WORKER)
class TestEnterpriseCourseEnrollmentCreateOldOrder(TestCase):
    """
    Test create_orders_for_old_enterprise_course_enrollment management command.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        UserFactory(username=TEST_ECOMMERCE_WORKER)
        cls._create_enterprise_course_enrollments(30)

    @classmethod
    def _create_enterprise_course_enrollments(cls, count):
        """
            Creates `count` test enrollments plus 1 invalid and 1 Audit enrollment
        """
        for _ in range(count):
            user = UserFactory()
            course_enrollment = CourseEnrollmentFactory(mode=CourseMode.VERIFIED, user=user)
            course = course_enrollment.course
            enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=user.id)
            EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user, course_id=course.id)

        # creating audit enrollment
        user = UserFactory()
        course_enrollment = CourseEnrollmentFactory(mode=CourseMode.AUDIT, user=user)
        course = course_enrollment.course
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=user.id)
        EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user, course_id=course.id)

        # creating invalid enrollment (with no CourseEnrollment)
        user = UserFactory()
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=user.id)
        EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user, course_id=course.id)

    @patch('lms.djangoapps.commerce.management.commands.create_orders_for_old_enterprise_course_enrollment'
           '.Command._create_manual_enrollment_orders')
    def test_command(self, mock_create_manual_enrollment_orders):
        """
            Test command with batch size
        """
        mock_create_manual_enrollment_orders.return_value = (0, 0, 0, [])  # not correct return value, just fixes unpack
        out = StringIO()
        call_command('create_orders_for_old_enterprise_course_enrollment', '--batch-size=10', stdout=out)
        output = out.getvalue()
        assert 'Total Enrollments count to process: 32' in output
        # 30 + 1 + 1
        assert re.search(r'\[Final Summary\] Enrollments Success: \d+, New: \d+, Failed: 0, Invalid: 1 , Non-Paid: 1',
                         output)
        # There are total 32 enrollments so there would be 4 batches (i.e: [10, 10, 10, 2])
        # as there are 2 enrollments in last batch and that 2 enrollments are not valid enrollment to process,
        # so _create_manual_enrollment_orders will not be called for last batch.
        assert mock_create_manual_enrollment_orders.call_count == 3

    @patch('lms.djangoapps.commerce.management.commands.create_orders_for_old_enterprise_course_enrollment'
           '.Command._create_manual_enrollment_orders')
    def test_command_start_and_end_index(self, mock_create_manual_enrollment_orders):
        """
            Test command with batch size
        """
        mock_create_manual_enrollment_orders.return_value = (0, 0, 0, [])  # not correct return value, just fixes unpack
        out = StringIO()
        call_command(
            'create_orders_for_old_enterprise_course_enrollment',
            '--start-index=5',
            '--end-index=20',
            '--batch-size=10',
            '--sleep-time=0.5',
            stdout=out
        )
        output = out.getvalue()
        assert 'Total Enrollments count to process: 15' in output
        assert '[Final Summary] Enrollments Success: ' in output
        assert mock_create_manual_enrollment_orders.call_count == 2
        # batch of 2 (10, 5)
