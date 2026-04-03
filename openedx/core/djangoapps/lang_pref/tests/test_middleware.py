"""
Tests for lang_pref middleware.
"""


import itertools
from unittest import mock

import ddt
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test.client import Client, RequestFactory
from django.urls import reverse
from django.utils.translation.trans_real import parse_accept_lang_header

from common.djangoapps.student.tests.factories import AnonymousUserFactory, UserFactory
from openedx.core.djangoapps.lang_pref import COOKIE_DURATION, LANGUAGE_KEY
from openedx.core.djangoapps.lang_pref.middleware import LanguagePreferenceMiddleware
from openedx.core.djangoapps.site_configuration.tests.test_util import (
    with_site_configuration,
    with_site_configuration_context,
)
from openedx.core.djangoapps.user_api.preferences.api import (
    delete_user_preference,
    get_user_preference,
    set_user_preference,
)
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.core.lib.api.test_utils import override_settings


@ddt.ddt
# HIBP settings are only defined in lms envs but needed for common tests.
@override_settings(
    ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY=False,
    ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY=False,
    ENABLE_AUTHN_REGISTER_HIBP_POLICY=False,
    HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD=100000,
)
class TestUserPreferenceMiddleware(CacheIsolationTestCase):
    """
    Tests to make sure user preferences are getting properly set in the middleware.
    """

    def setUp(self):
        super().setUp()
        self.middleware = LanguagePreferenceMiddleware(get_response=lambda request: None)
        self.session_middleware = SessionMiddleware(get_response=lambda request: None)
        self.user = UserFactory.create()
        self.anonymous_user = AnonymousUserFactory()
        self.request = RequestFactory().get('/somewhere')
        self.request.user = self.user
        self.request.META['HTTP_ACCEPT_LANGUAGE'] = 'ar;q=1.0'
        self.session_middleware.process_request(self.request)
        self.client = Client()

    def test_logout_shouldnt_remove_cookie(self):

        self.middleware.process_request(self.request)

        self.request.user = self.anonymous_user

        response = mock.Mock(spec=HttpResponse)
        self.middleware.process_response(self.request, response)

        response.delete_cookie.assert_not_called()

    @ddt.data(None, 'es', 'en')
    def test_preference_setting_changes_cookie(self, lang_pref_out):
        """
        Test that the LANGUAGE_COOKIE_NAME is always set to the user's current language preferences
        at the end of the request, with an expiry that's the same as the users current session cookie.
        """
        if lang_pref_out:
            set_user_preference(self.user, LANGUAGE_KEY, lang_pref_out)
        else:
            delete_user_preference(self.user, LANGUAGE_KEY)

        response = mock.Mock(spec=HttpResponse)
        self.middleware.process_response(self.request, response)

        if lang_pref_out:
            response.set_cookie.assert_called_with(
                settings.LANGUAGE_COOKIE_NAME,
                value=lang_pref_out,
                domain=settings.SESSION_COOKIE_DOMAIN,
                max_age=COOKIE_DURATION,
                secure=self.request.is_secure(),
                samesite="Lax"
            )
        else:
            response.delete_cookie.assert_called_with(
                settings.LANGUAGE_COOKIE_NAME,
                domain=settings.SESSION_COOKIE_DOMAIN,
            )

    @ddt.data(*itertools.product(
        (None, 'eo', 'es'),  # LANGUAGE_COOKIE_NAME
        (None, 'es', 'en'),  # Language Preference In
    ))
    @ddt.unpack
    @mock.patch('openedx.core.djangoapps.lang_pref.middleware.set_user_preference')
    def test_preference_cookie_changes_setting(self, lang_cookie, lang_pref_in, mock_set_user_preference):
        self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = lang_cookie

        if lang_pref_in:
            set_user_preference(self.user, LANGUAGE_KEY, lang_pref_in)
        else:
            delete_user_preference(self.user, LANGUAGE_KEY)

        self.middleware.process_request(self.request)

        if lang_cookie is None:
            assert mock_set_user_preference.mock_calls == []
        else:
            mock_set_user_preference.assert_called_with(self.user, LANGUAGE_KEY, lang_cookie)

    @ddt.data(*(
        (logged_in, ) + test_def
        for logged_in in (True, False)
        for test_def in [
            # (LANGUAGE_COOKIE_NAME, Accept-Language In, Accept-Language Out)
            (None, None, None),
            (None, None, None),
            (None, None, None),
            (None, 'en', 'en'),
            (None, 'en', 'en'),
            ('en', None, 'en'),
            ('en', None, 'en'),
            ('en', 'eo', 'en;q=1.0,eo'),
            ('en', 'en', 'en'),
            ('en', 'en', 'en'),
            ('en', 'eo', 'en;q=1.0,eo')
        ]
    ))
    @ddt.unpack
    def test_preference_cookie_overrides_browser(self, logged_in, lang_cookie, accept_lang_in, accept_lang_out):
        if not logged_in:
            self.request.user = self.anonymous_user
        if lang_cookie:
            self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = lang_cookie
        if accept_lang_in:
            self.request.META['HTTP_ACCEPT_LANGUAGE'] = accept_lang_in
        else:
            del self.request.META['HTTP_ACCEPT_LANGUAGE']

        self.middleware.process_request(self.request)

        accept_lang_result = self.request.META.get('HTTP_ACCEPT_LANGUAGE')
        if accept_lang_result:
            accept_lang_result = parse_accept_lang_header(accept_lang_result)

        if accept_lang_out:
            accept_lang_out = parse_accept_lang_header(accept_lang_out)

        if accept_lang_out and accept_lang_result:
            self.assertCountEqual(accept_lang_result, accept_lang_out)
        else:
            assert accept_lang_result == accept_lang_out

    @ddt.data(None, 'es', 'en')
    def test_logout_preserves_cookie(self, lang_cookie):
        if lang_cookie:
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang_cookie
        elif settings.LANGUAGE_COOKIE_NAME in self.client.cookies:
            del self.client.cookies[settings.LANGUAGE_COOKIE_NAME]
        # Use an actual call to the logout endpoint, because the logout function
        # explicitly clears all cookies
        self.client.get(reverse('logout'))
        if lang_cookie:
            assert self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value == lang_cookie
        else:
            assert settings.LANGUAGE_COOKIE_NAME not in self.client.cookies

    @ddt.data(
        (None, None),
        ('es', 'es-419'),
        ('en', 'en'),
        ('es-419', 'es-419')
    )
    @ddt.unpack
    def test_login_captures_lang_pref(self, lang_cookie, expected_lang):
        if lang_cookie:
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang_cookie
        elif settings.LANGUAGE_COOKIE_NAME in self.client.cookies:
            del self.client.cookies[settings.LANGUAGE_COOKIE_NAME]

        # Use an actual call to the login endpoint, to validate that the middleware
        # stack does the right thing
        response = self.client.post(
            reverse('user_api_login_session', kwargs={'api_version': 'v1'}),
            data={
                'email': self.user.email,
                'password': UserFactory._DEFAULT_PASSWORD,  # pylint: disable=protected-access
                'remember': True,
            }
        )

        assert response.status_code == 200

        if lang_cookie:
            assert response['Content-Language'] == expected_lang
            assert get_user_preference(self.user, LANGUAGE_KEY) == lang_cookie
            assert self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value == lang_cookie
        else:
            assert response['Content-Language'] == 'en'
            assert get_user_preference(self.user, LANGUAGE_KEY) is None
            assert self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value == ''

    def test_process_response_no_user_noop(self):
        del self.request.user
        response = mock.Mock(spec=HttpResponse)

        result = self.middleware.process_response(self.request, response)

        assert result is response
        assert not response.mock_calls

    def test_preference_update_noop(self):
        self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = 'es'

        # No preference yet, should write to the database

        assert get_user_preference(self.user, LANGUAGE_KEY) is None
        self.middleware.process_request(self.request)
        assert get_user_preference(self.user, LANGUAGE_KEY) == 'es'

        response = mock.Mock(spec=HttpResponse)

        with self.assertNumQueries(1):
            self.middleware.process_response(self.request, response)

        # Preference is the same as the cookie, shouldn't write to the database

        with self.assertNumQueries(2):
            self.middleware.process_request(self.request)

        assert get_user_preference(self.user, LANGUAGE_KEY) == 'es'

        response = mock.Mock(spec=HttpResponse)

        with self.assertNumQueries(1):
            self.middleware.process_response(self.request, response)

        # Cookie changed, should write to the database again

        self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = 'en'
        self.middleware.process_request(self.request)
        assert get_user_preference(self.user, LANGUAGE_KEY) == 'en'

        with self.assertNumQueries(1):
            self.middleware.process_response(self.request, response)

    @mock.patch('openedx.core.djangoapps.lang_pref.middleware.is_request_from_mobile_app')
    @mock.patch('openedx.core.djangoapps.lang_pref.middleware.get_user_preference')
    def test_remove_lang_cookie_for_mobile_app(self, mock_get_user_preference, mock_is_mobile_request):
        """
        Test to verify language preference cookie removed for mobile app requests.
        """
        mock_get_user_preference.return_value = 'en'
        mock_is_mobile_request.return_value = False
        response = mock.Mock(spec=HttpResponse)

        response = self.middleware.process_response(self.request, response)
        response.delete_cookie.assert_not_called()
        response.set_cookie.assert_called()

        mock_is_mobile_request.return_value = True
        response = self.middleware.process_response(self.request, response)
        response.delete_cookie.assert_called()

    @with_site_configuration(configuration={'LANGUAGE_CODE': 'eo'})
    @ddt.data(None, 'es', 'en')
    def test_site_language_ignores_user_preferences(self, user_preference):
        """
        Test that the language set in SiteConfiguration has a higher priority than user preferences.
        It also does not create or update user preferences.
        """
        self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = user_preference
        self.middleware.process_request(self.request)

        # Do not alter user preferences.
        assert get_user_preference(self.user, LANGUAGE_KEY) == user_preference
        # Change the request's cookie instead.
        assert self.request.COOKIES[settings.LANGUAGE_COOKIE_NAME] == 'eo'

        # Use an actual call to determine the language of the response.
        response = self.client.get('/')

        assert get_user_preference(self.user, LANGUAGE_KEY) == user_preference
        assert response['Content-Language'] == 'eo'
        # Clean up by making a request to a Site without specific configuration.
        with with_site_configuration_context():
            self.client.get('/')
