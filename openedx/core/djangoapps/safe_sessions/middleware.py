"""
This module defines SafeSessionMiddleware that makes use of a
SafeCookieData that cryptographically binds the user to the session id
in the cookie.

The primary goal is to avoid and detect situations where a session is
corrupted and the client becomes logged in as the wrong user. This
could happen via cache corruption (which we've seen before) or a
request handling bug. It's unlikely to happen again, but would be a
critical issue, so we've built in some checks to make sure the user on
the session doesn't change over the course of the session or between
the request and response phases.

The secondary goal is to improve upon Django's session handling by
including cryptographically enforced expiration.

The implementation is inspired in part by the proposal in the paper
<http://www.cse.msu.edu/~alexliu/publications/Cookie/cookie.pdf>
but deviates in a number of ways; mostly it just uses the technique
of an intermediate key for HMAC.

Note: The proposed protocol protects against replay attacks by
use of channel binding—specifically, by
incorporating the session key used in the SSL connection.  However,
this does not suit our needs since we want the ability to reuse the
same cookie over multiple browser sessions, and in any case the server
will be behind a load balancer and won't have access to the correct
SSL session information.  So instead, we mitigate
replay attacks by enforcing session cookie expiration
(via TimestampSigner) and assuming SESSION_COOKIE_SECURE (see below).

We use django's built-in Signer class, which makes use of a built-in
salted_hmac function that derives an intermediate key from the
server's SECRET_KEY, as proposed in the paper.

Note: The paper proposes deriving an intermediate key from the
session's expiration time in order to protect against volume attacks.
(Note that these hypothetical attacks would only succeed if HMAC-SHA1
were found to be weak, and there is presently no indication of this.)
However, since django does not always use an expiration time, we
instead use a random key salt to prevent volume attacks.

In fact, we actually use a specialized subclass of Signer called
TimestampSigner. This signer binds a timestamp along with the signed
data and verifies that the signature has not expired.  We do this
since django's session stores do not actually verify the expiration
of the session cookies.  Django instead relies on the browser to honor
session cookie expiration.

The resulting safe cookie data that gets stored as the value in the
session cookie is:

    version '|' session_id '|' key_salt '|' signed_hash

where signed_hash is a structure incorporating the following value and
a MAC (via TimestampSigner):

    SHA256(version '|' session_id '|' user_id '|')

TimestampSigner uses HMAC-SHA1 to derive a key from key_salt and the
server's SECRET_KEY; see django.core.signing for more details on the
structure of the output (which takes the form of colon-delimited
Base64.)

Note: We assume that the SESSION_COOKIE_SECURE setting is set to
TRUE to prevent inadvertent leakage of the session cookie to a
person-in-the-middle.  The SESSION_COOKIE_SECURE flag indicates
to the browser that the cookie should be sent only over an
SSL-protected channel.  Otherwise, a connection eavesdropper could copy
the entire cookie and use it to impersonate the victim.

Custom Attributes:
    safe_sessions.user_mismatch: 'request-response-mismatch' | 'request-session-mismatch'
        This attribute can be one of the above two values which correspond to the kind of comparison
        that failed when processing the response. See SafeSessionMiddleware._verify_user_and_log_mismatch
"""

import inspect
from hashlib import sha1, sha256
from logging import getLogger
from typing import Union

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import signing
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.crypto import get_random_string
from django.utils.deprecation import MiddlewareMixin
from edx_django_utils.cache import RequestCache
from edx_django_utils.logging import encrypt_for_log
from edx_django_utils.monitoring import set_custom_attribute
from edx_toggles.toggles import SettingToggle

from openedx.core.djangoapps.user_authn.cookies import delete_logged_in_cookies, set_logged_in_cookies
from openedx.core.lib.mobile_utils import is_request_from_mobile_app

# .. toggle_name: LOG_REQUEST_USER_CHANGES
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Turn this toggle on to provide additional debugging information in the case of a user
#      verification error. It will track anytime the `user` attribute of a request object is changed and store this
#      information on the request. This will also track the location where the change is coming from to quickly find
#      issues. If user verification fails at response time, all of the information about these
#      changes will be logged.
# .. toggle_warning: Adds some processing overhead to all requests to gather debug info. Will also double the logging
#      for failed verification checks.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2021-03-25
# .. toggle_tickets: https://openedx.atlassian.net/browse/ARCHBOM-1718
LOG_REQUEST_USER_CHANGES = getattr(settings, 'LOG_REQUEST_USER_CHANGES', False)

# .. toggle_name: LOG_REQUEST_USER_CHANGE_HEADERS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Turn this toggle on to log all request headers, for all requests, for all user ids involved in
#      any user id change detected by safe sessions. The headers will provide additional debugging information. The
#      headers will be logged for all requests up until LOG_REQUEST_USER_CHANGE_HEADERS_DURATION seconds after
#      the time of the last mismatch. The header details will be encrypted, and only available with the private key.
# .. toggle_warning: Logging headers of subsequent requests following a mismatch will only work if
#      LOG_REQUEST_USER_CHANGES is enabled and ENFORCE_SAFE_SESSIONS is disabled; otherwise, only headers of the initial
#      mismatch will be logged. Also, SAFE_SESSIONS_DEBUG_PUBLIC_KEY must be set. See
#      https://github.com/openedx/edx-platform/blob/master/common/djangoapps/util/log_sensitive.py
#      for instructions.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2021-12-22
# .. toggle_tickets: https://openedx.atlassian.net/browse/ARCHBOM-1940
LOG_REQUEST_USER_CHANGE_HEADERS = SettingToggle('LOG_REQUEST_USER_CHANGE_HEADERS', default=False)

# Duration in seconds to log user change request headers for additional requests; defaults to 5 minutes
LOG_REQUEST_USER_CHANGE_HEADERS_DURATION = getattr(settings, 'LOG_REQUEST_USER_CHANGE_HEADERS_DURATION', 300)

# .. toggle_name: ENFORCE_SAFE_SESSIONS
# .. toggle_implementation: SettingToggle
# .. toggle_default: True
# .. toggle_description: Invalidate session and response if mismatch detected.
#   That is, when the `user` attribute of the request object gets changed or
#   no longer matches the session, the session will be invalidated and the
#   response cancelled (changed to an error). This is intended as a backup
#   safety measure in case an attacker (or bug) is able to change the user
#   on a session in an unexpected way.
# .. toggle_warning: Should be disabled if debugging mismatches using the
#   LOG_REQUEST_USER_CHANGE_HEADERS toggle, otherwise series of mismatching
#   requests from the same user cannot be investigated.  Additionally, if
#   enabling for the first time, confirm that incidences of the string
#   "SafeCookieData user at request" in the logs are very rare; if they are
#   not, it is likely that there is either a bug or that a login or
#   registration endpoint needs to call ``mark_user_change_as_expected``.
# .. toggle_use_cases: opt_out
# .. toggle_creation_date: 2021-12-01
# .. toggle_tickets: https://openedx.atlassian.net/browse/ARCHBOM-1861
ENFORCE_SAFE_SESSIONS = SettingToggle('ENFORCE_SAFE_SESSIONS', default=True)

log = getLogger(__name__)

# RequestCache for conveying information from views back up to the
# middleware -- specifically, information about expected changes to
# request.user
#
# Rejected alternatives for where to place the annotation:
#
# - request object: Different request objects are presented to middlewares
#   and views, so the attribute would be lost.
# - response object: Doesn't help in cases where an exception is thrown
#   instead of a response returned. Still want to validate that users don't
#   change unexpectedly on a 404, for example.
request_cache = RequestCache(namespace="safe-sessions")


class SafeCookieError(Exception):
    """
    An exception class for safe cookie related errors.
    """
    def __init__(self, error_message):
        super().__init__(error_message)
        log.error(error_message)


class SafeCookieData:
    """
    Cookie data that cryptographically binds and timestamps the user
    to the session id.  It verifies the freshness of the cookie by
    checking its creation date using settings.SESSION_COOKIE_AGE.
    """
    CURRENT_VERSION = '1'
    SEPARATOR = "|"

    def __init__(self, version, session_id, key_salt, signature):
        """
        Arguments:
            version (string): The data model version of the safe cookie
                data that is checked for forward and backward
                compatibility.
            session_id (string): Unique and unguessable session
                identifier to which this safe cookie data is bound.
            key_salt (string): A securely generated random string that
                is used to derive an intermediate secret key for
                signing the safe cookie data to protect against volume
                attacks.
            signature (string): Cryptographically created signature
                for the safe cookie data that binds the session_id
                and its corresponding user as described at the top of
                this file.
        """
        self.version = version
        self.session_id = session_id
        self.key_salt = key_salt
        self.signature = signature

    @classmethod
    def create(cls, session_id, user_id):
        """
        Factory method for creating the cryptographically bound
        safe cookie data for the session and the user.

        Raises SafeCookieError if session_id is None.
        """
        cls._validate_cookie_params(session_id, user_id)
        safe_cookie_data = SafeCookieData(
            cls.CURRENT_VERSION,
            session_id,
            key_salt=get_random_string(12),
            signature=None,
        )
        safe_cookie_data.sign(user_id)
        return safe_cookie_data

    @classmethod
    def parse(cls, safe_cookie_string):
        """
        Factory method that parses the serialized safe cookie data,
        verifies the version, and returns the safe cookie object.

        Raises SafeCookieError if there are any issues parsing the
        safe_cookie_string.
        """
        try:
            raw_cookie_components = str(safe_cookie_string).split(cls.SEPARATOR)
            safe_cookie_data = SafeCookieData(*raw_cookie_components)
        except TypeError:
            raise SafeCookieError(  # pylint: disable=raise-missing-from  # noqa: B904
                f"SafeCookieData BWC parse error: {safe_cookie_string!r}."
            )
        if safe_cookie_data.version != cls.CURRENT_VERSION:
            raise SafeCookieError(
                "SafeCookieData version {!r} is not supported. Current version is {}.".format(  # noqa: UP032
                    safe_cookie_data.version,
                    cls.CURRENT_VERSION,
                ))
        return safe_cookie_data

    def __str__(self):
        """
        Returns a string serialization of the safe cookie data.
        """
        return self.SEPARATOR.join([self.version, self.session_id, self.key_salt, self.signature])

    def sign(self, user_id):
        """
        Signs the safe cookie data for this user using a timestamped signature
        and an intermediate key derived from key_salt and server's SECRET_KEY.
        Value under signature is the hexadecimal string from
        SHA256(version '|' session_id '|' user_id '|').
        """
        data_to_sign = self._compute_digest(user_id)
        self.signature = signing.dumps(data_to_sign, salt=self.key_salt)

    def verify(self, user_id):
        """
        Verifies the signature of this safe cookie data.
        Successful verification implies this cookie data is fresh
        (not expired) and bound to the given user.
        """
        try:
            unsigned_data = signing.loads(self.signature, salt=self.key_salt, max_age=settings.SESSION_COOKIE_AGE)
            if unsigned_data == self._compute_digest(user_id):
                return True
            log.error("SafeCookieData '%r' is not bound to user '%s'.", str(self), user_id)
        except signing.BadSignature as sig_error:
            log.error(
                "SafeCookieData signature error for cookie data {!r}: {}".format(  # pylint: disable=logging-format-interpolation  # noqa: UP032
                    str(self),
                    str(sig_error),
                )
            )
        return False

    def _compute_digest(self, user_id):
        """
        Returns SHA256(version '|' session_id '|' user_id '|') hex string.
        """
        hash_func = sha256()
        for data_item in [self.version, self.session_id, user_id]:
            hash_func.update(str(data_item).encode())
            hash_func.update(b'|')
        return hash_func.hexdigest()

    @staticmethod
    def _validate_cookie_params(session_id, user_id):
        """
        Validates the given parameters for cookie creation.

        Raises SafeCookieError if session_id is None.
        """
        # Compare against unicode(None) as well since the 'value'
        # property of a cookie automatically serializes None to a
        # string.
        if not session_id or session_id == str(None):
            # The session ID should always be valid in the cookie.
            raise SafeCookieError(
                "SafeCookieData not created due to invalid value for session_id '{}' for user_id '{}'.".format(  # noqa: UP032  # pylint: disable=line-too-long
                    session_id,
                    user_id,
                ))

        if not user_id:
            # The user ID is sometimes not set for
            # 3rd party Auth and external Auth transactions
            # as some of the session requests are made as
            # Anonymous users.
            log.debug(
                "SafeCookieData received empty user_id '%s' for session_id '%s'.",
                user_id,
                session_id,
            )


class SafeSessionMiddleware(SessionMiddleware, MiddlewareMixin):
    """
    A safer middleware implementation that uses SafeCookieData instead
    of just the session id to lookup and verify a user's session.
    """
    def process_request(self, request):
        """
        Processing the request is a multi-step process, as follows:

        Step 1. The safe_cookie_data is parsed and verified from the
        session cookie.

        Step 2. The session_id is retrieved from the safe_cookie_data
        and stored in place of the session cookie value, to be used by
        Django's Session middleware.

        Step 3. Call Django's Session Middleware to find the session
        corresponding to the session_id and to set the session in the
        request.

        Step 4. Once the session is retrieved, verify that the user
        bound in the safe_cookie_data matches the user attached to the
        server's session information. Otherwise, reject the request
        (bypass the view and return an error or redirect).

        Step 5. If all is successful, the now verified user_id is stored
        separately in the request object so it is available for another
        final verification before sending the response (in
        process_response).
        """
        # 2021-12-01: Temporary debugging attr to answer the question
        # "are browsers sometimes sending in multiple session
        # cookies?" Answer: Yes, this sometimes happens, although it
        # does not appear to cause user mismatches. (There was a theory
        # that multiple cookies might sometimes be sent in a varying
        # order.) We may still want to have the ability to monitor this
        # oddity, but as far as we can tell, it is not essential to the
        # core safe-sessions monitoring.
        try:
            set_custom_attribute(
                'safe_sessions.session_cookie_count',
                request.headers.get('Cookie', '').count(settings.SESSION_COOKIE_NAME + '=')
            )
        except:  # pylint: disable=bare-except
            pass

        cookie_data_string = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        if cookie_data_string:

            try:
                safe_cookie_data = SafeCookieData.parse(cookie_data_string)  # Step 1

            except SafeCookieError:
                # For security reasons, we don't support requests with
                # older or invalid session cookie models.
                return self._on_user_authentication_failed(request)

            else:
                request.COOKIES[settings.SESSION_COOKIE_NAME] = safe_cookie_data.session_id  # Step 2

                # Save off for debugging and logging in _verify_user_and_log_mismatch
                request.cookie_session_field = safe_cookie_data.session_id

        process_request_response = super().process_request(request)  # Step 3  # pylint: disable=assignment-from-no-return, super-with-arguments
        if process_request_response:
            # The process_request pipeline has been short circuited so
            # return the response.
            return process_request_response

        user_id = self.get_user_id_from_session(request)
        if cookie_data_string and user_id is not None:

            if safe_cookie_data.verify(user_id):  # Step 4
                request.safe_cookie_verified_user_id = user_id  # Step 5
                request.safe_cookie_verified_session_id = request.session.session_key
                if LOG_REQUEST_USER_CHANGES:
                    # Although it is non-obvious, this seems to be early enough
                    #   to track the very first setting of request.user for
                    #   real requests, but not mocked/test requests.
                    track_request_user_changes(request)
            else:
                # Return an error or redirect, and don't continue to
                # the underlying view.
                return self._on_user_authentication_failed(request)

    def process_response(self, request, response):
        """
        When creating a cookie for the response, a safe_cookie_data
        is created and put in place of the session_id in the session
        cookie.

        Also, the session cookie is deleted if prior verification failed
        or the new cookie can't be created for some reason.

        Processing the response is a multi-step process, as follows:

        Step 1. Call the parent's method to (maybe) generate the basic cookie.

        Step 2. Verify that the user marked at the time of
        process_request matches the user at this time when processing
        the response.  If not, log the error.

        Step 3. If a cookie is being sent with the response, update
        the cookie by replacing its session_id with a safe_cookie_data
        that binds the session and its corresponding user.

        Step 4. Delete the cookie, if it's marked for deletion.

        """
        response = super().process_response(request, response)  # Step 1

        user_id_in_session = self.get_user_id_from_session(request)
        user_matches = self._verify_user_and_log_mismatch(request, response, user_id_in_session)  # Step 2

        # If the user changed *unexpectedly* between the beginning and end of
        # the request (as observed by this middleware) or doesn't match the
        # user in the session object, then something is likely terribly wrong.
        # Most likely it's something benign such as a mix of authenticators
        # (session vs JWT) that have different user IDs, but that could lead
        # to various kinds of data corruption or arcane vulnerabilities. Forcing
        # a logout should fix it, at least.
        destroy_session = ENFORCE_SAFE_SESSIONS.is_enabled() and not user_matches

        if not destroy_session and not _is_cookie_marked_for_deletion(request) and _is_cookie_present(response):
            try:
                # Use the user_id marked in the session instead of the
                # one in the request in case the user is not set in the
                # request, for example during Anonymous API access.
                self.update_with_safe_session_cookie(response.cookies, user_id_in_session)  # Step 3
            except SafeCookieError:
                _mark_cookie_for_deletion(request)

        if destroy_session:
            # Destroy session in DB.
            request.session.flush()
            request.user = AnonymousUser()
            # Will mark cookie for deletion (matching session destruction), but
            # also prevents the original response from being returned. This could
            # be helpful if the mismatch is the result of some kind of attack.)
            response = self._on_user_authentication_failed(request)

        if _is_cookie_marked_for_deletion(request):
            _delete_cookie(request, response)  # Step 4

        return response

    @staticmethod
    def _on_user_authentication_failed(request):
        """
        To be called when user authentication fails when processing requests in the middleware.
        Sets a flag to delete the user's cookie and does one of the following:
        - Raises 401 for mobile requests and requests that are not specifically requesting a HTML response.
        - Redirects to login in case request expects a HTML response.
        """
        _mark_cookie_for_deletion(request)

        # Mobile apps have custom handling of authentication failures. They
        # should *not* be redirected to the website's login page.
        if is_request_from_mobile_app(request):
            set_custom_attribute("safe_sessions.auth_failure", "mobile")
            return HttpResponse(status=401)

        # only redirect to login if client is expecting html
        if 'text/html' in request.META.get('HTTP_ACCEPT', ''):
            set_custom_attribute("safe_sessions.auth_failure", "redirect_to_login")
            return redirect_to_login(request.path)
        set_custom_attribute("safe_sessions.auth_failure", "401")
        return HttpResponse(status=401)

    @staticmethod
    def _verify_user_and_log_mismatch(request, response, userid_in_session):
        """
        Logs an error if the user has changed unexpectedly.

        Other side effects:
        - Sets a variety of custom attributes for unexpected user changes with
            a 'safe_sessions.' prefix, like 'safe_sessions.session_id_changed'.
        - May log additional details for users involved in a past unexpected user change,
            if toggle is enabled. Uses the cache to track past user changes.

        Returns True if user matches in all places, False otherwise.
        """
        verify_user_results = SafeSessionMiddleware._verify_user_unchanged(request, response, userid_in_session)
        if verify_user_results['user_unchanged'] is True:
            # all is well; no unexpected user change was found

            try:

                if LOG_REQUEST_USER_CHANGE_HEADERS.is_enabled():

                    # add a session hash custom attribute for all requests to help monitoring
                    #   requests that come both before and after a mismatch
                    if hasattr(request, 'cookie_session_field'):
                        session_hash = obscure_token(request.cookie_session_field)
                        set_custom_attribute('safe_sessions.session_id_hash.parsed_cookie', session_hash)

                    # In the off chance that either userid_in_session or request.user.id could
                    #   be None while the other contains the actual user id, we'll use either.
                    user_id = userid_in_session or hasattr(request, 'user') and request.user.id
                    if user_id:
                        # log request header if this user id was involved in an earlier mismatch
                        log_request_headers = cache.get(
                            SafeSessionMiddleware._get_recent_user_change_cache_key(user_id), False
                        )
                        if log_request_headers:
                            log.info(
                                f'SafeCookieData request header for {user_id}: '
                                f'{SafeSessionMiddleware._get_encrypted_request_headers(request)}'
                            )
                            set_custom_attribute('safe_sessions.headers_logged', True)

            except BaseException as e:  # noqa: F841
                log.exception("SafeCookieData error while logging request headers.")

            return True

        # unpack results of an unexpected user change
        request_user_object_mismatch = verify_user_results['request_user_object_mismatch']
        session_user_mismatch = verify_user_results['session_user_mismatch']

        # Log accumulated information stored on request for each change of user
        extra_logs = []

        # Attach extra logging and metrics, but don't fail the request if there's a bug in here.
        try:
            response_session_id = getattr(getattr(request, 'session', None), 'session_key', None)

            # A safe-session user mismatch could be caused by the
            # wrong session being retrieved from cache. This
            # additional logging should reveal any such mismatch
            # (without revealing the actual session ID in logs).
            sessions_raw = [
                ('parsed_cookie', request.cookie_session_field),
                ('at_request', request.safe_cookie_verified_session_id),
                ('at_response', response_session_id),
            ]
            # Note that this is an ordered list of pairs, not a
            # dict, so that the output order is consistent.
            session_hashes = [(k, obscure_token(v)) for (k, v) in sessions_raw]
            session_id_changed = len(set(kv[1] for kv in sessions_raw)) > 1

            # delete old session id for security
            del request.safe_cookie_verified_session_id
            del request.cookie_session_field

            extra_logs.append('Session changed.' if session_id_changed else 'Session did not change.')

            # Allow comparing session IDs in both logs and metrics
            extra_logs.append(
                "Hash of session ID from various sources: " +
                '; '.join(f'{k}={v}' for (k, v) in session_hashes)
            )
            for source_name, id_hash in session_hashes:
                set_custom_attribute(f'safe_sessions.session_id_hash.{source_name}', id_hash)
            set_custom_attribute('safe_sessions.session_id_changed', session_id_changed)

            if hasattr(request, 'debug_user_changes'):
                extra_logs.append(
                    'An unsafe user transition was found. It either needs to be fixed or exempted.\n' +
                    '\n'.join(request.debug_user_changes)
                )

            if hasattr(request, 'user_id_list') and request.user_id_list:
                user_ids_string = ','.join(str(user_id) for user_id in request.user_id_list)
                set_custom_attribute('safe_sessions.user_id_list', user_ids_string)

                if LOG_REQUEST_USER_CHANGE_HEADERS.is_enabled():
                    # cache the fact that we should continue logging request headers for these user ids
                    #   for future requests until the cache values timeout.
                    cache_values = {
                        SafeSessionMiddleware._get_recent_user_change_cache_key(user_id): True
                        for user_id in set(request.user_id_list)
                    }
                    cache.set_many(cache_values, LOG_REQUEST_USER_CHANGE_HEADERS_DURATION)

                    extra_logs.append(
                        f'Safe session request headers: {SafeSessionMiddleware._get_encrypted_request_headers(request)}'
                    )

        except BaseException as e:  # noqa: F841
            log.exception("SafeCookieData error while computing additional logs.")

        if request_user_object_mismatch and not session_user_mismatch:
            log.warning(
                (
                    "SafeCookieData user at initial request '{}' does not match user at response time: '{}' "
                    "for request path '{}'.\n{}"
                ).format(  # pylint: disable=logging-format-interpolation
                    request.safe_cookie_verified_user_id, request.user.id, request.path, '\n'.join(extra_logs)
                ),
            )
            set_custom_attribute("safe_sessions.user_mismatch", "request-response-mismatch")
        elif session_user_mismatch and not request_user_object_mismatch:
            log.warning(
                (
                    "SafeCookieData user at initial request '{}' does not match user in session: '{}' "
                    "for request path '{}'.\n{}"
                ).format(  # pylint: disable=logging-format-interpolation
                    request.safe_cookie_verified_user_id, userid_in_session, request.path, '\n'.join(extra_logs)
                ),
            )
            set_custom_attribute("safe_sessions.user_mismatch", "request-session-mismatch")
        else:
            log.warning(
                (
                    "SafeCookieData user at initial request '{}' matches neither user in session: '{}' "
                    "nor user at response time: '{}' for request path '{}'.\n{}"
                ).format(  # pylint: disable=logging-format-interpolation
                    request.safe_cookie_verified_user_id, userid_in_session, request.user.id, request.path,
                    '\n'.join(extra_logs)
                ),
            )
            set_custom_attribute("safe_sessions.user_mismatch", "request-response-and-session-mismatch")

        return False

    @staticmethod
    def _verify_user_unchanged(request, response, userid_in_session):
        """
        Verifies that the user has not unexpectedly changed.

        Verifies that the user marked at the time of process_request
        matches both the current user in the request and the provided
        userid_in_session.

        Returns dict with the following fields:
            user_unchanged: True if user matches in all places, False otherwise.
            request_user_object_mismatch: True if the request.user is different
                now than it was on the initial request, False otherwise.
            session_user_mismatch: True if the current session user is different
                than the user in the initial request. False otherwise.

        """
        # default return value
        no_mismatch_dict = {
            'user_unchanged': True,
            'request_user_object_mismatch': False,
            'session_user_mismatch': False,
        }

        # It's expected that a small number of views may change the
        # user over the course of the request. We have exemptions for
        # the user changing to/from None, but the login view can
        # sometimes change the user from one value to another between
        # the request and response phases, specifically when the login
        # page is used during an active session.
        #

        # The relevant views set a flag to indicate the exemption.
        if request_cache.get_cached_response('expected_user_change').is_found:
            return no_mismatch_dict

        if not hasattr(request, 'safe_cookie_verified_user_id'):
            # Skip verification if request didn't come in with a session cookie
            return no_mismatch_dict

        if hasattr(request.user, 'real_user'):
            # If a view overrode the request.user with a masqueraded user, this will
            #   revert/clean-up that change during response processing.
            #   Known places this is set:
            #
            #   - lms.djangoapps.courseware.masquerade::setup_masquerade
            #   - openedx.core.djangoapps.content.learning_sequences.views::CourseOutlineView
            request.user = request.user.real_user

        # determine if the request.user is different now than it was on the initial request
        request_user_object_mismatch = request.safe_cookie_verified_user_id != request.user.id and\
            request.user.id is not None

        # determine if the current session user is different than the user in the initial request
        session_user_mismatch = request.safe_cookie_verified_user_id != userid_in_session and\
            userid_in_session is not None

        if not (request_user_object_mismatch or session_user_mismatch):
            # Great! No mismatch.
            return no_mismatch_dict

        return {
            'user_unchanged': False,
            'request_user_object_mismatch': request_user_object_mismatch,
            'session_user_mismatch': session_user_mismatch,
        }

    @staticmethod
    def get_user_id_from_session(request):
        """
        Return the user_id stored in the session of the request.
        """
        from django.contrib.auth import _get_user_session_key
        try:
            # Django call to get the user id which is serialized in the session.
            return _get_user_session_key(request)
        except KeyError:
            return None

    # TODO move to test code, maybe rename
    @staticmethod
    def set_user_id_in_session(request, user):
        """
        Stores the user_id in the session of the request.
        Used by unit tests.
        """
        # Django's request.session[SESSION_KEY] should contain the user serialized to a string.
        #   This is different from request.session.session_key, which holds the session id.
        request.session[SESSION_KEY] = user._meta.pk.value_to_string(user)

    @staticmethod
    def update_with_safe_session_cookie(cookies, user_id):
        """
        Replaces the session_id in the session cookie with a freshly
        computed safe_cookie_data.
        """
        # Create safe cookie data that binds the user with the session
        # in place of just storing the session_key in the cookie.
        safe_cookie_data = SafeCookieData.create(
            cookies[settings.SESSION_COOKIE_NAME].value,
            user_id,
        )

        # Update the cookie's value with the safe_cookie_data.
        cookies[settings.SESSION_COOKIE_NAME] = str(safe_cookie_data)

    @staticmethod
    def _get_recent_user_change_cache_key(user_id):
        """ Get cache key for flagging a recent mismatch for the provided user id. """
        return f"safe_sessions.middleware.recent_user_change_detected_{user_id}"

    @staticmethod
    def _get_encrypted_request_headers(request):
        """
        Return an encrypted version of the request headers preformatted for logging.

        See encrypt_for_log documentation for how to read using private key.
        """
        # NOTE: request.headers seems to pick up initial values, but won't adjust as the request object is edited.
        #   For example, the session cookie will likely be the safe session version.
        return encrypt_for_log(str(request.headers), getattr(settings, 'SAFE_SESSIONS_DEBUG_PUBLIC_KEY', None))


class EmailChangeMiddleware(MiddlewareMixin):
    """
    Middleware responsible for performing the following
    jobs on detecting an email change
    1) It will update the session's email and update the JWT cookie
        to match the new email.
    2) It will invalidate any future session on other browsers where
        the user's email does not match its session email.

    This middleware ensures that the sessions in other browsers
    are invalidated when a user changes their email in one browser.
    The active session in which the email change is made will remain valid.

    The user's email is stored in their session and JWT cookies during login
    and gets updated when the user changes their email.
    This middleware checks for any mismatch between the stored email
    and the current user's email in each request, and if found,
    it invalidates/flushes the session and mark cookies for deletion in request
    which are then deleted in the process_response of SafeSessionMiddleware.
    """

    def process_request(self, request):
        """
        Invalidate the user session if there's a mismatch
        between the email in the user's session and request.user.email.
        """
        if request.user.is_authenticated:
            user_session_email = request.session.get('email', None)
            are_emails_mismatched = user_session_email is not None and request.user.email != user_session_email
            EmailChangeMiddleware._set_session_email_match_custom_attributes(are_emails_mismatched)
            if settings.ENFORCE_SESSION_EMAIL_MATCH and are_emails_mismatched:
                # Flush the session and mark cookies for deletion.
                log.info(
                    f'EmailChangeMiddleware invalidating session for user: {request.user.id} due to email mismatch.'
                )
                request.session.flush()
                request.user = AnonymousUser()
                _mark_cookie_for_deletion(request)

    def process_response(self, request, response):
        """
        1. Update the logged-in cookies if the email change was requested
        2. Store user's email in session if not already
        """
        if request.user.is_authenticated:
            if request.session.get('email', None) is None:
                # .. custom_attribute_name: session_with_no_email_found
                # .. custom_attribute_description: Indicates that user's email was not
                #      yet stored in the user's session.
                set_custom_attribute('session_with_no_email_found', True)
                request.session['email'] = request.user.email

            if request_cache.get_cached_response('email_change_requested').is_found:
                # Update the JWT cookies with new user email
                response = set_logged_in_cookies(request, response, request.user)

        return response

    @staticmethod
    def register_email_change(request, email):
        """
        Stores the fact that an email change happened.

        1. Sets the email in session for later comparison.
        2. Sets a request level variable to mark that the user email change was requested.
        """
        request.session['email'] = email
        request_cache.set('email_change_requested', True)

    @staticmethod
    def _set_session_email_match_custom_attributes(are_emails_mismatched):
        """
        Sets custom attributes of session_email_match
        """
        # .. custom_attribute_name: session_email_match
        # .. custom_attribute_description: Indicates whether there is a match between the
        #      email in the user's session and the current user's email in the request.
        set_custom_attribute('session_email_mismatch', are_emails_mismatched)

        # .. custom_attribute_name: is_enforce_session_email_match_enabled
        # .. custom_attribute_description: Indicates whether session email match was enforced.
        #       When enforced/enabled, it invalidates sessions in other browsers upon email change,
        #       while preserving the session validity in the browser where the email change occurs.
        set_custom_attribute('is_enforce_session_email_match_enabled', settings.ENFORCE_SESSION_EMAIL_MATCH)


def obscure_token(value: Union[str, None]) -> Union[str, None]:  # noqa: UP007
    """
    Return a short string that can be used to detect other occurrences
    of this string without revealing the original. Return None if value
    is None.

    Outputs are intended to be *transient* and should not be stored or
    compared long-term, as they are dependent on the value of
    settings.SECRET_KEY, which can be rotated at any time.

    WARNING: This code must only be used for *high-entropy inputs*
    that an attacker cannot enumerate, predict, or guess for other
    parties. In particular, it must not be used for sequential IDs or
    timestamps, since an attacker possessing the pepper could
    precompute the hashes. A non-cryptographic de-identification
    technique must be used in such cases, such as a lookup table.
    """
    if value is None:
        return None
    else:
        # Use of hashing (and in particular use of SECRET_KEY as a
        # pepper) is overkill for safe-sessions, where at worst we
        # might end up logging an occasional session ID prefix... but
        # there's very little cost in overdoing it here, especially if
        # the code ends up getting copied around.
        return sha1((settings.SECRET_KEY + value).encode()).hexdigest()[:8]


def _mark_cookie_for_deletion(request):
    """
    Updates the given request object to designate that the session
    cookie should be deleted.
    """
    request.need_to_delete_cookie = True


def _is_cookie_marked_for_deletion(request):
    """
    Returns whether the session cookie has been designated for deletion
    in the given request object.
    """
    return getattr(request, 'need_to_delete_cookie', False)


def _is_cookie_present(response):
    """
    Returns whether the session cookie is present in the response.
    """
    return bool(
        response.cookies.get(settings.SESSION_COOKIE_NAME) and  # cookie in response
        response.cookies[settings.SESSION_COOKIE_NAME].value  # cookie is not empty
    )


def _delete_cookie(request, response):
    """
    Delete session cookie, as well as related login cookies.
    """
    response.delete_cookie(
        settings.SESSION_COOKIE_NAME,
        path='/',
        domain=settings.SESSION_COOKIE_DOMAIN,
    )
    # Keep JWT cookies and others in sync with session cookie
    # (meaning, in this case, delete them too).
    delete_logged_in_cookies(response)

    # Note, there is no request.user attribute at this point.
    if hasattr(request, 'session') and hasattr(request.session, 'session_key'):
        log.warning(
            "SafeCookieData deleted session cookie for session %s",
            request.session.session_key
        )


def track_request_user_changes(request):
    """
    Instrument the request object so that we store changes to the `user` attribute for future logging
    if needed for debugging user mismatches. This is done by changing the `__class__` attribute of the request
    object to point to a new class we created on the fly which is exactly the same as the underlying request class but
    with an override for the `__setattr__` function to catch the attribute changes.
    """
    original_user = getattr(request, 'user', None)

    class SafeSessionRequestWrapper(request.__class__):
        """
        A wrapper class for the request object.
        """

        def __setattr__(self, name, value):
            nonlocal original_user
            if name == 'user':
                stack = inspect.stack()
                # Written this way in case you need more of the stack for debugging.
                location = "\n".join("%30s : %s:%d" % (t[3], t[1], t[2]) for t in stack[0:12])  # noqa: UP031

                if not hasattr(self, 'debug_user_changes'):
                    # list of string debugging info for each user change (e.g. user id, stack trace, etc.)
                    self.debug_user_changes = []  # pylint: disable=attribute-defined-outside-init
                    # list of changed user ids
                    self.user_id_list = []  # pylint: disable=attribute-defined-outside-init

                if not hasattr(request, name):
                    original_user = value
                    if hasattr(value, 'id'):
                        self.user_id_list.append(value.id)
                        self.debug_user_changes.append(
                            f"SafeCookieData: Setting for the first time: {value.id!r}\n"
                            f"{location}"
                        )
                    else:
                        self.debug_user_changes.append(
                            f"SafeCookieData: Setting for the first time, but user has no id: {value!r}\n"
                            f"{location}"
                        )
                elif value != getattr(request, name):
                    current_user = getattr(request, name)
                    if hasattr(value, 'id'):
                        self.user_id_list.append(value.id)
                        self.debug_user_changes.append(
                            f"SafeCookieData: Changing request user. "
                            f"Originally {original_user.id!r}, now {current_user.id!r} and will become {value.id!r}\n"
                            f"{location}"
                        )
                    else:
                        self.debug_user_changes.append(
                            f"SafeCookieData: Changing request user but user has no id. "
                            f"Originally {original_user!r}, now {current_user!r} and will become {value!r}\n"
                            f"{location}"
                        )

                else:
                    # Value being set but not actually changing.
                    pass
            return super().__setattr__(name, value)
    request.__class__ = SafeSessionRequestWrapper


def mark_user_change_as_expected(new_user_id):
    """
    Indicate to the safe-sessions middleware that it is expected that
    the user is changing between the request and response phase of
    the current request.

    The new_user_id may be None or an LMS user ID, and may be the same
    as the previous user ID.
    """
    request_cache.set('expected_user_change', new_user_id)
