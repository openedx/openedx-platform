"""
Tests of DarkLangMiddleware
"""


import unittest
from unittest.mock import Mock

import ddt
from django.conf import settings
from django.http import HttpRequest
from django.test.client import Client

from openedx.core.djangoapps.dark_lang.middleware import DarkLangMiddleware
from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.site_configuration.tests.test_util import (
    with_site_configuration,
    with_site_configuration_context,
)
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from common.djangoapps.student.tests.factories import UserFactory

UNSET = object()


def set_if_set(dct, key, value):
    """
    Sets ``key`` in ``dct`` to ``value``
    unless ``value`` is ``UNSET``
    """
    if value is not UNSET:
        dct[key] = value


@ddt.ddt
class DarkLangMiddlewareTests(CacheIsolationTestCase):
    """
    Tests of DarkLangMiddleware
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.build(username='test', email='test@edx.org', password='test_password')
        self.user.save()
        self.client = Client()
        self.client.login(username=self.user.username, password='test_password')
        DarkLangConfig(
            released_languages='rel',
            changed_by=self.user,
            enabled=True
        ).save()

    def process_middleware_request(self, cookie_language=UNSET, accept=UNSET):
        """
        Build a request and then process it using the ``DarkLangMiddleware``.

        Args:
            cookie_language (str): The language code to set in self.client.cookies[LANGUAGE_COOKIE_NAME]
            accept (str): The accept header to set in request.META['HTTP_ACCEPT_LANGUAGE']
        """
        meta = {}
        set_if_set(meta, 'HTTP_ACCEPT_LANGUAGE', accept)
        # Setting language cookie
        set_if_set(self.client.cookies, settings.LANGUAGE_COOKIE_NAME, cookie_language)

        request = Mock(
            spec=HttpRequest,
            session={},
            META=meta,
            GET={},
            method='GET',
            user=self.user
        )

        # Process it through the Middleware to ensure the language is available as expected.
        assert DarkLangMiddleware(get_response=lambda request: None).process_request(request) is None
        return request

    def assertAcceptEquals(self, value, request):
        """
        Assert that the HTML_ACCEPT_LANGUAGE header in request
        is equal to value
        """
        assert value == request.META.get('HTTP_ACCEPT_LANGUAGE', UNSET)

    def test_empty_accept(self):
        self.assertAcceptEquals(UNSET, self.process_middleware_request())

    def test_wildcard_accept(self):
        self.assertAcceptEquals('*', self.process_middleware_request(accept='*'))

    def test_malformed_accept(self):
        self.assertAcceptEquals('', self.process_middleware_request(accept='xxxxxxxxxxxx'))
        self.assertAcceptEquals('', self.process_middleware_request(accept='en;q=1.0, es-419:q-0.8'))

    def test_released_accept(self):
        self.assertAcceptEquals(
            'rel;q=1.0',
            self.process_middleware_request(accept='rel;q=1.0')
        )

    def test_unreleased_accept(self):
        self.assertAcceptEquals(
            'rel;q=1.0',
            self.process_middleware_request(accept='rel;q=1.0, unrel;q=0.5')
        )

    def test_accept_with_syslang(self):
        self.assertAcceptEquals(
            'en;q=1.0, rel;q=0.8',
            self.process_middleware_request(accept='en;q=1.0, rel;q=0.8, unrel;q=0.5')
        )

    def test_accept_multiple_released_langs(self):
        DarkLangConfig(
            released_languages=('rel, unrel'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            'rel;q=1.0, unrel;q=0.5',
            self.process_middleware_request(accept='rel;q=1.0, unrel;q=0.5')
        )

        self.assertAcceptEquals(
            'rel;q=1.0, unrel;q=0.5',
            self.process_middleware_request(accept='rel;q=1.0, notrel;q=0.3, unrel;q=0.5')
        )

        self.assertAcceptEquals(
            'rel;q=1.0, unrel;q=0.5',
            self.process_middleware_request(accept='notrel;q=0.3, rel;q=1.0, unrel;q=0.5')
        )

    def test_accept_released_territory(self):
        # We will munge 'rel-ter' to be 'rel', so the 'rel-ter'
        # user will actually receive the released language 'rel'
        # (Otherwise, the user will actually end up getting the server default)
        self.assertAcceptEquals(
            'rel;q=1.0, rel;q=0.5',
            self.process_middleware_request(accept='rel-ter;q=1.0, rel;q=0.5')
        )

    def test_accept_mixed_case(self):
        self.assertAcceptEquals(
            'rel;q=1.0, rel;q=0.5',
            self.process_middleware_request(accept='rel-TER;q=1.0, REL;q=0.5')
        )

        DarkLangConfig(
            released_languages='REL-TER',
            changed_by=self.user,
            enabled=True
        ).save()

        # Since we have only released "rel-ter", the requested code "rel" will
        # fuzzy match to "rel-ter", in addition to "rel-ter" exact matching "rel-ter"
        self.assertAcceptEquals(
            'rel-ter;q=1.0, rel-ter;q=0.5',
            self.process_middleware_request(accept='rel-ter;q=1.0, rel;q=0.5')
        )

    @ddt.data(
        ('es;q=1.0, pt;q=0.5', 'es-419;q=1.0'),  # 'es' should get 'es-419', not English
        ('es-AR;q=1.0, pt;q=0.5', 'es-419;q=1.0'),  # 'es-AR' should get 'es-419', not English
    )
    @ddt.unpack
    def test_partial_match_es419(self, accept_header, expected):
        # Release es-419
        DarkLangConfig(
            released_languages=('es-419, en'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            expected,
            self.process_middleware_request(accept=accept_header)
        )

    def test_partial_match_esar_es(self):
        # If I release 'es', 'es-AR' should get 'es', not English
        DarkLangConfig(
            released_languages=('es, en'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            'es;q=1.0',
            self.process_middleware_request(accept='es-AR;q=1.0, pt;q=0.5')
        )

    @ddt.data(
        # Test condition: If I release 'es-419, es, es-es'...
        ('es;q=1.0, pt;q=0.5', 'es;q=1.0'),          # 1. es should get es
        ('es-419;q=1.0, pt;q=0.5', 'es-419;q=1.0'),  # 2. es-419 should get es-419
        ('es-es;q=1.0, pt;q=0.5', 'es-es;q=1.0'),    # 3. es-es should get es-es
    )
    @ddt.unpack
    def test_exact_match_gets_priority(self, accept_header, expected):
        # Release 'es-419, es, es-es'
        DarkLangConfig(
            released_languages=('es-419, es, es-es'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            expected,
            self.process_middleware_request(accept=accept_header)
        )

    @unittest.skip("This won't work until fallback is implemented for LA country codes. See LOC-86")
    @ddt.data(
        'es-AR',  # Argentina
        'es-PY',  # Paraguay
    )
    def test_partial_match_es_la(self, latin_america_code):
        # We need to figure out the best way to implement this. There are a ton of LA country
        # codes that ought to fall back to 'es-419' rather than 'es-es'.
        # http://unstats.un.org/unsd/methods/m49/m49regin.htm#americas
        # If I release 'es, es-419'
        # Latin American codes should get es-419
        DarkLangConfig(
            released_languages=('es, es-419'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            'es-419;q=1.0',
            self.process_middleware_request(accept=b'{};q=1.0, pt;q=0.5'.format(latin_america_code))  # pylint:disable=no-member
        )

    def _set_client_cookie_language(self, cookie_language):
        """
        Set the cookie language in the Client
        """
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie_language

    def assert_cookie_lang_equals(self, value):
        """
        Assert that the language set in cookies is equal to value
        """
        lang_cookie = self.client.cookies.get(settings.LANGUAGE_COOKIE_NAME)
        lang_cookie_value = lang_cookie.value if lang_cookie is not None and lang_cookie.value != '' else UNSET
        assert value == lang_cookie_value

    def _post_set_preview_lang(self, preview_language):
        """
        Sends a post request to set the preview language
        """
        # @@TODO make this call set_user_preference,
        # and then have a small separate LMS-only test class just to call the
        # POST and ensure it sets the user preference.
        return self.client.post('/update_lang/', {'preview_language': preview_language, 'action': 'set_preview_language'})  # lint-amnesty, pylint: disable=line-too-long

    def _post_clear_preview_lang(self):
        """
        Sends a post request to Clear the preview language
        """
        return self.client.post('/update_lang/', {'action': 'reset_preview_language'})

    def test_preview_lang_with_released_language(self):
        # Preview lang should always override selection
        self._post_set_preview_lang('rel')
        # Refresh the page with a get request to confirm the preview language was set
        self.client.get('/home')
        self.assert_cookie_lang_equals('rel')

        # Set the session language and ensure that the preview language overrides
        self._set_client_cookie_language('notrel')
        self._post_set_preview_lang('rel')
        self.client.get('/home')
        self.assert_cookie_lang_equals('rel')

    def test_preview_lang_with_dark_language(self):
        self._post_set_preview_lang('unrel')
        self.client.get('/home')
        self.assert_cookie_lang_equals('unrel')

        # Test a clear and then a set of the preview language
        self._post_clear_preview_lang()
        self._post_set_preview_lang('unrel')
        self.client.get('/home')
        self.assert_cookie_lang_equals('unrel')

    def test_empty_preview_language(self):
        # When posting an empty preview_language the currently set language should not change
        self._set_client_cookie_language('rel')
        self._post_set_preview_lang(' ')
        self.client.get('/home')
        self.assert_cookie_lang_equals('rel')

    def test_clear_lang(self):
        # Clear a language when no language was set
        self._post_clear_preview_lang()
        self.client.get('/home')
        self.assert_cookie_lang_equals(UNSET)

        # Set a language and clear it to ensure the clear is working as expected
        self._post_set_preview_lang('notclear')
        self.assert_cookie_lang_equals('notclear')
        self._post_clear_preview_lang()
        self.client.get('/home')
        self.assert_cookie_lang_equals(UNSET)

    def test_disabled(self):
        DarkLangConfig(enabled=False, changed_by=self.user).save()

        self.assertAcceptEquals(
            'notrel;q=0.3, rel;q=1.0, unrel;q=0.5',
            self.process_middleware_request(accept='notrel;q=0.3, rel;q=1.0, unrel;q=0.5')
        )

        # With DarkLang disabled the clear should not change the session language
        self._set_client_cookie_language('rel')
        self._post_clear_preview_lang()
        self.client.get('/home')
        self.assert_cookie_lang_equals('rel')

        # Test that setting the preview language with DarkLang disabled does nothing
        self._set_client_cookie_language('unrel')
        self._post_set_preview_lang('rel')
        self.client.get('/home')
        self.assert_cookie_lang_equals('unrel')

    def test_accept_chinese_language_codes(self):
        DarkLangConfig(
            released_languages=('zh-cn, zh-hk, zh-tw'),
            changed_by=self.user,
            enabled=True
        ).save()

        self.assertAcceptEquals(
            'zh-cn;q=1.0, zh-tw;q=0.5, zh-hk;q=0.3',
            self.process_middleware_request(accept='zh-Hans;q=1.0, zh-Hant-TW;q=0.5, zh-HK;q=0.3')
        )

    def test_language_cookie_is_set(self):
        site_lang = settings.LANGUAGE_CODE
        url = '/dashboard'

        response = self.client.get(url)
        assert response.cookies.get(settings.LANGUAGE_COOKIE_NAME).value == ''
        assert response['Content-Language'] == site_lang

        # Set preview language
        self._post_set_preview_lang("es-419")

        # Check if view has cookies and language set to desired preview language
        response = self.client.get(url)
        assert settings.LANGUAGE_COOKIE_NAME in response.cookies
        assert response.cookies.get(settings.LANGUAGE_COOKIE_NAME).value == 'es-419'
        assert response['Content-Language'] == 'es-419'

        # Change preview language
        self._post_set_preview_lang("eo")

        # Check if view has cookies and language set to desired preview language
        response = self.client.get(url)
        assert settings.LANGUAGE_COOKIE_NAME in response.cookies
        assert response.cookies.get(settings.LANGUAGE_COOKIE_NAME).value == 'eo'
        assert response['Content-Language'] == 'eo'

        # Reset preview language
        self._post_clear_preview_lang()

        # Check if view has cookies and language set to default language
        response = self.client.get(url)
        assert settings.LANGUAGE_COOKIE_NAME in response.cookies
        assert response.cookies.get(settings.LANGUAGE_COOKIE_NAME).value == ''
        assert response['Content-Language'] == site_lang

    @with_site_configuration(configuration={'LANGUAGE_CODE': 'es'})
    def test_preview_language_ignores_site_configuration(self):
        """
        Test that the preview language has a higher priority than the language set in SiteConfiguration.
        """
        response = self.client.get('/')
        assert response['Content-Language'] == 'es-419'

        # Set preview language.
        self._post_set_preview_lang('eo')
        response = self.client.get('/')
        assert response['Content-Language'] == 'eo'

        # Reset preview language.
        self._post_clear_preview_lang()
        response = self.client.get('/')
        assert response['Content-Language'] == 'es-419'

        # Clean up by making a request to a Site without specific configuration.
        with with_site_configuration_context():
            self.client.get('/')
