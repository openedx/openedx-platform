"""
Tests for Blocks Views
"""


import json
from unittest.mock import call, patch

import ddt
import httpretty
from django.conf import settings
from django.test import RequestFactory, TestCase
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_switch
from oauth2_provider import models as dot_models

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.tests.utils import ThirdPartyOAuthTestMixin, ThirdPartyOAuthTestMixinGoogle
from openedx.core.djangoapps.oauth_dispatch.toggles import DISABLE_JWT_FOR_MOBILE
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .. import adapters, models, views
from . import mixins
from .constants import DUMMY_REDIRECT_URL

# NOTE (CCB): We use this feature flag in a roundabout way to determine if the oauth_dispatch app is installed
# in the current service--LMS or Studio. Normally we would check if settings.ROOT_URLCONF == 'lms.urls'; however,
# simply importing the views will results in an error due to the requisite apps not being installed (in Studio). Thus,
# we are left with this hack, of checking the feature flag which will never be True for Studio.
#
# NOTE (BJM): As of Django 1.9 we also can't import models for apps which aren't in INSTALLED_APPS, so making all of
# these imports conditional except mixins, which doesn't currently import forbidden models, and is needed at test
# discovery time.



class AccessTokenLoginMixin:
    """
    Shared helper class to assert proper access levels when using access_tokens
    """

    def setUp(self):
        """
        Initialize mixin
        """
        super().setUp()
        self.login_with_access_token_url = reverse("login_with_access_token")

    def login_with_access_token(self, access_token=None):
        """
        Login with access token and return response.
        You can optionally send in an accss_token to override
        the object's attribute
        """

        return self.client.post(
            self.login_with_access_token_url,
            HTTP_AUTHORIZATION=f"Bearer {access_token if access_token else self.access_token}".encode('utf-8')  # noqa: UP012
        )

    def _assert_access_token_is_valid(self, access_token=None):
        """
        Asserts that oauth assigned access_token is valid and usable
        """
        assert self.login_with_access_token(access_token=access_token).status_code == 204

    def _assert_access_token_invalidated(self, access_token=None):
        """
        Asserts that oauth assigned access_token is not valid
        """
        assert self.login_with_access_token(access_token=access_token).status_code == 401


@skip_unless_lms
class _DispatchingViewTestCase(TestCase):
    """
    Base class for tests that exercise DispatchingViews.

    Subclasses need to define self.url.
    """
    def setUp(self):
        super().setUp()
        self.TEST_PASSWORD = 'Password1234'
        self.dot_adapter = adapters.DOTAdapter()
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.dot_app = self.dot_adapter.create_public_client(
            name='test dot application',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='dot-app-client-id',
        )

        self.dot_app_access = models.ApplicationAccess.objects.create(
            application=self.dot_app,
            scopes=['grades:read'],
        )

        # Create a "restricted" DOT Application which means any AccessToken/JWT
        # generated for this application will be immediately expired
        self.restricted_dot_app = self.dot_adapter.create_public_client(
            name='test restricted dot application',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='dot-restricted-app-client-id',
        )
        models.RestrictedApplication.objects.create(application=self.restricted_dot_app)

    def _post_request(self, user, client, token_type=None, scope=None, headers=None, asymmetric_jwt=False):
        """
        Call the view with a POST request object with the appropriate format,
        returning the response object.
        """
        post_body = self._post_body(user, client, token_type, scope, asymmetric_jwt=asymmetric_jwt)
        return self.client.post(self.url, post_body, **(headers or {}))  # pylint: disable=no-member

    def _post_body(self, user, client, token_type=None, scope=None, asymmetric_jwt=False):
        """
        Return a dictionary to be used as the body of the POST request
        """
        raise NotImplementedError()


@ddt.ddt
class TestAccessTokenView(AccessTokenLoginMixin, mixins.AccessTokenMixin, _DispatchingViewTestCase):
    """
    Test class for AccessTokenView
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('access_token')
        self.view_class = views.AccessTokenView

    def _post_body(self, user, client, token_type=None, scope=None, asymmetric_jwt=None):
        """
        Return a dictionary to be used as the body of the POST request
        """
        grant_type = getattr(client, 'authorization_grant_type', dot_models.Application.GRANT_PASSWORD)
        body = {
            'client_id': client.client_id,
            'grant_type': grant_type.replace('-', '_'),
        }

        if grant_type == dot_models.Application.GRANT_PASSWORD:
            body['username'] = user.username
            body['password'] = self.TEST_PASSWORD
        elif grant_type == dot_models.Application.GRANT_CLIENT_CREDENTIALS:
            body['client_secret'] = client.client_secret

        if token_type:
            body['token_type'] = token_type

        if scope:
            body['scope'] = scope

        if asymmetric_jwt:
            body['asymmetric_jwt'] = asymmetric_jwt

        return body

    def _test_jwt_access_token(self, client_attr, token_type=None, headers=None, grant_type=None, asymmetric_jwt=False):
        """
        Test response for JWT token.
        """
        client = getattr(self, client_attr)
        response = self._post_request(self.user, client, token_type=token_type,
                                      headers=headers or {}, asymmetric_jwt=asymmetric_jwt)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        expected_default_expires_in = 60 * 60
        assert data['expires_in'] == expected_default_expires_in
        assert data['token_type'] == 'JWT'
        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            data['scope'].split(' '),
            grant_type=grant_type,
            should_be_restricted=False,
            expires_in=expected_default_expires_in,
            should_be_asymmetric_key=asymmetric_jwt
        )

    @ddt.data('dot_app')
    def test_access_token_fields(self, client_attr):
        client = getattr(self, client_attr)
        response = self._post_request(self.user, client)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert 'access_token' in data
        assert 'expires_in' in data
        assert 'scope' in data
        assert 'token_type' in data

    def test_restricted_non_jwt_access_token_fields(self):
        response = self._post_request(self.user, self.restricted_dot_app)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert 'access_token' in data
        assert 'expires_in' in data
        assert 'scope' in data
        assert 'token_type' in data

        # Verify token expiration.
        assert (data['expires_in'] < 0) is True
        access_token = dot_models.AccessToken.objects.get(token=data['access_token'])
        assert models.RestrictedApplication.verify_access_token_as_expired(access_token) is True

    @ddt.data('dot_app')
    def test_jwt_access_token_from_parameter(self, client_attr):
        self._test_jwt_access_token(client_attr, token_type='jwt', grant_type='password')

    @ddt.data('dot_app')
    def test_jwt_access_token_from_header(self, client_attr):
        self._test_jwt_access_token(client_attr, headers={'HTTP_X_TOKEN_TYPE': 'jwt'}, grant_type='password')

    @ddt.data('dot_app')
    def test_jwt_access_token_from_parameter_not_header(self, client_attr):
        self._test_jwt_access_token(client_attr, token_type='jwt', grant_type='password',
                                    headers={'HTTP_X_TOKEN_TYPE': 'invalid'})

    @ddt.data(
        ('jwt', 'jwt'),
        (None, 'no_token_type_supplied'),
    )
    @ddt.unpack
    @patch('edx_django_utils.monitoring.set_custom_attribute')
    def test_access_token_attributes(self, token_type, expected_token_type, mock_set_custom_attribute):
        response = self._post_request(self.user, self.dot_app, token_type=token_type)
        assert response.status_code == 200
        expected_calls = [
            call('oauth_token_type', expected_token_type),
            call('oauth_grant_type', 'password'),
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)

    @patch('edx_django_utils.monitoring.set_custom_attribute')
    def test_access_token_attributes_for_bad_request(self, mock_set_custom_attribute):
        grant_type = dot_models.Application.GRANT_PASSWORD
        invalid_body = {
            'grant_type': grant_type.replace('-', '_'),
        }
        bad_response = self.client.post(self.url, invalid_body)
        assert bad_response.status_code == 401
        expected_calls = [
            call('oauth_token_type', 'no_token_type_supplied'),
            call('oauth_grant_type', 'password'),
        ]
        mock_set_custom_attribute.assert_has_calls(expected_calls, any_order=True)

    def test_restricted_jwt_access_token(self):
        """
        Verify that we get a restricted JWT that is not expired.
        """
        response = self._post_request(self.user, self.restricted_dot_app, token_type='jwt')
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))

        assert 'expires_in' in data
        assert data['expires_in'] > 0
        assert data['token_type'] == 'JWT'
        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            data['scope'].split(' '),
            should_be_expired=False,
            should_be_asymmetric_key=True,
            should_be_restricted=True,
            grant_type='password'
        )

    def test_restricted_access_token(self):
        """
        Verify that an access_token generated for a RestrictedApplication fails when
        submitted to an API endpoint
        """

        response = self._post_request(self.user, self.restricted_dot_app)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))

        assert 'expires_in' in data
        assert 'access_token' in data

        # the payload should indicate that the token is expired
        assert data['expires_in'] < 0

        # try submitting this expired access_token to an API,
        # and assert that it fails
        self._assert_access_token_invalidated(data['access_token'])

    def test_dot_access_token_provides_refresh_token(self):
        response = self._post_request(self.user, self.dot_app)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert 'refresh_token' in data

    @ddt.data(dot_models.Application.GRANT_CLIENT_CREDENTIALS, dot_models.Application.GRANT_PASSWORD)
    def test_jwt_access_token_scopes_and_filters(self, grant_type):
        """
        Verify the JWT contains the expected scopes and filters.
        """
        dot_app = self.dot_adapter.create_public_client(
            name='test dot application',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id=f'dot-app-client-id-{grant_type}',
            grant_type=grant_type,
        )
        dot_app_access = models.ApplicationAccess.objects.create(
            application=dot_app,
            scopes=['grades:read'],
            filters=['test:filter'],
        )
        requested_scopes = dot_app_access.scopes
        filters = self.dot_adapter.get_authorization_filters(dot_app)
        assert 'test:filter' in filters

        response = self._post_request(self.user, dot_app, token_type='jwt', scope=requested_scopes)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        scopes_in_response = data['scope'].split(' ')
        for requested_scope in requested_scopes:
            assert requested_scope in scopes_in_response

        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            scopes_in_response,
            filters=filters,
            grant_type=grant_type,
        )

    def test_asymmetric_jwt_access_token(self):
        """
        Verify the JWT is asymmetric when requested.
        """
        self._test_jwt_access_token('dot_app', token_type='jwt', grant_type='password', asymmetric_jwt=True)

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, False)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    def test_disable_jwt_waffle_switch_disabled_non_mobile(self, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE disabled
        Non-mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = False
        self._test_jwt_access_token('dot_app', token_type='jwt', grant_type='password')

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, True)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    def test_disable_jwt_waffle_switch_enabled_non_mobile(self, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE enabled
        Non-mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = False
        self._test_jwt_access_token('dot_app', token_type='jwt', grant_type='password')

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, True)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    def test_disable_jwt_waffle_switch_enabled_mobile(self, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE enabled
        Mobile request
        Test JWT token is not returned, and that Bearer token is returned instead
        """
        mock_is_request_from_mobile_app.return_value = True
        response = self._post_request(self.user, self.dot_app, token_type='jwt', asymmetric_jwt=True)
        data = json.loads(response.content.decode('utf-8'))
        access_token = data['access_token']
        assert response.status_code == 200
        assert data['token_type'] == "Bearer"
        assert dot_models.AccessToken.objects.filter(token=access_token).exists() is True

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, False)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    def test_disable_jwt_waffle_switch_disabled_mobile(self, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE disabled
        Mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = True
        self._test_jwt_access_token('dot_app', token_type='jwt', grant_type='password', asymmetric_jwt=True)


@ddt.ddt
@httpretty.activate
class TestAccessTokenExchangeView(ThirdPartyOAuthTestMixinGoogle, ThirdPartyOAuthTestMixin,
                                  _DispatchingViewTestCase, mixins.AccessTokenMixin):
    """
    Test class for AccessTokenExchangeView
    """

    def setUp(self):
        self.url = reverse('exchange_access_token', kwargs={'backend': 'google-oauth2'})
        self.view_class = views.AccessTokenExchangeView
        super().setUp()

    def _post_body(self, user, client, token_type=None, scope=None, asymmetric_jwt=None):
        body = {
            'client_id': client.client_id,
            'access_token': self.access_token,
        }
        if token_type:
            body['token_type'] = token_type

        if asymmetric_jwt:
            body['asymmetric_jwt'] = asymmetric_jwt

        return body

    def _test_jwt_access_token(self, client_attr, token_type=None, headers=None, grant_type=None, asymmetric_jwt=False):
        """
        Test response for JWT token.
        """
        client = getattr(self, client_attr)
        self.oauth_client = client
        profile_data = {'given_name': self.user.first_name, 'family_name': self.user.last_name}
        self._setup_provider_response(success=True, profile_data=profile_data)
        response = self._post_request(self.user, client, token_type=token_type,
                                      headers=headers or {}, asymmetric_jwt=asymmetric_jwt)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert 'expires_in' in data
        assert data['expires_in'] > 0
        assert data['token_type'] == 'JWT'

        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            data['scope'].split(' '),
            grant_type=grant_type,
            should_be_asymmetric_key=asymmetric_jwt
        )

    @ddt.data('dot_app')
    def test_access_token_exchange_calls_dispatched_view(self, client_attr):
        client = getattr(self, client_attr)
        self.oauth_client = client
        self._setup_provider_response(success=True)
        response = self._post_request(self.user, client)
        assert response.status_code == 200

    @ddt.data('dot_app')
    def test_jwt_access_token_exchange_calls_dispatched_view(self, client_attr):
        client = getattr(self, client_attr)
        self.oauth_client = client
        profile_data = {'given_name': self.user.first_name, 'family_name': self.user.last_name}
        self._setup_provider_response(success=True, profile_data=profile_data)
        response = self._post_request(self.user, client, token_type='jwt')
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            data['scope'].split(' '),
            grant_type='password'
        )

        assert 'expires_in' in data
        assert data['expires_in'] > 0
        assert data['token_type'] == 'JWT'

    @ddt.data('dot_app')
    def test_asymmetric_jwt_access_token_exchange_calls_dispatched_view(self, client_attr):
        client = getattr(self, client_attr)
        self.oauth_client = client
        profile_data = {'given_name': self.user.first_name, 'family_name': self.user.last_name}
        self._setup_provider_response(success=True, profile_data=profile_data)
        response = self._post_request(self.user, client, token_type='jwt', asymmetric_jwt=True)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        self.assert_valid_jwt_access_token(
            data['access_token'],
            self.user,
            data['scope'].split(' '),
            grant_type='password',
            should_be_asymmetric_key=True
        )

        assert 'expires_in' in data
        assert data['expires_in'] > 0
        assert data['token_type'] == 'JWT'

    @ddt.data('dot_app')
    def test_jwt_access_token_exchange_calls_dispatched_view_with_disabled_user(self, client_attr):
        self.user.set_unusable_password()
        self.user.save()
        client = getattr(self, client_attr)
        self.oauth_client = client
        self._setup_provider_response(success=True)
        response = self._post_request(self.user, client, token_type='jwt')
        assert response.status_code == 403
        data = json.loads(response.content.decode('utf-8'))
        assert data['error'] == 'account_disabled'

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, False)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    @ddt.data('dot_app')
    def test_disable_jwt_waffle_switch_disabled_non_mobile(self, client_attr, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE disabled
        Non-mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = False
        self._test_jwt_access_token(client_attr, token_type='jwt', grant_type='password')

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, True)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    @ddt.data('dot_app')
    def test_disable_jwt_waffle_switch_enabled_non_mobile(self, client_attr, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE enabled
        Non-mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = False
        self._test_jwt_access_token(client_attr, token_type='jwt', grant_type='password')

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, True)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    @ddt.data('dot_app')
    def test_disable_jwt_waffle_switch_enabled_mobile(self, client_attr, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE enabled
        Mobile request
        Test JWT token is not returned, and that Bearer token is returned instead
        """
        mock_is_request_from_mobile_app.return_value = True
        client = getattr(self, client_attr)
        self.oauth_client = client
        self._setup_provider_response(success=True)
        response = self._post_request(self.user, client, token_type='jwt', asymmetric_jwt=True)
        data = json.loads(response.content.decode('utf-8'))
        access_token = data['access_token']
        assert response.status_code == 200
        assert data['token_type'] == "Bearer"
        assert dot_models.AccessToken.objects.filter(token=access_token).exists() is True

    @override_waffle_switch(DISABLE_JWT_FOR_MOBILE, False)
    @patch('openedx.core.djangoapps.oauth_dispatch.views.is_request_from_mobile_app')
    @ddt.data('dot_app')
    def test_disable_jwt_waffle_switch_disabled_mobile(self, client_attr, mock_is_request_from_mobile_app):
        """
        DISABLE_JWT_FOR_MOBILE disabled
        Mobile request
        Test JWT token is returned
        """
        mock_is_request_from_mobile_app.return_value = True
        self._test_jwt_access_token(client_attr, token_type='jwt', grant_type='password', asymmetric_jwt=True)


# pylint: disable=abstract-method
@ddt.ddt
class TestAuthorizationView(_DispatchingViewTestCase):
    """
    Test class for AuthorizationView
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.dot_app = self.dot_adapter.create_confidential_client(
            name='test dot application',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='confidential-dot-app-client-id',
        )
        models.ApplicationAccess.objects.create(
            application=self.dot_app,
            scopes=['grades:read'],
            filters=[
                'content_org:test content org',
                'other_filter:filter_val',
            ]
        )

    @ddt.data(
        ('dot', 'allow'),
        ('dot', 'authorize')
    )
    @ddt.unpack
    def test_post_authorization_view(self, client_type, allow_field):
        oauth_application = getattr(self, f'{client_type}_app')
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.post(
            '/oauth2/authorize/',
            {
                'client_id': oauth_application.client_id,
                'response_type': 'code',
                'state': 'random_state_string',
                'redirect_uri': DUMMY_REDIRECT_URL,
                'scope': 'profile email',
                allow_field: True,
            },
            follow=True,
        )

        check_response = getattr(self, f'_check_{client_type}_response')
        check_response(response)

    def test_check_dot_authorization_page_get(self):
        """
        Make sure we get the overridden Authorization page - not
        the default django-oauth-toolkit when we perform a page load
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(
            '/oauth2/authorize/',
            {
                'client_id': self.dot_app.client_id,
                'response_type': 'code',
                'state': 'random_state_string',
                'redirect_uri': DUMMY_REDIRECT_URL,
                'scope': 'profile grades:read'
            },
            follow=True,
        )

        # are the requested scopes on the page? We only requested 'profile', lets make
        # sure the page only lists that one
        self.assertContains(response, settings.OAUTH2_PROVIDER['SCOPES']['profile'])
        self.assertContains(response, settings.OAUTH2_PROVIDER['SCOPES']['grades:read'])
        self.assertNotContains(response, settings.OAUTH2_PROVIDER['SCOPES']['read'])
        self.assertNotContains(response, settings.OAUTH2_PROVIDER['SCOPES']['write'])
        self.assertNotContains(response, settings.OAUTH2_PROVIDER['SCOPES']['email'])

        # is the application name specified?
        self.assertContains(
            response,
            f"Authorize {self.dot_app.name}"
        )

        # are the cancel and allow buttons on the page?
        self.assertContains(
            response,
            '<button type="submit" class="btn btn-authorization-cancel" name="cancel"/>Cancel</button>'
        )
        self.assertContains(
            response,
            '<button type="submit" class="btn btn-authorization-allow" name="allow" value="Authorize"/>Allow</button>'
        )

        # Are the content provider organizations listed on the page?
        self.assertContains(
            response,
            '<li>{org}</li>'.format(org='test content org')
        )

        # Make sure other filters don't show up as orgs.
        self.assertNotContains(
            response,
            '<li>{org}</li>'.format(org='filter_val')
        )

    def _check_dot_response(self, response):
        """
        Check that django-oauth-toolkit gives an appropriate authorization response.
        """
        # django-oauth-toolkit tries to redirect to the user's redirect URL
        assert response.status_code == 404
        # We used a non-existent redirect url.
        expected_redirect_prefix = f'{DUMMY_REDIRECT_URL}?'
        self._assert_startswith(self._redirect_destination(response), expected_redirect_prefix)

    def _assert_startswith(self, string, prefix):
        """
        Assert that the string starts with the specified prefix.
        """
        assert string.startswith(prefix), f'{string} does not start with {prefix}'

    @staticmethod
    def _redirect_destination(response):
        """
        Return the final destination of the redirect chain in the response object
        """
        return response.redirect_chain[-1][0]


@skip_unless_lms
class TestViewDispatch(TestCase):
    """
    Test that the DispatchingView dispatches the right way.
    """

    def setUp(self):
        super().setUp()
        self.dot_adapter = adapters.DOTAdapter()
        self.user = UserFactory()
        self.view = views._DispatchingView()  # pylint: disable=protected-access
        self.dot_adapter.create_public_client(
            name='',
            user=self.user,
            client_id='dot-id',
            redirect_uri=DUMMY_REDIRECT_URL
        )

    def assert_is_view(self, view_candidate):
        """
        Assert that a given object is a view.  That is, it is callable, and
        takes a request argument.  Note: while technically, the request argument
        could take any name, this assertion requires the argument to be named
        `request`.  This is good practice.  You should do it anyway.
        """
        _msg_base = '{view} is not a view: {reason}'
        msg_not_callable = _msg_base.format(view=view_candidate, reason='it is not callable')
        msg_no_request = _msg_base.format(view=view_candidate, reason='it has no request argument')
        assert hasattr(view_candidate, '__call__'), msg_not_callable  # noqa: B004
        args = view_candidate.__code__.co_varnames
        assert args, msg_no_request
        assert args[0] == 'request'

    def _post_request(self, client_id):
        """
        Return a request with the specified client_id in the body
        """
        return RequestFactory().post('/', {'client_id': client_id})

    def _get_request(self, client_id):
        """
        Return a request with the specified client_id in the get parameters
        """
        return RequestFactory().get(f'/?client_id={client_id}')

    def test_dispatching_post_to_dot(self):
        request = self._post_request('dot-id')
        assert self.view.select_backend(request) == self.dot_adapter.backend

    def test_dispatching_get_to_dot(self):
        request = self._get_request('dot-id')
        assert self.view.select_backend(request) == self.dot_adapter.backend

    def test_dispatching_with_no_client(self):
        request = self._post_request('')
        assert self.view.select_backend(request) == self.dot_adapter.backend

    def test_dispatching_with_invalid_client(self):
        request = self._post_request('abcesdfljh')
        assert self.view.select_backend(request) == self.dot_adapter.backend

    def test_get_view_for_dot(self):
        view_object = views.AccessTokenView()
        self.assert_is_view(view_object.get_view_for_backend(self.dot_adapter.backend))

    def test_get_view_for_no_backend(self):
        view_object = views.AccessTokenView()
        self.assertRaises(KeyError, view_object.get_view_for_backend, None)  # noqa: PT027


class TestRevokeTokenView(AccessTokenLoginMixin, _DispatchingViewTestCase):  # pylint: disable=abstract-method
    """
    Test class for RevokeTokenView
    """

    def setUp(self):
        self.revoke_token_url = reverse('revoke_token')
        self.access_token_url = reverse('access_token')

        super().setUp()
        response = self.client.post(self.access_token_url, self.access_token_post_body_with_password())
        access_token_data = json.loads(response.content.decode('utf-8'))
        self.access_token = access_token_data['access_token']
        self.refresh_token = access_token_data['refresh_token']

    def access_token_post_body_with_password(self):
        """
        Returns a dictionary to be used as the body of the access_token
        POST request with 'password' grant
        """
        return {
            'client_id': self.dot_app.client_id,
            'grant_type': 'password',
            'username': self.user.username,
            'password': self.TEST_PASSWORD,
        }

    def access_token_post_body_with_refresh_token(self, refresh_token):
        """
        Returns a dictionary to be used as the body of the access_token
        POST request with 'refresh_token' grant
        """
        return {
            'client_id': self.dot_app.client_id,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }

    def revoke_token_post_body(self, token):
        """
        Returns a dictionary to be used as the body of the revoke_token POST request
        """
        return {
            'client_id': self.dot_app.client_id,
            'token': token,
        }

    def assert_refresh_token_status_code(self, refresh_token, expected_status_code):
        """
        Asserts the status code using oauth assigned refresh_token
        """
        response = self.client.post(
            self.access_token_url,
            self.access_token_post_body_with_refresh_token(refresh_token)
        )
        assert response.status_code == expected_status_code

    def revoke_token(self, token):
        """
        Revokes the passed access or refresh token
        """
        response = self.client.post(self.revoke_token_url, self.revoke_token_post_body(token))
        assert response.status_code == 200

    def test_revoke_refresh_token_dot(self):
        """
        Tests invalidation/revoke of refresh token for django-oauth-toolkit
        """
        self.assert_refresh_token_status_code(self.refresh_token, expected_status_code=200)

        self.revoke_token(self.refresh_token)

        self.assert_refresh_token_status_code(self.refresh_token, expected_status_code=400)

    def test_revoke_access_token_dot(self):
        """
        Tests invalidation/revoke of user access token for django-oauth-toolkit
        """
        self._assert_access_token_is_valid(self.access_token)

        self.revoke_token(self.access_token)

        self._assert_access_token_invalidated(self.access_token)
