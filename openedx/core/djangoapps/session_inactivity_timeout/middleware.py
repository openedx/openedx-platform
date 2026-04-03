"""
Middleware to auto-expire inactive sessions after N seconds, which is configurable in
settings.

To enable this feature, set in a settings.py:

  SESSION_INACTIVITY_TIMEOUT_IN_SECS = 300

This was taken from StackOverflow (http://stackoverflow.com/questions/14830669/how-to-expire-django-session-in-5minutes)

If left unset, session expiration will be handled by Django's SESSION_COOKIE_AGE,
which defaults to 1209600 (2 weeks, in seconds).
"""


import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import auth
from django.utils.deprecation import MiddlewareMixin
from edx_django_utils import monitoring as monitoring_utils

log = logging.getLogger(__name__)

LAST_TOUCH_KEYNAME = 'SessionInactivityTimeout:last_touch_str'


class SessionInactivityTimeout(MiddlewareMixin):
    """
    Middleware class to keep track of activity on a given session
    """

    def process_request(self, request):
        """
        Standard entry point for processing requests in Django
        """
        if not hasattr(request, "user") or not request.user.is_authenticated:
            #Can't log out if not logged in
            return

        # .. setting_name: SESSION_INACTIVITY_TIMEOUT_IN_SECONDS
        # .. setting_default: None
        # .. setting_description: If set, this is used to end the session when there is no activity for N seconds.
        # .. setting_warning:  Keep in sync with SESSION_COOKIE_AGE and must be larger than SESSION_ACTIVITY_SAVE_DELAY_SECONDS.
        timeout_in_seconds = getattr(settings, "SESSION_INACTIVITY_TIMEOUT_IN_SECONDS", None)

        current_time = datetime.utcnow()
        # Do we have this feature enabled?
        if timeout_in_seconds:
            # .. setting_name: SESSION_ACTIVITY_SAVE_DELAY_SECONDS
            # .. setting_default: 900 (15 minutes in seconds)
            # .. setting_description: How often to allow a full session save (in seconds).
            #   This controls how frequently the session ID might change.
            #   A user could be inactive for almost SESSION_ACTIVITY_SAVE_DELAY_SECONDS but since their session
            #   isn't being saved during that time, their last activity timestamp isn't being updated.
            #   When they hit the inactivity timeout, it will be based on the last saved activity time.
            #   So the effective timeout could be as short as:
            #   SESSION_INACTIVITY_TIMEOUT_IN_SECONDS - SESSION_ACTIVITY_SAVE_DELAY_SECONDS.
            #   This means users might be logged out earlier than expected in some edge cases.
            # .. setting_warning:  Must be smaller than SESSION_INACTIVITY_TIMEOUT_IN_SECONDS.
            frequency_time_in_seconds = getattr(settings, "SESSION_ACTIVITY_SAVE_DELAY_SECONDS", 900)

            # Get the last time user made a request to server, which is stored in session data
            last_touch_str = request.session.get(LAST_TOUCH_KEYNAME)

            # have we stored a 'last visited' in session? NOTE: first time access after login
            # this key will not be present in the session data
            if last_touch_str:
                try:
                    last_touch = datetime.fromisoformat(last_touch_str)
                    time_since_last_activity = current_time - last_touch

                    has_exceeded_timeout_limit = time_since_last_activity > timedelta(seconds=timeout_in_seconds)

                    # .. custom_attribute_name: session_inactivity.has_exceeded_timeout_limit
                    # .. custom_attribute_description: Boolean indicating whether the user's session has exceeded the
                    #   inactivity timeout limit and should be logged out.
                    monitoring_utils.set_custom_attribute(
                        'session_inactivity.has_exceeded_timeout_limit',
                        has_exceeded_timeout_limit
                    )

                    if has_exceeded_timeout_limit:
                        del request.session[LAST_TOUCH_KEYNAME]
                        auth.logout(request)
                        return
                except (ValueError, TypeError) as e:
                    # If parsing fails, log warning and then treat as if no timestamp exists
                    log.warning("Parsing last touch time failed: %s", e)
                    # .. custom_attribute_name: session_inactivity.last_touch_error
                    # .. custom_attribute_description: Boolean. True if parsing the last activity timestamp failed for this request, indicating a session data error.
                    monitoring_utils.set_custom_attribute('session_inactivity.last_touch_error', str(e))
                    monitoring_utils.record_exception()

            else:
                # .. custom_attribute_name: session_inactivity.first_login
                # .. custom_attribute_description: Boolean. True if the user has no stored activity timestamp for this request.
                monitoring_utils.set_custom_attribute('session_inactivity.first_login', True)
                log.debug("No previous activity timestamp found (first login)")

            current_time_str = current_time.isoformat()

            has_save_delay_been_exceeded = (
                last_touch_str and
                datetime.fromisoformat(last_touch_str) + timedelta(seconds=frequency_time_in_seconds) < current_time
            )
            proceed_with_period_save = not last_touch_str or has_save_delay_been_exceeded
            # .. custom_attribute_name: session_inactivity.proceed_with_period_save
            # .. custom_attribute_description: Boolean indicating whether a session save should proceed based on the
            #   save delay frequency. True when either no previous timestamp exists (first login) or the save delay
            #   period has been exceeded since the last timestamp update.
            monitoring_utils.set_custom_attribute(
                'session_inactivity.proceed_with_period_save',
                proceed_with_period_save
            )
            if proceed_with_period_save:
                # Allow a full session save periodically
                request.session[LAST_TOUCH_KEYNAME] = current_time_str
