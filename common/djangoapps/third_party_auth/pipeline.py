"""Auth pipeline definitions.

Auth pipelines handle the process of authenticating a user. They involve a
consumer system and a provider service. The general pattern is:

    1. The consumer system exposes a URL endpoint that starts the process.
    2. When a user visits that URL, the client system redirects the user to a
       page served by the provider. The user authenticates with the provider.
       The provider handles authentication failure however it wants.
    3. On success, the provider POSTs to a URL endpoint on the consumer to
       invoke the pipeline. It sends back an arbitrary payload of data about
       the user.
    4. The pipeline begins, executing each function in its stack. The stack is
       defined on django's settings object's SOCIAL_AUTH_PIPELINE. This is done
       in settings._set_global_settings.
    5. Each pipeline function is variadic. Most pipeline functions are part of
       the pythons-social-auth library; our extensions are defined below. The
       pipeline is the same no matter what provider is used.
    6. Pipeline functions can return a dict to add arguments to the function
       invoked next. They can return None if this is not necessary.
    7. Pipeline functions may be decorated with @partial.partial. This pauses
       the pipeline and serializes its state onto the request's session. When
       this is done they may redirect to other edX handlers to execute edX
       account registration/sign in code.
    8. In that code, redirecting to get_complete_url() resumes the pipeline.
       This happens by hitting a handler exposed by the consumer system.
    9. In this way, execution moves between the provider, the pipeline, and
       arbitrary consumer system code.

Gotcha alert!:

Bear in mind that when pausing and resuming a pipeline function decorated with
@partial.partial, execution resumes by re-invoking the decorated function
instead of invoking the next function in the pipeline stack. For example, if
you have a pipeline of

    A
    B
    C

with an implementation of

    @partial.partial
    def B(*args, **kwargs):
        [...]

B will be invoked twice: once when initially proceeding through the pipeline
before it is paused, and once when other code finishes and the pipeline
resumes. Consequently, many decorated functions will first invoke a predicate
to determine if they are in their first or second execution (usually by
checking side-effects from the first run).

This is surprising but important behavior, since it allows a single function in
the pipeline to consolidate all the operations needed to establish invariants
rather than spreading them across two functions in the pipeline.

See https://python-social-auth.readthedocs.io/en/latest/pipeline.html for more docs.
"""


import base64
import hashlib
import hmac
import json
from collections import OrderedDict
from logging import getLogger
from smtplib import SMTPException
from uuid import uuid4
from random import randint

import six
import social_django
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.auth import logout, REDIRECT_FIELD_NAME
from django.core.mail.message import EmailMessage
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from edx_django_utils.monitoring import set_custom_attribute
from social_core.exceptions import AuthException
from social_core.pipeline import partial
from social_core.utils import module_member, slugify

from common.djangoapps import third_party_auth
from common.djangoapps.edxmako.shortcuts import render_to_string
from lms.djangoapps.verify_student.models import SSOVerification
from lms.djangoapps.verify_student.utils import earliest_allowed_verification_date
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.accounts.utils import username_suffix_generator
from openedx.core.djangoapps.user_authn import cookies as user_authn_cookies
from openedx.core.djangoapps.user_authn.toggles import is_auto_generated_username_enabled
from openedx.core.djangoapps.user_authn.utils import is_safe_login_or_logout_redirect
from common.djangoapps.third_party_auth.utils import (
    get_associated_user_by_email_response,
    get_user_from_email,
    is_enterprise_customer_user,
    is_oauth_provider,
    is_saml_provider,
    user_exists,
)
from common.djangoapps.track import segment
from common.djangoapps.util.json_request import JsonResponse

from . import provider

# These are the query string params you can pass
# to the URL that starts the authentication process.
#
# `AUTH_ENTRY_KEY` is required and indicates how the user
# enters the authentication process.
#
# `AUTH_REDIRECT_KEY` provides an optional URL to redirect
# to upon successful authentication
# (if not provided, defaults to `_SOCIAL_AUTH_LOGIN_REDIRECT_URL`)
AUTH_ENTRY_KEY = 'auth_entry'
AUTH_REDIRECT_KEY = 'next'


# The following are various possible values for the AUTH_ENTRY_KEY.
AUTH_ENTRY_LOGIN = 'login'
AUTH_ENTRY_REGISTER = 'register'
AUTH_ENTRY_ACCOUNT_SETTINGS = 'account_settings'

# Entry modes into the authentication process by a remote API call (as opposed to a browser session).
AUTH_ENTRY_LOGIN_API = 'login_api'
AUTH_ENTRY_REGISTER_API = 'register_api'

# AUTH_ENTRY_CUSTOM: Custom auth entry point for post-auth integrations.
# This should be a dict where the key is a word passed via ?auth_entry=, and the
# value is a dict with an arbitrary 'secret_key' and a 'url'.
# This can be used as an extension point to inject custom behavior into the auth
# process, replacing the registration/login form that would normally be seen
# immediately after the user has authenticated with the third party provider.
# If a custom 'auth_entry' query parameter is used, then once the user has
# authenticated with a specific backend/provider, they will be redirected to the
# URL specified with this setting, rather than to the built-in
# registration/login form/logic.
AUTH_ENTRY_CUSTOM = getattr(settings, 'THIRD_PARTY_AUTH_CUSTOM_AUTH_FORMS', {})


def is_api(auth_entry):
    """Returns whether the auth entry point is via an API call."""
    return (auth_entry == AUTH_ENTRY_LOGIN_API) or (auth_entry == AUTH_ENTRY_REGISTER_API)  # lint-amnesty, pylint: disable=consider-using-in

# URLs associated with auth entry points
# These are used to request additional user information
# (for example, account credentials when logging in),
# and when the user cancels the auth process
# (e.g., refusing to grant permission on the provider's login page).
# We don't use "reverse" here because doing so may cause modules
# to load that depend on this module.
AUTH_DISPATCH_URLS = {
    AUTH_ENTRY_LOGIN: '/login',
    AUTH_ENTRY_REGISTER: '/register',
    AUTH_ENTRY_ACCOUNT_SETTINGS: '/account/settings',
}

_AUTH_ENTRY_CHOICES = frozenset([
    AUTH_ENTRY_LOGIN,
    AUTH_ENTRY_REGISTER,
    AUTH_ENTRY_ACCOUNT_SETTINGS,
    AUTH_ENTRY_LOGIN_API,
    AUTH_ENTRY_REGISTER_API,
] + list(AUTH_ENTRY_CUSTOM.keys()))

USER_FIELDS = ['username', 'email']


logger = getLogger(__name__)


class AuthEntryError(AuthException):
    """Raised when auth_entry is invalid on URLs.

    auth_entry tells us whether the auth flow was initiated to register a new
    user (in which case it has the value of AUTH_ENTRY_REGISTER) or log in an
    existing user (in which case it has the value of AUTH_ENTRY_LOGIN).

    This is necessary because the edX code we hook into the pipeline to
    redirect to the existing auth flows needs to know what case we are in in
    order to format its output correctly (for example, the register code is
    invoked earlier than the login code, and it needs to know if the login flow
    was requested to dispatch correctly).
    """


class ProviderUserState:
    """Object representing the provider state (attached or not) for a user.

    This is intended only for use when rendering templates. See for example
    lms/templates/dashboard.html.
    """

    def __init__(self, enabled_provider, user, association):
        # Boolean. Whether the user has an account associated with the provider
        self.has_account = association is not None
        if self.has_account:
            # UserSocialAuth row ID
            self.association_id = association.id
            # Identifier of this user according to the remote provider:
            self.remote_id = enabled_provider.get_remote_id_from_social_auth(association)
        else:
            self.association_id = None
            self.remote_id = None
        # provider.BaseProvider child. Callers must verify that the provider is
        # enabled.
        self.provider = enabled_provider
        # django.contrib.auth.models.User.
        self.user = user

    def get_unlink_form_name(self):
        """Gets the name used in HTML forms that unlink a provider account."""
        return self.provider.provider_id + '_unlink_form'


def get(request):
    """Gets the running pipeline's data from the passed request."""
    strategy = social_django.utils.load_strategy(request)
    token = strategy.session_get('partial_pipeline_token')

    if not token:
        strategy.session_set('partial_pipeline_token', strategy.session_get('partial_pipeline_token_'))
        token = strategy.session_get('partial_pipeline_token')

    partial_object = strategy.partial_load(token)
    pipeline_data = None
    if partial_object:
        pipeline_data = {'kwargs': partial_object.kwargs, 'backend': partial_object.backend}
    return pipeline_data


def get_idp_logout_url_from_running_pipeline(request):
    """
    Returns: IdP's logout url associated with running pipeline
    """
    if third_party_auth.is_enabled():
        running_pipeline = get(request)
        if running_pipeline:
            tpa_provider = provider.Registry.get_from_pipeline(running_pipeline)
            if tpa_provider:
                try:
                    return tpa_provider.get_setting('logout_url')
                except KeyError:
                    logger.info('[THIRD_PARTY_AUTH] idP [%s] logout_url setting not defined', tpa_provider.name)


def get_real_social_auth_object(request):
    """
    At times, the pipeline will have a "social" kwarg that contains a dictionary
    rather than an actual DB-backed UserSocialAuth object. We need the real thing,
    so this method allows us to get that by passing in the relevant request.
    """
    running_pipeline = get(request)
    if running_pipeline and 'social' in running_pipeline['kwargs']:
        social = running_pipeline['kwargs']['social']
        if isinstance(social, dict):
            social = social_django.models.UserSocialAuth.objects.get(**social)
        return social


def quarantine_session(request, locations):
    """
    Set a session variable indicating that the session is restricted
    to being used in views contained in the modules listed by string
    in the `locations` argument.

    Example: ``quarantine_session(request, ('enterprise.views',))``
    """
    request.session['third_party_auth_quarantined_modules'] = locations


def lift_quarantine(request):
    """
    Remove the session quarantine variable.
    """
    request.session.pop('third_party_auth_quarantined_modules', None)


def get_authenticated_user(auth_provider, username, uid):
    """Gets a saved user authenticated by a particular backend.

    Between pipeline steps User objects are not saved. We need to reconstitute
    the user and set its .backend, which is ordinarily monkey-patched on by
    Django during authenticate(), so it will function like a user returned by
    authenticate().

    Args:
        auth_provider: the third_party_auth provider in use for the current pipeline.
        username: string. Username of user to get.
        uid: string. The user ID according to the third party.

    Returns:
        User if user is found and has a social auth from the passed
        provider.

    Raises:
        User.DoesNotExist: if no user matching user is found, or the matching
        user has no social auth associated with the given backend.
        AssertionError: if the user is not authenticated.
    """
    match = social_django.models.DjangoStorage.user.get_social_auth(provider=auth_provider.backend_name, uid=uid)

    if not match or match.user.username != username:
        raise User.DoesNotExist

    user = match.user
    user.backend = auth_provider.get_authentication_backend()
    return user


def _get_enabled_provider(provider_id):
    """Gets an enabled provider by its provider_id member or throws."""
    enabled_provider = provider.Registry.get(provider_id)

    if not enabled_provider:
        raise ValueError('Provider %s not enabled' % provider_id)

    return enabled_provider


def _get_url(view_name, backend_name, auth_entry=None, redirect_url=None,
             extra_params=None, url_params=None):
    """Creates a URL to hook into social auth endpoints."""
    url_params = url_params or {}
    url_params['backend'] = backend_name
    url = reverse(view_name, kwargs=url_params)

    query_params = OrderedDict()
    if auth_entry:
        query_params[AUTH_ENTRY_KEY] = auth_entry

    if redirect_url:
        query_params[AUTH_REDIRECT_KEY] = redirect_url

    if extra_params:
        query_params.update(extra_params)

    return "{url}?{params}".format(
        url=url,
        params=six.moves.urllib.parse.urlencode(query_params)
    )


def get_complete_url(backend_name):
    """Gets URL for the endpoint that returns control to the auth pipeline.

    Args:
        backend_name: string. Name of the python-social-auth backend from the
            currently-running pipeline.

    Returns:
        String. URL that finishes the auth pipeline for a provider.

    Raises:
        ValueError: if no provider is enabled with the given backend_name.
    """
    if not any(provider.Registry.get_enabled_by_backend_name(backend_name)):
        raise ValueError('Provider with backend %s not enabled' % backend_name)

    return _get_url('social:complete', backend_name)


def get_disconnect_url(provider_id, association_id):
    """Gets URL for the endpoint that starts the disconnect pipeline.

    Args:
        provider_id: string identifier of the social_django.models.ProviderConfig child you want
            to disconnect from.
        association_id: int. Optional ID of a specific row in the UserSocialAuth
            table to disconnect (useful if multiple providers use a common backend)

    Returns:
        String. URL that starts the disconnection pipeline.

    Raises:
        ValueError: if no provider is enabled with the given ID.
    """
    backend_name = _get_enabled_provider(provider_id).backend_name
    # Use custom JSON disconnect endpoint to avoid CORS issues
    if association_id:
        return _get_url(
            'custom_disconnect_json_individual', backend_name, url_params={'association_id': association_id}
        )
    else:
        return _get_url('custom_disconnect_json', backend_name)


def get_login_url(provider_id, auth_entry, redirect_url=None):
    """Gets the login URL for the endpoint that kicks off auth with a provider.

    Args:
        provider_id: string identifier of the social_django.models.ProviderConfig child you want
            to disconnect from.
        auth_entry: string. Query argument specifying the desired entry point
            for the auth pipeline. Used by the pipeline for later branching.
            Must be one of _AUTH_ENTRY_CHOICES.

    Keyword Args:
        redirect_url (string): If provided, redirect to this URL at the end
            of the authentication process.

    Returns:
        String. URL that starts the auth pipeline for a provider.

    Raises:
        ValueError: if no provider is enabled with the given provider_id.
    """
    assert auth_entry in _AUTH_ENTRY_CHOICES
    enabled_provider = _get_enabled_provider(provider_id)
    return _get_url(
        'social:begin',
        enabled_provider.backend_name,
        auth_entry=auth_entry,
        redirect_url=redirect_url,
        extra_params=enabled_provider.get_url_params(),
    )


def get_duplicate_provider(messages):
    """Gets provider from message about social account already in use.

    python-social-auth's exception middleware uses the messages module to
    record details about duplicate account associations. It records exactly one
    message there is a request to associate a social account S with an edX
    account E if S is already associated with an edX account E'.

    This messaging approach is stringly-typed and the particular string is
    unfortunately not in a reusable constant.

    Returns:
        string name of the python-social-auth backend that has the duplicate
        account, or None if there is no duplicate (and hence no error).
    """
    social_auth_messages = [m for m in messages if m.message.endswith('is already in use.')]

    if not social_auth_messages:
        return

    assert len(social_auth_messages) == 1
    backend_name = social_auth_messages[0].extra_tags.split()[1]
    return backend_name


def get_provider_user_states(user):
    """Gets list of states of provider-user combinations.

    Args:
        django.contrib.auth.User. The user to get states for.

    Returns:
        List of ProviderUserState. The list of states of a user's account with
            each enabled provider.
    """
    states = []
    found_user_auths = list(social_django.models.DjangoStorage.user.get_social_auth_for_user(user))

    for enabled_provider in provider.Registry.enabled():
        association = None
        for auth in found_user_auths:
            if enabled_provider.match_social_auth(auth):
                association = auth
                break
        if enabled_provider.accepts_logins or association:
            states.append(
                ProviderUserState(enabled_provider, user, association)
            )

    return states


def running(request):
    """Returns True iff request is running a third-party auth pipeline."""
    return get(request) is not None  # Avoid False for {}.


# Pipeline functions.
# Signatures are set by python-social-auth; prepending 'unused_' causes
# TypeError on dispatch to the auth backend's authenticate().
# pylint: disable=unused-argument


def parse_query_params(strategy, response, *args, **kwargs):
    """Reads whitelisted query params, transforms them into pipeline args."""
    # If auth_entry is not in the session, we got here by a non-standard workflow.
    # We simply assume 'login' in that case.
    auth_entry = strategy.request.session.get(AUTH_ENTRY_KEY) or AUTH_ENTRY_LOGIN
    if auth_entry not in _AUTH_ENTRY_CHOICES:
        raise AuthEntryError(strategy.request.backend, 'auth_entry invalid')

    # Enable monitoring of the third-party-auth auth_entry value.
    set_custom_attribute('tpa_pipeline.auth_entry', auth_entry)
    return {'auth_entry': auth_entry}


def set_pipeline_timeout(strategy, user, *args, **kwargs):
    """
    Set a short session timeout while the pipeline runs, to improve security.

    Consider the following attack:
    1. Attacker on a public computer visits edX and initiates the third-party login flow
    2. Attacker logs into their own third-party account
    3. Attacker closes the window and does not complete the login flow
    4. Victim on the same computer logs into edX with username/password
    5. edX links attacker's third-party account with victim's edX account
    6. Attacker logs into victim's edX account using attacker's own third-party account

    We have two features of the pipeline designed to prevent this attack:
    * This method shortens the Django session timeout during the pipeline. This should mean that
      if there is a reasonable delay between steps 3 and 4, the session and pipeline will be
      reset, and the attack foiled.
      Configure the timeout with the SOCIAL_AUTH_PIPELINE_TIMEOUT setting (Default: 600 seconds)
    * On step 4, the login page displays an obvious message to the user, saying "You've
      successfully signed into (Google), but your (Google) account isn't linked with an edX
      account. To link your accounts, login now using your edX password.".
    """
    if strategy.request and not user:  # If user is set, we're currently logged in (and/or linked) so it doesn't matter.
        strategy.request.session.set_expiry(strategy.setting('PIPELINE_TIMEOUT', 600))
        # We don't need to reset this timeout later. Because the user is not logged in and this
        # account is not yet linked to an edX account, either the normal 'login' or 'register'
        # code must occur during the subsequent ensure_user_information step, and those methods
        # will change the session timeout to the "normal" value according to the "Remember Me"
        # choice of the user.


def redirect_to_custom_form(request, auth_entry, details, kwargs):
    """
    If auth_entry is found in AUTH_ENTRY_CUSTOM, this is used to send provider
    data to an external server's registration/login page.

    The data is sent as a base64-encoded values in a POST request and includes
    a cryptographic checksum in case the integrity of the data is important.
    """
    backend_name = request.backend.name
    provider_id = provider.Registry.get_from_pipeline({'backend': backend_name, 'kwargs': kwargs}).provider_id
    form_info = AUTH_ENTRY_CUSTOM[auth_entry]
    secret_key = form_info['secret_key']
    if isinstance(secret_key, str):
        secret_key = secret_key.encode('utf-8')
    custom_form_url = form_info['url']
    data_bytes = json.dumps({
        "auth_entry": auth_entry,
        "backend_name": backend_name,
        "provider_id": provider_id,
        "user_details": details,
    }).encode('utf-8')
    digest = hmac.new(secret_key, msg=data_bytes, digestmod=hashlib.sha256).digest()
    # Store the data in the session temporarily, then redirect to a page that will POST it to
    # the custom login/register page.
    request.session['tpa_custom_auth_entry_data'] = {
        'data': base64.b64encode(data_bytes),
        'hmac': base64.b64encode(digest),
        'post_url': custom_form_url,
    }
    return redirect(reverse('tpa_post_to_custom_auth_form'))


@partial.partial
def ensure_user_information(strategy, auth_entry, backend=None, user=None, social=None, current_partial=None,  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
                            allow_inactive_user=False, details=None, *args, **kwargs):
    """
    Ensure that we have the necessary information about a user (either an
    existing account or registration data) to proceed with the pipeline.
    """

    # We're deliberately verbose here to make it clear what the intended
    # dispatch behavior is for the various pipeline entry points, given the
    # current state of the pipeline. Keep in mind the pipeline is re-entrant
    # and values will change on repeated invocations (for example, the first
    # time through the login flow the user will be None so we dispatch to the
    # login form; the second time it will have a value so we continue to the
    # next pipeline step directly).
    #
    # It is important that we always execute the entire pipeline. Even if
    # behavior appears correct without executing a step, it means important
    # invariants have been violated and future misbehavior is likely.
    def dispatch_to_login():
        """Redirects to the login page."""
        return redirect(AUTH_DISPATCH_URLS[AUTH_ENTRY_LOGIN])

    def dispatch_to_register():
        """Redirects to the registration page."""
        return redirect(AUTH_DISPATCH_URLS[AUTH_ENTRY_REGISTER])

    def should_force_account_creation():
        """ For some third party providers, we auto-create user accounts """
        current_provider = provider.Registry.get_from_pipeline({'backend': current_partial.backend, 'kwargs': kwargs})
        return (current_provider and
                (current_provider.skip_email_verification or current_provider.send_to_registration_first))

    def is_provider_saml():
        """ Verify that the third party provider uses SAML """
        current_provider = provider.Registry.get_from_pipeline({'backend': current_partial.backend, 'kwargs': kwargs})
        saml_providers_list = list(provider.Registry.get_enabled_by_backend_name('tpa-saml'))
        return (current_provider and
                current_provider.slug in [saml_provider.slug for saml_provider in saml_providers_list])

    if current_partial:
        strategy.session_set('partial_pipeline_token_', current_partial.token)
        strategy.storage.partial.store(current_partial)

    if not user:
        # Use only email for user existence check in case of saml provider
        if is_provider_saml():
            user_details = {'email': details.get('email')} if details else None
        else:
            user_details = details
        if user_exists(user_details or {}):
            # User has not already authenticated and the details sent over from
            # identity provider belong to an existing user.
            return dispatch_to_login()

        if is_api(auth_entry):
            return HttpResponseBadRequest()
        elif auth_entry == AUTH_ENTRY_LOGIN:
            # User has authenticated with the third party provider but we don't know which edX
            # account corresponds to them yet, if any.
            if should_force_account_creation():
                return dispatch_to_register()
            return dispatch_to_login()
        elif auth_entry == AUTH_ENTRY_REGISTER:
            # User has authenticated with the third party provider and now wants to finish
            # creating their edX account.
            return dispatch_to_register()
        elif auth_entry == AUTH_ENTRY_ACCOUNT_SETTINGS:
            raise AuthEntryError(backend, 'auth_entry is wrong. Settings requires a user.')
        elif auth_entry in AUTH_ENTRY_CUSTOM:
            # Pass the username, email, etc. via query params to the custom entry page:
            return redirect_to_custom_form(strategy.request, auth_entry, details or {}, kwargs)
        else:
            raise AuthEntryError(backend, 'auth_entry invalid')

    if not user.is_active:
        # The user account has not been verified yet.
        if allow_inactive_user:
            # This parameter is used by the auth_exchange app, which always allows users to
            # login, whether or not their account is validated.
            pass
        elif social is None:
            # The user has just registered a new account as part of this pipeline. Their account
            # is inactive but we allow the login to continue, because if we pause again to force
            # the user to activate their account via email, the pipeline may get lost (e.g.
            # email takes too long to arrive, user opens the activation email on a different
            # device, etc.). This is consistent with first party auth and ensures that the
            # pipeline completes fully, which is critical.
            pass
        else:
            # This is an existing account, linked to a third party provider but not activated.
            # Double-check these criteria:
            assert user is not None
            assert social is not None
            # We now also allow them to login again, because if they had entered their email
            # incorrectly then there would be no way for them to recover the account, nor
            # register anew via SSO. See SOL-1324 in JIRA.
            # However, we will log a warning for this case:
            logger.warning(
                '[THIRD_PARTY_AUTH] User is using third_party_auth to login but has not yet activated their account. '
                'Username: {username}'.format(username=user.username)
            )


@partial.partial
def set_logged_in_cookies(backend=None, user=None, strategy=None, auth_entry=None, current_partial=None,  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
                          *args, **kwargs):
    """This pipeline step sets the "logged in" cookie for authenticated users.

    Some installations have a marketing site front-end separate from
    edx-platform.  Those installations sometimes display different
    information for logged in versus anonymous users (e.g. a link
    to the student dashboard instead of the login page.)

    Since social auth uses Django's native `login()` method, it bypasses
    our usual login view that sets this cookie.  For this reason, we need
    to set the cookie ourselves within the pipeline.

    The procedure for doing this is a little strange.  On the one hand,
    we need to send a response to the user in order to set the cookie.
    On the other hand, we don't want to drop the user out of the pipeline.

    For this reason, we send a redirect back to the "complete" URL,
    so users immediately re-enter the pipeline.  The redirect response
    contains a header that sets the logged in cookie.

    If the user is not logged in, or the logged in cookie is already set,
    the function returns `None`, indicating that control should pass
    to the next pipeline step.

    """
    if not is_api(auth_entry) and user is not None and user.is_authenticated:
        request = strategy.request if strategy else None
        if not user.has_usable_password():
            msg = "Your account is disabled"
            logout(request)
            return JsonResponse(msg, status=403)
        # n.b. for new users, user.is_active may be False at this point; set the cookie anyways.
        if request is not None:
            # Check that the cookie isn't already set.
            # This ensures that we allow the user to continue to the next
            # pipeline step once he/she has the cookie set by this step.
            has_cookie = user_authn_cookies.are_logged_in_cookies_set(request)
            if not has_cookie:
                try:
                    redirect_url = get_complete_url(current_partial.backend)
                except ValueError:
                    # If for some reason we can't get the URL, just skip this step
                    # This may be overly paranoid, but it's far more important that
                    # the user log in successfully than that the cookie is set.
                    pass
                else:
                    response = redirect(redirect_url)
                    return user_authn_cookies.set_logged_in_cookies(request, response, user)


@partial.partial
def login_analytics(strategy, auth_entry, current_partial=None, *args, **kwargs):  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
    """ Sends login info to Segment """

    event_name = None
    anonymous_id = ""
    additional_params = {}

    try:
        request = kwargs['request']
        anonymous_id = request.COOKIES.get('ajs_anonymous_id', "")
    except:  # pylint: disable=bare-except
        pass

    if auth_entry == AUTH_ENTRY_LOGIN:
        event_name = 'edx.bi.user.account.authenticated'
        additional_params['anonymous_id'] = anonymous_id
    elif auth_entry in [AUTH_ENTRY_ACCOUNT_SETTINGS]:
        event_name = 'edx.bi.user.account.linked'

    if event_name is not None:
        segment.track(kwargs['user'].id, event_name, {
            'category': "conversion",
            'label': None,
            'provider': kwargs['backend'].name,
            **additional_params
        })


@partial.partial
def associate_by_email_if_login_api(auth_entry, backend, details, user, current_partial=None, *args, **kwargs):  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
    """
    This pipeline step associates the current social auth with the user with the
    same email address in the database.  It defers to the social library's associate_by_email
    implementation, which verifies that only a single database user is associated with the email.

    This association is done ONLY if the user entered the pipeline through a LOGIN API.
    """
    if auth_entry == AUTH_ENTRY_LOGIN_API:
        # Temporary custom attribute to help ensure there is no usage.
        set_custom_attribute('deprecated_auth_entry_login_api', True)

        association_response, user_is_active = get_associated_user_by_email_response(
            backend, details, user, *args, **kwargs)

        if user_is_active:
            return association_response


@partial.partial
def associate_by_email_if_oauth(auth_entry, backend, details, user, strategy, *args, **kwargs):
    """
    This pipeline step associates the current social auth with the user with the
    same email address in the database.  It defers to the social library's associate_by_email
    implementation, which verifies that only a single database user is associated with the email.

    This association is done ONLY if the user entered the pipeline belongs to Oauth provider and
    `ENABLE_REQUIRE_THIRD_PARTY_AUTH` is enabled.
    """

    if is_oauth_provider(backend.name, **kwargs):
        association_response, user_is_active = get_associated_user_by_email_response(
            backend, details, user, *args, **kwargs)

        if user_is_active:
            return association_response


@partial.partial
def associate_by_email_if_saml(auth_entry, backend, details, user, strategy, *args, **kwargs):
    """
    This pipeline step associates the current social auth with the user with the
    same email address in the database.  It defers to the social library's associate_by_email
    implementation, which verifies that only a single database user is associated with the email.

    This association is done ONLY if the user entered the pipeline belongs to SAML provider.
    """
    from openedx.features.enterprise_support.api import enterprise_is_enabled

    def get_user():
        """
        This is the helper method to get the user from system by matching email.
        """
        user_details = {'email': details.get('email')} if details else None
        return get_user_from_email(user_details or {})

    @enterprise_is_enabled()
    def associate_by_email_if_enterprise_user():
        """
        If the learner arriving via SAML is already linked to the enterprise customer linked to the same IdP,
        they should not be prompted for their edX password.
        """
        try:
            enterprise_customer_user = is_enterprise_customer_user(current_provider.provider_id, current_user)
            logger.info(
                '[Multiple_SSO_SAML_Accounts_Association_to_User] Enterprise user verification:'
                'User Email: {email}, User ID: {user_id}, Provider ID: {provider_id},'
                ' is_enterprise_customer_user: {enterprise_customer_user}'.format(
                    email=current_user.email,
                    user_id=current_user.id,
                    provider_id=current_provider.provider_id,
                    enterprise_customer_user=enterprise_customer_user,
                )
            )

            if enterprise_customer_user:
                # this is python social auth pipeline default method to automatically associate social accounts
                # if the email already matches a user account.
                association_response, user_is_active = get_associated_user_by_email_response(
                    backend, details, user, *args, **kwargs)

                if not user_is_active:
                    logger.info(
                        '[Multiple_SSO_SAML_Accounts_Association_to_User] User association account is not'
                        ' active: User Email: {email}, User ID: {user_id}, Provider ID: {provider_id},'
                        ' is_enterprise_customer_user: {enterprise_customer_user}'.format(
                            email=current_user.email,
                            user_id=current_user.id,
                            provider_id=current_provider.provider_id,
                            enterprise_customer_user=enterprise_customer_user
                        )
                    )
                    return None

                return association_response

        except Exception as ex:  # pylint: disable=broad-except
            logger.exception('[Multiple_SSO_SAML_Accounts_Association_to_User] Error in'
                             ' saml multiple accounts association: User ID: %s, User Email: %s:,'
                             'Provider ID: %s, Exception: %s', current_user.id, current_user.email,
                             current_provider.provider_id, ex)

    saml_provider, current_provider = is_saml_provider(strategy.request.backend.name, kwargs)

    if saml_provider:
        # get the user by matching email if the pipeline user is not available.
        current_user = user if user else get_user()

        # Verify that the user linked to enterprise customer of current identity provider and an active user
        associate_response = associate_by_email_if_enterprise_user() if current_user else None
        if associate_response:
            return associate_response


def user_details_force_sync(auth_entry, strategy, details, user=None, *args, **kwargs):  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
    """
    Update normally protected user details using data from provider.

    This step in the pipeline is akin to `social_core.pipeline.user.user_details`, which updates
    the user details but has an unconfigurable protection over updating the username & email, and
    is unable to update information such as the user's full name which isn't on the user model, but
    rather on the user profile model.

    Additionally, because the email field is normally used to log in, if the email is changed by this
    forced synchronization, we send an email to both the old and new emails, letting the user know.

    This step is controlled by the `sync_learner_profile_data` flag on the provider's configuration.
    """
    current_provider = provider.Registry.get_from_pipeline({'backend': strategy.request.backend.name, 'kwargs': kwargs})
    if user and current_provider and current_provider.sync_learner_profile_data:
        # Keep track of which incoming values get applied.
        changed = {}

        # Map each incoming field from the provider to the name on the user model (by default, they always match).
        field_mapping = {field: (user, field) for field in details.keys() if hasattr(user, field)}

        # This is a special case where the field mapping should go to the user profile object and not the user object,
        # in some cases with differing field names (i.e. 'fullname' vs. 'name').
        field_mapping.update({
            'fullname': (user.profile, 'name'),
            'country': (user.profile, 'country'),
        })

        # Remove username from list of fields for update
        field_mapping.pop('username', None)

        # Track any fields that would raise an integrity error if there was a conflict.
        integrity_conflict_fields = {'email': user.email, 'username': user.username}

        for provider_field, (model, field) in field_mapping.items():
            provider_value = details.get(provider_field)
            current_value = getattr(model, field)
            if provider_value is not None and current_value != provider_value:
                if field in integrity_conflict_fields and User.objects.filter(**{field: provider_value}).exists():
                    logger.warning('[THIRD_PARTY_AUTH] Profile data synchronization conflict. '
                                   'UserId: {user_id}, Provider: {provider}, ConflictField: {conflict_field}, '
                                   'ConflictValue: {conflict_value}'.format(
                                       user_id=user.id,
                                       provider=current_provider.name,
                                       conflict_field=field,
                                       conflict_value=provider_value))
                    continue
                changed[provider_field] = current_value
                setattr(model, field, provider_value)

        if changed:
            logger.info(
                '[THIRD_PARTY_AUTH] User performed SSO and data was synchronized. '
                'Username: {username}, Provider: {provider}, UpdatedKeys: {updated_keys}'.format(
                    username=user.username,
                    provider=current_provider.name,
                    updated_keys=list(changed.keys())
                )
            )

            # Save changes to user and user.profile models.
            strategy.storage.user.changed(user)
            user.profile.save()

            # Send an email to the old and new email to alert the user that their login email changed.
            if changed.get('email'):
                old_email = changed['email']
                new_email = user.email
                email_context = {'old_email': old_email, 'new_email': new_email}
                # Subjects shouldn't have new lines.
                subject = ''.join(render_to_string(
                    'emails/sync_learner_profile_data_email_change_subject.txt',
                    email_context
                ).splitlines())
                body = render_to_string('emails/sync_learner_profile_data_email_change_body.txt', email_context)
                from_email = configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)

                email = EmailMessage(subject=subject, body=body, from_email=from_email, to=[old_email, new_email])
                email.content_subtype = "html"
                try:
                    email.send()
                except SMTPException:
                    logger.exception('[THIRD_PARTY_AUTH] Error sending IdP learner data sync-initiated email change '
                                     'notification email. Username: {username}'.format(username=user.username))


def set_id_verification_status(auth_entry, strategy, details, user=None, *args, **kwargs):  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
    """
    Use the user's authentication with the provider, if configured, as evidence of their identity being verified.
    """
    current_provider = provider.Registry.get_from_pipeline({'backend': strategy.request.backend.name, 'kwargs': kwargs})
    if user and current_provider and current_provider.enable_sso_id_verification:
        # Get previous valid, non expired verification attempts for this SSO Provider and user
        verifications = SSOVerification.objects.filter(
            user=user,
            status="approved",
            created_at__gte=earliest_allowed_verification_date(),
            identity_provider_type=current_provider.full_class_name,
            identity_provider_slug=current_provider.slug,
        )

        # If there is none, create a new approved verification for the user.
        if not verifications:
            verification = SSOVerification.objects.create(
                user=user,
                status="approved",
                name=user.profile.name,
                identity_provider_type=current_provider.full_class_name,
                identity_provider_slug=current_provider.slug,
            )
            # Send a signal so users who have already passed their courses receive credit
            verification.send_approval_signal(current_provider.slug)


def get_username(strategy, details, backend, user=None, *args, **kwargs):  # lint-amnesty, pylint: disable=keyword-arg-before-vararg
    """
    Copy of social_core.pipeline.user.get_username to achieve
    1. additional logging
    2. case insensitive username checks
    3. enforce same maximum and minimum length restrictions we have in `user_api/accounts`
    """
    if 'username' not in backend.setting('USER_FIELDS', USER_FIELDS):
        return
    storage = strategy.storage

    if not user:
        email_as_username = strategy.setting('USERNAME_IS_FULL_EMAIL', False)
        uuid_length = strategy.setting('UUID_LENGTH', 16)
        min_length = strategy.setting('USERNAME_MIN_LENGTH', accounts.USERNAME_MIN_LENGTH)
        max_length = strategy.setting('USERNAME_MAX_LENGTH', accounts.USERNAME_MAX_LENGTH)
        do_slugify = strategy.setting('SLUGIFY_USERNAMES', False)
        do_clean = strategy.setting('CLEAN_USERNAMES', True)

        if do_clean:
            override_clean = strategy.setting('CLEAN_USERNAME_FUNCTION')
            if override_clean:
                clean_func = module_member(override_clean)
            else:
                clean_func = storage.user.clean_username
        else:
            clean_func = lambda val: val

        if do_slugify:
            override_slug = strategy.setting('SLUGIFY_FUNCTION')
            if override_slug:
                slug_func = module_member(override_slug)
            else:
                slug_func = slugify
        else:
            slug_func = lambda val: val

        if is_auto_generated_username_enabled() and details.get('username') is None:
            # Lazy import to avoid circular dependency
            from openedx.core.djangoapps.user_authn.views.utils import get_auto_generated_username
            username = get_auto_generated_username(details)
        else:
            if email_as_username and details.get('email'):
                username = details['email']
            elif details.get('username'):
                username = details['username']
            else:
                username = uuid4().hex

        input_username = username
        final_username = slug_func(clean_func(username[:max_length]))

        # Generate a unique username for current user using username
        # as base but adding a unique hash at the end. Original
        # username is cut to avoid any field max_length.
        # We are using our own version of user_exists to avoid possible case sensitivity issues.
        # We will attempt different length random suffixes
        while len(final_username) < min_length or user_exists({'username': final_username}):
            # try to use at least 3 characters to avoid weird '-1' '-a' etc
            # use less than 3 if that is whats being asked for
            this_uuid_length = randint(min(uuid_length, 3), uuid_length)
            short_username = (input_username[:max_length - (this_uuid_length + 1)]  # +1 for the '-'
                              if max_length is not None
                              else username)
            # adding a dash between user-supplied and system-generated values to avoid weird combinations
            username = short_username + '-' + username_suffix_generator(this_uuid_length)
            final_username = slug_func(clean_func(username[:max_length]))
            logger.info(
                '[THIRD_PARTY_AUTH] New username candidnate generated: '
                f'input_username={input_username}, '
                f'suffix_length={this_uuid_length}, '
                f'final_username={final_username}'
            )
    else:
        final_username = storage.user.get_username(user)
    logger.info(
        '[THIRD_PARTY_AUTH] get_username complete: '
        f'details={details}, '
        f'final_username={final_username}'
    )
    return {'username': final_username}


def ensure_redirect_url_is_safe(strategy, *args, **kwargs):
    """
    Ensure that the redirect url is save if a user logs in or registers by
    directly hitting the TPA url i.e /auth/login/backend_name?next=<redirect_to>

    Check it against the LOGIN_REDIRECT_WHITELIST. If it is not safe then
    redirect to SOCIAL_AUTH_LOGIN_REDIRECT_URL (defaults to /dashboard)
    """
    redirect_to = strategy.session_get(REDIRECT_FIELD_NAME, None)
    request = strategy.request

    if redirect_to and request:
        is_safe = is_safe_login_or_logout_redirect(
            redirect_to=redirect_to,
            request_host=request.get_host(),
            dot_client_id=None,
            require_https=request.is_secure(),
        )

        if not is_safe:
            safe_redirect_url = getattr(settings, 'SOCIAL_AUTH_LOGIN_REDIRECT_URL', '/dashboard')
            strategy.session_set(REDIRECT_FIELD_NAME, safe_redirect_url)
