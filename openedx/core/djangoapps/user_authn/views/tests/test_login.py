"""
Tests for student activation and login
"""


import datetime
import hashlib
import json
import unicodedata
import urllib.parse
from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse
from django.test.client import Client
from django.test.utils import override_settings
from django.urls import NoReverseMatch, reverse
from edx_toggles.toggles.testutils import override_waffle_switch
from freezegun import freeze_time
from openedx_events.testing import OpenEdxEventsTestMixin  # pylint: disable=wrong-import-order

from common.djangoapps.student.models import LoginFailures
from common.djangoapps.student.tests.factories import RegistrationFactory, UserFactory, UserProfileFactory
from common.djangoapps.util.password_policy_validators import DEFAULT_MAX_PASSWORD_LENGTH
from common.test.utils import assert_dict_contains_subset
from openedx.core.djangoapps.password_policy.compliance import (
    NonCompliantPasswordException,
    NonCompliantPasswordWarning,
)
from openedx.core.djangoapps.password_policy.hibp import PwnedPasswordsAPI
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangoapps.user_api.accounts import EMAIL_MAX_LENGTH, EMAIL_MIN_LENGTH
from openedx.core.djangoapps.user_authn.config.waffle import ENABLE_PWNED_PASSWORD_API
from openedx.core.djangoapps.user_authn.cookies import jwt_cookies
from openedx.core.djangoapps.user_authn.tests.utils import setup_login_oauth_client
from openedx.core.djangoapps.user_authn.views.login import (
    ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY,
    AllowedAuthUser,
    _check_user_auth_flow,
)
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.core.lib.api.test_utils import ApiTestCase
from openedx.features.enterprise_support.tests.factories import EnterpriseCustomerUserFactory


@ddt.ddt
# HIBP settings are only defined in lms envs but needed for common tests.
@override_settings(
    ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY=False,
    ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY=False,
)
class LoginTest(OpenEdxEventsTestMixin, SiteMixin, CacheIsolationTestCase):
    """
    Test login_user() view
    """

    ENABLED_OPENEDX_EVENTS = []

    ENABLED_CACHES = ['default']
    LOGIN_FAILED_WARNING = 'Email or password is incorrect'
    ACTIVATE_ACCOUNT_WARNING = 'In order to sign in, you need to activate your account'
    username = 'test'
    user_email = 'test@edx.org'
    password = 'test_password'

    def setUp(self):
        """Setup a test user along with its registration and profile"""
        super().setUp()
        self.user = self._create_user(self.username, self.user_email)

        RegistrationFactory(user=self.user)
        UserProfileFactory(user=self.user)

        self.client = Client()
        cache.clear()

        self.url = reverse('login_api')

    def _create_user(self, username, user_email):
        user = UserFactory.create(username=username, email=user_email)
        user.set_password(self.password)
        user.save()
        return user

    def test_login_success(self):
        response, mock_audit_log = self._login_response(
            self.user_email, self.password, patched_audit_log='common.djangoapps.student.models.user.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', ['Login success', self.user_email])

    FEATURES_WITH_AUTHN_MFE_ENABLED = settings.FEATURES.copy()
    FEATURES_WITH_AUTHN_MFE_ENABLED['ENABLE_AUTHN_MICROFRONTEND'] = True

    @override_settings(MARKETING_EMAILS_OPT_IN=True)
    def test_login_success_with_opt_in_flag_enabled(self):
        self.user.is_active = False
        self.user.save()
        response, mock_audit_log = self._login_response(
            self.user_email, self.password, patched_audit_log='common.djangoapps.student.models.user.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', ['Login success', self.user_email])

    @override_settings(MARKETING_EMAILS_OPT_IN=False)
    def test_login_failed_with_opt_in_flag_disabled(self):
        self.user.is_active = False
        self.user.save()
        response, mock_audit_log = self._login_response(self.user_email, self.password)
        self._assert_audit_log(
            mock_audit_log, 'warning', ['Login failed - Account not active for user.id: 1, resending activation']
        )

    @patch.dict(settings.FEATURES, {
        "ENABLE_THIRD_PARTY_AUTH": True
    })
    @patch(
        'openedx.core.djangoapps.user_authn.views.login.is_require_third_party_auth_enabled',
        Mock(return_value=True)
    )
    @skip_unless_lms
    def test_public_login_failure_with_only_third_part_auth_enabled(self):
        response, _ = self._login_response(
            self.user_email,
            self.password,
        )
        assert response.status_code == 403
        assert response.content == b'Third party authentication is required to login.' \
                                   b' Username and password were received instead.'

    @ddt.data(
        # Default redirect is dashboard.
        {
            'next_url': None,
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/dashboard',
        },
        # Added root url in next .
        {
            'next_url': '/harmless-relative-page',
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/harmless-relative-page',
        },
        # An absolute URL to a non-whitelisted domain is not an acceptable redirect.
        {
            'next_url': 'https://evil.sketchysite',
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/dashboard',
        },
        # An absolute URL to a whitelisted domain is acceptable.
        {
            'next_url': 'https://openedx.service/coolpage',
            'course_id': None,
            'expected_redirect': 'https://openedx.service/coolpage',
        },
        # If course_id is provided, redirect to finish_auth with dashboard as next.
        {
            'next_url': None,
            'course_id': 'coursekey',
            'expected_redirect': (
                '{root_url}/account/finish_auth?course_id=coursekey&next=%2Fdashboard'.  # noqa: UP032
                format(root_url=settings.LMS_ROOT_URL)
            ),
        },
        # If valid course_id AND next_url are provided, redirect to finish_auth with
        # provided next URL.
        {
            'next_url': 'freshpage',
            'course_id': 'coursekey',
            'expected_redirect': (
                settings.LMS_ROOT_URL + '/account/finish_auth?course_id=coursekey&next=freshpage'
            )
        },
        # If course_id is provided with invalid next_url, redirect to finish_auth with
        # course_id and dashboard as next URL.
        {
            'next_url': 'http://scam.scam',
            'course_id': 'coursekey',
            'expected_redirect': (
                '{root_url}/account/finish_auth?course_id=coursekey&next=%2Fdashboard'.  # noqa: UP032
                format(root_url=settings.LMS_ROOT_URL)
            ),
        },
    )
    @ddt.unpack
    @override_settings(LOGIN_REDIRECT_WHITELIST=['openedx.service'])
    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
    @skip_unless_lms
    def test_login_success_with_redirect(self, next_url, course_id, expected_redirect):
        post_params = {}

        if next_url:
            post_params['next'] = next_url
        if course_id:
            post_params['course_id'] = course_id

        response, _ = self._login_response(
            self.user_email,
            self.password,
            extra_post_params=post_params,
            HTTP_ACCEPT='*/*',
        )
        self._assert_response(response, success=True)
        self._assert_redirect_url(response, expected_redirect)

    @ddt.data(('/dashboard', False), ('/enterprise/select/active/?success_url=/dashboard', True))
    @ddt.unpack
    @patch.dict(settings.FEATURES, {'ENABLE_AUTHN_MICROFRONTEND': True, 'ENABLE_ENTERPRISE_INTEGRATION': True})
    @override_settings(LOGIN_REDIRECT_WHITELIST=['openedx.service'])
    @patch('openedx.features.enterprise_support.api.EnterpriseApiClient')
    @patch('openedx.core.djangoapps.user_authn.views.login.reverse')
    @skip_unless_lms
    def test_login_success_for_multiple_enterprises(
        self, expected_redirect, user_has_multiple_enterprises, reverse_mock, mock_api_client_class
    ):
        """
        Test that if multiple enterprise feature is enabled, user is redirected
        to correct page
        """
        api_response = {'results': []}
        enterprise = EnterpriseCustomerUserFactory(user_id=self.user.id).enterprise_customer
        api_response['results'].append(
            {
                "enterprise_customer": {
                    "uuid": enterprise.uuid,
                    "name": enterprise.name,
                    "active": enterprise.active,
                }
            }
        )

        if user_has_multiple_enterprises:
            enterprise = EnterpriseCustomerUserFactory(user_id=self.user.id).enterprise_customer
            api_response['results'].append(
                {
                    "enterprise_customer": {
                        "uuid": enterprise.uuid,
                        "name": enterprise.name,
                        "active": enterprise.active,
                    }
                }
            )

        mock_client = mock_api_client_class.return_value
        mock_client.fetch_enterprise_learner_data.return_value = api_response
        reverse_mock.return_value = '/enterprise/select/active'

        response, _ = self._login_response(
            self.user.email,
            self.password,
            HTTP_ACCEPT='*/*',
        )
        self._assert_response(response, success=True)
        self._assert_redirect_url(response, settings.LMS_ROOT_URL + expected_redirect)

    @ddt.data(('', True), ('/enterprise/select/active/?success_url=', False))
    @ddt.unpack
    @patch.dict(settings.FEATURES, {'ENABLE_AUTHN_MICROFRONTEND': True, 'ENABLE_ENTERPRISE_INTEGRATION': True})
    @patch('openedx.features.enterprise_support.api.EnterpriseApiClient')
    @patch('openedx.core.djangoapps.user_authn.views.login.activate_learner_enterprise')
    @patch('openedx.core.djangoapps.user_authn.views.login.reverse')
    @skip_unless_lms
    def test_enterprise_in_url(
        self, expected_redirect, is_activated, reverse_mock, mock_activate_learner_enterprise, mock_api_client_class
    ):
        """
        If user has multiple enterprises and the enterprise is present in url,
        activate that url
        """
        api_response = {}
        enterprise_1 = EnterpriseCustomerUserFactory(user_id=self.user.id).enterprise_customer
        enterprise_2 = EnterpriseCustomerUserFactory(user_id=self.user.id).enterprise_customer
        api_response['results'] = [
            {
                "enterprise_customer": {
                    "uuid": enterprise_1.uuid,
                    "name": enterprise_1.name,
                    "active": enterprise_1.active,
                }
            },
            {
                "enterprise_customer": {
                    "uuid": enterprise_2.uuid,
                    "name": enterprise_2.name,
                    "active": enterprise_2.active,
                }
            }
        ]

        next_url = '/enterprise/{}/course/{}/enroll/?catalog=catalog_uuid&utm_medium=enterprise'.format(
            enterprise_1.uuid,
            'course-v1:testX+test101+2T2020'
        )

        mock_client = mock_api_client_class.return_value
        mock_client.fetch_enterprise_learner_data.return_value = api_response
        mock_activate_learner_enterprise.return_value = is_activated
        reverse_mock.return_value = '/enterprise/select/active'

        response, _ = self._login_response(
            self.user.email,
            self.password,
            extra_post_params={'next': next_url},
            HTTP_ACCEPT='*/*',
        )

        if not is_activated:
            next_url = urllib.parse.quote(next_url)

        self._assert_response(response, success=True)
        self._assert_redirect_url(response, settings.LMS_ROOT_URL + expected_redirect + next_url)

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_success_no_pii(self):
        response, mock_audit_log = self._login_response(
            self.user_email, self.password, patched_audit_log='common.djangoapps.student.models.user.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', ['Login success'])
        self._assert_not_in_audit_log(mock_audit_log, 'info', [self.user_email])

    def test_login_success_unicode_email(self):
        unicode_email = 'test' + chr(40960) + '@edx.org'
        self.user.email = unicode_email
        self.user.save()

        response, mock_audit_log = self._login_response(
            unicode_email, self.password, patched_audit_log='common.djangoapps.student.models.user.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', ['Login success', unicode_email])

    def test_login_fail_no_user_exists(self):
        nonexistent_email = 'not_a_user@edx.org'
        email_hash = hashlib.shake_128(nonexistent_email.encode('utf-8')).hexdigest(16)
        response, mock_audit_log = self._login_response(
            nonexistent_email,
            self.password,
        )
        self._assert_response(
            response, success=False, value=self.LOGIN_FAILED_WARNING, status_code=400
        )
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', 'Unknown user email', email_hash])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_fail_no_user_exists_no_pii(self):
        nonexistent_email = 'not_a_user@edx.org'
        response, mock_audit_log = self._login_response(
            nonexistent_email,
            self.password,
        )
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', 'Unknown user email'])
        self._assert_not_in_audit_log(mock_audit_log, 'warning', [nonexistent_email])

    def test_login_fail_wrong_password(self):
        response, mock_audit_log = self._login_response(
            self.user_email,
            'wrong_password',
        )
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning',
                               ['Login failed', 'password for', str(self.user.id), 'invalid'])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_fail_wrong_password_no_pii(self):
        response, mock_audit_log = self._login_response(self.user_email, 'wrong_password')
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(
            mock_audit_log, 'warning', ['Login failed', 'password for', str(self.user.id), 'invalid']
        )
        self._assert_not_in_audit_log(mock_audit_log, 'warning', [self.user_email])

    # HIBP settings are only defined in lms envs but needed for common tests.
    @override_settings(
        ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY=True,
        HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD=5.0,
    )
    @override_waffle_switch(ENABLE_PWNED_PASSWORD_API, True)
    def test_password_compliance_block_error(self):
        """
        Test that if HIBP Block flag is set to True and user's password lies
        within block threshold, then login fails and user is not authenticated.
        """
        password = hashlib.sha1(self.password.encode('utf-8')).hexdigest().upper()
        api_response = {password[5:]: 1000000}
        with patch.object(PwnedPasswordsAPI, 'range', return_value=api_response):
            response, _ = self._login_response(self.user_email, self.password)

        self._assert_response(response, success=False, error_code='require-password-change')

    # HIBP settings are only defined in lms envs but needed for common tests.
    @override_settings(
        ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY=True,
        HIBP_LOGIN_NUDGE_PASSWORD_FREQUENCY_THRESHOLD=3.0,
    )
    @override_waffle_switch(ENABLE_PWNED_PASSWORD_API, True)
    def test_password_compliance_nudge_error(self):
        """
        Test that if HIBP Nudge flag is set to True and user's password lies
        within nudge threshold, then user is authenticated and response contains
        proper error code.
        """
        password = hashlib.sha1(self.password.encode('utf-8')).hexdigest().upper()
        api_response = {password[5:]: 10}
        with patch.object(PwnedPasswordsAPI, 'range', return_value=api_response):
            response, _ = self._login_response(self.user_email, self.password)

        self._assert_response(response, success=False, error_code='nudge-password-change')

    def test_login_not_activated_no_pii(self):
        # De-activate the user
        self.user.is_active = False
        self.user.save()

        # Should now be unable to login
        response, mock_audit_log = self._login_response(
            self.user_email,
            self.password
        )
        self._assert_response(response, success=False, error_code="inactive-user")
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', 'Account not active for user'])
        self._assert_not_in_audit_log(mock_audit_log, 'warning', ['test'])

    def test_login_not_activated_with_correct_credentials(self):
        """
        Tests that when user login with the correct credentials but with an inactive
        account, the system, send account activation email notification to the user.
        """
        self.user.is_active = False
        self.user.save()

        response, mock_audit_log = self._login_response(
            self.user_email,
            self.password,
        )
        self._assert_response(response, success=False, error_code="inactive-user")
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', 'Account not active for user'])

    @patch('openedx.core.djangoapps.user_authn.views.login._log_and_raise_inactive_user_auth_error')
    def test_login_inactivated_user_with_incorrect_credentials(self, mock_inactive_user_email_and_error):
        """
        Tests that when user login with incorrect credentials and an inactive account,
        the system does *not* send account activation email notification to the user.
        """
        nonexistent_email = 'incorrect@email.com'
        email_hash = hashlib.shake_128(nonexistent_email.encode('utf-8')).hexdigest(16)
        self.user.is_active = False
        self.user.save()
        response, mock_audit_log = self._login_response(nonexistent_email, 'incorrect_password')

        assert not mock_inactive_user_email_and_error.called
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', 'Unknown user email', email_hash])

    def test_login_unicode_email(self):
        unicode_email = self.user_email + chr(40960)
        email_hash = hashlib.shake_128(unicode_email.encode('utf-8')).hexdigest(16)
        response, mock_audit_log = self._login_response(
            unicode_email,
            self.password,
        )
        self._assert_response(response, success=False)
        self._assert_audit_log(mock_audit_log, 'warning', ['Login failed', email_hash])

    def test_login_unicode_password(self):
        unicode_password = self.password + chr(1972)
        response, mock_audit_log = self._login_response(
            self.user_email,
            unicode_password,
        )
        self._assert_response(response, success=False)
        self._assert_audit_log(mock_audit_log, 'warning',
                               ['Login failed', 'password for', str(self.user.id), 'invalid'])

    def test_logout_logging(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)
        logout_url = reverse('logout')
        with patch('common.djangoapps.student.models.user.AUDIT_LOG') as mock_audit_log:
            response = self.client.post(logout_url)
        assert response.status_code == 200
        self._assert_audit_log(mock_audit_log, 'info', ['Logout', 'test'])

    def test_login_user_info_cookie(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        # Verify the format of the "user info" cookie set on login
        cookie = self.client.cookies[settings.EDXMKTG_USER_INFO_COOKIE_NAME]
        user_info = json.loads(cookie.value)

        assert user_info['version'] == settings.EDXMKTG_USER_INFO_COOKIE_VERSION
        assert user_info['username'] == self.user.username

        # Check that the URLs are absolute
        for url in user_info["header_urls"].values():
            assert 'http://' in url

    def test_logout_deletes_mktg_cookies(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        # Check that the marketing site cookies have been set
        assert settings.EDXMKTG_LOGGED_IN_COOKIE_NAME in self.client.cookies
        assert settings.EDXMKTG_USER_INFO_COOKIE_NAME in self.client.cookies

        # Log out
        logout_url = reverse('logout')
        response = self.client.post(logout_url)

        # Check that the marketing site cookies have been deleted
        # (cookies are deleted by setting an expiration date in 1970)
        for cookie_name in [settings.EDXMKTG_LOGGED_IN_COOKIE_NAME, settings.EDXMKTG_USER_INFO_COOKIE_NAME]:
            cookie = self.client.cookies[cookie_name]
            assert '01 Jan 1970' in cookie.get('expires').replace('-', ' ')

    @override_settings(
        EDXMKTG_LOGGED_IN_COOKIE_NAME="unicode-logged-in",
        EDXMKTG_USER_INFO_COOKIE_NAME="unicode-user-info",
    )
    def test_unicode_mktg_cookie_names(self):
        # When logged in cookie names are loaded from JSON files, they may
        # have type `unicode` instead of `str`, which can cause errors
        # when calling Django cookie manipulation functions.
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        response = self.client.post(reverse('logout'))
        expected = {
            'target': '/',
        }
        assert_dict_contains_subset(self, expected, response.context_data)

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_logout_logging_no_pii(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)
        logout_url = reverse('logout')
        with patch('common.djangoapps.student.models.user.AUDIT_LOG') as mock_audit_log:
            response = self.client.post(logout_url)
        assert response.status_code == 200
        self._assert_audit_log(mock_audit_log, 'info', ['Logout'])
        self._assert_not_in_audit_log(mock_audit_log, 'info', ['test'])

    @override_settings(RATELIMIT_ENABLE=False)
    def test_excessive_login_attempts_success(self):
        # Try (and fail) logging in with fewer attempts than the limit of 30
        # and verify that you can still successfully log in afterwards.
        for i in range(20):
            password = f'test_password{i}'
            response, _audit_log = self._login_response(self.user_email, password)
            self._assert_response(response, success=False)
        # now try logging in with a valid password
        response, _audit_log = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

    @patch('openedx.core.djangoapps.util.ratelimit.real_ip')
    def test_excessive_login_attempts_by_email(self, real_ip_mock):
        # try logging in 6 times, the defalutlimit for the number of failed
        # login attempts in one 5 minute period before the rate gets limited
        # for a specific e-mail address.

        # We freeze time to deal with the fact that rate limit time boundaries
        # are not predictable and we don't want the test to be flaky.
        with freeze_time():
            for i in range(6):
                password = f'test_password{i}'
                # Provide unique IPs so we don't get ip rate limited.
                real_ip_mock.return_value = f'192.168.1.{i}'
                self._login_response(self.user_email, password)
            # check to see if this response indicates that this was ratelimited
            response, _audit_log = self._login_response(self.user_email, 'wrong_password')
        self._assert_response(response, success=False, value='Too many failed login attempts')

    @patch('openedx.core.djangoapps.util.ratelimit.real_ip')
    def test_excessive_login_attempts_by_username(self, real_ip_mock):
        # try logging in 6 times, the defalutlimit for the number of failed
        # login attempts in one 5 minute period before the rate gets limited
        # for a specific username.

        # We freeze time to deal with the fact that rate limit time boundaries
        # are not predictable and we don't want the test to be flaky.
        with freeze_time():
            for i in range(6):
                password = f'test_password{i}'
                # Provide unique IPs so we don't get ip rate limited.
                real_ip_mock.return_value = f'192.168.1.{i}'
                self._login_response(self.username, password)
            # check to see if this response indicates that this was ratelimited
            response, _audit_log = self._login_response(self.username, 'wrong_password')
        self._assert_response(response, success=False, value='Too many failed login attempts')

    def test_excessive_login_attempts_by_ip(self):
        # try logging in 5 times, the default limit for the number of failed
        # login attempts in one 5 minute period before the rate gets limited
        # from a specific ip address.

        # We freeze time to deal with the fact that rate limit time boundaries
        # are not predictable and we don't want the test to be flaky.
        with freeze_time():
            for i in range(5):
                # provide different e-mail addresses so we don't get rate-limited
                # by e-mail.
                email = f'test_email{i}@example.com'
                password = f'test_password{i}'
                self._login_response(email, password)
            # check to see if this response indicates that this was ratelimited
            response, _audit_log = self._login_response(self.user_email, 'wrong_password')
        self._assert_response(response, success=False, value='Too many failed login attempts')

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    def test_login_refresh(self):
        def _assert_jwt_cookie_present(response):
            assert response.status_code == 200
            assert jwt_cookies.jwt_cookie_header_payload_name() in self.client.cookies

        setup_login_oauth_client()
        response, _ = self._login_response(self.user_email, self.password)
        _assert_jwt_cookie_present(response)

        response = self.client.post(reverse('login_refresh'))
        _assert_jwt_cookie_present(response)

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    def test_login_refresh_anonymous_user(self):
        response = self.client.post(reverse('login_refresh'))
        assert response.status_code == 401
        assert jwt_cookies.jwt_cookie_header_payload_name() not in self.client.cookies

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session(self):
        creds = {'email': self.user_email, 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        self.user = User.objects.get(pk=self.user.pk)

        assert self.user.profile.get_meta()['session_id'] == client1.session.session_key

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        try:
            # this test can be run with either lms or studio settings
            # since studio does not have a dashboard url, we should
            # look for another url that is login_required, in that case
            url = reverse('dashboard')
        except NoReverseMatch:
            url = reverse('upload_transcripts')
        response = client1.get(url)
        # client1 will be logged out
        assert response.status_code == 302

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session_with_no_user_profile(self):
        """
        Assert that user login with cas (Central Authentication Service) is
        redirect to dashboard in case of lms or upload_transcripts in case of
        cms
        """
        user = UserFactory.build(username='tester', email='tester@edx.org')
        user.set_password(self.password)
        user.save()

        # Assert that no profile is created.
        assert not hasattr(user, 'profile')

        creds = {'email': 'tester@edx.org', 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        user = User.objects.get(pk=user.pk)

        # Assert that profile is created.
        assert hasattr(user, 'profile')

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        try:
            # this test can be run with either lms or studio settings
            # since studio does not have a dashboard url, we should
            # look for another url that is login_required, in that case
            url = reverse('dashboard')
        except NoReverseMatch:
            url = reverse('upload_transcripts')
        response = client1.get(url)
        # client1 will be logged out
        assert response.status_code == 302

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session_with_url_not_having_login_required_decorator(self):
        # accessing logout url as it does not have login-required decorator it will avoid redirect
        # and go inside the enforce_single_login

        creds = {'email': self.user_email, 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        self.user = User.objects.get(pk=self.user.pk)

        assert self.user.profile.get_meta()['session_id'] == client1.session.session_key

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        url = reverse('logout')

        response = client1.get(url)
        assert response.status_code == 200

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance(self):
        """
        Tests _enforce_password_policy_compliance succeeds when no exception is thrown
        """
        enforce_compliance_path = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_path) as mock_check_password_policy_compliance:
            mock_check_password_policy_compliance.return_value = HttpResponse()
            response, _ = self._login_response(self.user_email, self.password)
            response_content = json.loads(response.content.decode('utf-8'))
        assert response_content.get('success')

    @patch.dict(settings.FEATURES, {"ENABLE_MAX_FAILED_LOGIN_ATTEMPTS": True})
    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance_exception(self):
        """
        Tests _enforce_password_policy_compliance fails with an exception thrown
        """
        assert not LoginFailures.objects.filter(user=self.user).exists()
        enforce_compliance_on_login = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_on_login) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordException()
            response, _ = self._login_response(
                self.user_email,
                self.password
            )
            response_content = json.loads(response.content.decode('utf-8'))
        assert not response_content.get('success')
        assert len(mail.outbox) == 1
        assert 'Password reset' in mail.outbox[0].subject
        failure_record = LoginFailures.objects.get(user=self.user)
        assert failure_record.failure_count == 1
        LoginFailures.clear_lockout_counter(user=self.user)

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance_warning(self):
        """
        Tests _enforce_password_policy_compliance succeeds with a warning thrown
        """
        enforce_compliance_on_login = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_on_login) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordWarning('Test warning')
            response, _ = self._login_response(self.user_email, self.password)
            response_content = json.loads(response.content.decode('utf-8'))
            assert 'Test warning' in self.client.session['_messages']
        assert response_content.get('success')

    @ddt.data(
        ('test_password', 'test_password', True),
        (unicodedata.normalize('NFKD', 'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKC', 'Ṗŕệṿïệẅ Ṯệẍt'), False),
        (unicodedata.normalize('NFKC', 'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKD', 'Ṗŕệṿïệẅ Ṯệẍt'), True),
        (unicodedata.normalize('NFKD', 'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKD', 'Ṗŕệṿïệẅ Ṯệẍt'), False),
    )
    @ddt.unpack
    def test_password_unicode_normalization_login(self, password, password_entered, login_success):
        """
        Tests unicode normalization on user's passwords on login.
        """
        self.user.set_password(password)
        self.user.save()
        response, _ = self._login_response(self.user.email, password_entered)
        self._assert_response(response, success=login_success)

    def _login_response(
            self, email, password, patched_audit_log=None, extra_post_params=None, **extra
    ):
        """
        Post the login info
        """
        if patched_audit_log is None:
            patched_audit_log = 'openedx.core.djangoapps.user_authn.views.login.AUDIT_LOG'
        post_params = {'email': email, 'password': password}
        if extra_post_params is not None:
            post_params.update(extra_post_params)
        with patch(patched_audit_log) as mock_audit_log:
            result = self.client.post(self.url, post_params, **extra)
        return result, mock_audit_log

    def _assert_response(self, response, success=None, value=None, status_code=None, error_code=None):
        """
        Assert that the response has the expected status code and returned a valid
        JSON-parseable dict.

        If success is provided, assert that the response had that
        value for 'success' in the JSON dict.

        If value is provided, assert that the response contained that
        value for 'value' in the JSON dict.
        """
        expected_status_code = status_code or (400 if success is False else 200)
        assert response.status_code == expected_status_code

        try:
            response_dict = json.loads(response.content.decode('utf-8'))
        except ValueError:
            self.fail("Could not parse response content as JSON: %s"  # noqa: UP031
                      % str(response.content))

        if success is not None:
            assert response_dict['success'] == success

        if error_code is not None:
            assert response_dict['error_code'] == error_code

        if value is not None:
            msg = ("'%s' did not contain '%s'" %  # noqa: UP031
                   (str(response_dict['value']), str(value)))
            assert value in response_dict['value'], msg

    def _assert_redirect_url(self, response, expected_redirect_url):
        """
        Assert that the redirect URL is in the response and has the expected value.

        Assumes that response content is well-formed JSON
        (you can call `_assert_response` first to assert this).
        """
        response_dict = json.loads(response.content.decode('utf-8'))
        assert 'redirect_url' in response_dict, (
            "Response JSON unexpectedly does not have redirect_url: {!r}".format(  # noqa: UP032
                response_dict
            )
        )
        assert response_dict['redirect_url'] == expected_redirect_url

    def _assert_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        assert name == level
        assert len(args) == 1
        format_string = args[0]
        for log_string in log_strings:
            assert log_string in format_string

    def _assert_not_in_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        assert name == level
        assert len(args) == 1
        format_string = args[0]
        for log_string in log_strings:
            assert log_string not in format_string

    @ddt.data(
        {
            'switch_enabled': False,
            'whitelisted': False,
            'allowed_domain': 'edx.org',
            'user_domain': 'edx.org',
            'success': True,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': False,
            'whitelisted': True,
            'allowed_domain': 'edx.org',
            'user_domain': 'edx.org',
            'success': True,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': True,
            'whitelisted': False,
            'allowed_domain': 'edx.org',
            'user_domain': 'edx.org',
            'success': False,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': True,
            'whitelisted': False,
            'allowed_domain': 'fake.org',
            'user_domain': 'edx.org',
            'success': True,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': True,
            'whitelisted': True,
            'allowed_domain': 'edx.org',
            'user_domain': 'edx.org',
            'success': True,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': True,
            'whitelisted': False,
            'allowed_domain': 'batman.gotham',
            'user_domain': 'batman.gotham',
            'success': False,
            'is_third_party_authenticated': False
        },
        {
            'switch_enabled': True,
            'whitelisted': True,
            'allowed_domain': 'edx.org',
            'user_domain': 'edx.org',
            'success': True,
            'is_third_party_authenticated': True
        },
        {
            'switch_enabled': False,
            'whitelisted': False,
            'allowed_domain': 'edx.org',
            'user_domain': 'fake.org',
            'success': True,
            'is_third_party_authenticated': True
        },
    )
    @ddt.unpack
    @skip_unless_lms
    def test_login_for_user_auth_flow(
        self,
        switch_enabled,
        whitelisted,
        allowed_domain,
        user_domain,
        success,
        is_third_party_authenticated
    ):
        """
        Verify that `login._check_user_auth_flow` works as expected.
        """
        provider = 'Google'
        provider_tpa_hint = 'saml-test'
        username = 'batman'
        user_email = f'{username}@{user_domain}'
        user = self._create_user(username, user_email)
        default_site_configuration_values = {
            'SITE_NAME': allowed_domain,
            'THIRD_PARTY_AUTH_ONLY_DOMAIN': allowed_domain,
            'THIRD_PARTY_AUTH_ONLY_PROVIDER': provider,
            'THIRD_PARTY_AUTH_ONLY_HINT': provider_tpa_hint,
        }

        with override_waffle_switch(ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY, switch_enabled):
            if not is_third_party_authenticated:
                site = self.set_up_site(allowed_domain, default_site_configuration_values)

                if whitelisted:
                    AllowedAuthUser.objects.create(site=site, email=user.email)
                else:
                    AllowedAuthUser.objects.filter(site=site, email=user.email).delete()

                if success:
                    value = None
                else:
                    value = 'As {0} user, You must login with your {0} <a href=\'{1}\'>{2} account</a>.'.format(
                        allowed_domain,
                        '{}?tpa_hint={}'.format(reverse("dashboard"), provider_tpa_hint),
                        provider,
                    )
                response, __ = self._login_response(user.email, self.password)
                self._assert_response(
                    response,
                    success=success,
                    value=value,
                )
            else:
                default_site_configuration_values.update({'ENABLE_THIRD_PARTY_AUTH': True})
                self.set_up_site(allowed_domain, default_site_configuration_values)
                with patch('openedx.core.djangoapps.user_authn.views.login.pipeline'):
                    with patch(
                        'openedx.core.djangoapps.user_authn.views.login._check_user_auth_flow'
                    ) as mock_check_user_auth_flow:
                        # user is already authenticated by third_party_auth then
                        # we should by-pass _check_user_auth_flow function
                        response, __ = self._login_response(user.email, self.password)
                        self._assert_response(
                            response,
                            success=success
                        )
                        assert not mock_check_user_auth_flow.called

    def test_check_user_auth_flow_bad_email(self):
        """Regression Exception was thrown on missing @ char in TPA."""
        provider = 'Google'
        provider_tpa_hint = 'saml-test'
        username = 'batman'
        invalid_email_user = self._create_user(username, username)
        allowed_domain = 'edx.org'
        default_site_configuration_values = {
            'SITE_NAME': allowed_domain,
            'THIRD_PARTY_AUTH_ONLY_DOMAIN': allowed_domain,
            'THIRD_PARTY_AUTH_ONLY_PROVIDER': provider,
            'THIRD_PARTY_AUTH_ONLY_HINT': provider_tpa_hint,
        }

        with override_waffle_switch(ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY, True):
            site = self.set_up_site(allowed_domain, default_site_configuration_values)

            with self.assertLogs(level='WARN') as log:
                _check_user_auth_flow(site, invalid_email_user)
                assert any("Shortcircuiting THIRD_PARTY_AUTH_ONLY_DOMAIN check." in warning for warning in log.output)


@ddt.ddt
@skip_unless_lms
class LoginSessionViewTest(OpenEdxEventsTestMixin, ApiTestCase):
    """Tests for the login end-points of the user API. """

    ENABLED_OPENEDX_EVENTS = []

    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"

    def setUp(self):
        super().setUp()
        self.url = reverse("user_api_login_session", kwargs={'api_version': 'v1'})
        self.url_v2 = reverse("user_api_login_session", kwargs={'api_version': 'v2'})
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

    @ddt.data("get", "post")
    def test_auth_disabled(self, method):
        self.assertAuthDisabled(method, self.url)

    def test_allowed_methods(self):
        self.assertAllowedMethods(self.url, ["GET", "POST", "HEAD", "OPTIONS"])

    def test_put_not_allowed(self):
        response = self.client.put(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_delete_not_allowed(self):
        response = self.client.delete(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_patch_not_allowed(self):
        response = self.client.patch(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_login_form(self):
        # Retrieve the login form
        response = self.client.get(self.url, content_type="application/json")
        self.assertHttpOK(response)

        # Verify that the form description matches what we expect
        form_desc = json.loads(response.content.decode('utf-8'))
        assert form_desc['method'] == 'post'
        assert form_desc['submit_url'] == reverse('user_api_login_session', kwargs={'api_version': 'v1'})
        assert form_desc['fields'] == [{'name': 'email', 'defaultValue': '', 'type': 'email', 'exposed': True,
                                        'required': True, 'label': 'Email', 'placeholder': '',
                                        'instructions': 'The email address you used to register with {platform_name}'  # noqa: UP032  # pylint: disable=line-too-long
                                        .format(platform_name=settings.PLATFORM_NAME),
                                        'restrictions': {'min_length': EMAIL_MIN_LENGTH,
                                                         'max_length': EMAIL_MAX_LENGTH},
                                        'errorMessages': {},
                                        'supplementalText': '',
                                        'supplementalLink': '',
                                        'loginIssueSupportLink': 'https://support.example.com/login-issue-help.html'},
                                       {'name': 'password',
                                        'defaultValue': '',
                                        'type': 'password',
                                        'exposed': True,
                                        'required': True,
                                        'label': 'Password',
                                        'placeholder': '',
                                        'instructions': '',
                                        'restrictions': {'max_length': DEFAULT_MAX_PASSWORD_LENGTH},
                                        'errorMessages': {},
                                        'supplementalText': '',
                                        'supplementalLink': '',
                                        'loginIssueSupportLink': 'https://support.example.com/login-issue-help.html'}]

    @ddt.data(True, False)
    @patch('openedx.core.djangoapps.user_authn.views.login.segment')
    def test_login(self, include_analytics, mock_segment):
        data = {
            "email": self.EMAIL,
            "password": self.PASSWORD,
        }
        if include_analytics:
            track_label = "edX/DemoX/Fall"
            data.update({
                "analytics": json.dumps({"enroll_course_id": track_label})
            })
        else:
            track_label = None

        # Login
        response = self.client.post(self.url, data)
        self.assertHttpOK(response)

        # Verify that we logged in successfully by accessing
        # a page that requires authentication.
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

        # Verify events are called
        expected_user_id = self.user.id
        mock_segment.identify.assert_called_once_with(
            expected_user_id,
            {'username': self.USERNAME, 'email': self.EMAIL},
            {'MailChimp': False}
        )
        mock_segment.track.assert_called_once_with(
            expected_user_id,
            'edx.bi.user.account.authenticated',
            {'category': 'conversion', 'provider': None, 'label': track_label, 'anonymous_id': ''}
        )

    def test_login_with_username(self):
        data = {
            "email_or_username": self.USERNAME,
            "password": self.PASSWORD,
        }
        response = self.client.post(self.url_v2, data)
        self.assertHttpOK(response)

    def test_session_cookie_expiry(self):
        # Login and remember me
        data = {
            "email": self.EMAIL,
            "password": self.PASSWORD,
        }

        response = self.client.post(self.url, data)
        self.assertHttpOK(response)

        # Verify that the session expiration was set correctly
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        expected_expiry = datetime.datetime.utcnow() + datetime.timedelta(weeks=4)
        assert expected_expiry.strftime('%d %b %Y') in cookie.get('expires').replace('-', ' ')

    def test_invalid_credentials(self):
        # Invalid password
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "password": "invalid"
        })
        self.assertHttpBadRequest(response)

        # Invalid email address
        response = self.client.post(self.url, {
            "email": "invalid@example.com",
            "password": self.PASSWORD,
        })
        self.assertHttpBadRequest(response)

    @ddt.data(True, False)
    def test_missing_login_params(self, is_api_v1):
        email_field_name = "email" if is_api_v1 else "email_or_username"
        url = self.url if is_api_v1 else self.url_v2
        # Missing password
        response = self.client.post(url, {
            email_field_name: self.EMAIL,
        })
        self.assertHttpBadRequest(response)

        # Missing email
        response = self.client.post(url, {
            "password": self.PASSWORD,
        })
        self.assertHttpBadRequest(response)

        # Missing both email and password
        response = self.client.post(url, {})
        self.assertHttpBadRequest(response)
