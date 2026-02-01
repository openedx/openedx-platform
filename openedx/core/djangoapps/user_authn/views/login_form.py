""" Login related views """


import json
import logging
import urllib

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from common.djangoapps import third_party_auth
from common.djangoapps.edxmako.shortcuts import render_to_response
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.accounts.utils import (
    is_secondary_email_feature_enabled
)
from openedx.core.djangoapps.user_api.helpers import FormDescription
from openedx.core.djangoapps.user_authn.cookies import set_logged_in_cookies
from openedx.core.djangoapps.user_authn.toggles import should_redirect_to_authn_microfrontend
from openedx.core.djangoapps.user_authn.views.password_reset import get_password_reset_form
from openedx.core.djangoapps.user_authn.views.registration_form import RegistrationFormFactory
from openedx.core.djangoapps.user_authn.views.utils import third_party_auth_context
from openedx.core.djangoapps.user_authn.toggles import is_require_third_party_auth_enabled
from openedx.features.enterprise_support.api import enterprise_customer_for_request, enterprise_enabled
from openedx.features.enterprise_support.utils import (
    get_enterprise_slug_login_url,
    handle_enterprise_cookies_for_logistration,
    update_logistration_context_for_enterprise
)
from common.djangoapps.student.helpers import get_next_url_for_login_page
from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.decorators import xframe_allow_whitelisted
from common.djangoapps.util.password_policy_validators import DEFAULT_MAX_PASSWORD_LENGTH

log = logging.getLogger(__name__)


def _apply_third_party_auth_overrides(request, form_desc):
    """Modify the login form if the user has authenticated with a third-party provider.
    If a user has successfully authenticated with a third-party provider,
    and an email is associated with it then we fill in the email field with readonly property.
    Arguments:
        request (HttpRequest): The request for the registration form, used
            to determine if the user has successfully authenticated
            with a third-party provider.
        form_desc (FormDescription): The registration form description
    """
    if third_party_auth.is_enabled():
        running_pipeline = third_party_auth.pipeline.get(request)
        if running_pipeline:
            current_provider = third_party_auth.provider.Registry.get_from_pipeline(running_pipeline)
            if current_provider and enterprise_customer_for_request(request):
                pipeline_kwargs = running_pipeline.get('kwargs')

                # Details about the user sent back from the provider.
                details = pipeline_kwargs.get('details')
                email = details.get('email', '')

                # override the email field.
                form_desc.override_field_properties(
                    "email",
                    default=email,
                    restrictions={"readonly": "readonly"} if email else {
                        "min_length": accounts.EMAIL_MIN_LENGTH,
                        "max_length": accounts.EMAIL_MAX_LENGTH,
                    }
                )


def get_login_session_form(request):
    """Return a description of the login form.

    This decouples clients from the API definition:
    if the API decides to modify the form, clients won't need
    to be updated.

    See `user_api.helpers.FormDescription` for examples
    of the JSON-encoded form description.

    Returns:
        HttpResponse

    """
    form_desc = FormDescription("post", reverse("user_api_login_session", kwargs={'api_version': 'v1'}))
    _apply_third_party_auth_overrides(request, form_desc)

    # Translators: This label appears above a field on the login form
    # meant to hold the user's email address.
    email_label = _("Email")

    # Translators: These instructions appear on the login form, immediately
    # below a field meant to hold the user's email address.
    email_instructions = _("The email address you used to register with {platform_name}").format(
        platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
    )

    form_desc.add_field(
        "email",
        field_type="email",
        label=email_label,
        instructions=email_instructions,
        restrictions={
            "min_length": accounts.EMAIL_MIN_LENGTH,
            "max_length": accounts.EMAIL_MAX_LENGTH,
        }
    )

    # Translators: This label appears above a field on the login form
    # meant to hold the user's password.
    password_label = _("Password")

    form_desc.add_field(
        "password",
        label=password_label,
        field_type="password",
        restrictions={'max_length': DEFAULT_MAX_PASSWORD_LENGTH}
    )

    return form_desc


@require_http_methods(['GET'])
@ratelimit(
    key='openedx.core.djangoapps.util.ratelimit.real_ip',
    rate=settings.LOGIN_AND_REGISTER_FORM_RATELIMIT,
    method='GET',
    block=True
)
@ensure_csrf_cookie
@xframe_allow_whitelisted
def login_and_registration_form(request, initial_mode="login"):
    """Render the combined login/registration form, defaulting to login

    This relies on the JS to asynchronously load the actual form from
    the user_api.

    Keyword Args:
        initial_mode (string): Either "login" or "register".

    """
    # Determine the URL to redirect to following login/registration/third_party_auth
    redirect_to = get_next_url_for_login_page(request)

    # If we're already logged in, redirect to the dashboard
    # Note: If the session is valid, we update all logged_in cookies(in particular JWTs)
    #  since Django's SessionAuthentication middleware auto-updates session cookies but not
    #  the other login-related cookies. See ARCH-282 and ARCHBOM-1718
    if request.user.is_authenticated:
        response = redirect(redirect_to)
        response = set_logged_in_cookies(request, response, request.user)
        return response

    # Retrieve the form descriptions from the user API
    form_descriptions = _get_form_descriptions(request)

    # Our ?next= URL may itself contain a parameter 'tpa_hint=x' that we need to check.
    # If present, we display a login page focused on third-party auth with that provider.
    third_party_auth_hint = None
    tpa_hint_provider = None
    if '?' in redirect_to:  # lint-amnesty, pylint: disable=too-many-nested-blocks
        try:
            next_args = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_to).query)
            if 'tpa_hint' in next_args:
                provider_id = next_args['tpa_hint'][0]
                tpa_hint_provider = third_party_auth.provider.Registry.get(provider_id=provider_id)
                if tpa_hint_provider:
                    if tpa_hint_provider.skip_hinted_login_dialog:
                        # Forward the user directly to the provider's login URL when the provider is configured
                        # to skip the dialog.
                        if initial_mode == "register":
                            auth_entry = pipeline.AUTH_ENTRY_REGISTER
                        else:
                            auth_entry = pipeline.AUTH_ENTRY_LOGIN
                        return redirect(
                            pipeline.get_login_url(provider_id, auth_entry, redirect_url=redirect_to)
                        )
                    third_party_auth_hint = provider_id
                    initial_mode = "hinted_login"
        except (KeyError, ValueError, IndexError) as ex:
            log.exception("Unknown tpa_hint provider: %s", ex)

    # Redirect to authn MFE if it is enabled
    # AND
    #   user is not an enterprise user
    # AND
    #   tpa_hint_provider is not available
    # AND
    #   user is not coming from a SAML IDP.
    saml_provider = False
    running_pipeline = pipeline.get(request)
    if running_pipeline:
        saml_provider, __ = third_party_auth.utils.is_saml_provider(
            running_pipeline.get('backend'), running_pipeline.get('kwargs')
        )

    enterprise_customer = enterprise_customer_for_request(request)

    if should_redirect_to_authn_microfrontend() and \
            not enterprise_customer and \
            not tpa_hint_provider and \
            not saml_provider:

        # This is to handle a case where a logged-in cookie is not present but the user is authenticated.
        # Note: If we don't handle this learner is redirected to authn MFE and then back to dashboard
        # instead of the desired redirect URL (e.g. finish_auth) resulting in learners not enrolling
        # into the courses.
        if request.user.is_authenticated and redirect_to:
            return redirect(redirect_to)

        query_params = request.GET.urlencode()
        url_path = '/{}{}'.format(
            initial_mode,
            '?' + query_params if query_params else ''
        )
        return redirect(configuration_helpers.get_value(
            'AUTHN_MICROFRONTEND_URL',
            settings.AUTHN_MICROFRONTEND_URL,
        ) + url_path)

    # Account activation message
    account_activation_messages = [
        {
            'message': message.message, 'tags': message.tags
        } for message in messages.get_messages(request) if 'account-activation' in message.tags
    ]

    account_recovery_messages = [
        {
            'message': message.message, 'tags': message.tags
        } for message in messages.get_messages(request) if 'account-recovery' in message.tags
    ]

    # Otherwise, render the combined login/registration page
    context = {
        'data': {
            'login_redirect_url': redirect_to,
            'initial_mode': initial_mode,
            'third_party_auth': third_party_auth_context(request, redirect_to, third_party_auth_hint),
            'third_party_auth_hint': third_party_auth_hint or '',
            'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            'support_link': configuration_helpers.get_value('SUPPORT_SITE_LINK', settings.SUPPORT_SITE_LINK),
            'password_reset_support_link': configuration_helpers.get_value(
                'PASSWORD_RESET_SUPPORT_LINK', settings.PASSWORD_RESET_SUPPORT_LINK
            ) or settings.SUPPORT_SITE_LINK,
            'account_activation_messages': account_activation_messages,
            'account_recovery_messages': account_recovery_messages,

            # Include form descriptions retrieved from the user API.
            # We could have the JS client make these requests directly,
            # but we include them in the initial page load to avoid
            # the additional round-trip to the server.
            'login_form_desc': json.loads(form_descriptions['login']),
            'registration_form_desc': json.loads(form_descriptions['registration']),
            'password_reset_form_desc': json.loads(form_descriptions['password_reset']),
            'account_creation_allowed': configuration_helpers.get_value(
                'ALLOW_PUBLIC_ACCOUNT_CREATION', settings.FEATURES.get('ALLOW_PUBLIC_ACCOUNT_CREATION', True)),
            'register_links_allowed': settings.FEATURES.get('SHOW_REGISTRATION_LINKS', True),
            'is_account_recovery_feature_enabled': is_secondary_email_feature_enabled(),
            'enterprise_slug_login_url': get_enterprise_slug_login_url(),
            'is_enterprise_enable': enterprise_enabled(),
            'is_require_third_party_auth_enabled': is_require_third_party_auth_enabled(),
            'enable_coppa_compliance': settings.ENABLE_COPPA_COMPLIANCE,
            'edx_user_info_cookie_name': settings.EDXMKTG_USER_INFO_COOKIE_NAME,
        },
        'login_redirect_url': redirect_to,  # This gets added to the query string of the "Sign In" button in header
        'responsive': True,
        'allow_iframing': True,
        'disable_courseware_js': True,
        'combined_login_and_register': True,
        'disable_footer': not configuration_helpers.get_value(
            'ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER',
            settings.FEATURES['ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER']
        ),
    }

    update_logistration_context_for_enterprise(request, context, enterprise_customer)

    response = render_to_response('student_account/login_and_register.html', context)
    handle_enterprise_cookies_for_logistration(request, response, context)

    return response


def _get_form_descriptions(request):
    """Retrieve form descriptions from the user API.

    Arguments:
        request (HttpRequest): The original request, used to retrieve session info.

    Returns:
        dict: Keys are 'login', 'registration', and 'password_reset';
            values are the JSON-serialized form descriptions.

    """

    return {
        'password_reset': get_password_reset_form().to_json(),
        'login': get_login_session_form(request).to_json(),
        'registration': RegistrationFormFactory().get_registration_form(request).to_json()
    }
