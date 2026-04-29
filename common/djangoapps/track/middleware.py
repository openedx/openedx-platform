"""
This is a middleware layer which keeps a log of all requests made
to the server. It is responsible for removing security tokens and
similar from such events, and relaying them to the event tracking
framework.
"""


import hashlib
import json
import logging
import re
import sys

import six  # pylint: disable=unused-import  # noqa: F401
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from eventtracking import tracker
from ipware.ip import get_client_ip

from common.djangoapps.track import contexts, views

log = logging.getLogger(__name__)

CONTEXT_NAME = 'edx.request'
META_KEY_TO_CONTEXT_KEY = {
    'SERVER_NAME': 'host',
    'HTTP_USER_AGENT': 'agent',
    'PATH_INFO': 'path',
    # Not a typo. See:
    # http://en.wikipedia.org/wiki/HTTP_referer#Origin_of_the_term_referer
    'HTTP_REFERER': 'referer',
    'HTTP_ACCEPT_LANGUAGE': 'accept_language',
}


class TrackMiddleware(MiddlewareMixin):
    """
    Tracks all requests made, as well as setting up context for other server
    emitted events.
    """

    def process_request(self, request):  # pylint: disable=missing-function-docstring
        try:
            self.enter_request_context(request)

            if not self.should_process_request(request):
                return

            # Removes passwords from the tracking logs
            # WARNING: This list needs to be changed whenever we change
            # password handling functionality.
            #
            # As of the time of this comment, only 'password' is used
            # The rest are there for future extension.
            #
            # Passwords should never be sent as GET requests, but
            # this can happen due to older browser bugs. We censor
            # this too.
            #
            # We should manually confirm no passwords make it into log
            # files when we change this.

            censored_strings = ['password', 'newpassword', 'new_password',
                                'oldpassword', 'old_password', 'new_password1', 'new_password2']
            post_dict = dict(request.POST)
            get_dict = dict(request.GET)
            for string in censored_strings:
                if string in post_dict:
                    post_dict[string] = '*' * 8
                if string in get_dict:
                    get_dict[string] = '*' * 8

            event = {
                'GET': dict(get_dict),
                'POST': dict(post_dict),
            }

            # TODO: Confirm no large file uploads
            event = json.dumps(event)
            event = event[:512]

            views.server_track(request, request.META['PATH_INFO'], event)
        except:  # pylint: disable=bare-except
            ## Why do we have the overly broad except?
            ##
            ## I added instrumentation so if we drop events on the
            ## floor, we at least know about it. However, we really
            ## should just return a 500 here: (1) This will translate
            ## to much more insidious user-facing bugs if we make any
            ## decisions based on incorrect data.  (2) If the system
            ## is down, we should fail and fix it.
            event = {'event-type': 'exception', 'exception': repr(sys.exc_info()[0])}
            try:
                views.server_track(request, request.META['PATH_INFO'], event)
            except:  # pylint: disable=bare-except
                # At this point, things are really broken. We really
                # should fail return a 500 to the user here.  However,
                # the interim decision is to just fail in order to be
                # consistent with current policy, and expedite the PR.
                # This version of the code makes no compromises
                # relative to the code before, while a proper failure
                # here would involve shifting compromises and
                # discussion.
                pass

    def should_process_request(self, request):
        """Don't track requests to the specified URL patterns"""
        path = request.META['PATH_INFO']

        ignored_url_patterns = getattr(settings, 'TRACKING_IGNORE_URL_PATTERNS', [])
        for pattern in ignored_url_patterns:
            # Note we are explicitly relying on python's internal caching of
            # compiled regular expressions here.
            if re.match(pattern, path):
                return False
        return True

    def enter_request_context(self, request):
        """
        Extract information from the request and add it to the tracking
        context.

        The following fields are injected into the context:

        * session - The Django session key that identifies the user's session.
        * user_id - The numeric ID for the logged in user.
        * username - The username of the logged in user.
        * ip - The IP address of the client.
        * host - The "SERVER_NAME" header, which should be the name of the server running this code.
        * agent - The client browser identification string.
        * path - The path part of the requested URL.
        * client_id - The unique key used by Google Analytics to identify a user
        """
        context = {
            'session': self.get_session_key(request),
            'user_id': self.get_user_primary_key(request),
            'username': self.get_username(request),
            'ip': self.get_request_ip_address(request),
        }
        for header_name, context_key in META_KEY_TO_CONTEXT_KEY.items():
            # HTTP headers may contain Latin1 characters. Decoding using Latin1 encoding here
            # avoids encountering UnicodeDecodeError exceptions when these header strings are
            # output to tracking logs.
            context_value = request.META.get(header_name, '')
            if isinstance(context_value, bytes):
                context_value = context_value.decode('latin1')
            context[context_key] = context_value

        # Google Analytics uses the clientId to keep track of unique visitors. A GA cookie looks like
        # this: _ga=GA1.2.1033501218.1368477899. The clientId is this part: 1033501218.1368477899.
        google_analytics_cookie = request.COOKIES.get('_ga')
        if google_analytics_cookie is None:
            context['client_id'] = request.META.get('HTTP_X_EDX_GA_CLIENT_ID')
        else:
            context['client_id'] = '.'.join(google_analytics_cookie.split('.')[2:])

        context.update(contexts.course_context_from_url(request.build_absolute_uri()))

        tracker.get_tracker().enter_context(
            CONTEXT_NAME,
            context
        )

    def get_session_key(self, request):
        """
        Gets a key suitable for representing this Django session for tracking purposes.

        Returns an empty string if there is no active session.
        """
        try:
            return self.substitute_session_key(request.session.session_key)
        except AttributeError:
            # NB: This can hide a missing SECRET_KEY
            return ''

    def substitute_session_key(self, session_key):
        """
        Deterministically generate a tracking session key from the real one.

        If a session key is not provided, returns empty string.

        The tracking session ID is a 32-character hexadecimal string (matching
        Django session key format for convenience, and in case something
        downstream makes assumptions.) The tracking ID does not allow recovery
        of the original session key but will always be the same unless server
        secrets are changed, and will be unique for each session key.
        """
        if not session_key:
            return ''
        # Prevent confirmation attacks by using SECRET_KEY as a pepper (see
        # ADR: docs/decisions/0008-secret-key-usage.rst).
        # Tracking ID and session key will only be linkable
        # by someone in possession of the pepper.
        #
        # This assumes that session_key is high-entropy and unpredictable (which
        # it should be anyway.)
        #
        # Use SHAKE-128 from SHA-3 hash family to generate a hash of arbitrary
        # length.
        hasher = hashlib.shake_128()
        # This is one of several uses of SECRET_KEY.
        #
        # Impact of exposure: Could result in identifying the tracking data for
        # users if their actual session keys are already known.
        #
        # Rotation process: Can be rotated at will. Results in a one-time
        # discontinuity in tracking metrics and should be accompanied by a
        # heads-up to data researchers.
        hasher.update(settings.SECRET_KEY.encode())
        hasher.update(session_key.encode())
        return hasher.hexdigest(16)

    def get_user_primary_key(self, request):
        """Gets the primary key of the logged in Django user"""
        try:
            return request.user.pk
        except AttributeError:
            return ''

    def get_username(self, request):
        """Gets the username of the logged in Django user"""
        try:
            return request.user.username
        except AttributeError:
            return ''

    def get_request_ip_address(self, request):
        """Gets the IP address of the request"""
        ip_address = get_client_ip(request)[0]
        if ip_address is not None:
            return ip_address
        else:
            return ''

    def process_response(self, _request, response):
        """Exit the context if it exists."""
        try:
            tracker.get_tracker().exit_context(CONTEXT_NAME)
        except Exception:  # pylint: disable=broad-except
            pass

        return response
