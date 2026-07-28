"""
Middleware for dark-launching languages. These languages won't be used
when determining which translation to give a user based on their browser
header, but can be selected by setting the Preview Languages on the Dark
Language setting page.

This middleware must be placed before the LocaleMiddleware, but after
the SessionMiddleware.
"""


from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.utils.translation.trans_real import parse_accept_lang_header

from openedx.core.djangoapps.dark_lang import DARK_LANGUAGE_KEY
from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.lang_pref.helpers import set_language_cookie
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference

# If django 1.7 or higher is used, the right-side can be updated with new-style codes.
CHINESE_LANGUAGE_CODE_MAP = {
    # The following are the new-style language codes for chinese language
    'zh-hans': 'zh-CN',  # Chinese (Simplified),
    'zh-hans-cn': 'zh-CN',  # Chinese (Simplified, China)
    'zh-hans-sg': 'zh-CN',  # Chinese (Simplified, Singapore)
    'zh-hant': 'zh-TW',  # Chinese (Traditional)
    'zh-hant-hk': 'zh-HK',  # Chinese (Traditional, Hongkong)
    'zh-hant-mo': 'zh-TW',  # Chinese (Traditional, Macau)
    'zh-hant-tw': 'zh-TW',  # Chinese (Traditional, Taiwan)
    # The following are the old-style language codes that django does not recognize
    'zh-mo': 'zh-TW',  # Chinese (Traditional, Macau)
    'zh-sg': 'zh-CN',  # Chinese (Simplified, Singapore)
}


def _dark_parse_accept_lang_header(accept):
    """
    The use of 'zh-cn' for 'Simplified Chinese' and 'zh-tw' for 'Traditional Chinese'
    are now deprecated, as discussed here: https://code.djangoproject.com/ticket/18419.
    The new language codes 'zh-hans' and 'zh-hant' are now used since django 1.7.
    Although majority of browsers still use the old language codes, some new browsers
    such as IE11 in Windows 8.1 start to use the new ones, which makes the current
    chinese translations of edX don't work properly under these browsers.
    This function can keep compatibility between the old and new language codes. If one
    day edX uses django 1.7 or higher, this function can be modified to support the old
    language codes until there are no browsers use them.
    """
    browser_langs = parse_accept_lang_header(accept)
    django_langs = []
    for lang, priority in browser_langs:
        lang = CHINESE_LANGUAGE_CODE_MAP.get(lang.lower(), lang)
        django_langs.append((lang, priority))

    return django_langs


class DarkLangMiddleware(MiddlewareMixin):
    """
    Middleware for dark-launching languages.

    This is configured by creating ``DarkLangConfig`` rows in the database,
    using the django admin site.
    """
    @property
    def released_langs(self):
        """
        Current list of released languages
        """
        language_options = DarkLangConfig.current().released_languages_list
        if settings.LANGUAGE_CODE not in language_options:
            language_options.append(settings.LANGUAGE_CODE)
        return language_options

    @property
    def beta_langs(self):
        """
        Current list of beta languages
        """
        language_options = DarkLangConfig.current().beta_languages_list
        if settings.LANGUAGE_CODE not in language_options:
            language_options.append(settings.LANGUAGE_CODE)
        return language_options

    def process_request(self, request):
        """
        Prevent user from requesting un-released languages except by using the preview-lang query string.
        """
        if not DarkLangConfig.current().enabled:
            return

        self._clean_accept_headers(request)

    def process_response(self, request, response):
        """
        Apply user's dark lang preference as a cookie for future requests.
        """
        if DarkLangConfig.current().enabled:
            self._activate_preview_language(request, response)

        return response

    def _fuzzy_match(self, lang_code):
        """Returns a fuzzy match for lang_code"""
        match = None
        dark_lang_config = DarkLangConfig.current()

        if dark_lang_config.enable_beta_languages:
            langs = self.released_langs + self.beta_langs
        else:
            langs = self.released_langs

        if lang_code in langs:
            match = lang_code
        else:
            lang_prefix = lang_code.partition('-')[0]
            for released_lang in langs:
                released_prefix = released_lang.partition('-')[0]
                if lang_prefix == released_prefix:
                    match = released_lang
        return match

    def _clean_accept_headers(self, request):
        """
        Remove any language that is not either in ``self.released_langs`` or ``self.beta_langs`` (if enabled) or
        a territory of one of those languages.
        """
        accept = request.META.get('HTTP_ACCEPT_LANGUAGE', None)
        if accept is None or accept == '*':
            return

        new_accept = []
        for lang, priority in _dark_parse_accept_lang_header(accept):
            fuzzy_code = self._fuzzy_match(lang.lower())
            if fuzzy_code:
                # Formats lang and priority into a valid accept header fragment.
                new_accept.append(f"{fuzzy_code};q={priority}")

        new_accept = ", ".join(new_accept)

        request.META['HTTP_ACCEPT_LANGUAGE'] = new_accept

    def _activate_preview_language(self, request, response):
        """
        Check the user's dark language setting in the session and apply it
        """
        auth_user = request.user.is_authenticated
        preview_lang = None
        if auth_user:
            # Get the request user's dark lang preference
            preview_lang = get_user_preference(request.user, DARK_LANGUAGE_KEY)

        # User doesn't have a dark lang preference, so just return
        if not preview_lang:
            return

        # Set the response language_cookie to the requested preview lang
        set_language_cookie(request, response, preview_lang)
