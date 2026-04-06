"""
Tests for logout
"""

import urllib
from unittest import mock

import ddt
import nh3
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import assert_dict_contains_subset
from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
@ddt.ddt
class LogoutTests(TestCase):
    """ Tests for the logout functionality. """

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password='test')

    def _create_oauth_client(self):
        """ Creates a trusted OAuth client. """
        return ApplicationFactory(redirect_uris='https://www.example.com/logout/', skip_authorization=True)

    def _assert_session_logged_out(self, oauth_client, **logout_headers):
        """ Authenticates a user via OAuth 2.0, logs out, and verifies the session is logged out. """
        self._authenticate_with_oauth(oauth_client)

        # Logging out should remove the session variables, and send a list of logout URLs to the template.
        # The template will handle loading those URLs and redirecting the user. That functionality is not tested here.
        response = self.client.get(reverse('logout'), **logout_headers)
        assert response.status_code == 200

        return response

    def _authenticate_with_oauth(self, oauth_client):
        """ Perform an OAuth authentication using the current web client.
        """
        data = {
            'client_id': oauth_client.client_id,
            'client_secret': oauth_client.client_secret,
            'response_type': 'code'
        }
        # Authenticate with OAuth to set the appropriate session values
        response = self.client.post(reverse('oauth2_provider:authorize'), data, follow=True)
        assert response.status_code == 200

    @ddt.data(
        ('%2Fcourses', 'testserver'),
        ('https%3A%2F%2Fedx.org%2Fcourses', 'edx.org'),
        ('https%3A%2F%2Ftest.edx.org%2Fcourses', 'edx.org'),
        ('/courses/course-v1:ARTS+D1+2018_T/course/', 'edx.org'),
        ('%2Fcourses%2Fcourse-v1%3AARTS%2BD1%2B2018_T%2Fcourse%2F', 'edx.org'),
        ('/courses/course-v1:ARTS+D1+2018_T/course/?q=computer+science', 'edx.org'),
        ('%2Fcourses%2Fcourse-v1%3AARTS%2BD1%2B2018_T%2Fcourse%2F%3Fq%3Dcomputer+science', 'edx.org'),
        ('/enterprise/c5dad9a7-741c-4841-868f-850aca3ff848/course/Microsoft+DAT206x/enroll/', 'edx.org'),
        ('%2Fenterprise%2Fc5dad9a7-741c-4841-868f-850aca3ff848%2Fcourse%2FMicrosoft%2BDAT206x%2Fenroll%2F', 'edx.org'),
    )
    @ddt.unpack
    @override_settings(LOGIN_REDIRECT_WHITELIST=['test.edx.org'])
    def test_logout_redirect_success(self, redirect_url, host):
        url = '{logout_path}?redirect_url={redirect_url}'.format(
            logout_path=reverse('logout'),
            redirect_url=redirect_url
        )
        response = self.client.get(url, HTTP_HOST=host)
        expected = {
            'target': urllib.parse.unquote(redirect_url),
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    def test_no_redirect_supplied(self):
        response = self.client.get(reverse('logout'), HTTP_HOST='testserver')
        expected = {
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    @ddt.data(
        ('https://www.amazon.org', 'edx.org'),
        ('/%09/google.com/', 'edx.org'),
        ('java%0D%0Ascript%0D%0A%3aalert(document.domain)', 'edx.org'),
    )
    @ddt.unpack
    def test_logout_redirect_failure(self, redirect_url, host):
        url = '{logout_path}?redirect_url={redirect_url}'.format(
            logout_path=reverse('logout'),
            redirect_url=redirect_url
        )
        response = self.client.get(url, HTTP_HOST=host)
        expected = {
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    def test_client_logout(self):
        """ Verify the context includes a list of the logout URIs of the authenticated OpenID Connect clients.

        The list should only include URIs of the clients for which the user has been authenticated.
        """
        client = self._create_oauth_client()
        response = self._assert_session_logged_out(client)
        expected = {
            'logout_uris': [],
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    @mock.patch(
        'django.conf.settings.IDA_LOGOUT_URI_LIST',
        ['http://fake.ida1/logout', 'http://fake.ida2/accounts/logout', ]
    )
    def test_client_logout_with_dot_idas(self):
        """
        Verify the context includes a list of the logout URIs of the authenticated OpenID Connect clients AND OAuth2/DOT
        clients.

        The list should only include URIs of the OIDC clients for which the user has been authenticated, and all the
        configured DOT clients regardless of login status..
        """
        client = self._create_oauth_client()
        response = self._assert_session_logged_out(client)
        # Add the logout endpoints for the IDAs where auth was established via DOT/OAuth2.
        expected_logout_uris = [
            'http://fake.ida1/logout?no_redirect=1',
            'http://fake.ida2/accounts/logout?no_redirect=1',
        ]
        expected = {
            'logout_uris': expected_logout_uris,
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    @mock.patch(
        'django.conf.settings.IDA_LOGOUT_URI_LIST',
        ['http://fake.ida1/logout', 'http://fake.ida2/accounts/logout', ]
    )
    def test_client_logout_with_dot_idas_and_no_oidc_idas(self):
        """
        Verify the context includes a list of the logout URIs of the OAuth2/DOT clients, even if there are no currently
        authenticated OpenID Connect clients.

        The list should include URIs of all the configured DOT clients.
        """
        response = self.client.get(reverse('logout'))
        # Add the logout endpoints for the IDAs where auth was established via DOT/OAuth2.
        expected_logout_uris = [
            'http://fake.ida1/logout?no_redirect=1',
            'http://fake.ida2/accounts/logout?no_redirect=1',
        ]
        expected = {
            'logout_uris': expected_logout_uris,
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    def test_filter_referring_service(self):
        """ Verify that, if the user is directed to the logout page from a service, that service's logout URL
        is not included in the context sent to the template.
        """
        client = self._create_oauth_client()
        response = self._assert_session_logged_out(client, HTTP_REFERER=client.redirect_uris)
        expected = {
            'logout_uris': [],
            'target': '/',
            'show_tpa_logout_link': False,
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    def test_learner_portal_logout_having_idp_logout_url(self):
        """
        Test when learner logout from learner portal having active SSO session
        logout page should have link to logout url IdP.
        """
        learner_portal_logout_url = f'{settings.LEARNER_PORTAL_URL_ROOT}/logout'
        idp_logout_url = 'http://mock-idp.com/logout'
        client = self._create_oauth_client()

        with mock.patch(
            'openedx.core.djangoapps.user_authn.views.logout.tpa_pipeline.get_idp_logout_url_from_running_pipeline'
        ) as mock_idp_logout_url:
            mock_idp_logout_url.return_value = idp_logout_url
            response = self._assert_session_logged_out(client, HTTP_REFERER=learner_portal_logout_url)
            expected = {
                'tpa_logout_url': idp_logout_url,
                'show_tpa_logout_link': True,
            }
            assert_dict_contains_subset(self, expected, response.context_data)

    @mock.patch('django.conf.settings.TPA_AUTOMATIC_LOGOUT_ENABLED', True)
    def test_automatic_tpa_logout_url_redirect(self):
        """
        Test user automatically redirected to tpa logout_url
        when TPA_AUTOMATIC_LOGOUT is set to True.
        """
        idp_logout_url = 'http://mock-idp.com/logout'
        client = self._create_oauth_client()

        with mock.patch(
            'openedx.core.djangoapps.user_authn.views.logout.tpa_pipeline.get_idp_logout_url_from_running_pipeline'
        ) as mock_idp_logout_url:
            mock_idp_logout_url.return_value = idp_logout_url
            self._authenticate_with_oauth(client)
            response = self.client.get(reverse('logout'))
            expected = {
                'target': idp_logout_url,
            }
            assert_dict_contains_subset(self, expected, response.context_data)

    @mock.patch('django.conf.settings.TPA_AUTOMATIC_LOGOUT_ENABLED', True)
    def test_no_automatic_tpa_logout_without_logout_url(self):
        """
        Test user is NOT automatically redirected when tpa logout_url is not set
        even if TPA_AUTOMATIC_LOGOUT is set to True.
        """
        client = self._create_oauth_client()
        self._assert_session_logged_out(client)

    @ddt.data(
        ('%22%3E%3Cscript%3Ealert(%27xss%27)%3C/script%3E', 'edx.org'),
    )
    @ddt.unpack
    def test_logout_redirect_failure_with_xss_vulnerability(self, redirect_url, host):
        """
        Verify that it will block the XSS attack on edX’s LMS logout page
        """
        url = '{logout_path}?redirect_url={redirect_url}'.format(
            logout_path=reverse('logout'),
            redirect_url=redirect_url
        )
        response = self.client.get(url, HTTP_HOST=host)
        expected = {
            'target': nh3.clean(urllib.parse.unquote(redirect_url)),
        }
        assert_dict_contains_subset(self, expected, response.context_data)
