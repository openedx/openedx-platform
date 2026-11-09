""" Tests for Logistration views. """

from urllib.parse import urlencode

import ddt
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin, simulate_running_pipeline
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme_context
from openedx.core.djangoapps.user_authn.cookies import JWT_COOKIE_NAMES
from openedx.core.djangoapps.user_authn.tests.utils import setup_login_oauth_client
from openedx.core.djangolib.js_utils import dump_js_escaped_json
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


@skip_unless_lms
@ddt.ddt
@override_settings(EMBARGO=True)
class LoginAndRegistrationTest(ThirdPartyAuthTestMixin, UrlResetMixin, ModuleStoreTestCase):
    """ Tests for Login and Registration. """
    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()

        # Several third party auth providers are created for these tests:
        self.google_provider = self.configure_google_provider(enabled=True, visible=True)
        self.configure_facebook_provider(enabled=True, visible=True)
        self.configure_dummy_provider(
            visible=True,
            enabled=True,
            icon_class='',
            icon_image=SimpleUploadedFile('icon.svg', b'<svg><rect width="50" height="100"/></svg>'),
        )
        self.hidden_enabled_provider = self.configure_linkedin_provider(
            visible=False,
            enabled=True,
        )
        self.hidden_disabled_provider = self.configure_azure_ad_provider()

    @ddt.data(
        ("signin_user", "/login"),
        ("register_user", "/register"),
        ("password_assistance", "/reset"),
    )
    @ddt.unpack
    @override_settings(ENABLE_AUTHN_MICROFRONTEND=True)
    def test_logistration_mfe_redirects(self, url_name, path):
        """
        Test that if Logistration MFE is enabled, then we redirect to
        the correct URL.
        """

        response = self.client.get(reverse(url_name))
        self.assertRedirects(response, settings.AUTHN_MICROFRONTEND_URL + path, fetch_redirect_response=False)

    @ddt.data(
        (
            "signin_user",
            "/login",
            {"next": "dashboard"},
        ),
        (
            "register_user",
            "/register",
            {"course_id": "course-v1:edX+DemoX+Demo_Course", "enrollment_action": "enroll"}
        )
    )
    @ddt.unpack
    @override_settings(ENABLE_AUTHN_MICROFRONTEND=True)
    def test_logistration_redirect_params(self, url_name, path, query_params):
        """
        Test that if request is redirected to logistration MFE,
        query params are passed to the redirect url.
        """
        expected_url = settings.AUTHN_MICROFRONTEND_URL + path + '?' + urlencode(query_params)
        response = self.client.get(reverse(url_name), query_params)

        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    @ddt.data(
        ("signin_user", "login"),
        ("register_user", "register"),
    )
    @ddt.unpack
    def test_login_and_registration_form(self, url_name, initial_mode):
        response = self.client.get(reverse(url_name))
        expected_data = f'"initial_mode": "{initial_mode}"'
        self.assertContains(response, expected_data)

    @override_settings(DISABLE_SET_JWT_COOKIES_FOR_TESTS=False)
    @ddt.data("signin_user", "register_user")
    def test_login_and_registration_form_already_authenticated(self, url_name):
        setup_login_oauth_client()
        # call the account registration api that sets the login cookies
        url = reverse('user_api_registration')
        request_data = {
            'username': self.USERNAME,
            'password': self.PASSWORD,
            'email': self.EMAIL,
            'name': self.USERNAME,
            'terms_of_service': 'true',
            'honor_code': 'true',
        }
        result = self.client.post(url, data=request_data)
        assert result.status_code == 200

        result = self.client.login(username=self.USERNAME, password=self.PASSWORD)
        assert result

        # Verify that we're redirected to the dashboard
        response = self.client.get(reverse(url_name))
        self.assertRedirects(response, reverse("dashboard"))

        # Refresh login even if JWT cookies are expired.
        # (Give precedence to the session.)
        for name in JWT_COOKIE_NAMES:
            del self.client.cookies[name]

        # Verify that we're still redirected to the dashboard
        response = self.client.get(reverse(url_name))
        self.assertRedirects(response, reverse("dashboard"))

        # Verify that we got new JWT cookies.
        for name in JWT_COOKIE_NAMES:
            assert name in self.client.cookies

    @ddt.data(
        (None, "signin_user"),
        (None, "register_user"),
    )
    @ddt.unpack
    def test_login_and_registration_form_signin_not_preserves_params(self, theme, url_name):
        params = [
            ('course_id', 'edX/DemoX/Demo_Course'),
            ('enrollment_action', 'enroll'),
        ]

        # The response should not have a "Sign In" button with the URL
        # that preserves the querystring params
        with with_comprehensive_theme_context(theme):
            response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")

        expected_url = '/login?{}'.format(self._finish_auth_url_param(params + [('next', '/dashboard')]))
        self.assertNotContains(response, expected_url)

        # Add additional parameters:
        params = [
            ('course_id', 'edX/DemoX/Demo_Course'),
            ('enrollment_action', 'enroll'),
            ('course_mode', CourseMode.DEFAULT_MODE_SLUG),
            ('email_opt_in', 'true'),
            ('next', '/custom/final/destination')
        ]

        # Verify that this parameter is also preserved
        with with_comprehensive_theme_context(theme):
            response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")

        expected_url = f'/login?{self._finish_auth_url_param(params)}'
        self.assertNotContains(response, expected_url)

    @override_settings(ENABLE_THIRD_PARTY_AUTH=False)
    @ddt.data("signin_user", "register_user")
    def test_third_party_auth_disabled(self, url_name):
        response = self.client.get(reverse(url_name))
        self._assert_third_party_auth_data(response, None, None, [])

    @ddt.data(
        ("signin_user", None, None),
        ("register_user", None, None),
        ("signin_user", "google-oauth2", "Google"),
        ("register_user", "google-oauth2", "Google"),
        ("signin_user", "facebook", "Facebook"),
        ("register_user", "facebook", "Facebook"),
        ("signin_user", "dummy", "Dummy"),
        ("register_user", "dummy", "Dummy"),
    )
    @ddt.unpack
    def test_third_party_auth(
            self,
            url_name,
            current_backend,
            current_provider,
    ):
        params = [
            ('course_id', 'course-v1:Org+Course+Run'),
            ('enrollment_action', 'enroll'),
            ('course_mode', CourseMode.DEFAULT_MODE_SLUG),
            ('email_opt_in', 'true'),
            ('next', '/custom/final/destination'),
        ]

        # Simulate a running pipeline
        if current_backend is not None:
            pipeline_target = "openedx.core.djangoapps.user_authn.views.login_form.third_party_auth.pipeline"
            with simulate_running_pipeline(pipeline_target, current_backend):
                response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")

        # Do NOT simulate a running pipeline
        else:
            response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")

        # This relies on the THIRD_PARTY_AUTH configuration in the test settings
        expected_providers = [
            {
                "id": "oa2-dummy",
                "name": "Dummy",
                "iconClass": None,
                "iconImage": settings.MEDIA_URL + "icon.svg",
                "skipHintedLogin": False,
                "skipRegistrationForm": False,
                "loginUrl": self._third_party_login_url("dummy", "login", params),
                "registerUrl": self._third_party_login_url("dummy", "register", params)
            },
            {
                "id": "oa2-facebook",
                "name": "Facebook",
                "iconClass": "fa-facebook",
                "iconImage": None,
                "skipHintedLogin": False,
                "skipRegistrationForm": False,
                "loginUrl": self._third_party_login_url("facebook", "login", params),
                "registerUrl": self._third_party_login_url("facebook", "register", params)
            },
            {
                "id": "oa2-google-oauth2",
                "name": "Google",
                "iconClass": "fa-google-plus",
                "iconImage": None,
                "skipHintedLogin": False,
                "skipRegistrationForm": False,
                "loginUrl": self._third_party_login_url("google-oauth2", "login", params),
                "registerUrl": self._third_party_login_url("google-oauth2", "register", params)
            },
        ]
        self._assert_third_party_auth_data(
            response,
            current_backend,
            current_provider,
            expected_providers,
        )

    def test_hinted_login(self):
        params = [("next", "/courses/something/?tpa_hint=oa2-google-oauth2")]
        response = self.client.get(reverse('signin_user'), params, HTTP_ACCEPT="text/html")
        self.assertContains(response, '"third_party_auth_hint": "oa2-google-oauth2"')

        tpa_hint = self.hidden_enabled_provider.provider_id
        params = [("next", f"/courses/something/?tpa_hint={tpa_hint}")]
        response = self.client.get(reverse('signin_user'), params, HTTP_ACCEPT="text/html")
        self.assertContains(response, f'"third_party_auth_hint": "{tpa_hint}"')

        tpa_hint = self.hidden_disabled_provider.provider_id
        params = [("next", f"/courses/something/?tpa_hint={tpa_hint}")]
        response = self.client.get(reverse('signin_user'), params, HTTP_ACCEPT="text/html")
        assert response.content.decode('utf-8') not in tpa_hint

    @ddt.data(
        ('signin_user', 'login'),
        ('register_user', 'register'),
    )
    @ddt.unpack
    def test_hinted_login_dialog_disabled(self, url_name, auth_entry):
        """Test that the dialog doesn't show up for hinted logins when disabled. """
        self.google_provider.skip_hinted_login_dialog = True
        self.google_provider.save()
        params = [("next", "/courses/something/?tpa_hint=oa2-google-oauth2")]
        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")
        expected_url = '/auth/login/google-oauth2/?auth_entry={}&next=%2Fcourses'\
                       '%2Fsomething%2F%3Ftpa_hint%3Doa2-google-oauth2'.format(auth_entry)  # noqa: UP032
        self.assertRedirects(
            response,
            expected_url,
            target_status_code=302
        )

    @override_settings(THIRD_PARTY_AUTH_HINT='oa2-google-oauth2')
    @ddt.data(
        'signin_user',
        'register_user',
    )
    def test_settings_tpa_hinted_login(self, url_name):
        """
        Ensure that settings.THIRD_PARTY_AUTH_HINT can set third_party_auth_hint.
        """
        params = [("next", "/courses/something/")]
        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")
        self.assertContains(response, '"third_party_auth_hint": "oa2-google-oauth2"')

        # THIRD_PARTY_AUTH_HINT can be overridden via the query string
        tpa_hint = self.hidden_enabled_provider.provider_id
        params = [("next", f"/courses/something/?tpa_hint={tpa_hint}")]
        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")
        self.assertContains(response, f'"third_party_auth_hint": "{tpa_hint}"')

        # Even disabled providers in the query string will override THIRD_PARTY_AUTH_HINT
        tpa_hint = self.hidden_disabled_provider.provider_id
        params = [("next", f"/courses/something/?tpa_hint={tpa_hint}")]
        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")
        assert response.content.decode('utf-8') not in tpa_hint

    @override_settings(THIRD_PARTY_AUTH_HINT='oa2-google-oauth2')
    @ddt.data(
        ('signin_user', 'login'),
        ('register_user', 'register'),
    )
    @ddt.unpack
    def test_settings_tpa_hinted_login_dialog_disabled(self, url_name, auth_entry):
        """Test that the dialog doesn't show up for hinted logins when disabled via settings.THIRD_PARTY_AUTH_HINT. """
        self.google_provider.skip_hinted_login_dialog = True
        self.google_provider.save()
        params = [("next", "/courses/something/")]
        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")
        expected_url = '/auth/login/google-oauth2/?auth_entry={}&next=%2Fcourses'\
                       '%2Fsomething%2F%3Ftpa_hint%3Doa2-google-oauth2'.format(auth_entry)  # noqa: UP032
        self.assertRedirects(
            response,
            expected_url,
            target_status_code=302
        )

    def test_login_registration_xframe_protected(self):
        resp = self.client.get(
            reverse("register_user"),
            {},
            HTTP_REFERER="http://localhost/iframe"
        )

        assert resp['X-Frame-Options'] == 'DENY'

        self.configure_lti_provider(name='Test', lti_hostname='localhost', lti_consumer_key='test_key', enabled=True)

        resp = self.client.get(
            reverse("register_user"),
            HTTP_REFERER="http://localhost/iframe"
        )

        assert resp['X-Frame-Options'] == 'ALLOW'

    def _assert_third_party_auth_data(self, response, current_backend, current_provider, providers):
        """Verify that third party auth info is rendered correctly in a DOM data attribute. """
        finish_auth_url = None
        if current_backend:
            finish_auth_url = reverse("social:complete", kwargs={"backend": current_backend}) + "?"
        auth_info = {
            "currentProvider": current_provider,
            "platformName": settings.PLATFORM_NAME,
            "providers": providers,
            "secondaryProviders": [],
            "finishAuthUrl": finish_auth_url,
            "errorMessage": None,
            "registerFormSubmitButtonText": "Create Account",
            "syncLearnerProfileData": False,
            "pipeline_user_details": {},
            "skipRegistrationOptionalCheckboxes": False
        }
        auth_info = dump_js_escaped_json(auth_info)

        expected_data = '"third_party_auth": {auth_info}'.format(  # noqa: UP032
            auth_info=auth_info
        )
        self.assertContains(response, expected_data)

    def _third_party_login_url(self, backend_name, auth_entry, login_params):
        """Construct the login URL to start third party authentication. """
        return "{url}?auth_entry={auth_entry}&{param_str}".format(
            url=reverse("social:begin", kwargs={"backend": backend_name}),
            auth_entry=auth_entry,
            param_str=self._finish_auth_url_param(login_params),
        )

    def _finish_auth_url_param(self, params):
        """
        Make the next=... URL parameter that indicates where the user should go next.

        >>> _finish_auth_url_param([('next', '/dashboard')])
        '/account/finish_auth?next=%2Fdashboard'
        """
        return urlencode({
            'next': f'/account/finish_auth?{urlencode(params)}'
        })

    def test_english_by_default(self):
        response = self.client.get(reverse('signin_user'), [], HTTP_ACCEPT="text/html")

        assert response['Content-Language'] == 'en'

    def test_unsupported_language(self):
        response = self.client.get(reverse('signin_user'), [], HTTP_ACCEPT="text/html", HTTP_ACCEPT_LANGUAGE="ts-zx")

        assert response['Content-Language'] == 'en'

    def test_browser_language(self):
        response = self.client.get(reverse('signin_user'), [], HTTP_ACCEPT="text/html", HTTP_ACCEPT_LANGUAGE="es")

        assert response['Content-Language'] == 'es-419'

    def test_browser_language_dialent(self):
        response = self.client.get(reverse('signin_user'), [], HTTP_ACCEPT="text/html", HTTP_ACCEPT_LANGUAGE="es-es")

        assert response['Content-Language'] == 'es-es'


@skip_unless_lms
class AccountCreationTestCaseWithSiteOverrides(SiteMixin, TestCase):
    """
    Test cases for Feature flag ALLOW_PUBLIC_ACCOUNT_CREATION which when
    turned off disables the account creation options in lms
    """

    def setUp(self):
        """Set up the tests"""
        super().setUp()

        # Set the feature flag ALLOW_PUBLIC_ACCOUNT_CREATION to False
        self.site_configuration_values = {
            'ALLOW_PUBLIC_ACCOUNT_CREATION': False
        }
        self.site_domain = 'testserver1.com'
        self.set_up_site(self.site_domain, self.site_configuration_values)

    def test_register_option_login_page(self):
        """
        Navigate to the login page and check the Register option is hidden when
        ALLOW_PUBLIC_ACCOUNT_CREATION flag is turned off
        """
        response = self.client.get(reverse('signin_user'))
        self.assertNotContains(response, '<a class="btn-neutral" href="/register?next=%2Fdashboard">Register</a>')
