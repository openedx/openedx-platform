"""
This is the courseware context_processor module.

This is meant to simplify the process of sending user preferences (espec. time_zone and pref-lang)
to the templates without having to append every view file.

"""
import string

from django.utils.translation import get_language
from edx_django_utils.cache import TieredCache
from pytz import timezone
from pytz.exceptions import UnknownTimeZoneError

from lms.djangoapps.courseware.models import LastSeenCoursewareTimezone
from openedx.core.djangoapps.site_configuration.helpers import get_value
from openedx.core.djangoapps.user_api.errors import UserAPIInternalError, UserNotFound
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference, get_user_preferences
from openedx.core.lib.cache_utils import get_cache

RETRIEVABLE_PREFERENCES = {
    'user_timezone': 'time_zone',
    'user_language': 'pref-lang'
}
CACHE_NAME = "context_processor.user_timezone_preferences"


def user_timezone_locale_prefs(request):
    """
    Checks if request has an authenticated user.
    If so, sends set (or none if unset) time_zone and language prefs.
    If site-wide language is set, that language is used over the language set
    in user preferences.

    This interacts with the DateUtils to either display preferred or attempt to determine
    system/browser set time_zones and languages

    """
    cached_value = get_cache(CACHE_NAME)
    if not cached_value:
        user_prefs = {
            'user_timezone': None,
            'user_language': get_language(),
        }
        if hasattr(request, 'user') and request.user.is_authenticated:
            try:
                user_preferences = get_user_preferences(request.user)
            except (UserNotFound, UserAPIInternalError):
                cached_value.update(user_prefs)
            else:
                user_prefs = {
                    key: user_preferences.get(pref_name, None)
                    for key, pref_name in RETRIEVABLE_PREFERENCES.items()
                }
        site_wide_language = get_value('LANGUAGE_CODE', None)
        if site_wide_language:
            user_prefs['user_language'] = site_wide_language

        cached_value.update(user_prefs)
    return cached_value


def get_last_seen_courseware_timezone(user):
    """
    The above method is for the timezone that is set on the user's account.
    That timezone is often not set, so this field retrieves the browser timezone
    from a recent courseware visit (updated daily)
    """
    cache_key = 'browser_timezone_{}'.format(str(user.id))
    cached_value = TieredCache.get_cached_response(cache_key)
    if not cached_value.is_found:
        try:
            LastSeenCoursewareTimezone.objects.get(user=user)
        except LastSeenCoursewareTimezone.DoesNotExist:
            return None

    else:
        return cached_value.value


def get_user_timezone_or_last_seen_timezone_or_utc(user):
    """
    Helper method for returning a reasonable timezone for a user.
    This method returns the timezone in the user's account if that is set.
    If that is not set, it returns a recent timezone that we have recorded from a user's visit to the courseware.
    If that is not set or the timezone is unknown, it returns UTC.
    """
    user_timezone = (
        get_user_preference(user, 'time_zone') or
        get_last_seen_courseware_timezone(user) or
        'UTC'
    )
    # We have seen non-printable characters (i.e. \x00) showing up in the
    # user_timezone (I believe via the get_last_seen_courseware_timezone method).
    # This sanitizes the user_timezone before passing it in.
    user_timezone = filter(lambda l: l in string.printable, user_timezone)
    user_timezone = ''.join(user_timezone)
    try:
        return timezone(user_timezone)
    except UnknownTimeZoneError as err:
        return timezone('UTC')
