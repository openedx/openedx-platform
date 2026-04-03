"""
Views for login / logout and associated functionality

Much of this file was broken out from views.py, previous history can be found there.
"""

import hashlib
import json
import logging
import re
import urllib

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from edx_django_utils.monitoring import set_custom_attribute
from eventtracking import tracker
from openedx_events.learning.data import UserData, UserPersonalData
from openedx_events.learning.signals import SESSION_LOGIN_COMPLETED
from openedx_filters.learning.filters import StudentLoginRequested
from rest_framework import status
from rest_framework.views import APIView

from common.djangoapps import third_party_auth
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.helpers import get_next_url_for_login_page, get_redirect_url_with_host
from common.djangoapps.student.models import AllowedAuthUser, LoginFailures, UserProfile
from common.djangoapps.student.views import compose_and_send_activation_email
from common.djangoapps.third_party_auth import pipeline, provider
from common.djangoapps.track import segment
from common.djangoapps.util.json_request import JsonResponse
from common.djangoapps.util.password_policy_validators import normalize_password
from openedx.core.djangoapps.password_policy import compliance as password_policy_compliance
from openedx.core.djangoapps.safe_sessions.middleware import mark_user_change_as_expected
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_authn.config.waffle import ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY
from openedx.core.djangoapps.user_authn.cookies import get_response_with_refreshed_jwt_cookies, set_logged_in_cookies
from openedx.core.djangoapps.user_authn.exceptions import AuthFailedError, VulnerablePasswordError
from openedx.core.djangoapps.user_authn.tasks import check_pwned_password_and_send_track_event
from openedx.core.djangoapps.user_authn.toggles import (
    is_require_third_party_auth_enabled,
    should_redirect_to_authn_microfrontend,
)
from openedx.core.djangoapps.user_authn.views.login_form import get_login_session_form
from openedx.core.djangoapps.user_authn.views.password_reset import send_password_reset_email_for_user
from openedx.core.djangoapps.user_authn.views.utils import API_V1, ENTERPRISE_ENROLLMENT_URL_REGEX, UUID4_REGEX
from openedx.core.djangoapps.util.user_messages import PageLevelMessages
from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.api.view_utils import require_post_params  # lint-amnesty, pylint: disable=unused-import
from openedx.features.enterprise_support.api import activate_learner_enterprise, get_enterprise_learner_data_from_api

log = logging.getLogger("edx.student")
AUDIT_LOG = logging.getLogger("audit")
USER_MODEL = get_user_model()
PASSWORD_RESET_INITIATED = "edx.user.passwordreset.initiated"


def _do_third_party_auth(request):
    """
    User is already authenticated via 3rd party, now try to find and return their associated Django user.
    """
    running_pipeline = pipeline.get(request)
    username = running_pipeline["kwargs"].get("username")
    backend_name = running_pipeline["backend"]
    third_party_uid = running_pipeline["kwargs"]["uid"]
    requested_provider = provider.Registry.get_from_pipeline(running_pipeline)
    platform_name = configuration_helpers.get_value("platform_name", settings.PLATFORM_NAME)

    try:
        return pipeline.get_authenticated_user(requested_provider, username, third_party_uid)
    except USER_MODEL.DoesNotExist:
        AUDIT_LOG.info(
            "Login failed - user with username {username} has no social auth "
            "with backend_name {backend_name}".format(username=username, backend_name=backend_name)
        )
        message = Text(
            _(
                "You've successfully signed in to your {provider_name} account, "
                "but this account isn't linked with your {platform_name} account yet. {blank_lines}"
                "Use your {platform_name} username and password to sign in to {platform_name} below, "
                "and then link your {platform_name} account with {provider_name} from your dashboard. {blank_lines}"
                "If you don't have an account on {platform_name} yet, "
                "click {register_label_strong} at the top of the page."
            )
        ).format(
            blank_lines=HTML("<br/><br/>"),
            platform_name=platform_name,
            provider_name=requested_provider.name,
            register_label_strong=HTML("<strong>{register_text}</strong>").format(register_text=_("Register")),
        )

        raise AuthFailedError(message, error_code="third-party-auth-with-no-linked-account")  # lint-amnesty, pylint: disable=raise-missing-from


def _get_user_by_email(email):
    """
    Finds a user object in the database based on the given email, ignores all fields except for email.
    """
    try:
        return USER_MODEL.objects.get(email=email)
    except USER_MODEL.DoesNotExist:
        return None


def _get_user_by_username(username):
    """
    Finds a user object in the database based on the given username.
    """
    try:
        return USER_MODEL.objects.get(username=username)
    except USER_MODEL.DoesNotExist:
        return None


def _get_user_by_email_or_username(request, api_version):
    """
    Finds a user object in the database based on the given request, ignores all fields except for email and username.
    """
    is_api_v2 = api_version != API_V1
    login_fields = ["email", "password"]
    if is_api_v2:
        login_fields = ["email_or_username", "password"]

    if any(f not in request.POST.keys() for f in login_fields):
        raise AuthFailedError(_("There was an error receiving your login information. Please email us."))

    email_or_username = request.POST.get("email", None) or request.POST.get("email_or_username", None)
    user = _get_user_by_email(email_or_username)

    if not user and is_api_v2:
        # If user not found with email and API_V2, try username lookup
        user = _get_user_by_username(email_or_username)

    if not user:
        digest = hashlib.shake_128(email_or_username.encode("utf-8")).hexdigest(16)
        AUDIT_LOG.warning(f"Login failed - Unknown user email or username {digest}")

    return user


def _check_excessive_login_attempts(user):
    """
    See if account has been locked out due to excessive login failures
    """
    if user and LoginFailures.is_feature_enabled():
        if LoginFailures.is_user_locked_out(user):
            _generate_locked_out_error_message()


def _generate_locked_out_error_message():
    """
    Helper function to generate error message for users consumed all
    login attempts.
    """

    locked_out_period_in_sec = settings.MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS
    error_message = Text(
        _(
            "To protect your account, it’s been temporarily "
            "locked. Try again in {locked_out_period} minutes."
            "{li_start}To be on the safe side, you can reset your "
            "password {link_start}here{link_end} before you try again."
        )
    ).format(
        link_start=HTML('<a http="#login" class="form-toggle" data-type="password-reset">'),
        link_end=HTML("</a>"),
        li_start=HTML("<li>"),
        li_end=HTML("</li>"),
        locked_out_period=int(locked_out_period_in_sec / 60),
    )
    raise AuthFailedError(
        error_message,
        error_code="account-locked-out",
        context={"locked_out_period": int(locked_out_period_in_sec / 60)},
    )


def _enforce_password_policy_compliance(request, user):  # lint-amnesty, pylint: disable=missing-function-docstring
    try:
        password_policy_compliance.enforce_compliance_on_login(user, request.POST.get("password"))
    except password_policy_compliance.NonCompliantPasswordWarning as e:
        # Allow login, but warn the user that they will be required to reset their password soon.
        PageLevelMessages.register_warning_message(request, HTML(str(e)))
    except password_policy_compliance.NonCompliantPasswordException as e:
        # Increment the lockout counter to safeguard from further brute force requests
        # if user's password has been compromised.
        if LoginFailures.is_feature_enabled():
            LoginFailures.increment_lockout_counter(user)

        AUDIT_LOG.info("Password reset initiated for email %s.", user.email)
        tracker.emit(
            PASSWORD_RESET_INITIATED,
            {
                "user_id": user.id,
                "source": "Policy Compliance",
            },
        )
        send_password_reset_email_for_user(user, request)

        # Prevent the login attempt.
        raise AuthFailedError(HTML(str(e)), error_code=e.__class__.__name__)  # lint-amnesty, pylint: disable=raise-missing-from


def _log_and_raise_inactive_user_auth_error(unauthenticated_user):
    """
    Depending on Django version we can get here a couple of ways, but this takes care of logging an auth attempt
    by an inactive user, re-sending the activation email, and raising an error with the correct message.
    """
    AUDIT_LOG.warning(f"Login failed - Account not active for user.id: {unauthenticated_user.id}, resending activation")

    profile = UserProfile.objects.get(user=unauthenticated_user)
    compose_and_send_activation_email(unauthenticated_user, profile)

    raise AuthFailedError(
        error_code="inactive-user",
        context={
            "platformName": configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
            "supportLink": configuration_helpers.get_value("SUPPORT_SITE_LINK", settings.SUPPORT_SITE_LINK),
        },
    )


def _authenticate_first_party(request, unauthenticated_user, third_party_auth_requested):
    """
    Use Django authentication on the given request, using rate limiting if configured
    """
    should_be_rate_limited = getattr(request, "limited", False)
    if should_be_rate_limited:
        raise AuthFailedError(
            _("Too many failed login attempts. Try again later.")
        )  # lint-amnesty, pylint: disable=raise-missing-from

    # If the user doesn't exist, we want to set the username to an invalid username so that authentication is guaranteed
    # to fail and we can take advantage of the ratelimited backend
    username = unauthenticated_user.username if unauthenticated_user else ""

    # First time when a user login through third_party_auth account then user needs to link
    # third_party account with the platform account by login through email and password that's
    # why we need to by-pass this check when user is already authenticated by third_party_auth.
    if not third_party_auth_requested:
        _check_user_auth_flow(request.site, unauthenticated_user)

    password = normalize_password(request.POST["password"])
    return authenticate(username=username, password=password, request=request)


def _handle_failed_authentication(user, authenticated_user):
    """
    Handles updating the failed login count, inactive user notifications, and logging failed authentications.
    """
    failure_count = 0
    if user:
        if LoginFailures.is_feature_enabled():
            LoginFailures.increment_lockout_counter(user)

        if authenticated_user and not user.is_active:
            _log_and_raise_inactive_user_auth_error(user)

        # if we didn't find this username earlier, the account for this email
        # doesn't exist, and doesn't have a corresponding password
        loggable_id = user.id if user else "<unknown>"
        AUDIT_LOG.warning(f"Login failed - password for user.id: {loggable_id} is invalid")

    if user and LoginFailures.is_feature_enabled():
        blocked_threshold, failure_count = LoginFailures.check_user_reset_password_threshold(user)
        if blocked_threshold:
            if not LoginFailures.is_user_locked_out(user):
                max_failures_allowed = settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED
                remaining_attempts = max_failures_allowed - failure_count
                error_message = Text(
                    _(
                        "Email or password is incorrect."
                        "{li_start}You have {remaining_attempts} more sign-in "
                        "attempts before your account is temporarily locked.{li_end}"
                        "{li_start}If you've forgotten your password, click "
                        "{link_start}here{link_end} to reset.{li_end}"
                    )
                ).format(
                    link_start=HTML('<a http="#login" class="form-toggle" data-type="password-reset">'),
                    link_end=HTML("</a>"),
                    li_start=HTML("<li>"),
                    li_end=HTML("</li>"),
                    remaining_attempts=remaining_attempts,
                )
                raise AuthFailedError(
                    error_message,
                    error_code="failed-login-attempt",
                    context={
                        "remaining_attempts": remaining_attempts,
                        "allowed_failure_attempts": max_failures_allowed,
                        "failure_count": failure_count,
                    },
                )

            _generate_locked_out_error_message()

    raise AuthFailedError(
        _("Email or password is incorrect."),
        error_code="incorrect-email-or-password",
        context={"failure_count": failure_count},
    )


def _handle_successful_authentication_and_login(user, request):
    """
    Handles clearing the failed login counter, login tracking, and setting session timeout.
    """
    if LoginFailures.is_feature_enabled():
        LoginFailures.clear_lockout_counter(user)

    _track_user_login(user, request)

    try:
        django_login(request, user)
        request.session.set_expiry(604800 * 4)
        log.debug("Setting user session expiry to 4 weeks")

        # .. event_implemented_name: SESSION_LOGIN_COMPLETED
        # .. event_type: org.openedx.learning.auth.session.login.completed.v1
        SESSION_LOGIN_COMPLETED.send_event(
            user=UserData(
                pii=UserPersonalData(
                    username=user.username,
                    email=user.email,
                    name=user.profile.name,
                ),
                id=user.id,
                is_active=user.is_active,
            ),
        )
    except Exception as exc:
        AUDIT_LOG.critical("Login failed - Could not create session. Is memcached running?")
        log.critical("Login failed - Could not create session. Is memcached running?")
        log.exception(exc)
        raise


def _track_user_login(user, request):
    """
    Sends a tracking event for a successful login.
    """
    # .. pii: Username and email are sent to Segment here. Retired directly through Segment API call in Tubular.
    # .. pii_types: email_address, username
    # .. pii_retirement: third_party
    anonymous_id = ""
    try:
        anonymous_id = request.COOKIES.get('ajs_anonymous_id', "")
    except:  # pylint: disable=bare-except
        pass
    segment.identify(
        user.id,
        {"email": user.email, "username": user.username},
        {
            # Disable MailChimp because we don't want to update the user's email
            # and username in MailChimp on every page load. We only need to capture
            # this data on registration/activation.
            "MailChimp": False
        },
    )
    segment.track(
        user.id,
        "edx.bi.user.account.authenticated",
        {
            "category": "conversion",
            "label": request.POST.get("course_id"),
            "provider": None,
            "anonymous_id": anonymous_id,
        },
    )


def _create_message(site, root_url, allowed_domain):
    """
    Helper function to create error message for those users that belongs
    to an allowed domain and not whitelisted then ask such users to login
    through allowed domain SSO provider.
    """
    msg = Text(
        _(
            "As {allowed_domain} user, You must login with your {allowed_domain} "
            "{link_start}{provider} account{link_end}."
        )
    ).format(
        allowed_domain=allowed_domain,
        link_start=HTML("<a href='{root_url}{tpa_provider_link}'>").format(
            root_url=root_url if root_url else "",
            tpa_provider_link="{dashboard_url}?tpa_hint={tpa_hint}".format(
                dashboard_url=reverse("dashboard"),
                tpa_hint=site.configuration.get_value("THIRD_PARTY_AUTH_ONLY_HINT"),
            ),
        ),
        provider=site.configuration.get_value("THIRD_PARTY_AUTH_ONLY_PROVIDER"),
        link_end=HTML("</a>"),
    )
    return msg


def _check_user_auth_flow(site, user):
    """
    Check if user belongs to an allowed domain and not whitelisted
    then ask user to login through allowed domain SSO provider.
    """
    if user and ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY.is_enabled():
        allowed_domain = site.configuration.get_value("THIRD_PARTY_AUTH_ONLY_DOMAIN", "").lower()
        email_parts = user.email.split("@")
        if len(email_parts) != 2:
            # User has a nonstandard email so we record their id.
            # we don't record their e-mail in case there is sensitive info accidentally
            # in there.
            set_custom_attribute("login_tpa_domain_shortcircuit_user_id", user.id)
            log.warning("User %s has nonstandard e-mail. Shortcircuiting THIRD_PARTY_AUTH_ONLY_DOMAIN check.", user.id)
            return
        user_domain = email_parts[1].strip().lower()

        # If user belongs to allowed domain and not whitelisted then user must login through allowed domain SSO
        if user_domain == allowed_domain and not AllowedAuthUser.objects.filter(site=site, email=user.email).exists():
            if not should_redirect_to_authn_microfrontend():
                msg = _create_message(site, None, allowed_domain)
                raise AuthFailedError(msg)

            raise AuthFailedError(
                error_code="allowed-domain-login-error",
                context={
                    "allowed_domain": allowed_domain,
                    "provider": site.configuration.get_value("THIRD_PARTY_AUTH_ONLY_PROVIDER"),
                    "tpa_hint": site.configuration.get_value("THIRD_PARTY_AUTH_ONLY_HINT"),
                },
            )


@login_required
@require_http_methods(["GET"])
def finish_auth(request):
    """Following logistration (1st or 3rd party), handle any special query string params.

    See FinishAuthView.js for details on the query string params.

    e.g. auto-enroll the user in a course, set email opt-in preference.

    This view just displays a "Please wait" message while AJAX calls are made to enroll the
    user in the course etc. This view is only used if a parameter like "course_id" is present
    during login/registration/third_party_auth. Otherwise, there is no need for it.

    Ideally this view will finish and redirect to the next step before the user even sees it.

    Args:
        request (HttpRequest)

    Returns:
        HttpResponse: 200 if the page was sent successfully
        HttpResponse: 302 if not logged in (redirect to login page)
        HttpResponse: 405 if using an unsupported HTTP method

    Example usage:

        GET /account/finish_auth/?course_id=course-v1:blah&enrollment_action=enroll

    """
    return render_to_response(
        "student_account/finish_auth.html",
        {
            "disable_courseware_js": True,
            "disable_footer": True,
        },
    )


def enterprise_selection_page(request, user, next_url):
    """
    Updates redirect url to enterprise selection page if user is associated
    with multiple enterprises otherwise return the next url.

    param:
      next_url(string): The URL to redirect to after multiple enterprise selection or in case
      the selection page is bypassed e.g when dealing with direct enrolment urls.
    """
    redirect_url = next_url

    response = get_enterprise_learner_data_from_api(user)
    if response and len(response) > 1:
        redirect_url = reverse("enterprise_select_active") + "/?success_url=" + urllib.parse.quote(next_url)

        # Check to see if next url has an enterprise in it. In this case if user is associated with
        # that enterprise, activate that enterprise and bypass the selection page.
        if re.match(ENTERPRISE_ENROLLMENT_URL_REGEX, urllib.parse.unquote(next_url)):
            enterprise_in_url = re.search(UUID4_REGEX, next_url).group(0)
            for enterprise in response:
                if enterprise_in_url == str(enterprise["enterprise_customer"]["uuid"]):
                    is_activated_successfully = activate_learner_enterprise(request, user, enterprise_in_url)
                    if is_activated_successfully:
                        redirect_url = next_url
                    break

    return redirect_url


@ensure_csrf_cookie
@require_http_methods(["POST"])
@ratelimit(
    key="openedx.core.djangoapps.util.ratelimit.request_post_email_or_username",
    rate=settings.LOGISTRATION_PER_EMAIL_RATELIMIT_RATE,
    method="POST",
    block=False,
)  # lint-amnesty, pylint: disable=too-many-statements
@ratelimit(
    key="openedx.core.djangoapps.util.ratelimit.real_ip",
    rate=settings.LOGISTRATION_RATELIMIT_RATE,
    method="POST",
    block=False,
)  # lint-amnesty, pylint: disable=too-many-statements
def login_user(request, api_version="v1"):  # pylint: disable=too-many-statements
    """
    AJAX request to log in the user.

    Arguments:
        request (HttpRequest)

    Required params:
        email, password

    Optional params:
        analytics: a JSON-encoded object with additional info to include in the login analytics event. The only
            supported field is "enroll_course_id" to indicate that the user logged in while enrolling in a particular
            course.

    Returns:
        HttpResponse: 200 if successful.
            Ex. {'success': true}
        HttpResponse: 400 if the request failed.
            Ex. {'success': false, 'value': '{'success': false, 'value: 'Email or password is incorrect.'}
        HttpResponse: 403 if successful authentication with a third party provider but does not have a linked account.
            Ex. {'success': false, 'error_code': 'third-party-auth-with-no-linked-account'}

    Example Usage:

        POST /login_ajax
        with POST params `email`, `password`

        200 {'success': true}

    """
    _parse_analytics_param_for_course_id(request)

    third_party_auth_requested = third_party_auth.is_enabled() and pipeline.running(request)
    first_party_auth_requested = any(bool(request.POST.get(p)) for p in ["email", "email_or_username", "password"])
    is_user_third_party_authenticated = False

    set_custom_attribute("login_user_course_id", request.POST.get("course_id"))

    if is_require_third_party_auth_enabled() and not third_party_auth_requested:
        return HttpResponseForbidden(
            "Third party authentication is required to login. Username and password were received instead."
        )
    possibly_authenticated_user = None
    try:
        if third_party_auth_requested and not first_party_auth_requested:
            # The user has already authenticated via third-party auth and has not
            # asked to do first party auth by supplying a username or password. We
            # now want to put them through the same logging and cookie calculation
            # logic as with first-party auth.

            # This nested try is due to us only returning an HttpResponse in this
            # one case vs. JsonResponse everywhere else.
            try:
                user = _do_third_party_auth(request)
                is_user_third_party_authenticated = True
                set_custom_attribute("login_user_tpa_success", True)
            except AuthFailedError as e:
                set_custom_attribute("login_user_tpa_success", False)
                set_custom_attribute("login_user_tpa_failure_msg", e.value)
                if e.error_code:
                    set_custom_attribute("login_error_code", e.error_code)

                # user successfully authenticated with a third party provider, but has no linked Open edX account
                response_content = e.get_response()
                return JsonResponse(response_content, status=403)
        else:
            user = _get_user_by_email_or_username(request, api_version)

        _check_excessive_login_attempts(user)

        possibly_authenticated_user = user

        try:
            # .. filter_implemented_name: StudentLoginRequested
            # .. filter_type: org.openedx.learning.student.login.requested.v1
            possibly_authenticated_user = StudentLoginRequested.run_filter(user=possibly_authenticated_user)
        except StudentLoginRequested.PreventLogin as exc:
            raise AuthFailedError(
                str(exc),
                redirect_url=exc.redirect_to,
                error_code=exc.error_code,
                context=exc.context,
            ) from exc

        if not is_user_third_party_authenticated:
            possibly_authenticated_user = _authenticate_first_party(request, user, third_party_auth_requested)
            if possibly_authenticated_user and password_policy_compliance.should_enforce_compliance_on_login():
                # Important: This call must be made AFTER the user was successfully authenticated.
                _enforce_password_policy_compliance(request, possibly_authenticated_user)

        if possibly_authenticated_user is None or not (
            possibly_authenticated_user.is_active or settings.MARKETING_EMAILS_OPT_IN
        ):
            _handle_failed_authentication(user, possibly_authenticated_user)

        pwned_properties = (
            check_pwned_password_and_send_track_event(
                user_id=user.id,
                password=request.POST.get("password"),
                internal_user=user.is_staff,
                request_page="login",
            )
            if not is_user_third_party_authenticated
            else {}
        )
        # Set default for third party login
        password_frequency = pwned_properties.get("frequency", -1)
        if (
            settings.ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY
            and password_frequency >= settings.HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD
        ):
            raise VulnerablePasswordError(accounts.AUTHN_LOGIN_BLOCK_HIBP_POLICY_MSG, "require-password-change")

        _handle_successful_authentication_and_login(possibly_authenticated_user, request)

        # The AJAX method calling should know the default destination upon success
        redirect_url, finish_auth_url = None, ""

        if third_party_auth_requested:
            running_pipeline = pipeline.get(request)
            finish_auth_url = pipeline.get_complete_url(backend_name=running_pipeline["backend"])

        if is_user_third_party_authenticated:
            redirect_url = finish_auth_url
        elif should_redirect_to_authn_microfrontend():
            next_url, root_url = get_next_url_for_login_page(request, include_host=True)
            redirect_url = get_redirect_url_with_host(
                root_url, enterprise_selection_page(request, possibly_authenticated_user, finish_auth_url or next_url)
            )

        if (
            settings.ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY
            and 0 <= password_frequency <= settings.HIBP_LOGIN_NUDGE_PASSWORD_FREQUENCY_THRESHOLD
        ):
            raise VulnerablePasswordError(
                accounts.AUTHN_LOGIN_NUDGE_HIBP_POLICY_MSG, "nudge-password-change", redirect_url
            )

        response = JsonResponse(
            {
                "success": True,
                "redirect_url": redirect_url,
            }
        )

        # Ensure that the external marketing site can
        # detect that the user is logged in.
        response = set_logged_in_cookies(request, response, possibly_authenticated_user)
        set_custom_attribute("login_user_auth_failed_error", False)
        set_custom_attribute("login_user_response_status", response.status_code)
        set_custom_attribute("login_user_redirect_url", redirect_url)
        mark_user_change_as_expected(user.id)
        return response
    except AuthFailedError as error:
        response_content = error.get_response()
        log.exception(response_content)

        error_code = response_content.get("error_code")
        if error_code:
            set_custom_attribute("login_error_code", error_code)
        email_or_username_key = "email" if api_version == API_V1 else "email_or_username"
        email_or_username = request.POST.get(email_or_username_key, None)
        email_or_username = possibly_authenticated_user.email if possibly_authenticated_user else email_or_username
        response_content["email"] = email_or_username
    except VulnerablePasswordError as error:
        response_content = error.get_response()
        log.exception(response_content)

    response = JsonResponse(response_content, status=400)
    set_custom_attribute("login_user_auth_failed_error", True)
    set_custom_attribute("login_user_response_status", response.status_code)
    return response


# CSRF protection is not needed here because the only side effect
# of this endpoint is to refresh the cookie-based JWT, and attempting
# to get a CSRF token before we need to refresh adds too much
# complexity.
@csrf_exempt
@require_http_methods(["POST"])
def login_refresh(request):  # lint-amnesty, pylint: disable=missing-function-docstring
    if not request.user.is_authenticated or request.user.is_anonymous:
        return JsonResponse("Unauthorized", status=401)

    try:
        return get_response_with_refreshed_jwt_cookies(request, request.user)
    except AuthFailedError as error:
        log.exception(error.get_response())
        return JsonResponse(error.get_response(), status=400)


def redirect_to_lms_login(request):
    """
    This view redirect the admin/login url to the site's login page if
    waffle switch is on otherwise returns the admin site's login view.
    """
    return redirect("/login?next=/admin")


login_user_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "email": openapi.Schema(type=openapi.TYPE_STRING),
        "password": openapi.Schema(type=openapi.TYPE_STRING),
    },
)

login_user_return_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "success": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "value": openapi.Schema(type=openapi.TYPE_STRING),
        "error_code": openapi.Schema(type=openapi.TYPE_STRING),
    },
)


class LoginSessionView(APIView):
    """HTTP end-points for logging in users."""

    # This end-point is available to anonymous users,
    # so do not require authentication.
    authentication_classes = []

    login_user_responses = {
        status.HTTP_200_OK: login_user_return_schema,
        status.HTTP_400_BAD_REQUEST: login_user_return_schema,
        status.HTTP_403_FORBIDDEN: login_user_return_schema,
    }

    @method_decorator(ensure_csrf_cookie)
    def get(self, request, *args, **kwargs):
        return HttpResponse(get_login_session_form(request).to_json(), content_type="application/json")  # lint-amnesty, pylint: disable=http-response-with-content-type-json

    @swagger_auto_schema(
        request_body=login_user_schema,
        responses=login_user_responses,
        security=[
            {"csrf": []},
        ],
    )
    @method_decorator(csrf_protect)
    def post(self, request, api_version):
        """
        POST /user/{api_version}/account/login_session/

        Returns 200 on success, and a detailed error message otherwise.
        """
        return login_user(request, api_version)

    @method_decorator(sensitive_post_parameters("password"))
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


def _parse_analytics_param_for_course_id(request):
    """If analytics request param is found, parse and add course id as a new request param."""
    # Make a copy of the current POST request to modify.
    modified_request = request.POST.copy()
    if isinstance(request, HttpRequest):
        # Works for an HttpRequest but not a rest_framework.request.Request.
        # Note: This case seems to be used for tests only.
        request.POST = modified_request
        set_custom_attribute("login_user_request_type", "django")
    else:
        # The request must be a rest_framework.request.Request.
        # Note: Only DRF seems to be used in Production.
        request._data = modified_request  # pylint: disable=protected-access
        set_custom_attribute("login_user_request_type", "drf")

    # Include the course ID if it's specified in the analytics info
    # so it can be included in analytics events.
    if "analytics" in modified_request:
        try:
            analytics = json.loads(modified_request["analytics"])
            if "enroll_course_id" in analytics:
                modified_request["course_id"] = analytics.get("enroll_course_id")
        except (ValueError, TypeError):
            set_custom_attribute("shim_analytics_course_id", "parse-error")
            log.error("Could not parse analytics object sent to user API: {analytics}".format(analytics=analytics))
