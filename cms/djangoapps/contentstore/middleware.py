"""
Middleware for Studio.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import resolve_url
from edx_django_utils.monitoring import set_custom_attribute


class LoginRedirectToUnauthorizedMiddleware:
    """
    Answer 401 instead of redirecting to login when the client cannot use a login page.

    Studio views are mostly protected by ``login_required``, which redirects an
    unauthenticated request to ``LOGIN_URL``. That is right for a page navigation
    and wrong for a request made by a script: the script follows the redirect,
    and with LMS SSO each such request starts an OAuth login of its own. A page
    that sends many requests at once after its Studio session has expired starts
    that many logins against one session. They overwrite each other's ``state``,
    and all but a few fail in ``social_core`` with ``AuthStateForbidden`` or
    ``AuthStateMissing``, which Studio serves as a 500.

    A 401 lets the caller start a single login instead. The test is the one
    ``SafeSessionMiddleware`` uses when it rejects a session: only a client that
    asks for HTML is redirected. Unlike there, a request with no ``Accept``
    header at all keeps the redirect, so only clients that say what they want
    are affected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if self._is_unusable_login_redirect(request, response):
            # .. custom_attribute_name: studio_login_redirect_replaced
            # .. custom_attribute_description: Set to True when a redirect to login was
            #      replaced by a 401 because the client did not ask for HTML.
            set_custom_attribute('studio_login_redirect_replaced', True)
            return HttpResponse(status=401)
        return response

    @staticmethod
    def _is_unusable_login_redirect(request, response):
        """
        Return whether the response sends an anonymous, non-HTML client to the login page.
        """
        if response.status_code != 302 or not response.has_header('Location'):
            return False

        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            return False

        accept = request.headers.get('Accept')
        if accept is None or 'text/html' in accept:
            return False

        location = urlsplit(response['Location'])
        login_url = urlsplit(resolve_url(settings.LOGIN_URL))
        return location.path == login_url.path and location.netloc in ('', login_url.netloc, request.get_host())
