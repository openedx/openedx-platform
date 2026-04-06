"""
Tests for logout for enterprise flow
"""


from unittest import mock

import ddt
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from common.test.utils import assert_dict_contains_subset
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.features.enterprise_support.api import enterprise_enabled
from openedx.features.enterprise_support.tests import (
    FAKE_ENTERPRISE_CUSTOMER,
    FEATURES_WITH_ENTERPRISE_ENABLED,
    factories,
)
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseServiceMockMixin


@ddt.ddt
@override_settings(FEATURES=FEATURES_WITH_ENTERPRISE_ENABLED)
@skip_unless_lms
class EnterpriseLogoutTests(EnterpriseServiceMockMixin, CacheIsolationTestCase, UrlResetMixin):
    """ Tests for the enterprise logout functionality. """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.enterprise_customer = FAKE_ENTERPRISE_CUSTOMER
        self.enterprise_learner = factories.EnterpriseCustomerUserFactory(user_id=self.user.id)

        self.client.login(username=self.user.username, password='test')
        patcher = mock.patch('openedx.features.enterprise_support.api.enterprise_customer_from_api')
        self.mock_enterprise_customer_from_api = patcher.start()
        self.mock_enterprise_customer_from_api.return_value = self.enterprise_customer
        self.addCleanup(patcher.stop)

    @ddt.data(
        ('https%3A%2F%2Ftest.edx.org%2Fcourses', False),
        ('/courses/course-v1:ARTS+D1+2018_T/course/', False),
        ('invalid-url', False),
        ('/enterprise/c5dad9a7-741c-4841-868f-850aca3ff848/course/Microsoft+DAT206x/enroll/', True),
        ('%2Fenterprise%2Fc5dad9a7-741c-4841-868f-850aca3ff848%2Fcourse%2FMicrosoft%2BDAT206x%2Fenroll%2F', True),
        ('/enterprise/handle_consent_enrollment/efd91463-dc40-4882-aeb9-38202131e7b2/course', True),
        ('%2Fenterprise%2Fhandle_consent_enrollment%2Fefd91463-dc40-4882-aeb9-38202131e7b2%2Fcourse', True),
    )
    @ddt.unpack
    def test_logout_enterprise_target(self, redirect_url, enterprise_target):
        url = '{logout_path}?redirect_url={redirect_url}'.format(
            logout_path=reverse('logout'),
            redirect_url=redirect_url
        )
        assert enterprise_enabled()
        response = self.client.get(url, HTTP_HOST='testserver')
        expected = {
            'enterprise_target': enterprise_target,
        }
        assert_dict_contains_subset(self, expected, response.context_data)

        if enterprise_target:
            self.assertContains(response, 'We are signing you in.')
