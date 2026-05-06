"""Tests for account activation"""

import urllib.parse
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.http import urlencode

from common.djangoapps.student.models import Registration
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.enterprise_support.tests.factories import EnterpriseCustomerUserFactory

FEATURES_WITH_AUTHN_MFE_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_AUTHN_MFE_ENABLED['ENABLE_AUTHN_MICROFRONTEND'] = True


@skip_unless_lms
class TestActivateAccount(TestCase):
    """Tests for account creation"""

    def setUp(self):
        super().setUp()
        self.username = "jack"
        self.email = "jack@fake.edx.org"
        self.password = "test-password"
        self.user = UserFactory.create(
            username=self.username, email=self.email, password=self.password, is_active=False,
        )

        # Set Up Registration
        self.registration = Registration()
        self.registration.register(self.user)
        self.registration.save()

        self.platform_name = configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
        self.activation_email_support_link = configuration_helpers.get_value(
            'ACTIVATION_EMAIL_SUPPORT_LINK', settings.ACTIVATION_EMAIL_SUPPORT_LINK
        ) or settings.SUPPORT_SITE_LINK

    def login(self):
        """
        Login with test user.

        Since, only active users can login, so we must activate the user before login.
        This method does the following tasks in order,
            1. Stores user's active/in-active status in a variable.
            2. Makes sure user account is active.
            3. Authenticated user with the client.
            4. Reverts user's original active/in-active status.
        """
        is_active = self.user.is_active

        # Make sure user is active before login
        self.user.is_active = True
        self.user.save()
        self.client.login(username=self.username, password=self.password)

        # Revert user activation status
        self.user.is_active = is_active
        self.user.save()

    def assert_no_tracking(self, mock_segment_identify):
        """ Assert that activate sets the flag but does not call segment. """
        # Ensure that the user starts inactive
        assert not self.user.is_active

        # Until you explicitly activate it
        self.registration.activate()
        assert self.user.is_active
        assert not mock_segment_identify.called

    @patch('common.djangoapps.student.models.user.USER_ACCOUNT_ACTIVATED')
    def test_activation_signal(self, mock_signal):
        """
        Verify that USER_ACCOUNT_ACTIVATED is emitted upon account email activation.
        """
        assert not self.user.is_active, 'Ensure that the user starts inactive'
        assert not mock_signal.send_robust.call_count, 'Ensure no signal is fired before activation'
        self.registration.activate()  # Until you explicitly activate it
        assert self.user.is_active, 'Sanity check for .activate()'
        mock_signal.send_robust.assert_called_once_with(Registration, user=self.user)  # Ensure the signal is emitted

    def test_activation_timestamp(self):
        """ Assert that activate sets the flag but does not call segment. """
        # Ensure that the user starts inactive
        assert not self.user.is_active
        # Until you explicitly activate it
        timestamp_before_activation = datetime.utcnow()
        self.registration.activate()
        assert self.user.is_active
        assert self.registration.activation_timestamp > timestamp_before_activation

    def test_account_activation_message(self):
        """
        Verify that account correct activation message is displayed.

        If logged in user has not activated their account, make sure that an
        account activation message is displayed on dashboard sidebar.
        """
        # Log in with test user.
        self.login()
        expected_message = (
            "Check your {email_start}{email}{email_end} inbox for an account activation link from "
            "{platform_name}. If you need help, contact {link_start}{platform_name} Support{link_end}."
        ).format(
            platform_name=self.platform_name,
            email_start="<strong>",
            email_end="</strong>",
            email=self.user.email,
            link_start="<a target='_blank' href='{activation_email_support_link}'>".format(  # noqa: UP032
                activation_email_support_link=self.activation_email_support_link,
            ),
            link_end="</a>",
        )

        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, expected_message)

        # Now make sure account activation message goes away when user activated the account
        self.user.is_active = True
        self.user.save()
        self.login()
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, expected_message)

    def _assert_user_active_state(self, expected_active_state):
        user = User.objects.get(username=self.user.username)
        assert user.is_active == expected_active_state

    def test_account_activation_notification_on_logistration(self):
        """
        Verify that logistration page displays success/error/info messages
        about account activation.
        """
        login_page_url = "{login_url}?next={redirect_url}".format(
            login_url=reverse('signin_user'),
            redirect_url=reverse('dashboard'),
        )
        self._assert_user_active_state(expected_active_state=False)

        # Access activation link, message should say that account has been activated.
        response = self.client.get(reverse('activate', args=[self.registration.activation_key]), follow=True)
        self.assertRedirects(response, login_page_url)
        self.assertContains(response, 'Success! You have activated your account.')
        self._assert_user_active_state(expected_active_state=True)

        # Access activation link again, message should say that account is already active.
        response = self.client.get(reverse('activate', args=[self.registration.activation_key]), follow=True)
        self.assertRedirects(response, login_page_url)
        self.assertContains(response, 'This account has already been activated.')
        self._assert_user_active_state(expected_active_state=True)

        # Open account activation page with an invalid activation link,
        # there should be an error message displayed.
        response = self.client.get(reverse('activate', args=[uuid4().hex]), follow=True)
        self.assertRedirects(response, login_page_url)
        self.assertContains(response, 'Your account could not be activated')

    @override_settings(MARKETING_EMAILS_OPT_IN=True)
    def test_email_confirmation_notification_on_logistration(self):
        """
        Verify that logistration page displays success/error/info messages
        about email confirmation instead of activation when MARKETING_EMAILS_OPT_IN
        is set to True.
        """
        response = self.client.get(reverse('activate', args=[self.registration.activation_key]), follow=True)
        self.assertContains(response, 'Success! You have confirmed your email.')

        response = self.client.get(reverse('activate', args=[self.registration.activation_key]), follow=True)
        self.assertContains(response, 'This email has already been confirmed.')

        response = self.client.get(reverse('activate', args=[uuid4().hex]), follow=True)
        self.assertContains(response, 'Your email could not be confirmed')

    @override_settings(LOGIN_REDIRECT_WHITELIST=['localhost:1991'])
    @override_settings(FEATURES={**FEATURES_WITH_AUTHN_MFE_ENABLED, 'ENABLE_ENTERPRISE_INTEGRATION': True})
    def test_authenticated_account_activation_with_valid_next_url(self):
        """
        Verify that an activation link with a valid next URL will redirect
        the activated enterprise user to that next URL, even if the AuthN
        MFE is active and redirects to it are enabled.
        """
        self._assert_user_active_state(expected_active_state=False)
        EnterpriseCustomerUserFactory(user_id=self.user.id)

        # Make sure the user is authenticated before activation.
        self.login()

        redirect_url = 'http://localhost:1991/pied-piper/learn'
        base_activation_url = reverse('activate', args=[self.registration.activation_key])
        activation_url = '{base}?{params}'.format(
            base=base_activation_url,
            params=urlencode({'next': redirect_url}),
        )

        # HTTP_ACCEPT is needed so the safe redirect checks pass.
        response = self.client.get(activation_url, follow=True, HTTP_ACCEPT='*/*')

        # There's not actually a server running at localhost:1991 for testing,
        # so we should expect to land on `redirect_url` but with a status code of 404.
        self.assertRedirects(response, redirect_url, target_status_code=404)
        self._assert_user_active_state(expected_active_state=True)

    @override_settings(LOGIN_REDIRECT_WHITELIST=['localhost:9876'])
    def test_account_activation_invalid_next_url_redirects_dashboard(self):
        """
        Verify that an activation link with an invalid next URL (i.e. it's for a domain
        not in the allowed list of redirect destinations) will redirect
        the activated, but unauthenticated, user to a login URL
        that points to 'dashboard' as the next URL.
        """
        self._assert_user_active_state(expected_active_state=False)

        redirect_url = 'http://localhost:1991/pied-piper/learn'
        base_activation_url = reverse('activate', args=[self.registration.activation_key])
        activation_url = '{base}?{params}'.format(
            base=base_activation_url,
            params=urlencode({'next': redirect_url}),
        )

        response = self.client.get(activation_url, follow=True, HTTP_ACCEPT='*/*')

        expected_destination = "{login_url}?next={redirect_url}".format(
            login_url=reverse('signin_user'),
            redirect_url=reverse('dashboard'),
        )
        self.assertRedirects(response, expected_destination)
        self._assert_user_active_state(expected_active_state=True)

    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
    def test_unauthenticated_user_redirects_to_mfe(self):
        """
        Verify that if Authn MFE is enabled then authenticated user redirects to
        login page with correct query param.
        """
        login_page_url = "{authn_mfe}/login?account_activation_status=".format(  # noqa: UP032
            authn_mfe=settings.AUTHN_MICROFRONTEND_URL
        )

        self._assert_user_active_state(expected_active_state=False)

        # Access activation link, the user is redirected to login page with success query param
        response = self.client.get(reverse('activate', args=[self.registration.activation_key]))
        assert response.url == (login_page_url + 'success')

        # Access activation link again, the user is redirected to login page with info query param
        response = self.client.get(reverse('activate', args=[self.registration.activation_key]))
        assert response.url == (login_page_url + 'info')

        # Open account activation page with an invalid activation link, the query param should contain error
        response = self.client.get(reverse('activate', args=[uuid4().hex]))
        assert response.url == (login_page_url + 'error')

    @override_settings(LOGIN_REDIRECT_WHITELIST=['localhost:1991'])
    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
    def test_unauthenticated_user_redirects_to_mfe_with_valid_next_url(self):
        """
        Verify that if Authn MFE is enabled then authenticated user redirects to
        login page with correct account_activation_status param.  Additionally, if a valid
        `next` redirect URL is provided to the activation URL, it should be included
        as a parameter in the login page the requesting user is redirected to.
        """
        login_page_url = "{authn_mfe}/login?account_activation_status=".format(  # noqa: UP032
            authn_mfe=settings.AUTHN_MICROFRONTEND_URL
        )

        self._assert_user_active_state(expected_active_state=False)

        redirect_url = 'http://localhost:1991/pied-piper/learn'
        encoded_next_param = urllib.parse.urlencode({'next': redirect_url})
        base_activation_url = reverse('activate', args=[self.registration.activation_key])
        activation_url = '{base}?{params}'.format(
            base=base_activation_url,
            params=urlencode({'next': redirect_url}),
        )

        # HTTP_ACCEPT is needed so the safe redirect checks pass.
        response = self.client.get(activation_url, HTTP_ACCEPT='*/*')
        assert response.url == (login_page_url + 'success&' + encoded_next_param)

        # Access activation link again, the user is redirected to login page with info query param
        response = self.client.get(activation_url, HTTP_ACCEPT='*/*')
        assert response.url == (login_page_url + 'info&' + encoded_next_param)

    def test_authenticated_user_cannot_activate_another_account(self):
        """
        Verify that if a user is authenticated and tries to activate another account,
        error message is shown.
        """
        # create a new user and registration link
        second_user = UserFactory.create(
            username='jack-2', email='jack-2@fake.edx.org', password='test-password', is_active=False,
        )

        registration = Registration()
        registration.register(second_user)
        registration.save()

        # Login first user
        self.login()
        # Try activating second user's account
        response = self.client.get(reverse('activate', args=[registration.activation_key]), follow=True)
        self.assertContains(response, 'Your account could not be activated')

        # verify that both users have their is_active state set to False
        self._assert_user_active_state(expected_active_state=False)
        second_user.refresh_from_db()
        assert second_user.is_active is False
