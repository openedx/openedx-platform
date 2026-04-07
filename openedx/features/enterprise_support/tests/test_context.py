"""
Test the enterprise support APIs.
"""
from django.conf import settings
from django.test.utils import override_settings

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.features.enterprise_support.context import get_enterprise_event_context
from openedx.features.enterprise_support.tests import FEATURES_WITH_ENTERPRISE_ENABLED
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerUserFactory,
)
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseServiceMockMixin


@override_settings(FEATURES=FEATURES_WITH_ENTERPRISE_ENABLED)
@skip_unless_lms
class TestEnterpriseContext(EnterpriseServiceMockMixin, CacheIsolationTestCase):
    """
    Test enterprise event context APIs.
    """
    ENABLED_CACHES = ['default']

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory.create(
            username=settings.ENTERPRISE_SERVICE_WORKER_USERNAME,
            email='ent_worker@example.com',
            password='password123',
        )
        super().setUpTestData()

    def test_get_enterprise_event_context(self):
        course_enrollment = CourseEnrollmentFactory(user=self.user)
        course = course_enrollment.course
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=self.user.id)
        EnterpriseCourseEnrollmentFactory(
            enterprise_customer_user=enterprise_customer_user,
            course_id=course.id
        )
        assert get_enterprise_event_context(course_id=course.id, user_id=self.user.id) == \
               {'enterprise_uuid': str(enterprise_customer_user.enterprise_customer_id)}
