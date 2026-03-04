"""
Tests for the Third Party Auth REST API
"""

import urllib
from types import SimpleNamespace
from unittest.mock import patch

import ddt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from rest_framework.test import APITestCase
from social_django.models import UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.api.permissions import (
    JwtHasScope,
    JwtHasTpaProviderFilterForRequestedProvider,
    JwtRestrictedApplication
)
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_lms

VALID_API_KEY = "i am a key"
IDP_SLUG_TESTSHIB = 'testshib'
PROVIDER_ID_TESTSHIB = 'saml-' + IDP_SLUG_TESTSHIB

ALICE_USERNAME = "alice"
CARL_USERNAME = "carl"
STAFF_USERNAME = "staff"
ADMIN_USERNAME = "admin"
NONEXISTENT_USERNAME = "nobody"
# These users will be created and linked to third party accounts:
LINKED_USERS = (ALICE_USERNAME, STAFF_USERNAME, ADMIN_USERNAME)
PASSWORD = "edx"


def get_mapping_data_by_usernames(usernames, remote_id_field_name=False):
    """ Generate mapping data used in response """
    if remote_id_field_name:
        return [{'username': username, 'remote_id': 'external_' + username} for username in usernames]
    return [{'username': username, 'remote_id': 'remote_' + username} for username in usernames]


class TpaAPITestCase(ThirdPartyAuthTestMixin, APITestCase):
    """ Base test class """

    def setUp(self):  # pylint: disable=arguments-differ
        """ Create users for use in the tests """
        super().setUp()

        google = self.configure_google_provider(enabled=True)
        self.configure_facebook_provider(enabled=True)
        self.configure_linkedin_provider(enabled=False)
        self.enable_saml()
        testshib = self.configure_saml_provider(
            name='TestShib',
            enabled=True,
            slug=IDP_SLUG_TESTSHIB
        )

        # Create several users and link each user to Google and TestShib
        for username in LINKED_USERS:
            make_superuser = username == ADMIN_USERNAME
            make_staff = (username == STAFF_USERNAME) or make_superuser
            user = UserFactory.create(
                username=username,
                email=f'{username}@example.com',
                password=PASSWORD,
                is_staff=make_staff,
                is_superuser=make_superuser,
            )
            UserSocialAuth.objects.create(
                user=user,
                provider=google.backend_name,
                uid=f'{username}@gmail.com',
            )
            usa = UserSocialAuth.objects.create(
                user=user,
                provider=testshib.backend_name,
                uid=f'{testshib.slug}:remote_{username}',
            )
            usa.set_extra_data({'external_user_id': f'external_{username}'})
            usa.refresh_from_db()
        # Create another user not linked to any providers:
        UserFactory.create(username=CARL_USERNAME, email=f'{CARL_USERNAME}@example.com', password=PASSWORD)


@ddt.ddt
class UserViewsMixin:
    """
    Generic TestCase to exercise the v1 and v2 UserViews.
    """

    def expected_active(self, username):
        """ The JSON active providers list response expected for the given user """
        if username not in LINKED_USERS:
            return []
        return [
            {
                "provider_id": "oa2-google-oauth2",
                "name": "Google",
                "remote_id": f"{username}@gmail.com",
            },
            {
                "provider_id": PROVIDER_ID_TESTSHIB,
                "name": "TestShib",
                # The "testshib:" prefix is stored in the UserSocialAuth.uid field but should
                # not be present in the 'remote_id', since that's an implementation detail:
                "remote_id": 'remote_' + username,
            },
        ]

    @ddt.data(
        # Any user can query their own list of providers
        (ALICE_USERNAME, ALICE_USERNAME, 200),
        (CARL_USERNAME, CARL_USERNAME, 200),
        # A regular user cannot query another user nor deduce the existence of users based on the status code
        (ALICE_USERNAME, STAFF_USERNAME, 403),
        (ALICE_USERNAME, "nonexistent_user", 403),
        # Even Staff cannot query other users
        (STAFF_USERNAME, ALICE_USERNAME, 403),
        # But admins can
        (ADMIN_USERNAME, ALICE_USERNAME, 200),
        (ADMIN_USERNAME, CARL_USERNAME, 200),
        (ADMIN_USERNAME, "invalid_username", 404),
    )
    @ddt.unpack
    def test_list_connected_providers(self, request_user, target_user, expect_result):
        self.client.login(username=request_user, password=PASSWORD)
        url = self.make_url({'username': target_user})

        response = self.client.get(url)
        assert response.status_code == expect_result
        if expect_result == 200:
            assert 'active' in response.data
            self.assertCountEqual(response.data["active"], self.expected_active(target_user))

    @ddt.data(
        # A server with a valid API key can query any user's list of providers
        (VALID_API_KEY, ALICE_USERNAME, 200),
        (VALID_API_KEY, "invalid_username", 404),
        ("i am an invalid key", ALICE_USERNAME, 403),
        (None, ALICE_USERNAME, 403),
    )
    @ddt.unpack
    def test_list_connected_providers_with_api_key(self, api_key, target_user, expect_result):
        url = self.make_url({'username': target_user})
        response = self.client.get(url, HTTP_X_EDX_API_KEY=api_key)
        assert response.status_code == expect_result
        if expect_result == 200:
            assert 'active' in response.data
            self.assertCountEqual(response.data["active"], self.expected_active(target_user))

    @ddt.data(
        (True, ALICE_USERNAME, 200, True),
        (True, CARL_USERNAME, 200, False),
        (False, ALICE_USERNAME, 200, True),
        (False, CARL_USERNAME, 403, None),
    )
    @ddt.unpack
    def test_allow_unprivileged_response(self, allow_unprivileged, requesting_user, expect, include_remote_id):
        self.client.login(username=requesting_user, password=PASSWORD)
        with override_settings(ALLOW_UNPRIVILEGED_SSO_PROVIDER_QUERY=allow_unprivileged):
            url = self.make_url({'username': ALICE_USERNAME})
            response = self.client.get(url)
        assert response.status_code == expect
        if response.status_code == 200:
            assert len(response.data['active']) > 0
            for provider_data in response.data['active']:
                assert include_remote_id == ('remote_id' in provider_data)

    def test_allow_query_by_email(self):
        self.client.login(username=ALICE_USERNAME, password=PASSWORD)
        url = self.make_url({'email': f'{ALICE_USERNAME}@example.com'})
        response = self.client.get(url)
        assert response.status_code == 200
        assert len(response.data['active']) > 0

    def test_throttling(self):
        # Default throttle is 10/min.  Make 11 requests to verify
        throttling_user = UserFactory.create(password=PASSWORD)
        self.client.login(username=throttling_user.username, password=PASSWORD)
        url = self.make_url({'username': ALICE_USERNAME})
        with override_settings(ALLOW_UNPRIVILEGED_SSO_PROVIDER_QUERY=True):
            for _ in range(10):
                response = self.client.get(url)
                assert response.status_code == 200
            response = self.client.get(url)
            assert response.status_code == 200


@override_settings(EDX_API_KEY=VALID_API_KEY)
@ddt.ddt
@skip_unless_lms
class UserViewAPITests(UserViewsMixin, TpaAPITestCase):
    """
    Test the Third Party Auth User REST API
    """

    def make_url(self, identifier):
        """
        Return the view URL, with the identifier provided
        """
        return reverse(
            'third_party_auth_users_api',
            kwargs={'username': list(identifier.values())[0]}
        )


@override_settings(EDX_API_KEY=VALID_API_KEY)
@ddt.ddt
@skip_unless_lms
class UserViewV2APITests(UserViewsMixin, TpaAPITestCase):
    """
    Test the Third Party Auth User REST API
    """

    def setUp(self):  # pylint: disable=arguments-differ
        """ Create users for use in the tests """
        super().setUp()
        admin_user = get_user_model().objects.get(username=ADMIN_USERNAME)
        self.auth_token = f"JWT {generate_jwt(admin_user, is_restricted=False, scopes=None, filters=None)}"

    def make_url(self, params):
        """
        Return the view URL, with the identifier provided
        """
        return '?'.join([
            reverse('third_party_auth_users_api_v2'),
            urllib.parse.urlencode(params)
        ])

    @ddt.data(
        ({}, 400, ["Must provide one of ['email', 'username']"]),
        ({'username': ALICE_USERNAME}, 400, ["Must provide uid"]),
        (
            {'username': 'invalid-user', 'uid': f'{ALICE_USERNAME}@gmail.com'},
            404,
            {f"Either user invalid-user or social auth record {ALICE_USERNAME}@gmail.com does not exist."}
        ),
        (
            {'username': ALICE_USERNAME, 'uid': 'invalid-uid'},
            404,
            {f"Either user {ALICE_USERNAME} or social auth record invalid-uid does not exist."}
        ),
        ({'username': ALICE_USERNAME, 'uid': f'{ALICE_USERNAME}@gmail.com'}, 204, None),
    )
    @ddt.unpack
    def test_delete_social_auth_record(self, identifier, expect_code, expect_data):
        url = self.make_url(identifier)
        response = self.client.delete(url, HTTP_AUTHORIZATION=self.auth_token)
        assert response.status_code == expect_code
        assert (response.data == expect_data)

    def test_unauthorized_delete_social_auth_record_call(self):
        user = get_user_model().objects.get(username=CARL_USERNAME)
        auth_token = f"JWT {generate_jwt(user, is_restricted=False, scopes=None, filters=None)}"
        url = self.make_url({'username': ALICE_USERNAME, 'uid': f'{ALICE_USERNAME}@gmail.com'})
        response = self.client.delete(url, HTTP_AUTHORIZATION=auth_token)
        assert response.status_code == 403


@override_settings(EDX_API_KEY=VALID_API_KEY)
@ddt.ddt
@skip_unless_lms
class UserMappingViewAPITests(TpaAPITestCase):
    """
    Test the Third Party Auth User Mapping REST API
    """
    @ddt.data(
        (VALID_API_KEY, PROVIDER_ID_TESTSHIB, 200, get_mapping_data_by_usernames(LINKED_USERS)),
        ("i am an invalid key", PROVIDER_ID_TESTSHIB, 401, None),
        (None, PROVIDER_ID_TESTSHIB, 401, None),
        (VALID_API_KEY, 'non-existing-id', 404, []),
    )
    @ddt.unpack
    def test_list_all_user_mappings_withapi_key(self, api_key, provider_id, expect_code, expect_data):
        url = reverse('third_party_auth_user_mapping_api', kwargs={'provider_id': provider_id})
        response = self.client.get(url, HTTP_X_EDX_API_KEY=api_key)
        self._verify_response(response, expect_code, expect_data)

    def _create_jwt_header(self, user, is_restricted=False, scopes=None, filters=None):
        token = generate_jwt(user, is_restricted=is_restricted, scopes=scopes, filters=filters)
        return f"JWT {token}"

    @ddt.data(
        (True, 200, get_mapping_data_by_usernames(LINKED_USERS)),
        (False, 401, []),
    )
    @ddt.unpack
    def test_list_all_user_mappings_oauth2(self, valid_call, expect_code, expect_data):
        url = reverse('third_party_auth_user_mapping_api', kwargs={'provider_id': PROVIDER_ID_TESTSHIB})
        provider_filter = 'tpa_provider:' + PROVIDER_ID_TESTSHIB
        filters = [provider_filter, 'tpa_provider:another_tpa_provider']
        # create oauth2 auth data
        user = UserFactory.create(username='api_user')
        if valid_call:
            auth_header = self._create_jwt_header(user, is_restricted=True, scopes=['tpa:read'], filters=filters)
        else:
            auth_header = ''
        response = self.client.get(url, HTTP_AUTHORIZATION=auth_header)
        self._verify_response(response, expect_code, expect_data)

    @ddt.data(
        ({'username': [ALICE_USERNAME, STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME, STAFF_USERNAME], 'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
        ({'remote_id': ['remote_' + ALICE_USERNAME, 'remote_' + STAFF_USERNAME, 'remote_' + CARL_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'remote_id': ['remote_' + ALICE_USERNAME, 'remote_' + STAFF_USERNAME, 'remote_' + CARL_USERNAME],
          'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
        ({'username': [ALICE_USERNAME, CARL_USERNAME, STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME], 'remote_id': ['remote_' + STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME], 'remote_id': ['remote_' + STAFF_USERNAME],
          'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
    )
    @ddt.unpack
    def test_user_mappings_with_query_params_comma_separated(self, query_params, expect_code, expect_data):
        """ test queries like username=user1,user2,... """
        base_url = reverse(
            'third_party_auth_user_mapping_api', kwargs={'provider_id': PROVIDER_ID_TESTSHIB}
        )
        params = []
        for attr in ['username', 'remote_id']:
            if attr in query_params:
                params.append('{}={}'.format(attr, ','.join(query_params[attr])))
        if 'remote_id_field_name' in query_params:
            params.append('remote_id_field_name={}'.format(query_params['remote_id_field_name']))
        url = "{}?{}".format(base_url, '&'.join(params))
        response = self.client.get(url, HTTP_X_EDX_API_KEY=VALID_API_KEY)
        self._verify_response(response, expect_code, expect_data)

    @ddt.data(
        ({'username': [ALICE_USERNAME, STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME, STAFF_USERNAME], 'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
        ({'remote_id': ['remote_' + ALICE_USERNAME, 'remote_' + STAFF_USERNAME, 'remote_' + CARL_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'remote_id': ['remote_' + ALICE_USERNAME, 'remote_' + STAFF_USERNAME, 'remote_' + CARL_USERNAME],
          'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
        ({'username': [ALICE_USERNAME, CARL_USERNAME, STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME], 'remote_id': ['remote_' + STAFF_USERNAME]}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME])),
        ({'username': [ALICE_USERNAME], 'remote_id': ['remote_' + STAFF_USERNAME],
          'remote_id_field_name': 'external_user_id'}, 200,
         get_mapping_data_by_usernames([ALICE_USERNAME, STAFF_USERNAME], remote_id_field_name=True)),
    )
    @ddt.unpack
    def test_user_mappings_with_query_params_multi_value_key(self, query_params, expect_code, expect_data):
        """ test queries like username=user1&username=user2&... """
        base_url = reverse(
            'third_party_auth_user_mapping_api', kwargs={'provider_id': PROVIDER_ID_TESTSHIB}
        )
        params = QueryDict('', mutable=True)
        for attr in ['username', 'remote_id']:
            if attr in query_params:
                params.setlist(attr, query_params[attr])
        if 'remote_id_field_name' in query_params:
            params['remote_id_field_name'] = query_params['remote_id_field_name']
        url = f"{base_url}?{params.urlencode()}"
        response = self.client.get(url, HTTP_X_EDX_API_KEY=VALID_API_KEY)
        self._verify_response(response, expect_code, expect_data)

    def test_user_mappings_only_return_requested_idp_mapping_by_provider_id(self):
        testshib2 = self.configure_saml_provider(
            name='TestShib2',
            enabled=True,
            slug='testshib2',
            entity_id='entity-id-user-mapping'
        )
        username = 'testshib2user'
        user = UserFactory.create(
            username=username,
            password=PASSWORD,
            is_staff=False,
            is_superuser=False
        )
        UserSocialAuth.objects.create(
            user=user,
            provider=testshib2.backend_name,
            uid=f'{testshib2.slug}:{username}',
        )

        url = reverse('third_party_auth_user_mapping_api', kwargs={'provider_id': PROVIDER_ID_TESTSHIB})
        response = self.client.get(url, HTTP_X_EDX_API_KEY=VALID_API_KEY)
        assert response.status_code == 200
        self._verify_response(response, 200, get_mapping_data_by_usernames(LINKED_USERS))

    @ddt.data(
        (True, 200),
        (False, 401),
    )
    @ddt.unpack
    def test_list_all_user_mappings_tpa_permission_logic(self, has_permission, expect):
        url = reverse('third_party_auth_user_mapping_api', kwargs={'provider_id': PROVIDER_ID_TESTSHIB})
        with patch.object(JwtHasTpaProviderFilterForRequestedProvider, 'has_permission', return_value=has_permission):
            with patch.object(JwtRestrictedApplication, 'has_permission', return_value=has_permission):
                with patch.object(JwtHasScope, 'has_permission', return_value=has_permission):
                    response = self.client.get(url)
                    assert response.status_code == expect

    def _verify_response(self, response, expect_code, expect_result):
        """ verify the items in data_list exists in response and data_results matches results in response """
        assert response.status_code == expect_code
        if expect_code == 200:
            for item in ['results', 'count', 'num_pages']:
                assert item in response.data
            self.assertCountEqual(response.data['results'], expect_result)


@skip_unless_lms
class TestThirdPartyAuthUserStatusView(ThirdPartyAuthTestMixin, APITestCase):
    """
    Tests ThirdPartyAuthStatusView.
    """

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.user = UserFactory.create(password=PASSWORD)
        self.google_provider = self.configure_google_provider(enabled=True, visible=True)
        self.url = reverse('third_party_auth_user_status_api')

    def test_get(self):
        """
        Verify that get returns the expected data.
        """
        self.client.login(username=self.user.username, password=PASSWORD)
        response = self.client.get(self.url, content_type="application/json")
        next_url = urllib.parse.quote(settings.ACCOUNT_MICROFRONTEND_URL, safe="")
        assert response.status_code == 200
        assert (response.data ==
               [{
                   'accepts_logins': True, 'name': 'Google',
                   'disconnect_url': '/auth/disconnect_json/google-oauth2/?',
                   'connect_url': f'/auth/login/google-oauth2/?auth_entry=account_settings&next={next_url}',
                   'connected': False, 'id': 'oa2-google-oauth2'
               }])

    def test_get_uses_site_config_account_mfe_url(self):
        """
        The providers API uses site-configured ACCOUNT_MICROFRONTEND_URL in the next link.
        """
        self.client.login(username=self.user.username, password=PASSWORD)
        siteconf_url = "https://accounts.siteconf.example"

        helpers_stub = SimpleNamespace(
            get_value=lambda key, default=None, *args, **kwargs:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default
        )

        with patch("common.djangoapps.third_party_auth.api.views.configuration_helpers", new=helpers_stub):
            response = self.client.get(self.url)

        assert response.status_code == 200
        providers = response.data
        google = next(p for p in providers if p["id"].endswith("google-oauth2"))
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(google["connect_url"]).query)
        assert qs.get("next") == [siteconf_url]
