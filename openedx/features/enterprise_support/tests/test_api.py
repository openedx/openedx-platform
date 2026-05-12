"""
Test the enterprise support APIs.
"""
from unittest import mock

import ddt
import httpretty
import pytest
from consent.models import DataSharingConsent
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.test.utils import override_settings
from django.urls import reverse
from edx_django_utils.cache import TieredCache, get_cache_key
from requests.exceptions import HTTPError
from six.moves.urllib.parse import parse_qs
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.features.enterprise_support.api import (
    _CACHE_MISS,
    ENTERPRISE_CUSTOMER_KEY_NAME,
    ConsentApiClient,
    ConsentApiServiceClient,
    EnterpriseApiClient,
    EnterpriseApiException,
    EnterpriseApiServiceClient,
    activate_learner_enterprise,
    add_enterprise_customer_to_session,
    consent_needed_for_course,
    data_sharing_consent_required,
    enterprise_customer_for_request,
    enterprise_customer_from_api,
    enterprise_customer_from_session,
    enterprise_customer_from_session_or_learner_data,
    enterprise_customer_uuid_for_request,
    enterprise_enabled,
    get_active_enterprise_customer_user,
    get_consent_notification_data,
    get_dashboard_consent_notification,
    get_data_sharing_consents,
    get_enterprise_consent_url,
    get_enterprise_course_enrollments,
    get_enterprise_learner_data_from_api,
    get_enterprise_learner_data_from_db,
    get_enterprise_learner_portal_enabled_message,
)
from openedx.features.enterprise_support.tests import FEATURES_WITH_ENTERPRISE_ENABLED
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerIdentityProviderFactory,
    EnterpriseCustomerUserFactory,
)
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseServiceMockMixin
from openedx.features.enterprise_support.utils import clear_data_consent_share_cache, get_data_consent_share_cache_key

LOGGER_NAME = "edx.enterprise_helpers"


class MockEnrollment(mock.MagicMock):
    """
    Mock object for an enrollment which has a consistent string representation
    suitable for use in ddt parameters.
    """
    def __repr__(self):
        return '<MockEnrollment course_id={}>'.format(getattr(self, 'course_id', None))


@ddt.ddt
@override_settings(FEATURES=FEATURES_WITH_ENTERPRISE_ENABLED)
@skip_unless_lms
class TestEnterpriseApi(EnterpriseServiceMockMixin, CacheIsolationTestCase):
    """
    Test enterprise support APIs.
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

    def _assert_api_service_client(self, api_client, mocked_jwt_builder):
        """
        Verify that the provided api client uses the enterprise service user to generate
        JWT token for auth.
        """
        mocked_jwt_builder.return_value = 'test-token'
        enterprise_service_user = User.objects.get(username=settings.ENTERPRISE_SERVICE_WORKER_USERNAME)
        enterprise_api_service_client = api_client()

        mocked_jwt_builder.assert_called_once_with(enterprise_service_user)
        # pylint: disable=protected-access
        assert enterprise_api_service_client.client.auth.token == 'test-token'

    def _assert_api_client_with_user(self, api_client, mocked_jwt_builder):
        """
        Verify that the provided api client uses the expected user to generate
        JWT token for auth.
        """
        mocked_jwt_builder.return_value = 'test-token'
        dummy_enterprise_user = UserFactory.create(
            username='dummy-enterprise-user',
            email='dummy-enterprise-user@example.com',
            password='password123',
        )
        enterprise_api_service_client = api_client(dummy_enterprise_user)

        mocked_jwt_builder.assert_called_once_with(dummy_enterprise_user)
        # pylint: disable=protected-access
        assert enterprise_api_service_client.client.auth.token == 'test-token'
        return enterprise_api_service_client

    def _assert_get_enterprise_customer(self, api_client, enterprise_api_data_for_mock):
        """
        DRY method to verify caching for get enterprise customer method.
        """
        cache_key = get_cache_key(
            resource='enterprise-customer',
            resource_id=enterprise_api_data_for_mock['uuid'],
            username=settings.ENTERPRISE_SERVICE_WORKER_USERNAME,
        )
        self.mock_get_enterprise_customer(enterprise_api_data_for_mock['uuid'], enterprise_api_data_for_mock, 200)
        self._assert_get_enterprise_customer_with_cache(api_client, enterprise_api_data_for_mock, cache_key)

    def _assert_get_enterprise_customer_with_cache(self, api_client, enterprise_customer_data, cache_key):
        """
        DRY method to verify that get enterprise customer response is cached.
        """
        cached_enterprise_customer = cache.get(cache_key)
        assert cached_enterprise_customer is None

        enterprise_customer = api_client.get_enterprise_customer(enterprise_customer_data['uuid'])
        assert enterprise_customer_data == enterprise_customer
        cached_enterprise_customer = cache.get(cache_key)
        assert cached_enterprise_customer == enterprise_customer

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_enterprise_api_client_with_service_user(self, mock_jwt_builder):
        """
        Verify that enterprise API service client uses enterprcreate_jwt_for_userise service user
        by default to authenticate and access enterprise API.
        """
        self._assert_api_service_client(EnterpriseApiServiceClient, mock_jwt_builder)

        # Verify that enterprise customer data is cached properly for the
        # enterprise api client.
        enterprise_api_client = EnterpriseApiServiceClient()
        enterprise_api_data_for_mock_1 = {'name': 'dummy-enterprise-customer-1', 'uuid': 'enterprise-uuid-1'}
        self._assert_get_enterprise_customer(enterprise_api_client, enterprise_api_data_for_mock_1)

        # Now try to get enterprise customer for another enterprise and verify
        # that enterprise api client returns data according to the provided
        # enterprise UUID.
        enterprise_api_data_for_mock_2 = {'name': 'dummy-enterprise-customer-2', 'uuid': 'enterprise-uuid-2'}
        self._assert_get_enterprise_customer(enterprise_api_client, enterprise_api_data_for_mock_2)

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_enterprise_api_client_with_user(self, mock_jwt_builder):
        """
        Verify that enterprise API client uses the provided user to
        authenticate and access enterprise API.
        """
        self._assert_api_client_with_user(EnterpriseApiClient, mock_jwt_builder)

    @ddt.data(True, False)
    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_enterprise_api_client_with_user_post_enrollment(self, should_raise_http_error, mock_jwt_builder):
        """
        Verify that enterprise API client uses the provided user to
        authenticate and access enterprise API.
        """
        api_client = self._assert_api_client_with_user(EnterpriseApiClient, mock_jwt_builder)
        mock_client = mock.Mock()
        api_client.client = mock_client
        if should_raise_http_error:
            mock_client.post.side_effect = HTTPError

        username = 'spongebob'
        course_id = 'burger-flipping-101'

        if should_raise_http_error:
            with pytest.raises(EnterpriseApiException):
                api_client.post_enterprise_course_enrollment(username, course_id)
        else:
            api_client.post_enterprise_course_enrollment(username, course_id)

        mock_client.post.assert_called_once_with(
            f"{api_client.base_api_url}enterprise-course-enrollment/",
            data={
                'username': username,
                'course_id': course_id,
            }
        )

    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    @mock.patch('openedx.features.enterprise_support.api.EnterpriseApiClient')
    def test_enterprise_customer_from_api_cache_miss(self, mock_client_class, mock_uuid_from_request):
        mock_uuid_from_request.return_value = _CACHE_MISS
        mock_request = mock.Mock()

        actual_result = enterprise_customer_from_api(mock_request)
        assert actual_result is None
        assert not mock_client_class.called

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_enterprise_consent_api_client_with_service_user(self, mock_jwt_builder):
        """
        Verify that enterprise API consent service client uses enterprise
        service user by default to authenticate and access enterprise API.
        """
        self._assert_api_service_client(ConsentApiServiceClient, mock_jwt_builder)

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_enterprise_consent_api_client_with_user(self, mock_jwt_builder):
        """
        Verify that enterprise API consent service client uses the provided
        user to authenticate and access enterprise API.
        """
        consent_client = self._assert_api_client_with_user(ConsentApiClient, mock_jwt_builder)
        mock_client = mock.Mock()
        consent_client.client = mock_client

        kwargs = {
            'foo': 'a',
            'bar': 'b',
        }
        consent_client.provide_consent(**kwargs)
        consent_client.revoke_consent(**kwargs)

        mock_client.post.assert_called_once_with(consent_client.consent_endpoint, json=kwargs)
        mock_client.delete.assert_called_once_with(consent_client.consent_endpoint, json=kwargs)

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    def test_consent_needed_for_course_no_active_enterprise_user(self, mock_get_active_enterprise_customer_user):
        """Tests that consent is not needed for non-enterprise user."""
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain="example.com"),
            session={},
            COOKIES={},
            GET={},
        )
        course_id = 'fake-course'
        mock_get_active_enterprise_customer_user.return_value = None

        # test that consent is not required for a non-enterprise learner and appropriate message is logged
        expected_message = "[ENTERPRISE DSC] Consent from user [{username}] is not needed for course [{course_id}]." \
                           " The user is not linked to an enterprise."
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(username=user.username, course_id=course_id),
                )
            )

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    def test_consent_needed_for_course_dsc_cache_found(self, mock_get_active_enterprise_customer_user):
        """
        Tests that a consent is not needed when a DSC record is already found in cache and it is equal to 0.
        """
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain="example.com"),
            session={},
            COOKIES={},
            GET={},
        )
        ec_uuid = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        course_id = 'fake-course'
        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=ec_uuid,
        )

        # store a DSC record value in cache
        consent_cache_key = get_data_consent_share_cache_key(user.id, course_id, ec_uuid)
        TieredCache.set_all_tiers(consent_cache_key, 0, settings.DATA_CONSENT_SHARE_CACHE_TIMEOUT)

        # test that consent is not required if DSC record is already found in cache.
        expected_message = "[ENTERPRISE DSC] Consent from user [{username}] is not needed for course [{course_id}]. " \
                           "The DSC cache was checked and the value was 0."
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(username=user.username, course_id=course_id),
                )
            )
        # clearing up
        clear_data_consent_share_cache(user.id, course_id, ec_uuid)

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    def test_consent_needed_for_course_dsc_not_enabled(self, mock_get_active_enterprise_customer_user):
        """
        Tests that a consent is not needed when enterprise customer has disabled DSC.
        """
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain="example.com"),
            session={},
            COOKIES={},
            GET={},
        )

        ec_uuid = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        course_id = 'fake-course'
        ent_slug = 'test-slug'
        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=ec_uuid,
            enable_data_sharing_consent=False,
            slug=ent_slug
        )

        # test that consent is not required if DSC is disabled for the enterprise.
        expected_message = "[ENTERPRISE DSC] DSC is disabled for enterprise customer [{slug}]. " \
                           "Consent from user [{username}] is not needed for course [{course_id}]"
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(slug=ent_slug, username=user.username, course_id=course_id)
                )
            )

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    def test_consent_needed_for_course_enterprise_mismatch(
        self,
        enterprise_customer_uuid_for_request_mock,
        mock_get_active_enterprise_customer_user
    ):
        """
        Tests that a consent is not needed when current request enterprise and learner's active
        enterprise does not match.
        """
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain='example.com'),
            session={},
            COOKIES={},
            GET={},
        )

        learner_enterprise = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        current_request_enterprise = '1234-23434-vfdfa-242sdczz'
        course_id = 'fake-course'
        enterprise_customer_uuid_for_request_mock.return_value = current_request_enterprise
        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=learner_enterprise,
        )

        expected_message = '[ENTERPRISE DSC] Enterprise mismatch. USER: [{username}], RequestEnterprise: ' \
                           '[{current_enterprise_uuid}], LearnerEnterprise: [{active_enterprise_customer}]'
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(
                        username=user.username,
                        current_enterprise_uuid=current_request_enterprise,
                        active_enterprise_customer=learner_enterprise
                    )
                )
            )

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    def test_consent_needed_for_course_site_mismatch(
        self,
        enterprise_customer_uuid_for_request_mock,
        mock_get_active_enterprise_customer_user,
    ):
        """
        Tests that a consent is not needed when enterprise customer and learner's site domain does not match.
        """
        user = UserFactory(username='janedoe')
        request_site_domain = 'site-mismatch.com'
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain=request_site_domain),
            session={},
            COOKIES={},
            GET={},
        )

        ec_uuid = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        course_id = 'fake-course'
        ent_site = SiteFactory(domain='ent-site.com')
        enterprise_customer_uuid_for_request_mock.return_value = ec_uuid
        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=ec_uuid,
            site_domain=str(ent_site.domain),
        )

        expected_message = "[ENTERPRISE DSC] Site mismatch. USER: [{username}], RequestSite: [{request_site}], " \
                           "LearnerEnterpriseDomain: [{enterprise_domain}]"
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(
                        username=user.username,
                        request_site=request_site_domain,
                        enterprise_domain=ent_site.domain
                    )
                )
            )

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    @mock.patch('openedx.features.enterprise_support.api.ConsentApiClient.consent_required')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    def test_consent_needed_for_course_when_consent_is_not_required(
        self,
        enterprise_customer_uuid_for_request_mock,
        mock_consent_required,
        mock_get_active_enterprise_customer_user
    ):
        """
        Tests that a consent is not needed when learner is not already enrolled and the enterprise requires consent.
        """
        mock_consent_required.return_value = False
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain="example.com"),
            session={},
            COOKIES={},
            GET={},
        )
        ec_uuid = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        course_id = 'fake-course'
        enterprise_customer_uuid_for_request_mock.return_value = ec_uuid

        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=ec_uuid,
        )

        expected_message = "[ENTERPRISE DSC] Consent from user [{username}] is not needed for course [{course_id}]. " \
                           "The user's current enterprise does not require data sharing consent."
        with LogCapture(LOGGER_NAME) as log:
            assert not consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(username=user.username, course_id=course_id)
                )
            )

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_active_enterprise_customer_user')
    @mock.patch('openedx.features.enterprise_support.api.ConsentApiClient.consent_required')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    def test_consent_needed_for_course_when_consent_is_required(
        self,
        enterprise_customer_uuid_for_request_mock,
        mock_consent_required,
        mock_get_active_enterprise_customer_user
    ):
        """
        Tests that a consent is needed when learner is not already enrolled and the enterprise requires consent.
        """
        mock_consent_required.return_value = True
        user = UserFactory(username='janedoe')
        request = mock.MagicMock(
            user=user,
            site=SiteFactory(domain="example.com"),
            session={},
            COOKIES={},
            GET={},
        )
        ec_uuid = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        course_id = 'fake-course'
        enterprise_customer_uuid_for_request_mock.return_value = ec_uuid

        mock_get_active_enterprise_customer_user.return_value = self.get_mock_active_enterprise_learner_details(
            learner_id=user.id,
            enterprise_customer_uuid=ec_uuid,
        )

        expected_message = "[ENTERPRISE DSC] Consent from user [{username}] is needed for course [{course_id}]. " \
                           "The user's current enterprise requires data sharing consent, and it has not been given."
        with LogCapture(LOGGER_NAME) as log:
            assert consent_needed_for_course(request, user, course_id)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_message.format(username=user.username, course_id=course_id)
                )
            )

    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_fetch_enterprise_learner_data_unauthenticated(self, mock_jwt_builder):
        api_client = self._assert_api_client_with_user(EnterpriseApiClient, mock_jwt_builder)
        setattr(api_client.client, 'enterprise-learner', mock.Mock())
        mock_endpoint = getattr(api_client.client, 'enterprise-learner')

        user = mock.Mock(is_authenticated=False)
        assert api_client.fetch_enterprise_learner_data(user) is None

        assert not mock_endpoint.called

    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_fetch_enterprise_learner_data(self, mock_jwt_builder):
        """
        Test EnterpriseApiClient's fetch_enterprise_learner_data method.
        """
        api_client = self._assert_api_client_with_user(EnterpriseApiClient, mock_jwt_builder)
        mock_client = mock.Mock()
        api_client.client = mock_client

        user = mock.Mock(is_authenticated=True, username='spongebob')
        response = api_client.fetch_enterprise_learner_data(user)

        assert mock_client.get.return_value.json.return_value == response
        mock_client.get.assert_called_once_with(
            f"{api_client.base_api_url}enterprise-learner/",
            params={'username': user.username},
        )

    @mock.patch('openedx.features.enterprise_support.api.get_current_request')
    @mock.patch('openedx.features.enterprise_support.api.create_jwt_for_user')
    def test_fetch_enterprise_learner_data_http_error(self, mock_jwt_builder, mock_get_current_request):
        """
        Test error handling for the EnterpriseApiClient's fetch_enterprise_learner_data method.
        """
        api_client = self._assert_api_client_with_user(EnterpriseApiClient, mock_jwt_builder)

        mock_client = mock.Mock()
        mock_client.get.side_effect = HTTPError
        api_client.client = mock_client
        mock_get_current_request.return_value.META = {
            'PATH_INFO': 'whatever',
        }

        user = mock.Mock(is_authenticated=True, username='spongebob')

        assert api_client.fetch_enterprise_learner_data(user) is None
        url = f"{api_client.base_api_url}enterprise-learner/"
        mock_client.get.assert_called_once_with(url, params={'username': user.username})

    @mock.patch('openedx.features.enterprise_support.api.EnterpriseApiClient')
    def test_get_enterprise_learner_data_from_api(self, mock_api_client_class):
        user = mock.Mock(is_authenticated=True)
        mock_client = mock_api_client_class.return_value
        mock_client.fetch_enterprise_learner_data.return_value = {
            'results': 'the-learner-data',
        }

        learner_data = get_enterprise_learner_data_from_api(user)

        assert 'the-learner-data' == learner_data
        mock_api_client_class.assert_called_once_with(user=user)
        mock_client.fetch_enterprise_learner_data.assert_called_once_with(user)

    def test_activate_learner_enterprise(self):
        """
        Test enterprise is activated successfully for user
        """
        request_mock = mock.MagicMock(session={}, user=self.user)
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=self.user.id)
        enterprise_customer_uuid = enterprise_customer_user.enterprise_customer.uuid

        activate_learner_enterprise(request_mock, self.user, enterprise_customer_uuid)
        assert request_mock.session['enterprise_customer']['uuid'] == str(enterprise_customer_uuid)

    def test_get_enterprise_learner_data_from_db_no_data(self):
        assert not get_enterprise_learner_data_from_db(self.user)

    def test_get_enterprise_learner_data_from_db(self):
        EnterpriseCustomerUserFactory(user_id=self.user.id)
        user_data = get_enterprise_learner_data_from_db(self.user)[0]['user']
        assert user_data['username'] == self.user.username

    @ddt.data(True, False)
    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    def test_get_active_enterprise_customer_user(self, is_enterprise_enabled, mock_enterprise_enabled):
        """Tests that get_active_enterprise_customer_user utility returns correct user."""
        mock_enterprise_enabled.return_value = is_enterprise_enabled
        EnterpriseCustomerUserFactory(user_id=self.user.id, active=True)
        if not is_enterprise_enabled:
            assert not get_active_enterprise_customer_user(self.user)
        else:
            user_data = get_active_enterprise_customer_user(self.user)
            assert user_data['user']['username'] == self.user.username
            assert user_data['active']

    def test_get_active_enterprise_customer_user_no_user_found(self):
        """
        Test that get_active_enterprise_customer_user utility returns None if active user not found.
        And logs the appropriate message.
        """
        expected_logger_message = "Active EnterpriseCustomerUser for user [{username}] does not exist"
        with LogCapture(LOGGER_NAME) as log:
            assert not get_active_enterprise_customer_user(self.user)
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    expected_logger_message.format(username=self.user.username),
                )
            )

    @ddt.data(True, False)
    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    def test_get_data_sharing_consents(self, is_enterprise_enabled, mock_enterprise_enabled):
        mock_enterprise_enabled.return_value = is_enterprise_enabled
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=self.user.id)

        if not is_enterprise_enabled:
            assert get_data_sharing_consents(self.user) == []
        else:
            course_id = 'fake-course'
            data_sharing_consent = DataSharingConsent(
                course_id=course_id,
                enterprise_customer=enterprise_customer_user.enterprise_customer,
                username=self.user.username,
                granted=False
            )
            data_sharing_consent.save()
            data_sharing_consents = get_data_sharing_consents(self.user)
            assert len(data_sharing_consents) == 1
            assert data_sharing_consents[0].id == data_sharing_consent.id

    @ddt.data(True, False)
    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    def test_get_enterprise_course_enrollments(self, is_enterprise_enabled, mock_enterprise_enabled):
        mock_enterprise_enabled.return_value = is_enterprise_enabled
        enterprise_customer_user = EnterpriseCustomerUserFactory(user_id=self.user.id)

        if not is_enterprise_enabled:
            assert get_enterprise_course_enrollments(self.user) == []
        else:
            ece = EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user)
            enterprise_course_enrollments = get_enterprise_course_enrollments(self.user)
            assert len(enterprise_course_enrollments) == 1
            assert enterprise_course_enrollments[0].id == ece.id

    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    @mock.patch('openedx.features.enterprise_support.api.EnterpriseCustomer')
    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline')
    @mock.patch('openedx.features.enterprise_support.api.Registry')
    def test_enterprise_customer_for_request(
            self,
            mock_registry,
            mock_partial,
            mock_enterprise_customer_model,
            mock_get_enterprise_learner_data,
    ):
        def mock_get_enterprise_customer(**kwargs):
            uuid = kwargs.get('enterprise_customer_identity_providers__provider_id')
            if uuid:
                return mock.MagicMock(uuid=uuid, user=self.user)
            raise Exception

        dummy_request = mock.MagicMock(session={}, user=self.user)
        mock_enterprise_customer_model.objects.get.side_effect = mock_get_enterprise_customer
        mock_enterprise_customer_model.DoesNotExist = Exception
        mock_partial.return_value = True
        mock_registry.get_from_pipeline.return_value.provider_id = 'real-ent-uuid'

        # Verify that the method `enterprise_customer_for_request` returns
        # expected enterprise customer against the requesting user.
        self.mock_get_enterprise_customer('real-ent-uuid', {'real': 'enterprisecustomer'}, 200)
        enterprise_customer = enterprise_customer_for_request(dummy_request)
        assert enterprise_customer == {'real': 'enterprisecustomer'}

        httpretty.reset()

        # Verify that the method `enterprise_customer_for_request` returns no
        # enterprise customer if the enterprise customer API throws 404.
        del dummy_request.session['enterprise_customer']
        self.mock_get_enterprise_customer('real-ent-uuid', {'detail': 'Not found.'}, 404)
        enterprise_customer = enterprise_customer_for_request(dummy_request)
        assert enterprise_customer is None

        httpretty.reset()

        # Verify that the method `enterprise_customer_for_request` returns
        # expected enterprise customer against the requesting user even if
        # the third-party auth pipeline has no `provider_id`.
        mock_registry.get_from_pipeline.return_value.provider_id = None
        self.mock_get_enterprise_customer('real-ent-uuid', {'real': 'enterprisecustomer'}, 200)
        mock_request = mock.MagicMock(
            GET={'enterprise_customer': 'real-ent-uuid'},
            COOKIES={},
            session={},
            user=self.user
        )
        enterprise_customer = enterprise_customer_for_request(mock_request)
        assert enterprise_customer == {'real': 'enterprisecustomer'}

        # Verify that the method `enterprise_customer_for_request` returns
        # expected enterprise customer against the requesting user even if
        # the third-party auth pipeline has no `provider_id` but there is
        # enterprise customer UUID in the cookie.
        mock_request = mock.MagicMock(
            GET={},
            COOKIES={settings.ENTERPRISE_CUSTOMER_COOKIE_NAME: 'real-ent-uuid'},
            session={},
            user=self.user
        )
        enterprise_customer = enterprise_customer_for_request(mock_request)
        assert enterprise_customer == {'real': 'enterprisecustomer'}

        # Verify that the method `enterprise_customer_for_request` returns
        # expected enterprise customer against the requesting user if
        # data is cached only in the request session
        mock_registry.get_from_pipeline.return_value.provider_id = None
        self.mock_get_enterprise_customer('real-ent-uuid', {'real': 'enterprisecustomer'}, 200)
        mock_request = mock.MagicMock(
            GET={},
            COOKIES={},
            session={'enterprise_customer': {'real': 'enterprisecustomer'}},
            user=self.user
        )
        enterprise_customer = enterprise_customer_for_request(mock_request)
        assert enterprise_customer == {'real': 'enterprisecustomer'}

        # Verify that we can still get enterprise customer from enterprise
        # learner API even if we are unable to get it from preferred sources,
        # e.g. url query parameters, third-party auth pipeline, enterprise
        # cookie, or session.
        mock_get_enterprise_learner_data.return_value = [{'enterprise_customer': {'uuid': 'real-ent-uuid'}}]
        mock_request = mock.MagicMock(
            GET={},
            COOKIES={},
            session={},
            user=self.user,
            site=1
        )
        enterprise_customer = enterprise_customer_for_request(mock_request)
        assert enterprise_customer == {'real': 'enterprisecustomer'}

    def test_enterprise_customer_for_request_with_session(self):
        """
        Verify enterprise_customer_for_request stores and retrieves data from session appropriately
        """
        dummy_request = mock.MagicMock(session={}, user=self.user)
        enterprise_data = {'name': 'dummy-enterprise-customer', 'uuid': '8dc65e66-27c9-447b-87ff-ede6d66e3a5d'}

        # Verify enterprise customer data fetched from API when it is not available in session
        with mock.patch(
                'openedx.features.enterprise_support.api.enterprise_customer_from_api',
                return_value=enterprise_data
        ):
            assert dummy_request.session.get('enterprise_customer') is None
            enterprise_customer = enterprise_customer_for_request(dummy_request)
            assert enterprise_customer == enterprise_data
            assert dummy_request.session.get('enterprise_customer') == enterprise_data

        # Verify enterprise customer data fetched from session for subsequent calls
        with mock.patch(
                'openedx.features.enterprise_support.api.enterprise_customer_from_api',
                return_value=enterprise_data
        ) as mock_enterprise_customer_from_api, mock.patch(
                'openedx.features.enterprise_support.api.enterprise_customer_from_session',
                return_value=enterprise_data
        ) as mock_enterprise_customer_from_session:
            enterprise_customer = enterprise_customer_for_request(dummy_request)
            assert enterprise_customer == enterprise_data
            assert mock_enterprise_customer_from_api.called is False
            assert mock_enterprise_customer_from_session.called is True

        # Verify enterprise customer data fetched from session for subsequent calls
        # with unauthenticated user in SAML case
        del dummy_request.user

        with mock.patch(
            'openedx.features.enterprise_support.api.enterprise_customer_from_api',
            return_value=enterprise_data
        ) as mock_enterprise_customer_from_api, mock.patch(
            'openedx.features.enterprise_support.api.enterprise_customer_from_session',
            return_value=enterprise_data
        ) as mock_enterprise_customer_from_session:
            enterprise_customer = enterprise_customer_for_request(dummy_request)
            assert enterprise_customer == enterprise_data
            assert mock_enterprise_customer_from_api.called is False
            assert mock_enterprise_customer_from_session.called is True

    def check_data_sharing_consent(self, consent_required=False, consent_url=None):
        """
        Used to test the data_sharing_consent_required view decorator.
        """

        # Test by wrapping a function that has the expected signature
        @data_sharing_consent_required
        def view_func(request, course_id, *args, **kwargs):
            """
            Return the function arguments, so they can be tested.
            """
            return ((request, course_id,) + args, kwargs)

        # Call the wrapped function
        args = (mock.MagicMock(), 'course-id', 'another arg', 'and another')
        kwargs = dict(a=1, b=2, c=3)
        response = view_func(*args, **kwargs)

        # If consent required, then the response should be a redirect to the consent URL, and the view function would
        # not be called.
        if consent_required:
            assert isinstance(response, HttpResponseRedirect)
            assert response.url == consent_url  # pylint: disable=no-member

        # Otherwise, the view function should have been called with the expected arguments.
        else:
            assert response == (args, kwargs)

    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    @mock.patch('openedx.features.enterprise_support.api.consent_needed_for_course')
    def test_data_consent_required_enterprise_disabled(self,
                                                       mock_consent_necessary,
                                                       mock_enterprise_enabled):
        """
        Verify that the wrapped view is called directly when enterprise integration is disabled,
        without checking for course consent necessary.
        """
        mock_enterprise_enabled.return_value = False

        self.check_data_sharing_consent(consent_required=False)

        mock_enterprise_enabled.assert_called_once()
        mock_consent_necessary.assert_not_called()

    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    @mock.patch('openedx.features.enterprise_support.api.consent_needed_for_course')
    def test_no_course_data_consent_required(self,
                                             mock_consent_necessary,
                                             mock_enterprise_enabled):
        """
        Verify that the wrapped view is called directly when enterprise integration is enabled,
        and no course consent is required.
        """
        mock_enterprise_enabled.return_value = True
        mock_consent_necessary.return_value = False

        self.check_data_sharing_consent(consent_required=False)

        mock_enterprise_enabled.assert_called_once()
        mock_consent_necessary.assert_called_once()

    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    @mock.patch('openedx.features.enterprise_support.api.consent_needed_for_course')
    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_consent_url')
    def test_data_consent_required(self, mock_get_consent_url, mock_consent_necessary, mock_enterprise_enabled):
        """
        Verify that the wrapped function returns a redirect to the consent URL when enterprise integration is enabled,
        and course consent is required.
        """
        mock_enterprise_enabled.return_value = True
        mock_consent_necessary.return_value = True
        consent_url = '/abc/def'
        mock_get_consent_url.return_value = consent_url

        self.check_data_sharing_consent(consent_required=True, consent_url=consent_url)

        mock_get_consent_url.assert_called_once()

    @ddt.data(True, False)
    @httpretty.activate
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    @mock.patch('openedx.features.enterprise_support.api.reverse')
    @mock.patch('openedx.features.enterprise_support.api.consent_needed_for_course')
    def test_get_enterprise_consent_url(
            self,
            is_return_to_null,
            needed_for_course_mock,
            reverse_mock,
            enterprise_customer_uuid_for_request_mock,
    ):
        """
        Verify that get_enterprise_consent_url correctly builds URLs.
        """

        def fake_reverse(*args, **kwargs):
            if args[0] == 'grant_data_sharing_permissions':
                return '/enterprise/grant_data_sharing_permissions'
            return reverse(*args, **kwargs)

        enterprise_customer_uuid_for_request_mock.return_value = 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        reverse_mock.side_effect = fake_reverse
        needed_for_course_mock.return_value = True
        request_mock = mock.MagicMock(
            user=self.user,
            path='/request_path',
            build_absolute_uri=lambda x: 'http://localhost:8000' + x  # Don't do it like this in prod. Ever.
        )

        course_id = 'course-v1:edX+DemoX+Demo_Course'
        return_to = None if is_return_to_null else 'courseware'

        if is_return_to_null:
            expected_path = request_mock.path
        else:
            expected_path = '/courses/course-v1:edX+DemoX+Demo_Course/courseware'
        expected_url_args = {
            'course_id': ['course-v1:edX+DemoX+Demo_Course'],
            'source': ['lms'],
            'failure_url': ['http://localhost:8000/dashboard?consent_failed=course-v1%3AedX%2BDemoX%2BDemo_Course'],
            'enterprise_customer_uuid': ['cf246b88-d5f6-4908-a522-fc307e0b0c59'],
            'next': [f'http://localhost:8000{expected_path}']
        }

        actual_url = get_enterprise_consent_url(request_mock, course_id, return_to=return_to)
        actual_url_args = parse_qs(actual_url.split('/enterprise/grant_data_sharing_permissions?')[1])
        assert actual_url_args == expected_url_args

    @ddt.data(
        (False, {'real': 'enterprise', 'uuid': ''}, 'course', [], [], "", ""),
        (True, {}, 'course', [], [], "", ""),
        (True, {'real': 'enterprise'}, None, [], [], "", ""),
        (True, {'name': 'GriffCo', 'uuid': ''}, 'real-course', [], [], "", ""),
        (True, {'name': 'GriffCo', 'uuid': ''}, 'real-course', [MockEnrollment(course_id='other-id')], [], "", ""),
        (
            True,
            {'name': 'GriffCo', 'uuid': 'real-uuid'},
            'real-course',
            [
                MockEnrollment(
                    course_id='real-course',
                    course_overview=mock.MagicMock(
                        display_name='My Cool Course'
                    )
                )
            ],
            [
                'If you have concerns about sharing your data, please contact your administrator at GriffCo.',
                'Enrollment in My Cool Course was not complete.'
            ],
            "", ""
        ),
        (
            True,
            {'name': 'GriffCo', 'uuid': 'real-uuid'},
            'real-course',
            [
                MockEnrollment(
                    course_id='real-course',
                    course_overview=mock.MagicMock(
                        display_name='My Cool Course'
                    )
                )
            ],
            [
                'If you have concerns about sharing your data, please contact your administrator at GriffCo.',
                'Enrollment in My Cool Course was not complete.'
            ],
            "Title from DataSharingConsentTextOverrides model in consent app",
            "Message from DataSharingConsentTextOverrides model in consent app"
        ),

    )
    @ddt.unpack
    @mock.patch('openedx.features.enterprise_support.api.ConsentApiClient')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    @mock.patch('openedx.features.enterprise_support.api.get_consent_notification_data')
    def test_get_dashboard_consent_notification(
            self,
            consent_return_value,
            enterprise_customer,
            course_id,
            enrollments,
            expected_substrings,
            notification_title,
            notification_message,
            consent_notification_data,
            ec_for_request,
            consent_client_class
    ):
        request = mock.MagicMock(
            GET={'consent_failed': course_id}
        )
        consent_notification_data.return_value = notification_title, notification_message
        consent_client = consent_client_class.return_value
        consent_client.consent_required.return_value = consent_return_value

        ec_for_request.return_value = enterprise_customer

        user = mock.MagicMock()

        notification_string = get_dashboard_consent_notification(
            request, user, enrollments,
        )

        if notification_message and notification_title:
            assert notification_title in notification_string
            assert notification_message in notification_string
        elif expected_substrings:
            for substr in expected_substrings:
                assert substr in notification_string
        else:
            assert notification_string == ''

    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=False))
    def test_utils_with_enterprise_disabled(self):
        """
        Test that disabling the enterprise integration flag causes
        the utilities to return the expected default values.
        """
        assert not enterprise_enabled()

    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    def test_enterprise_customer_from_session_or_db_cache_miss_no_customer(self, mock_learner_data_from_db):
        """
        When no customer data exists in the request session _and_
        no customer is associated with the requesting user, then ``enterprise_customer_from_session_or_learner_data()``
        should return None.
        """
        mock_request = mock.Mock(session={})
        mock_learner_data_from_db.return_value = None

        actual_result = enterprise_customer_from_session_or_learner_data(mock_request)
        assert actual_result is None
        mock_learner_data_from_db.assert_called_once_with(mock_request.user)

    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    @override_settings(ENTERPRISE_LEARNER_PORTAL_BASE_URL='http://localhost')
    def test_enterprise_customer_from_session_or_db_cache_miss_customer_exists(self, mock_learner_data_from_db):
        """
        When no customer data exists in the request session but a
        customer is associated with the requesting user, then ``enterprise_customer_from_session_or_learner_data()``
        should return the customer metadata.
        """
        mock_request = mock.Mock(session={})
        mock_enterprise_customer = {
            'uuid': 'some-uuid',
            'name': 'Best Corp',
            'enable_learner_portal': True,
            'slug': 'best-corp',
        }
        mock_learner_data_from_db.return_value = [
            {
                'enterprise_customer': mock_enterprise_customer,
            },
        ]

        actual_result = enterprise_customer_from_session_or_learner_data(mock_request)
        assert actual_result['uuid'] == mock_enterprise_customer['uuid']
        mock_learner_data_from_db.assert_called_once_with(mock_request.user)
        # assert we cached the enterprise customer data in the request session after fetching it
        assert mock_request.session.get(ENTERPRISE_CUSTOMER_KEY_NAME) == mock_enterprise_customer

    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    def test_enterprise_customer_from_session_or_db_cache_hit_no_customer(self, mock_learner_data_from_db):
        """
        When customer data exists in the request session but it's null/empty,
        then ``enterprise_customer_from_session_or_learner_data()`` should return None.
        """
        mock_request = mock.Mock(session={
            ENTERPRISE_CUSTOMER_KEY_NAME: None,
        })

        actual_result = enterprise_customer_from_session_or_learner_data(mock_request)
        assert actual_result is None
        assert not mock_learner_data_from_db.called

    @ddt.data(True, False)
    @override_settings(ENTERPRISE_LEARNER_PORTAL_BASE_URL='http://localhost')
    def test_enterprise_learner_portal_message_customer_exists(self, enable_learner_portal):
        """
        When an enterprise customer exists with learner portal enabled, then
        ``get_enterprise_learner_portal_enabled_message()`` should return an appropriate message
        for that customer.
        """
        mock_enterprise_customer = {
            'uuid': 'some-uuid',
            'name': 'Best Corp',
            'enable_learner_portal': enable_learner_portal,
            'slug': 'best-corp',
        }

        actual_result = get_enterprise_learner_portal_enabled_message(mock_enterprise_customer)
        if not enable_learner_portal:
            assert actual_result is None
        else:
            assert 'To access the courses available to you through' in actual_result
            assert 'Best Corp' in actual_result

    def test_enterprise_learner_portal_message_no_customer(self):
        """
        When an enterprise customer does not exists, then
        ``get_enterprise_learner_portal_enabled_message()`` should return None.
        """
        actual_result = get_enterprise_learner_portal_enabled_message(None)
        assert actual_result is None

    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_customer_uuid_for_request_sso_provider_id_customer_exists(self, mock_partial_pipeline):
        mock_idp = EnterpriseCustomerIdentityProviderFactory.create()
        mock_customer = mock_idp.enterprise_customer
        mock_request = mock.Mock(
            GET={'tpa_hint': mock_idp.provider_id},
            COOKIES={},
            session={},
        )

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        expected_uuid = mock_customer.uuid
        assert expected_uuid == actual_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        assert ENTERPRISE_CUSTOMER_KEY_NAME not in mock_request.session

    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_customer_uuid_for_request_sso_provider_id_customer_non_existent_but_exist_in_db(
        self,
        mock_partial_pipeline,
        mock_data_from_db,
    ):
        enterprise_customer_uuid = 'adab9a14-f263-42e6-a234-db707026c4a6'
        mock_request = mock.Mock(
            GET={'tpa_hint': 'my-third-party-auth'},
            COOKIES={},
            session={},
        )
        mock_data_from_db.return_value = [
            {'enterprise_customer': {'uuid': enterprise_customer_uuid}},
        ]

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        assert actual_uuid == enterprise_customer_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        assert ENTERPRISE_CUSTOMER_KEY_NAME in mock_request.session

    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_enterprise_uuid_for_request_from_query_params(self, mock_partial_pipeline):
        expected_uuid = 'my-uuid'
        mock_request = mock.Mock(
            GET={ENTERPRISE_CUSTOMER_KEY_NAME: expected_uuid},
            COOKIES={},
            session={},
        )

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        assert expected_uuid == actual_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        assert ENTERPRISE_CUSTOMER_KEY_NAME not in mock_request.session

    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_enterprise_uuid_for_request_from_cookies(self, mock_partial_pipeline):
        expected_uuid = 'my-uuid'
        mock_request = mock.Mock(
            GET={},
            COOKIES={settings.ENTERPRISE_CUSTOMER_COOKIE_NAME: expected_uuid},
            session={},
        )

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        assert expected_uuid == actual_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        assert ENTERPRISE_CUSTOMER_KEY_NAME not in mock_request.session

    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_enterprise_uuid_for_request_from_session(self, mock_partial_pipeline):
        expected_uuid = 'my-uuid'
        mock_request = mock.Mock(
            GET={},
            COOKIES={},
            session={ENTERPRISE_CUSTOMER_KEY_NAME: {'uuid': expected_uuid}},
        )

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        assert expected_uuid == actual_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        assert {'uuid': expected_uuid} == mock_request.session.get(ENTERPRISE_CUSTOMER_KEY_NAME)

    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db')
    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_enterprise_uuid_for_request_cache_miss_but_exists_in_db(self, mock_partial_pipeline, mock_data_from_db):
        mock_request = mock.Mock(
            GET={},
            COOKIES={},
            session={},
        )
        mock_data_from_db.return_value = [
            {'enterprise_customer': {'uuid': 'my-uuid'}},
        ]

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        expected_uuid = 'my-uuid'
        assert expected_uuid == actual_uuid
        mock_partial_pipeline.assert_called_once_with(mock_request)
        mock_data_from_db.assert_called_once_with(mock_request.user)
        assert {'uuid': 'my-uuid'} == mock_request.session[ENTERPRISE_CUSTOMER_KEY_NAME]

    @ddt.data(True, False)
    @mock.patch('openedx.features.enterprise_support.api.get_enterprise_learner_data_from_db', return_value=None)
    @mock.patch('openedx.features.enterprise_support.api.get_partial_pipeline', return_value=None)
    def test_enterprise_uuid_for_request_cache_miss_non_existent(
        self,
        is_user_authenticated,
        mock_partial_pipeline,
        mock_data_from_db
    ):
        mock_request = mock.Mock(
            GET={},
            COOKIES={},
            session={},
        )
        mock_request.user.is_authenticated = is_user_authenticated

        actual_uuid = enterprise_customer_uuid_for_request(mock_request)

        assert actual_uuid is None
        mock_partial_pipeline.assert_called_once_with(mock_request)

        if is_user_authenticated:
            mock_data_from_db.assert_called_once_with(mock_request.user)
            assert mock_request.session[ENTERPRISE_CUSTOMER_KEY_NAME] is None
        else:
            assert not mock_data_from_db.called
            assert ENTERPRISE_CUSTOMER_KEY_NAME not in mock_request.session

    def test_enterprise_customer_from_session(self):
        mock_request = mock.Mock(
            GET={},
            COOKIES={},
            session={},
        )
        mock_request.user.is_authenticated = True

        enterprise_customer = {
            'name': 'abc',
            'uuid': 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        }

        # set enterprise customer info with authenticate user
        add_enterprise_customer_to_session(mock_request, enterprise_customer)
        assert mock_request.session[ENTERPRISE_CUSTOMER_KEY_NAME] == enterprise_customer

        # Now try to set info with un-authenticated user
        mock_request.user.is_authenticated = False
        add_enterprise_customer_to_session(mock_request, None)
        # verify that existing session value should not be updated for un-authenticate user
        assert mock_request.session[ENTERPRISE_CUSTOMER_KEY_NAME] == enterprise_customer

    @ddt.data(None, object())
    def test_enterprise_customer_from_session_no_session_CACHE_MISS(self, request):
        assert enterprise_customer_from_session(request) == _CACHE_MISS

    def test_get_consent_notification_data_no_overrides(self):
        enterprise_customer = {
            'name': 'abc',
            'uuid': 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        }

        title_template, message_template = get_consent_notification_data(enterprise_customer)

        assert title_template is None
        assert message_template is None

    @mock.patch('openedx.features.enterprise_support.api.DataSharingConsentTextOverrides')
    def test_get_consent_notification_data(self, mock_override_model):
        enterprise_customer = {
            'name': 'abc',
            'uuid': 'cf246b88-d5f6-4908-a522-fc307e0b0c59'
        }
        mock_override = mock.Mock(
            declined_notification_title='the title',
            declined_notification_message='the message',
        )
        mock_override_model.objects.get.return_value = mock_override

        title_template, message_template = get_consent_notification_data(enterprise_customer)

        assert mock_override.declined_notification_title == title_template
        assert mock_override.declined_notification_message == message_template
