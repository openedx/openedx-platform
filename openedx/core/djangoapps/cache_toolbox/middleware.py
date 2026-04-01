"""
Cache-backed ``AuthenticationMiddleware``
-----------------------------------------

``CacheBackedAuthenticationMiddleware`` is an
``django.contrib.auth.middleware.AuthenticationMiddleware`` replacement to
avoid querying the database for a ``User`` instance in each request.

Whilst the built-in ``AuthenticationMiddleware`` mechanism will only obtain the
``User`` instance when it is required, the vast majority of sites will do so on
every page to render "Logged in as 'X'" text as well to evaluate the result of
``user.is_authenticated`` and ``user.is_superuser`` to provide conditional
functionality.

This middleware eliminates the cost of retrieving this ``User`` instance by
caching it using the ``cache_toolbox`` instance caching mechanisms.

Depending on your average number of queries per page, saving one query per
request can---in aggregate---reduce load on your database. In addition,
avoiding the database entirely for pages can avoid incurring any connection
latency in your environment, resulting in faster page loads for your users.

Saving this data in the cache can also be used as a way of authenticating users
in systems outside of Django that should not access your database.  For
example, a "maintenance mode" page would be able to render a personalised
message without touching the database at all but rather authenticating via the
cache.

``CacheBackedAuthenticationMiddleware`` is ``AUTHENTICATION_BACKENDS`` agnostic.

Implementation
~~~~~~~~~~~~~~

The cache and session backends are still accessed on each request - we are
simply assuming that they are cheaper (or otherwise more preferable) to access
than your database. (In the future, signed cookies may allow us to avoid this
lookup altogether -- whilst we could not safely save ``User.password`` in a
cookie, we could use delayed loading to pull it out when needed.)

Another alternative solution would be to store the attributes in the user's
session instead of in the cache. This would save the cache hit on every request
as all the relevant data would be pulled in one go from the session backend.
However, this has two main disadvantages:

 * Session keys are not deterministic -- after making changes to an
   ``auth_user`` row in the database, you cannot determine the user's session
   key to flush the now out-of-sync data (and doing so would log them out
   anyway).

 * Stores data per-session rather than per-user -- if a user logs in from
   multiple computers the data is duplicated in each session. This problem is
   compounded by most projects wishing to avoid expiring session data as long
   as possible (in addition to storing sessions in persistent stores).

Dependency with SafeSessionMiddleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CacheBackedAuthenticationMiddleware middleware logs out the user if the
session hash is changed due to password change. It flushes the session
and mark cookies for deletion in request which are then deleted in the
process_response of SafeSessionMiddleware.

Usage
~~~~~

To use, find ``MIDDLEWARE`` in your ``settings.py`` and replace::

    MIDDLEWARE = [
        ...
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        ...
    ]

with::

    MIDDLEWARE = [
        ...
        'openedx.core.djangoapps.cache_toolbox.middleware.CacheBackedAuthenticationMiddleware',
        ...
    ]

You should confirm you are using a ``SESSION_ENGINE`` that doesn't query the
database for each request. The built-in ``cached_db`` engine is the safest
choice for most environments but you may be happy with the trade-offs of the
``memcached`` backend - see the Django documentation for more details.

"""


from logging import getLogger

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.utils.crypto import constant_time_compare
from django.utils.deprecation import MiddlewareMixin
from edx_django_utils.monitoring import set_custom_attribute

from openedx.core.djangoapps.safe_sessions.middleware import SafeSessionMiddleware, _mark_cookie_for_deletion

from .model import cache_model

log = getLogger(__name__)


class CacheBackedAuthenticationMiddleware(AuthenticationMiddleware, MiddlewareMixin):
    """
    See documentation above.
    """
    def __init__(self, *args, **kwargs):
        cache_model(User)
        super().__init__(*args, **kwargs)

    def process_request(self, request):
        try:
            # Try and construct a User instance from data stored in the cache
            session_user_id = SafeSessionMiddleware.get_user_id_from_session(request)
            request.user = User.get_cached(session_user_id)  # lint-amnesty, pylint: disable=no-member
            if request.user.id != session_user_id:
                log.error(
                    "CacheBackedAuthenticationMiddleware cached user '%s' does not match requested user '%s'.",
                    request.user.id,
                    session_user_id,
                )
                # Raise an exception to fall through to the except clause below.
                raise Exception
            self._verify_session_auth(request)
        except:  # pylint: disable=bare-except
            # Fallback to constructing the User from the database.
            super().process_request(request)

    def _verify_session_auth(self, request):
        """
        Ensure that the user's session hash hasn't changed.
        """
        # Since session verification is a
        # security feature, we can turn it off when auto-auth is
        # enabled since auto-auth is highly insecure and only for
        # tests.
        auto_auth_enabled = settings.FEATURES.get('AUTOMATIC_AUTH_FOR_TESTING', False)
        if not auto_auth_enabled and hasattr(request.user, 'get_session_auth_hash'):
            session_hash = request.session.get(HASH_SESSION_KEY)
            session_hash_verified = session_hash and constant_time_compare(
                session_hash, request.user.get_session_auth_hash())

            # session hash is verified from the default algo, so skip legacy check
            if session_hash_verified:
                return

            # The session hash has changed due to a password
            # change. Log the user out.
            request.session.flush()
            request.user = AnonymousUser()
            _mark_cookie_for_deletion(request)
            set_custom_attribute('failed_session_verification', True)
