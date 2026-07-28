""" Views related to logout. """


import re
import urllib.parse as parse  # pylint: disable=import-error
from urllib.parse import parse_qs, urlsplit, urlunsplit  # pylint: disable=import-error

import nh3
from django.conf import settings
from django.contrib.auth import logout
from django.utils.http import urlencode
from django.views.generic import TemplateView
from oauth2_provider.models import Application

from common.djangoapps.third_party_auth import pipeline as tpa_pipeline
from openedx.core.djangoapps.safe_sessions.middleware import mark_user_change_as_expected
from openedx.core.djangoapps.user_authn.cookies import delete_logged_in_cookies
from openedx.core.djangoapps.user_authn.utils import is_safe_login_or_logout_redirect


class LogoutView(TemplateView):
    """
    Logs out user and redirects.

    The template should load iframes to log the user out of OpenID Connect services.
    See http://openid.net/specs/openid-connect-logout-1_0.html.
    """
    oauth_client_ids = []
    template_name = 'logout.html'

    # Keep track of the page to which the user should ultimately be redirected.
    default_target = '/'
    tpa_logout_url = ''

    def post(self, request, *args, **kwargs):
        """
        Proxy to the GET handler.

        TODO: remove GET as an allowed method, and update all callers to use POST.
        """
        return self.get(request, *args, **kwargs)

    @property
    def target(self):
        """
        If a redirect_url is specified in the querystring for this request, and the value is a safe
        url for redirect, the view will redirect to this page after rendering the template.
        If it is not specified, we will use the default target url.
        Redirect to tpa_logout_url if TPA_AUTOMATIC_LOGOUT_ENABLED is set to True and if
        tpa_logout_url is configured.
        """

        if getattr(settings, 'TPA_AUTOMATIC_LOGOUT_ENABLED', False) and self.tpa_logout_url:
            return self.tpa_logout_url

        target_url = self.request.GET.get('redirect_url') or self.request.GET.get('next')

        #  Some third party apps do not build URLs correctly and send next query param without URL-encoding, resulting
        #  all plus('+') signs interpreted as space(' ') in the process of URL-decoding
        #  for example if we hit on:
        #  >> http://example.com/logout?next=/courses/course-v1:ARTS+D1+2018_T/course/
        #  we will receive in request.GET['next']
        #  >> /courses/course-v1:ARTS D1 2018_T/course/
        #  instead of
        #  >> /courses/course-v1:ARTS+D1+2018_T/course/
        #  to handle this scenario we need to encode our URL using quote_plus and then unquote it again.
        if target_url:
            target_url = nh3.clean(parse.unquote(parse.quote_plus(target_url)))

        use_target_url = target_url and is_safe_login_or_logout_redirect(
            redirect_to=target_url,
            request_host=self.request.get_host(),
            dot_client_id=self.request.GET.get('client_id'),
            require_https=self.request.is_secure(),
        )
        return target_url if use_target_url else self.default_target

    def dispatch(self, request, *args, **kwargs):
        # We do not log here, because we have a handler registered to perform logging on successful logouts.

        # Get third party auth provider's logout url
        self.tpa_logout_url = tpa_pipeline.get_idp_logout_url_from_running_pipeline(request)

        logout(request)

        response = super().dispatch(request, *args, **kwargs)

        # Clear the cookie used by the edx.org marketing site
        delete_logged_in_cookies(response)

        mark_user_change_as_expected(None)

        return response

    def _build_logout_url(self, url):
        """
        Builds a logout URL with the `no_redirect` query string parameter.

        Args:
            url (str): IDA logout URL

        Returns:
            str
        """
        scheme, netloc, path, query_string, fragment = urlsplit(url)
        query_params = parse_qs(query_string)
        query_params['no_redirect'] = 1
        new_query_string = urlencode(query_params, doseq=True)
        return urlunsplit((scheme, netloc, path, new_query_string, fragment))

    def _is_enterprise_target(self, url):
        """
        Check if url belongs to enterprise app

        Args: url(str): url path
        """
        unquoted_url = parse.unquote_plus(parse.quote(url))
        return bool(re.match(r'^/enterprise(/handle_consent_enrollment)?/[a-z0-9\-]+/course', unquoted_url))

    def _show_tpa_logout_link(self, target, referrer):
        """
        Return Boolean value indicating if TPA logout link needs to displayed or not.
        We display TPA logout link when user has active SSO session, logout flow is
        triggered via learner portal and TPA_AUTOMATIC_LOGOUT_ENABLED toggle is False.
        Args:
            target: url of the page to land after logout
            referrer: url of the page where logout request initiated
        """
        tpa_automatic_logout_enabled = getattr(settings, 'TPA_AUTOMATIC_LOGOUT_ENABLED', False)
        if (
            bool(target == self.default_target and self.tpa_logout_url) and
            settings.LEARNER_PORTAL_URL_ROOT in referrer and
            not tpa_automatic_logout_enabled
        ):
            return True

        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create a list of URIs that must be called to log the user out of all of the IDAs.
        uris = []

        # Add the logout URIs for IDAs that the user was logged into (according to the session).  This line is specific
        # to DOP.
        uris += Application.objects.filter(client_id__in=self.oauth_client_ids,
                                           redirect_uris__isnull=False).values_list('redirect_uris', flat=True)

        # Add the extra logout URIs from settings.  This is added as a stop-gap solution for sessions that were
        # established via DOT.
        uris += settings.IDA_LOGOUT_URI_LIST

        referrer = self.request.META.get('HTTP_REFERER', '').strip('/')
        logout_uris = []

        for uri in uris:
            # Only include the logout URI if the browser didn't come from that IDA's logout endpoint originally,
            # avoiding a double-logout.
            if not referrer or (referrer and not uri.startswith(referrer)):
                logout_uris.append(self._build_logout_url(uri))

        target = self.target
        context.update({
            'target': target,
            'logout_uris': logout_uris,
            'enterprise_target': self._is_enterprise_target(target),
            'tpa_logout_url': self.tpa_logout_url,
            'show_tpa_logout_link': self._show_tpa_logout_link(target, referrer),
        })

        return context
