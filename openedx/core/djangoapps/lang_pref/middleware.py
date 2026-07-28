"""
Middleware for Language Preferences
"""
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.utils.translation.trans_real import parse_accept_lang_header

from openedx.core.djangoapps.dark_lang import DARK_LANGUAGE_KEY
from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.lang_pref import LANGUAGE_HEADER, LANGUAGE_KEY
from openedx.core.djangoapps.lang_pref import helpers as lang_pref_helpers
from openedx.core.djangoapps.site_configuration.helpers import get_value
from openedx.core.djangoapps.user_api.errors import UserAPIInternalError, UserAPIRequestError
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference, set_user_preference
from openedx.core.lib.mobile_utils import is_request_from_mobile_app


class LanguagePreferenceMiddleware(MiddlewareMixin):
    """
    Middleware for user preferences.

    Ensures that, once set, a user's preferences are reflected in the page
    whenever they are logged in.
    """

    def process_request(self, request):
        """
        If a user's UserPreference contains a language preference, use the user's preference.
        Save the current language preference cookie as the user's preferred language.

        If you specify the LANGUAGE_CODE in SiteConfiguration, it will have a higher priority than the user's language
        preference.
        """
        cookie_lang = lang_pref_helpers.get_language_cookie(request)
        if cookie_lang:
            if request.user.is_authenticated:
                # DarkLangMiddleware will take care of this so don't change anything
                if DarkLangConfig.current().enabled and get_user_preference(request.user, DARK_LANGUAGE_KEY):
                    return
                set_user_preference(request.user, LANGUAGE_KEY, cookie_lang)
            else:
                request._anonymous_user_cookie_lang = cookie_lang  # pylint: disable=protected-access

            accept_header = request.META.get(LANGUAGE_HEADER, None)
            if accept_header:
                current_langs = parse_accept_lang_header(accept_header)
                # Promote the cookie_lang over any language currently in the accept header
                current_langs = [(lang, qvalue) for (lang, qvalue) in current_langs if lang != cookie_lang]
                current_langs.insert(0, (cookie_lang, 1))
                accept_header = ",".join(f"{lang};q={qvalue}" for (lang, qvalue) in current_langs)
            else:
                accept_header = cookie_lang
            request.META[LANGUAGE_HEADER] = accept_header

        # Apply language specified in SiteConfiguration, ignoring user preferences.
        if language := get_value('LANGUAGE_CODE'):
            request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = language

    def process_response(self, request, response):  # pylint: disable=missing-function-docstring
        # If the user is logged in, check for their language preference. Also check for real user
        # if current user is a masquerading user,
        user_pref = None
        current_user = None
        if hasattr(request, 'user'):
            current_user = getattr(request.user, 'real_user', request.user)

        if current_user and current_user.is_authenticated:

            # DarkLangMiddleware has already set this cookie
            if DarkLangConfig.current().enabled and get_user_preference(current_user, DARK_LANGUAGE_KEY):
                return response

            anonymous_cookie_lang = getattr(request, '_anonymous_user_cookie_lang', None)
            if anonymous_cookie_lang:
                user_pref = anonymous_cookie_lang
                set_user_preference(current_user, LANGUAGE_KEY, anonymous_cookie_lang)
            else:
                # Get the user's language preference
                try:
                    user_pref = get_user_preference(current_user, LANGUAGE_KEY)
                except (UserAPIRequestError, UserAPIInternalError):
                    # If we can't find the user preferences, then don't modify the cookie
                    pass

            # If set, set the user_pref in the LANGUAGE_COOKIE_NAME
            if user_pref and not is_request_from_mobile_app(request):
                lang_pref_helpers.set_language_cookie(request, response, user_pref)
            else:
                lang_pref_helpers.unset_language_cookie(response)

        return response
