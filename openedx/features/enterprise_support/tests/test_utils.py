"""
Test the enterprise support utils.
"""

import json
import uuid
from unittest import mock

import ddt
from completion.models import BlockCompletion
from completion.test_utils import CompletionWaffleTestMixin
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import NoReverseMatch
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.enterprise_support.tests import FEATURES_WITH_ENTERPRISE_ENABLED
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCustomerBrandingConfigurationFactory,
    EnterpriseCustomerFactory,
    EnterpriseCustomerUserFactory,
)
from openedx.features.enterprise_support.utils import (
    ENTERPRISE_HEADER_LINKS,
    _user_has_social_auth_record,
    clear_data_consent_share_cache,
    enterprise_fields_only,
    fetch_enterprise_customer_by_id,
    get_data_consent_share_cache_key,
    get_enterprise_learner_generic_name,
    get_enterprise_learner_portal,
    get_enterprise_readonly_account_fields,
    get_enterprise_sidebar_context,
    get_enterprise_slug_login_url,
    get_provider_login_url,
    handle_enterprise_cookies_for_logistration,
    is_course_accessed,
    is_enterprise_learner,
    update_account_settings_context_for_enterprise,
    update_logistration_context_for_enterprise,
    update_third_party_auth_context_for_enterprise,
)
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

TEST_PASSWORD = 'test'


@ddt.ddt
@override_settings(FEATURES=FEATURES_WITH_ENTERPRISE_ENABLED)
@skip_unless_lms
class TestEnterpriseUtils(TestCase):
    """
    Test enterprise support utils.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory.create(password='password')
        super().setUpTestData()

    @mock.patch('openedx.features.enterprise_support.utils.get_cache_key')
    def test_get_data_consent_share_cache_key(self, mock_get_cache_key):
        expected_cache_key = mock_get_cache_key.return_value

        assert expected_cache_key == get_data_consent_share_cache_key(
            'some-user-id',
            'some-course-id',
            '1a9cae8f-abb7-4336-b075-6ff32ecf73de'
        )

        mock_get_cache_key.assert_called_once_with(
            type='data_sharing_consent_needed',
            user_id='some-user-id',
            course_id='some-course-id',
            enterprise_customer_uuid='1a9cae8f-abb7-4336-b075-6ff32ecf73de'
        )

    @mock.patch('openedx.features.enterprise_support.utils.get_cache_key')
    @mock.patch('openedx.features.enterprise_support.utils.TieredCache')
    def test_clear_data_consent_share_cache(self, mock_tiered_cache, mock_get_cache_key):
        user_id = 'some-user-id'
        course_id = 'some-course-id'
        enterprise_customer_uuid = '1a9cae8f-abb7-4336-b075-6ff32ecf73de'

        clear_data_consent_share_cache(user_id, course_id, enterprise_customer_uuid)

        mock_get_cache_key.assert_called_once_with(
            type='data_sharing_consent_needed',
            user_id='some-user-id',
            course_id='some-course-id',
            enterprise_customer_uuid=enterprise_customer_uuid
        )
        mock_tiered_cache.delete_all_tiers.assert_called_once_with(mock_get_cache_key.return_value)

    @mock.patch('openedx.features.enterprise_support.utils.update_third_party_auth_context_for_enterprise')
    def test_update_logistration_context_no_customer_data(self, mock_update_tpa_context):
        request = mock.Mock()
        context = {}
        enterprise_customer = {}

        update_logistration_context_for_enterprise(request, context, enterprise_customer)

        assert context['enable_enterprise_sidebar'] is False
        mock_update_tpa_context.assert_called_once_with(request, context, enterprise_customer)

    @mock.patch('openedx.features.enterprise_support.utils.update_third_party_auth_context_for_enterprise')
    @mock.patch('openedx.features.enterprise_support.utils.get_enterprise_sidebar_context', return_value={})
    def test_update_logistration_context_no_sidebar_context(self, mock_sidebar_context, mock_update_tpa_context):
        request = mock.Mock(GET={'proxy_login': False})
        context = {}
        enterprise_customer = {'key': 'value'}

        update_logistration_context_for_enterprise(request, context, enterprise_customer)

        assert context['enable_enterprise_sidebar'] is False
        mock_update_tpa_context.assert_called_once_with(request, context, enterprise_customer)
        mock_sidebar_context.assert_called_once_with(enterprise_customer, False)

    @mock.patch('openedx.features.enterprise_support.utils.update_third_party_auth_context_for_enterprise')
    @mock.patch('openedx.features.enterprise_support.utils.get_enterprise_sidebar_context')
    @mock.patch('openedx.features.enterprise_support.utils.enterprise_fields_only')
    def test_update_logistration_context_with_sidebar_context(
            self, mock_enterprise_fields_only, mock_sidebar_context, mock_update_tpa_context
    ):
        request = mock.Mock(GET={'proxy_login': False})
        context = {
            'data': {
                'registration_form_desc': {
                    'thing-1': 'one',
                    'thing-2': 'two',
                },
            },
        }
        enterprise_customer = {'name': 'pied-piper'}
        mock_sidebar_context.return_value = {
            'sidebar-1': 'one',
            'sidebar-2': 'two',
        }

        update_logistration_context_for_enterprise(request, context, enterprise_customer)

        assert context['enable_enterprise_sidebar'] is True
        mock_update_tpa_context.assert_called_once_with(request, context, enterprise_customer)
        mock_enterprise_fields_only.assert_called_once_with(context['data']['registration_form_desc'])
        mock_sidebar_context.assert_called_once_with(enterprise_customer, False)

    @ddt.data(
        {'is_proxy_login': True, 'branding_configuration': {'logo': 'path-to-logo'}},
        {'is_proxy_login': True, 'branding_configuration': {}},
        {'is_proxy_login': False, 'branding_configuration': {'nonsense': 'foo'}},
    )
    @ddt.unpack
    def test_get_enterprise_sidebar_context(self, is_proxy_login, branding_configuration):
        enterprise_customer = {
            'name': 'pied-piper',
            'branding_configuration': branding_configuration,
        }
        actual_result = get_enterprise_sidebar_context(enterprise_customer, is_proxy_login)

        assert 'pied-piper' == actual_result['enterprise_name']
        expected_logo_url = branding_configuration.get('logo', '')
        assert expected_logo_url == actual_result['enterprise_logo_url']
        assert 'pied-piper' in str(actual_result['enterprise_branded_welcome_string'])

    @ddt.data(
        ('notfoundpage', 0),
    )
    @ddt.unpack
    def test_enterprise_customer_for_request_called_on_404(self, resource, expected_calls):
        """
        Test enterprise customer API is not called from 404 page
        """
        self.client.login(username=self.user.username, password='password')

        with mock.patch(
            'openedx.features.enterprise_support.api.enterprise_customer_for_request'
        ) as mock_customer_request:
            self.client.get(resource)
            assert mock_customer_request.call_count == expected_calls

    @mock.patch('openedx.features.enterprise_support.utils.configuration_helpers.get_value')
    def test_enterprise_fields_only(self, mock_get_value):
        mock_get_value.return_value = ['cat', 'dog', 'sheep']
        fields = {
            'fields': [
                {'name': 'cat', 'value': 1},
                {'name': 'fish', 'value': 2},
                {'name': 'dog', 'value': 3},
                {'name': 'emu', 'value': 4},
                {'name': 'sheep', 'value': 5},
            ],
        }

        expected_fields = [
            {'name': 'fish', 'value': 2},
            {'name': 'emu', 'value': 4},
        ]
        assert expected_fields == enterprise_fields_only(fields)

    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_update_third_party_auth_context_for_enterprise(self, mock_tpa):
        context = {
            'data': {
                'third_party_auth': {
                    'errorMessage': 'Widget error.',
                },
            },
        }

        enterprise_customer = mock.Mock()
        request = mock.Mock()

        # This will directly modify context
        update_third_party_auth_context_for_enterprise(request, context, enterprise_customer)

        assert 'We are sorry, you are not authorized' in str(context['data']['third_party_auth']['errorMessage'])
        assert 'Widget error.' in str(context['data']['third_party_auth']['errorMessage'])
        assert [] == context['data']['third_party_auth']['providers']
        assert [] == context['data']['third_party_auth']['secondaryProviders']
        assert not context['data']['third_party_auth']['autoSubmitRegForm']
        assert 'Just a couple steps' in str(context['data']['third_party_auth']['autoRegisterWelcomeMessage'])
        assert 'Continue' == str(context['data']['third_party_auth']['registerFormSubmitButtonText'])
        mock_tpa.pipeline.get.assert_called_once_with(request)

    @mock.patch('openedx.features.enterprise_support.utils.standard_cookie_settings', return_value={})
    def test_handle_enterprise_cookies_for_logistration(self, mock_cookie_settings):
        context = {'enable_enterprise_sidebar': True}
        request = mock.Mock()
        response = mock.Mock()

        handle_enterprise_cookies_for_logistration(request, response, context)

        response.set_cookie.assert_called_once_with(
            'experiments_is_enterprise',
            'true',
        )
        response.delete_cookie.assert_called_once_with(
            settings.ENTERPRISE_CUSTOMER_COOKIE_NAME,
            domain=settings.BASE_COOKIE_DOMAIN,
        )
        mock_cookie_settings.assert_called_once_with(request)

    @mock.patch('openedx.features.enterprise_support.utils.get_enterprise_readonly_account_fields', return_value=[])
    def test_update_account_settings_context_for_enterprise(self, mock_get_fields):
        enterprise_customer = {
            'name': 'pied-piper',
            'identity_provider': None,
        }
        context = {}
        user = mock.Mock()

        update_account_settings_context_for_enterprise(context, enterprise_customer, user)

        expected_context = {
            'enterprise_name': 'pied-piper',
            'sync_learner_profile_data': False,
            'edx_support_url': settings.SUPPORT_SITE_LINK,
            'enterprise_readonly_account_fields': {
                'fields': mock_get_fields.return_value,
            },
        }
        mock_get_fields.assert_called_once_with(user)
        assert expected_context == context

    @ddt.data(settings.ENTERPRISE_READONLY_ACCOUNT_FIELDS, ['username', 'email', 'country'])
    @mock.patch('openedx.features.enterprise_support.utils.get_current_request')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    def test_get_enterprise_readonly_account_fields_no_sync_learner_profile_data(
            self, readonly_fields, mock_customer_for_request, mock_get_current_request,
    ):
        mock_get_current_request.return_value = mock.Mock(
            GET={'enterprise_customer': 'some-uuid'},
        )
        mock_customer_for_request.return_value = {
            'uuid': 'some-uuid',
            'identity_provider': None,
            'identity_providers': [],
        }
        user = mock.Mock()

        with override_settings(ENTERPRISE_READONLY_ACCOUNT_FIELDS=readonly_fields):
            actual_fields = get_enterprise_readonly_account_fields(user)
        assert set() == actual_fields
        mock_customer_for_request.assert_called_once_with(mock_get_current_request.return_value)
        mock_get_current_request.assert_called_once_with()

    @mock.patch('openedx.features.enterprise_support.utils.UserSocialAuth')
    @mock.patch('openedx.features.enterprise_support.utils.get_current_request')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_get_enterprise_readonly_account_fields_with_idp_sync(
            self, mock_tpa, mock_customer_for_request, mock_get_current_request, mock_user_social_auth
    ):
        mock_get_current_request.return_value = mock.Mock(
            GET={'enterprise_customer': 'some-uuid'},
        )
        mock_customer_for_request.return_value = {
            'uuid': 'some-uuid',
            'identity_provider': 'mock-idp',
            'identity_providers': [
                {
                    "provider_id": "mock-idp",
                },
            ]
        }
        mock_idp = mock.MagicMock(
            backend_name='mock-backend',
            sync_learner_profile_data=True,
        )
        mock_tpa.provider.Registry.get.return_value = mock_idp
        user = mock.Mock()

        actual_fields = get_enterprise_readonly_account_fields(user)

        assert set(settings.ENTERPRISE_READONLY_ACCOUNT_FIELDS) == actual_fields

        mock_customer_for_request.assert_called_once_with(mock_get_current_request.return_value)
        mock_get_current_request.assert_called_once_with()

        mock_tpa.provider.Registry.get.assert_called_with(provider_id='mock-idp')
        mock_select_related = mock_user_social_auth.objects.select_related
        mock_select_related.assert_called_once_with('user')
        mock_select_related.return_value.filter.assert_called_once_with(
            provider__in=[mock_idp.backend_name],
            user=user
        )

    @override_waffle_flag(ENTERPRISE_HEADER_LINKS, True)
    def test_get_enterprise_learner_portal_uncached(self):
        """
        Test that only an enabled enterprise portal is returned,
        and that it matches the customer UUID provided in the request.
        """
        enterprise_customer_user = EnterpriseCustomerUserFactory(active=True, user_id=self.user.id)
        EnterpriseCustomerBrandingConfigurationFactory(
            enterprise_customer=enterprise_customer_user.enterprise_customer,
        )
        enterprise_customer_user.enterprise_customer.enable_learner_portal = True
        enterprise_customer_user.enterprise_customer.save()

        request = mock.MagicMock(session={}, user=self.user)
        # Indicate the "preferred" customer in the request
        request.GET = {'enterprise_customer': enterprise_customer_user.enterprise_customer.uuid}

        # Create another enterprise customer association for the same user.
        # There should be no data returned for this customer's portal,
        # because we filter for only the enterprise customer uuid found in the request.
        other_enterprise_customer_user = EnterpriseCustomerUserFactory(active=True, user_id=self.user.id)
        other_enterprise_customer_user.enable_learner_portal = True
        other_enterprise_customer_user.save()

        portal = get_enterprise_learner_portal(request)
        self.assertDictEqual(portal, {  # noqa: PT009
            'name': enterprise_customer_user.enterprise_customer.name,
            'slug': enterprise_customer_user.enterprise_customer.slug,
            'logo': enterprise_customer_user.enterprise_customer.safe_branding_configuration.safe_logo_url,
        })

    @override_waffle_flag(ENTERPRISE_HEADER_LINKS, True)
    def test_get_enterprise_learner_portal_no_branding_config(self):
        """
        Test that only an enabled enterprise portal is returned,
        and that it matches the customer UUID provided in the request,
        even if no branding config is associated with the customer.
        """
        enterprise_customer_user = EnterpriseCustomerUserFactory.create(active=True, user_id=self.user.id)
        enterprise_customer_user.enterprise_customer.enable_learner_portal = True
        enterprise_customer_user.enterprise_customer.save()

        request = mock.MagicMock(session={}, user=self.user)
        # Indicate the "preferred" customer in the request
        request.GET = {'enterprise_customer': enterprise_customer_user.enterprise_customer.uuid}

        portal = get_enterprise_learner_portal(request)
        self.assertDictEqual(portal, {  # noqa: PT009
            'name': enterprise_customer_user.enterprise_customer.name,
            'slug': enterprise_customer_user.enterprise_customer.slug,
            'logo': enterprise_customer_user.enterprise_customer.safe_branding_configuration.safe_logo_url,
        })

    @override_waffle_flag(ENTERPRISE_HEADER_LINKS, True)
    def test_get_enterprise_learner_portal_no_customer_from_request(self):
        """
        Test that only one enabled enterprise portal is returned,
        even if enterprise_customer_uuid_from_request() returns None.
        """
        # Create another enterprise customer association for the same user.
        # There should be no data returned for this customer's portal,
        # because another customer is later created with a more recent active/modified time.
        other_enterprise_customer_user = EnterpriseCustomerUserFactory(active=True, user_id=self.user.id)
        other_enterprise_customer_user.enable_learner_portal = True
        other_enterprise_customer_user.save()

        enterprise_customer_user = EnterpriseCustomerUserFactory(active=True, user_id=self.user.id)
        EnterpriseCustomerBrandingConfigurationFactory(
            enterprise_customer=enterprise_customer_user.enterprise_customer,
        )
        enterprise_customer_user.enterprise_customer.enable_learner_portal = True
        enterprise_customer_user.enterprise_customer.save()

        request = mock.MagicMock(session={}, user=self.user)

        with mock.patch(
                'openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request',
                return_value=None,
        ):
            portal = get_enterprise_learner_portal(request)

        self.assertDictEqual(portal, {  # noqa: PT009
            'name': enterprise_customer_user.enterprise_customer.name,
            'slug': enterprise_customer_user.enterprise_customer.slug,
            'logo': enterprise_customer_user.enterprise_customer.safe_branding_configuration.safe_logo_url,
        })

    @override_waffle_flag(ENTERPRISE_HEADER_LINKS, True)
    def test_get_enterprise_learner_portal_cached(self):
        enterprise_customer_data = {
            'name': 'Enabled Customer',
            'slug': 'enabled_customer',
            'logo': 'https://logo.url',
        }
        request = mock.MagicMock(session={
            'enterprise_learner_portal': json.dumps(enterprise_customer_data)
        }, user=self.user)
        portal = get_enterprise_learner_portal(request)
        self.assertDictEqual(portal, enterprise_customer_data)  # noqa: PT009

    @override_waffle_flag(ENTERPRISE_HEADER_LINKS, True)
    def test_get_enterprise_learner_portal_no_enterprise_user(self):
        request = mock.MagicMock(session={}, user=self.user)
        # Indicate the "preferred" customer in the request
        request.GET = {'enterprise_customer': uuid.uuid4()}

        portal = get_enterprise_learner_portal(request)
        assert portal is None

    def test_get_enterprise_learner_generic_name_404_pages(self):
        request = mock.Mock(view_name='404')
        assert get_enterprise_learner_generic_name(request) is None

    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    def test_get_enterprise_learner_generic_name_with_replacement(self, mock_customer_for_request):
        request = mock.Mock()
        mock_customer_for_request.return_value = {
            'name': 'Test Corp',
            'replace_sensitive_sso_username': True,
        }
        generic_name = get_enterprise_learner_generic_name(request)
        assert 'Test CorpLearner' == generic_name

    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    def test_get_enterprise_learner_generic_name_no_replacement(self, mock_customer_for_request):
        request = mock.Mock()
        mock_customer_for_request.return_value = {
            'name': 'Test Corp',
            'replace_sensitive_sso_username': False,
        }
        generic_name = get_enterprise_learner_generic_name(request)
        assert '' == generic_name

    def test_is_enterprise_learner(self):
        with mock.patch(
            'django.core.cache.cache.set'
        ) as mock_cache_set:
            EnterpriseCustomerUserFactory.create(active=True, user_id=self.user.id)
            assert is_enterprise_learner(self.user)
            assert is_enterprise_learner(self.user.id)

        assert mock_cache_set.called

    def test_is_enterprise_learner_no_enterprise_user(self):
        with mock.patch(
            'django.core.cache.cache.set'
        ) as mock_cache_set:
            assert not is_enterprise_learner(self.user)

        assert not mock_cache_set.called

    @mock.patch('django.core.cache.cache.set')
    @mock.patch('django.core.cache.cache.get')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled', return_value=False)
    def test_is_enterprise_learner_enterprise_disabled(self, _, mock_cache_get, mock_cache_set):  # noqa: PT019
        assert not is_enterprise_learner(self.user)
        assert not is_enterprise_learner(self.user.id)
        assert not mock_cache_get.called
        assert not mock_cache_set.called

    @mock.patch('openedx.features.enterprise_support.utils.reverse')
    def test_get_enterprise_slug_login_url_no_reverse_match(self, mock_reverse):
        mock_reverse.side_effect = NoReverseMatch
        assert get_enterprise_slug_login_url() is None
        mock_reverse.assert_called_once_with('enterprise_slug_login')

    @mock.patch('openedx.features.enterprise_support.utils.reverse')
    def test_get_enterprise_slug_login_url_with_match(self, mock_reverse):
        assert get_enterprise_slug_login_url() is not None
        mock_reverse.assert_called_once_with('enterprise_slug_login')

    def test_fetch_enterprise_customer_by_id(self):
        the_uuid = uuid.uuid4()
        customer = EnterpriseCustomerFactory.create(uuid=the_uuid)
        assert customer == fetch_enterprise_customer_by_id(the_uuid)

    @mock.patch('openedx.features.enterprise_support.utils.get_next_url_for_login_page')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_get_provider_login_url_no_redirect_url(self, mock_tpa, mock_next_login_url):
        request = mock.Mock()
        provider_id = 'anything'

        login_url = get_provider_login_url(request, provider_id)
        assert mock_tpa.pipeline.get_login_url.return_value == login_url
        mock_tpa.pipeline.get_login_url.assert_called_once_with(
            provider_id,
            mock_tpa.pipeline.AUTH_ENTRY_LOGIN,
            redirect_url=mock_next_login_url.return_value,
        )
        mock_next_login_url.assert_called_once_with(request)

    @mock.patch('openedx.features.enterprise_support.utils.get_next_url_for_login_page')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_get_provider_login_url_with_redirect_url(self, mock_tpa, mock_next_login_url):
        request = mock.Mock()
        provider_id = 'anything'
        redirect_url = 'the-next-url'

        login_url = get_provider_login_url(request, provider_id, redirect_url=redirect_url)
        assert mock_tpa.pipeline.get_login_url.return_value == login_url
        mock_tpa.pipeline.get_login_url.assert_called_once_with(
            provider_id,
            mock_tpa.pipeline.AUTH_ENTRY_LOGIN,
            redirect_url=redirect_url,
        )
        assert not mock_next_login_url.called

    @mock.patch('openedx.features.enterprise_support.utils.UserSocialAuth')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_user_has_social_auth_record(self, mock_tpa, mock_user_social_auth):
        user = mock.Mock()
        enterprise_customer = {
            'identity_providers': [
                {'provider_id': 'mock-idp'},
            ],
        }
        mock_idp = mock.MagicMock(backend_name='mock-backend')
        mock_tpa.provider.Registry.get.return_value = mock_idp
        mock_user_social_auth.objects.select_related.return_value.filter.return_value.exists.return_value = True

        result = _user_has_social_auth_record(user, enterprise_customer)
        assert result is True

        mock_tpa.provider.Registry.get.assert_called_once_with(provider_id='mock-idp')
        mock_user_social_auth.objects.select_related.assert_called_once_with('user')
        mock_user_social_auth.objects.select_related.return_value.filter.assert_called_once_with(
            provider__in=['mock-backend'], user=user
        )

    @mock.patch('openedx.features.enterprise_support.utils.UserSocialAuth')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_user_has_social_auth_record_no_providers(self, mock_tpa, mock_user_social_auth):
        user = mock.Mock()
        enterprise_customer = {
            'identity_providers': [],
        }

        result = _user_has_social_auth_record(user, enterprise_customer)
        assert result is False

        assert not mock_tpa.provider.Registry.get.called
        assert not mock_user_social_auth.objects.select_related.called

    @mock.patch('openedx.features.enterprise_support.utils.UserSocialAuth')
    @mock.patch('openedx.features.enterprise_support.utils.third_party_auth')
    def test_user_has_social_auth_record_no_enterprise_customer(self, mock_tpa, mock_user_social_auth):
        user = mock.Mock()
        enterprise_customer = None

        result = _user_has_social_auth_record(user, enterprise_customer)
        assert result is False

        assert not mock_tpa.provider.Registry.get.called
        assert not mock_user_social_auth.objects.select_related.called


@override_settings(FEATURES=FEATURES_WITH_ENTERPRISE_ENABLED)
@skip_unless_lms
class TestCourseAccessed(SharedModuleStoreTestCase, CompletionWaffleTestMixin):
    """
    Test the course accessed functionality.

    """
    @classmethod
    def setUpClass(cls):
        """
        Creates a test course that can be used for non-destructive tests
        """
        # setUpClassAndTestData() already calls setUpClass on SharedModuleStoreTestCase
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            cls.course = cls.create_test_course()

    @classmethod
    def setUpTestData(cls):  # pylint: disable=super-method-not-called
        """Set up and enroll our fake user in the course."""
        cls.user = UserFactory(password=TEST_PASSWORD)
        CourseEnrollment.enroll(cls.user, cls.course.id)
        cls.site = Site.objects.get_current()

    @classmethod
    def create_test_course(cls):
        """
        Creates a test course.
        """
        course = CourseFactory.create()
        with cls.store.bulk_operations(course.id):
            chapter = BlockFactory.create(category='chapter', parent_location=course.location)
            chapter2 = BlockFactory.create(category='chapter', parent_location=course.location)
            sequential = BlockFactory.create(category='sequential', parent_location=chapter.location)
            sequential2 = BlockFactory.create(category='sequential', parent_location=chapter.location)
            sequential3 = BlockFactory.create(category='sequential', parent_location=chapter2.location)
            sequential4 = BlockFactory.create(category='sequential', parent_location=chapter2.location)
            vertical = BlockFactory.create(category='vertical', parent_location=sequential.location)
            vertical2 = BlockFactory.create(category='vertical', parent_location=sequential2.location)
            vertical3 = BlockFactory.create(category='vertical', parent_location=sequential3.location)
            vertical4 = BlockFactory.create(category='vertical', parent_location=sequential4.location)
        course.children = [chapter, chapter2]
        chapter.children = [sequential, sequential2]
        chapter2.children = [sequential3, sequential4]
        sequential.children = [vertical]
        sequential2.children = [vertical2]
        sequential3.children = [vertical3]
        sequential4.children = [vertical4]
        if hasattr(cls, 'user'):
            CourseEnrollment.enroll(cls.user, course.id)
        return course

    def setUp(self):
        """
        Set up for the tests.
        """
        super().setUp()
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    @override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, active=True)
    def complete_sequential(self, course, sequential):
        """
        Completes provided sequential.
        """
        course_key = CourseKey.from_string(str(course.id))
        # Fake a visit to sequence2/vertical2
        block_key = UsageKey.from_string(str(sequential.location))
        if block_key.course_key.run is None:
            # Old mongo keys must be annotated with course run info before calling submit_completion:
            block_key = block_key.replace(course_key=course_key)
        completion = 1.0
        BlockCompletion.objects.submit_completion(
            user=self.user,
            block_key=block_key,
            completion=completion
        )

    @override_settings(LMS_BASE='test_url:9999')
    def test_course_accessed_with_completion_api(self):
        """
        Tests the course accessed function with completion API functionality
        """
        self.override_waffle_switch(True)

        # Course tree
        course = self.course
        vertical1 = course.children[0].children[0].children[0]

        self.complete_sequential(self.course, vertical1)
        course_accessed = is_course_accessed(self.user, str(self.course.id))
        self.assertTrue(course_accessed)  # noqa: PT009
