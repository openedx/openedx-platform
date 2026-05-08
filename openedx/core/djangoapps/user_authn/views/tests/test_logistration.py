""" Tests for Logistration views. """


from http.cookies import SimpleCookie
from unittest import mock
from urllib.parse import urlencode

import ddt
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.translation import gettext as _

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin, simulate_running_pipeline
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.branding.api import get_privacy_url
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme_context
from openedx.core.djangoapps.user_authn.cookies import JWT_COOKIE_NAMES
from openedx.core.djangoapps.user_authn.tests.utils import setup_login_oauth_client
from openedx.core.djangoapps.user_authn.views.login_form import login_and_registration_form
from openedx.core.djangolib.js_utils import dump_js_escaped_json
from openedx.core.djangolib.markup import HTML, Text
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


@skip_unless_lms
@ddt.ddt
class LoginAndRegistrationTest(ThirdPartyAuthTestMixin, UrlResetMixin, ModuleStoreTestCase):
    """ Tests for Login and Registration. """
    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    @mock.patch.dict(settings.FEATURES, {'EMBARGO': True})
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

    FEATURES_WITH_AUTHN_MFE_ENABLED = settings.FEATURES.copy()
    FEATURES_WITH_AUTHN_MFE_ENABLED['ENABLE_AUTHN_MICROFRONTEND'] = True

    @ddt.data(
        ("signin_user", "/login"),
        ("register_user", "/register"),
        ("password_assistance", "/reset"),
    )
    @ddt.unpack
    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
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
    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
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

    @mock.patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
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

    @mock.patch.dict(settings.FEATURES, {"ENABLE_THIRD_PARTY_AUTH": False})
    @ddt.data("signin_user", "register_user")
    def test_third_party_auth_disabled(self, url_name):
        response = self.client.get(reverse(url_name))
        self._assert_third_party_auth_data(response, None, None, [], None)

    @mock.patch('openedx.core.djangoapps.user_authn.views.login_form.enterprise_customer_for_request')
    @ddt.data(
        ("signin_user", None, None, None, False),
        ("register_user", None, None, None, False),
        ("signin_user", "google-oauth2", "Google", None, False),
        ("register_user", "google-oauth2", "Google", None, False),
        ("signin_user", "facebook", "Facebook", None, False),
        ("register_user", "facebook", "Facebook", None, False),
        ("signin_user", "dummy", "Dummy", None, False),
        ("register_user", "dummy", "Dummy", None, False),
        (
            "signin_user",
            "google-oauth2",
            "Google",
            {
                'name': 'FakeName',
                'logo': 'https://host.com/logo.jpg',
                'welcome_msg': 'No message'
            },
            True
        )
    )
    @ddt.unpack
    def test_third_party_auth(
            self,
            url_name,
            current_backend,
            current_provider,
            expected_enterprise_customer_mock_attrs,
            add_user_details,
            enterprise_customer_mock,
    ):
        params = [
            ('course_id', 'course-v1:Org+Course+Run'),
            ('enrollment_action', 'enroll'),
            ('course_mode', CourseMode.DEFAULT_MODE_SLUG),
            ('email_opt_in', 'true'),
            ('next', '/custom/final/destination'),
        ]

        if expected_enterprise_customer_mock_attrs:
            expected_ec = {
                'name': expected_enterprise_customer_mock_attrs['name'],
                'branding_configuration': {
                    'logo': 'https://host.com/logo.jpg',
                    'welcome_message': expected_enterprise_customer_mock_attrs['welcome_msg']
                }
            }
        else:
            expected_ec = None

        email = None
        if add_user_details:
            email = 'test@test.com'
        enterprise_customer_mock.return_value = expected_ec

        # Simulate a running pipeline
        if current_backend is not None:
            pipeline_target = "openedx.core.djangoapps.user_authn.views.login_form.third_party_auth.pipeline"
            with simulate_running_pipeline(pipeline_target, current_backend, email=email):
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
            expected_ec,
            add_user_details
        )

    def _configure_testshib_provider(self, provider_name, idp_slug):
        """
        Enable and configure the TestShib SAML IdP as a third_party_auth provider.
        """
        kwargs = {}
        kwargs.setdefault('name', provider_name)
        kwargs.setdefault('enabled', True)
        kwargs.setdefault('visible', True)
        kwargs.setdefault('slug', idp_slug)
        kwargs.setdefault('entity_id', 'https://idp.testshib.org/idp/shibboleth')
        kwargs.setdefault('metadata_source', 'https://mock.testshib.org/metadata/testshib-providers.xml')
        kwargs.setdefault('icon_class', 'fa-university')
        kwargs.setdefault('attr_email', 'dummy-email-attr')
        kwargs.setdefault('max_session_length', None)
        kwargs.setdefault('skip_registration_form', False)
        self.configure_saml_provider(**kwargs)

    @mock.patch('django.conf.settings.MESSAGE_STORAGE', 'django.contrib.messages.storage.cookie.CookieStorage')
    @mock.patch('openedx.core.djangoapps.user_authn.views.login_form.enterprise_customer_for_request')
    @ddt.data(
        (
            'signin_user',
            'tpa-saml',
            'TestShib',
        )
    )
    @ddt.unpack
    def test_saml_auth_with_error(
            self,
            url_name,
            current_backend,
            current_provider,
            enterprise_customer_mock,
    ):
        params = []
        request = RequestFactory().get(reverse(url_name), params, HTTP_ACCEPT='text/html')
        SessionMiddleware(get_response=lambda request: None).process_request(request)
        request.user = AnonymousUser()

        self.enable_saml()
        dummy_idp = 'testshib'
        self._configure_testshib_provider(current_provider, dummy_idp)
        enterprise_customer_data = {
            'uuid': '72416e52-8c77-4860-9584-15e5b06220fb',
            'name': 'Dummy Enterprise',
            'identity_provider': dummy_idp,
        }
        enterprise_customer_mock.return_value = enterprise_customer_data
        dummy_error_message = 'Authentication failed: SAML login failed ' \
                              '["invalid_response"] [SAML Response must contain 1 assertion]'

        # Add error message for error in auth pipeline
        MessageMiddleware(get_response=lambda request: None).process_request(request)
        messages.error(request, dummy_error_message, extra_tags='social-auth')

        # Simulate a running pipeline
        pipeline_response = {
            'response': {
                'idp_name': dummy_idp
            }
        }
        pipeline_target = 'openedx.core.djangoapps.user_authn.views.login_form.third_party_auth.pipeline'
        with simulate_running_pipeline(pipeline_target, current_backend, **pipeline_response):
            with mock.patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
                response = login_and_registration_form(request)

        expected_error_message = Text(_(
            'We are sorry, you are not authorized to access {platform_name} via this channel. '
            'Please contact your learning administrator or manager in order to access {platform_name}.'
            '{line_break}{line_break}'
            'Error Details:{line_break}{error_message}')
        ).format(
            platform_name=settings.PLATFORM_NAME,
            error_message=dummy_error_message,
            line_break=HTML('<br/>')
        )
        self._assert_saml_auth_data_with_error(
            response,
            current_backend,
            current_provider,
            expected_error_message
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

    @override_settings(FEATURES=dict(settings.FEATURES, THIRD_PARTY_AUTH_HINT='oa2-google-oauth2'))
    @ddt.data(
        'signin_user',
        'register_user',
    )
    def test_settings_tpa_hinted_login(self, url_name):
        """
        Ensure that settings.FEATURES['THIRD_PARTY_AUTH_HINT'] can set third_party_auth_hint.
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

    @override_settings(FEATURES=dict(settings.FEATURES, THIRD_PARTY_AUTH_HINT='oa2-google-oauth2'))
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

    @mock.patch('openedx.core.djangoapps.user_authn.views.login_form.enterprise_customer_for_request')
    @ddt.data(
        ('signin_user', False, None, None, False),
        ('register_user', False, None, None, False),
        ('signin_user', True, 'Fake EC', 'http://logo.com/logo.jpg', False),
        ('register_user', True, 'Fake EC', 'http://logo.com/logo.jpg', False),
        ('signin_user', True, 'Fake EC', 'http://logo.com/logo.jpg', True),
        ('register_user', True, 'Fake EC', 'http://logo.com/logo.jpg', True),
        ('signin_user', True, 'Fake EC', None, False),
        ('register_user', True, 'Fake EC', None, False),
    )
    @ddt.unpack
    def test_enterprise_register(self, url_name, ec_present, ec_name, logo_url, is_proxy, mock_get_ec):
        """
        Verify that when an EnterpriseCustomer is received on the login and register views,
        the appropriate sidebar is rendered.
        """
        if ec_present:
            mock_get_ec.return_value = {
                'name': ec_name,
                'branding_configuration': {'logo': logo_url}
            }
        else:
            mock_get_ec.return_value = None

        params = []
        if is_proxy:
            params.append(("proxy_login", "True"))

        response = self.client.get(reverse(url_name), params, HTTP_ACCEPT="text/html")

        enterprise_sidebar_div_id = 'enterprise-content-container'

        if not ec_present:
            self.assertNotContains(response, text=enterprise_sidebar_div_id)
        else:
            self.assertContains(response, text=enterprise_sidebar_div_id)
            if is_proxy:
                welcome_message = settings.ENTERPRISE_PROXY_LOGIN_WELCOME_TEMPLATE
            else:
                welcome_message = settings.ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE
            expected_message = Text(welcome_message).format(
                start_bold=HTML('<b>'),
                end_bold=HTML('</b>'),
                line_break=HTML('<br/>'),
                enterprise_name=ec_name,
                platform_name=settings.PLATFORM_NAME,
                privacy_policy_link_start=HTML("<a href='{pp_url}' rel='noopener' target='_blank'>").format(
                    pp_url=get_privacy_url()
                ),
                privacy_policy_link_end=HTML("</a>"),
            )
            self.assertContains(response, expected_message)
            if logo_url:
                self.assertContains(response, logo_url)

    def test_enterprise_cookie_delete(self):
        """
        Test that enterprise cookies are deleted in login/registration views.

        Cookies must be deleted in login/registration views so that *default* login/registration branding
        is displayed to subsequent requests from non-enterprise customers.
        """
        cookies = SimpleCookie()
        cookies[settings.ENTERPRISE_CUSTOMER_COOKIE_NAME] = 'test-enterprise-customer'
        response = self.client.get(reverse('signin_user'), HTTP_ACCEPT="text/html", cookies=cookies)

        assert settings.ENTERPRISE_CUSTOMER_COOKIE_NAME in response.cookies
        enterprise_cookie = response.cookies[settings.ENTERPRISE_CUSTOMER_COOKIE_NAME]

        assert enterprise_cookie['domain'] == settings.BASE_COOKIE_DOMAIN
        assert enterprise_cookie.value == ''

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

    def _assert_third_party_auth_data(self, response, current_backend, current_provider, providers, expected_ec,
                                      add_user_details=False):
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
            "pipeline_user_details": {"email": "test@test.com"} if add_user_details else {},
            "skipRegistrationOptionalCheckboxes": False
        }
        if expected_ec is not None:
            # If we set an EnterpriseCustomer, third-party auth providers ought to be hidden.
            auth_info['providers'] = []
        auth_info = dump_js_escaped_json(auth_info)

        expected_data = '"third_party_auth": {auth_info}'.format(  # noqa: UP032
            auth_info=auth_info
        )
        self.assertContains(response, expected_data)

    def _assert_saml_auth_data_with_error(
            self, response, current_backend, current_provider, expected_error_message
    ):
        """
        Verify that third party auth info is rendered correctly in a DOM data attribute.
        """
        finish_auth_url = None
        if current_backend:
            finish_auth_url = reverse('social:complete', kwargs={'backend': current_backend}) + '?'

        auth_info = {
            'currentProvider': current_provider,
            'platformName': settings.PLATFORM_NAME,
            'providers': [],
            'secondaryProviders': [],
            'finishAuthUrl': finish_auth_url,
            'errorMessage': expected_error_message,
            'registerFormSubmitButtonText': 'Create Account',
            'syncLearnerProfileData': False,
            'pipeline_user_details': {'response': {'idp_name': 'testshib'}},
            'skipRegistrationOptionalCheckboxes': False
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
