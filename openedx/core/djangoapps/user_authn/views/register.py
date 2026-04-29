"""
Registration related views.
"""


import datetime
import json
import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied
from django.core.validators import ValidationError
from django.db import transaction
from django.dispatch import Signal
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django_countries import countries
from django_ratelimit.decorators import ratelimit
from edx_django_utils.monitoring import set_custom_attribute
from edx_django_utils.user import generate_password  # pylint: disable=wrong-import-order
from openedx_events.learning.data import UserData, UserPersonalData
from openedx_events.learning.signals import STUDENT_REGISTRATION_COMPLETED
from openedx_filters.learning.filters import StudentRegistrationRequested
from requests import HTTPError
from rest_framework.response import Response
from rest_framework.views import APIView
from social_core.exceptions import AuthAlreadyAssociated, AuthException
from social_django import utils as social_utils

from common.djangoapps import third_party_auth

# Note that this lives in LMS, so this dependency should be refactored.
# TODO Have the discussions code subscribe to the REGISTER_USER signal instead.
from common.djangoapps.student.helpers import (
    AccountValidationError,
    authenticate_new_user,
    create_or_set_user_attribute_created_on_site,
    do_create_account,
    get_next_url_for_login_page,
    get_redirect_url_with_host,
)
from common.djangoapps.student.models import (
    RegistrationCookieConfiguration,
    UserAttribute,
    create_comments_service_user,
    email_exists_or_retired,
    username_exists_or_retired,
)
from common.djangoapps.student.views import compose_and_send_activation_email
from common.djangoapps.third_party_auth import pipeline, provider
from common.djangoapps.third_party_auth.saml import SAP_SUCCESSFACTORS_SAML_KEY
from common.djangoapps.track import segment
from common.djangoapps.util.db import outer_atomic
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.discussion.notification_prefs.views import enable_notifications
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.safe_sessions.middleware import mark_user_change_as_expected
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts as accounts_settings
from openedx.core.djangoapps.user_api.accounts.api import (
    get_confirm_email_validation_error,
    get_country_validation_error,
    get_email_existence_validation_error,
    get_email_validation_error,
    get_name_validation_error,
    get_password_validation_error,
    get_username_existence_validation_error,
    get_username_validation_error,
)
from openedx.core.djangoapps.user_api.preferences import api as preferences_api
from openedx.core.djangoapps.user_authn.cookies import set_logged_in_cookies
from openedx.core.djangoapps.user_authn.tasks import check_pwned_password_and_send_track_event
from openedx.core.djangoapps.user_authn.toggles import (
    is_auto_generated_username_enabled,
    is_require_third_party_auth_enabled,
)
from openedx.core.djangoapps.user_authn.utils import generate_username_suggestions, is_registration_api_v1
from openedx.core.djangoapps.user_authn.views.registration_form import (
    AccountCreationForm,
    RegistrationFormFactory,
    get_registration_extension_form,
)
from openedx.core.djangoapps.user_authn.views.utils import get_auto_generated_username

log = logging.getLogger("edx.student")
AUDIT_LOG = logging.getLogger("audit")


# Used as the name of the user attribute for tracking affiliate registrations
REGISTRATION_AFFILIATE_ID = 'registration_affiliate_id'
REGISTRATION_UTM_PARAMETERS = {
    'utm_source': 'registration_utm_source',
    'utm_medium': 'registration_utm_medium',
    'utm_campaign': 'registration_utm_campaign',
    'utm_term': 'registration_utm_term',
    'utm_content': 'registration_utm_content',
}
REGISTRATION_UTM_CREATED_AT = 'registration_utm_created_at'
IS_MARKETABLE = 'is_marketable'
# used to announce a registration
# providing_args=["user", "registration"]
REGISTER_USER = Signal()


REAL_IP_KEY = 'openedx.core.djangoapps.util.ratelimit.real_ip'


@transaction.non_atomic_requests
def create_account_with_params(request, params):  # pylint: disable=too-many-statements
    """
    Given a request and a dict of parameters (which may or may not have come
    from the request), create an account for the requesting user, including
    creating a comments service user object and sending an activation email.
    This also takes external/third-party auth into account, updates that as
    necessary, and authenticates the user for the request's session.

    Does not return anything.

    Raises AccountValidationError if an account with the username or email
    specified by params already exists, or ValidationError if any of the given
    parameters is invalid for any other reason.

    Issues with this code:
    * It is non-transactional except where explicitly wrapped in atomic to
      alleviate deadlocks and improve performance. This means failures at
      different places in registration can leave users in inconsistent
      states.
    * Third-party auth passwords are not verified. There is a comment that
      they are unused, but it would be helpful to have a sanity check that
      they are sane.
    * The user-facing text is rather unfriendly (e.g. "Username must be a
      minimum of two characters long" rather than "Please use a username of
      at least two characters").
    * Duplicate email raises a ValidationError (rather than the expected
      AccountValidationError). Duplicate username returns an inconsistent
      user message (i.e. "An account with the Public Username '{username}'
      already exists." rather than "It looks like {username} belongs to an
      existing account. Try again with a different username.") The two checks
      occur at different places in the code; as a result, registering with
      both a duplicate username and email raises only a ValidationError for
      email only.
    """
    # Copy params so we can modify it; we can't just do dict(params) because if
    # params is request.POST, that results in a dict containing lists of values
    params = dict(list(params.items()))

    # allow to define custom set of required/optional/hidden fields via configuration
    extra_fields = configuration_helpers.get_value(
        'REGISTRATION_EXTRA_FIELDS',
        getattr(settings, 'REGISTRATION_EXTRA_FIELDS', {})
    )
    if is_registration_api_v1(request):
        if 'confirm_email' in extra_fields:
            del extra_fields['confirm_email']

    if settings.ENABLE_COPPA_COMPLIANCE and 'year_of_birth' in params:
        params['year_of_birth'] = ''

    # registration via third party (Google, Facebook) using mobile application
    # doesn't use social auth pipeline (no redirect uri(s) etc involved).
    # In this case all related info (required for account linking)
    # is sent in params.
    # `third_party_auth_credentials_in_api` essentially means 'request
    # is made from mobile application'
    third_party_auth_credentials_in_api = 'provider' in params
    is_third_party_auth_enabled = third_party_auth.is_enabled()

    if is_third_party_auth_enabled and (pipeline.running(request) or third_party_auth_credentials_in_api):
        params["password"] = generate_password()

    # in case user is registering via third party (Google, Facebook) and pipeline has expired, show appropriate
    # error message
    if is_third_party_auth_enabled and ('social_auth_provider' in params and not pipeline.running(request)):
        raise ValidationError(
            {
                'session_expired': [
                    _("Registration using {provider} has timed out.").format(
                        provider=params.get('social_auth_provider'))
                ],
                'error_code': 'tpa-session-expired',
            }
        )

    if is_third_party_auth_enabled:
        set_custom_attribute('register_user_tpa', pipeline.running(request))
    extended_profile_fields = configuration_helpers.get_value('extended_profile_fields', [])
    # Can't have terms of service for certain SHIB users, like at Stanford
    tos_required = (
        extra_fields.get('terms_of_service') != 'hidden' or
        extra_fields.get('honor_code') != 'hidden'
    )

    form = AccountCreationForm(
        data=params,
        extra_fields=extra_fields,
        extended_profile_fields=extended_profile_fields,
        do_third_party_auth=False,
        tos_required=tos_required,
    )
    custom_form = get_registration_extension_form(data=params)
    is_marketable = params.get('marketing_emails_opt_in') in ['true', '1']

    # Perform operations within a transaction that are critical to account creation
    with outer_atomic():
        # first, create the account
        (user, profile, registration) = do_create_account(form, custom_form)

        third_party_provider, running_pipeline = _link_user_to_third_party_provider(
            is_third_party_auth_enabled, third_party_auth_credentials_in_api, user, request, params,
        )

        new_user = authenticate_new_user(request, user.username, form.cleaned_data['password'])
        django_login(request, new_user)
        request.session.set_expiry(0)

    try:
        _record_is_marketable_attribute(is_marketable, new_user)
    # Don't prevent a user from registering if is_marketable is not being set.
    # Also update the is_marketable value to None so that it is consistent with
    # our database when we send it to segment.
    except Exception:   # pylint: disable=broad-except
        log.exception('Error while setting is_marketable attribute.')
        is_marketable = None

    _track_user_registration(user, profile, params, third_party_provider, registration, is_marketable, request=request)

    # Sites using multiple languages need to record the language used during registration.
    # If not, compose_and_send_activation_email will be sent in site's default language only.
    create_or_set_user_attribute_created_on_site(user, request.site)

    # Only add a default user preference if user does not already has one.
    if not preferences_api.has_user_preference(user, LANGUAGE_KEY):
        preferences_api.set_user_preference(user, LANGUAGE_KEY, get_language())

    # Check if system is configured to skip activation email for the current user.
    skip_email = _skip_activation_email(
        user, running_pipeline, third_party_provider,
    )

    if skip_email:
        registration.activate()
    else:
        redirect_to, root_url = get_next_url_for_login_page(request, include_host=True)
        redirect_url = get_redirect_url_with_host(root_url, redirect_to)
        compose_and_send_activation_email(user, profile, registration, redirect_url, True)

    if settings.FEATURES.get('ENABLE_DISCUSSION_EMAIL_DIGEST'):
        try:
            enable_notifications(user)
        except Exception:  # pylint: disable=broad-except
            log.exception(f"Enable discussion notifications failed for user {user.id}.")

    # Announce registration
    REGISTER_USER.send(sender=None, user=user, registration=registration)

    # .. event_implemented_name: STUDENT_REGISTRATION_COMPLETED
    # .. event_type: org.openedx.learning.student.registration.completed.v1
    STUDENT_REGISTRATION_COMPLETED.send_event(
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

    create_comments_service_user(user)

    try:
        _record_registration_attributions(request, new_user)
    # Don't prevent a user from registering due to attribution errors.
    except Exception:   # pylint: disable=broad-except
        log.exception('Error while attributing cookies to user registration.')

    # TODO: there is no error checking here to see that the user actually logged in successfully,
    # and is not yet an active user.
    is_new_user(form.cleaned_data['password'], new_user)
    return new_user


def is_new_user(password, user):
    if user is not None:
        AUDIT_LOG.info(f"Login success on new account creation - {user.username}")
        check_pwned_password_and_send_track_event.delay(
            user_id=user.id,
            password=password,
            internal_user=user.is_staff,
            is_new_user=True,
            request_page='registration'
        )


def _link_user_to_third_party_provider(
    is_third_party_auth_enabled,
    third_party_auth_credentials_in_api,
    user,
    request,
    params,
):
    """
    If a 3rd party auth provider and credentials were provided in the API, link the account with social auth
    (If the user is using the normal register page, the social auth pipeline does the linking, not this code)

    Note: this is orthogonal to the 3rd party authentication pipeline that occurs
    when the account is created via the browser and redirect URLs.
    """
    third_party_provider, running_pipeline = None, None
    if is_third_party_auth_enabled and third_party_auth_credentials_in_api:
        backend_name = params['provider']
        request.social_strategy = social_utils.load_strategy(request)
        redirect_uri = reverse('social:complete', args=(backend_name, ))
        request.backend = social_utils.load_backend(request.social_strategy, backend_name, redirect_uri)
        social_access_token = params.get('access_token')
        if not social_access_token:
            raise ValidationError({
                'access_token': [
                    _("An access_token is required when passing value ({}) for provider.").format(
                        params['provider']
                    )
                ],
                'error_code': 'tpa-missing-access-token'
            })
        request.session[pipeline.AUTH_ENTRY_KEY] = pipeline.AUTH_ENTRY_REGISTER_API
        pipeline_user = None
        error_message = ""
        error_code = None
        try:
            pipeline_user = request.backend.do_auth(social_access_token, user=user)
        except AuthAlreadyAssociated:
            error_message = _("The provided access_token is already associated with another user.")
            error_code = 'tpa-token-already-associated'
        except (HTTPError, AuthException):
            error_message = _("The provided access_token is not valid.")
            error_code = 'tpa-invalid-access-token'
        if not pipeline_user or not isinstance(pipeline_user, User):
            # Ensure user does not re-enter the pipeline
            request.social_strategy.clean_partial_pipeline(social_access_token)
            raise ValidationError({'access_token': [error_message], 'error_code': error_code})

    # If the user is registering via 3rd party auth, track which provider they use
    if is_third_party_auth_enabled and pipeline.running(request):
        running_pipeline = pipeline.get(request)
        third_party_provider = provider.Registry.get_from_pipeline(running_pipeline)

    return third_party_provider, running_pipeline


def _track_user_registration(user, profile, params, third_party_provider, registration, is_marketable, request=None):
    """ Track the user's registration. """
    if hasattr(settings, 'LMS_SEGMENT_KEY') and settings.LMS_SEGMENT_KEY:
        anonymous_id = ""
        try:
            anonymous_id = request.COOKIES.get('ajs_anonymous_id', "")
        except:       # pylint: disable=bare-except
            pass
        traits = {
            'email': user.email,
            'username': user.username,
            'name': profile.name,
            # Mailchimp requires the age & yearOfBirth to be integers, we send a sane integer default if falsey.
            'age': profile.age or -1,
            'yearOfBirth': profile.year_of_birth or datetime.datetime.now(ZoneInfo("UTC")).year,
            'education': profile.level_of_education_display,
            'address': profile.mailing_address,
            'gender': profile.gender_display,
            'country': str(profile.country),
            'is_marketable': is_marketable,
            'anonymous_id': anonymous_id
        }
        if settings.MARKETING_EMAILS_OPT_IN and params.get('marketing_emails_opt_in'):
            email_subscribe = 'subscribed' if is_marketable else 'unsubscribed'
            traits['email_subscribe'] = email_subscribe

        # .. pii: Many pieces of PII are sent to Segment here. Retired directly through Segment API call in Tubular.
        # .. pii_types: email_address, username, name, birth_date, location, gender
        # .. pii_retirement: third_party
        segment.identify(user.id, traits)
        properties = {
            'category': 'conversion',
            # ..pii: Learner email is sent to Segment in following line and will be associated with analytics data.
            'email': user.email,
            'label': params.get('course_id'),
            'provider': third_party_provider.name if third_party_provider else None,
            'is_gender_selected': bool(profile.gender_display),
            'is_year_of_birth_selected': bool(profile.year_of_birth),
            'is_education_selected': bool(profile.level_of_education_display),
            'is_goal_set': bool(profile.goals),
            'total_registration_time': round(
                float(params.get('total_registration_time') or params.get('totalRegistrationTime') or 0)
            ),
            'activation_key': registration.activation_key if registration else None,
            'host': params.get('host', ''),
            'app_name': params.get('app_name', ''),
            'utm_campaign': params.get('utm_campaign', ''),
            'anonymous_id': anonymous_id
        }
        # VAN-738 - added below properties to experiment marketing emails opt in/out events on Braze.
        if params.get('marketing_emails_opt_in') and settings.MARKETING_EMAILS_OPT_IN:
            properties['marketing_emails_opt_in'] = is_marketable

        # DENG-803: For segment events forwarded along to Hubspot, duplicate the `properties` section of
        # the event payload into the `traits` section so that they can be received. This is a temporary
        # fix until we implement this behavior outside of the LMS.
        # TODO: DENG-805: remove the properties duplication in the event traits.
        segment_traits = dict(properties)
        segment_traits['user_id'] = user.id
        segment_traits['joined_date'] = user.date_joined.strftime("%Y-%m-%d")
        segment.track(
            user.id,
            "edx.bi.user.account.registered",
            properties=properties,
            traits=segment_traits,
        )


def _skip_activation_email(user, running_pipeline, third_party_provider):
    """
    Return `True` if activation email should be skipped.

    Skip email if we are:
        1. Doing load testing.
        2. Random user generation for other forms of testing.
        3. External auth bypassing activation.
        4. Have the platform configured to not require e-mail activation.
        5. Registering a new user using a trusted third party provider (with skip_email_verification=True)

    Note that this feature is only tested as a flag set one way or
    the other for *new* systems. we need to be careful about
    changing settings on a running system to make sure no users are
    left in an inconsistent state (or doing a migration if they are).

    Arguments:
        user (User): Django User object for the current user.
        running_pipeline (dict): Dictionary containing user and pipeline data for third party authentication.
        third_party_provider (ProviderConfig): An instance of third party provider configuration.

    Returns:
        (bool): `True` if account activation email should be skipped, `False` if account activation email should be
            sent.
    """
    sso_pipeline_email = running_pipeline and running_pipeline['kwargs'].get('details', {}).get('email')

    # Email is valid if the SAML assertion email matches the user account email or
    # no email was provided in the SAML assertion. Some IdP's use a callback
    # to retrieve additional user account information (including email) after the
    # initial account creation.
    valid_email = (
        sso_pipeline_email == user.email or (
            sso_pipeline_email is None and
            third_party_provider and
            getattr(third_party_provider, "identity_provider_type", None) == SAP_SUCCESSFACTORS_SAML_KEY
        )
    )

    # log the cases where skip activation email flag is set, but email validity check fails
    if third_party_provider and third_party_provider.skip_email_verification and not valid_email:
        log.info(
            '[skip_email_verification=True][user=%s][pipeline-email=%s][identity_provider=%s][provider_type=%s] '
            'Account activation email sent as user\'s system email differs from SSO email.',
            user.email,
            sso_pipeline_email,
            getattr(third_party_provider, "provider_id", None),
            getattr(third_party_provider, "identity_provider_type", None)
        )

    return (
        settings.FEATURES.get('SKIP_EMAIL_VALIDATION', None) or
        settings.FEATURES.get('AUTOMATIC_AUTH_FOR_TESTING') or
        (third_party_provider and third_party_provider.skip_email_verification and valid_email)
    )


def _record_is_marketable_attribute(is_marketable, user):
    """
    Attribute this user's registration based on form data
    """
    if settings.MARKETING_EMAILS_OPT_IN and user:
        UserAttribute.set_user_attribute(user, IS_MARKETABLE, str(is_marketable).lower())


def _record_registration_attributions(request, user):
    """
    Attribute this user's registration based on referrer cookies.
    """
    _record_affiliate_registration_attribution(request, user)
    _record_utm_registration_attribution(request, user)


def _record_affiliate_registration_attribution(request, user):
    """
    Attribute this user's registration to the referring affiliate, if
    applicable.
    """
    affiliate_id = request.COOKIES.get(settings.AFFILIATE_COOKIE_NAME)
    if user and affiliate_id:
        UserAttribute.set_user_attribute(user, REGISTRATION_AFFILIATE_ID, affiliate_id)


def _record_utm_registration_attribution(request, user):
    """
    Attribute this user's registration to the latest UTM referrer, if
    applicable.
    """
    utm_cookie_name = RegistrationCookieConfiguration.current().utm_cookie_name
    utm_cookie = request.COOKIES.get(utm_cookie_name)
    if user and utm_cookie:
        utm = json.loads(utm_cookie)
        for utm_parameter_name in REGISTRATION_UTM_PARAMETERS:
            utm_parameter = utm.get(utm_parameter_name)
            if utm_parameter:
                UserAttribute.set_user_attribute(
                    user,
                    REGISTRATION_UTM_PARAMETERS.get(utm_parameter_name),
                    utm_parameter
                )
        created_at_unixtime = utm.get('created_at')
        if created_at_unixtime:
            # We divide by 1000 here because the javascript timestamp generated is in milliseconds not seconds.
            # PYTHON: time.time()      => 1475590280.823698
            # JS: new Date().getTime() => 1475590280823
            created_at_datetime = datetime.datetime.fromtimestamp(
                int(created_at_unixtime) / float(1000), tz=ZoneInfo("UTC")
            )
            UserAttribute.set_user_attribute(
                user,
                REGISTRATION_UTM_CREATED_AT,
                created_at_datetime
            )


class RegistrationView(APIView):
    # pylint: disable=missing-docstring
    """HTTP end-points for creating a new user. """

    # This end-point is available to anonymous users,
    # so do not require authentication.
    authentication_classes = []

    @method_decorator(transaction.non_atomic_requests)
    @method_decorator(sensitive_post_parameters("password"))
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return HttpResponse(RegistrationFormFactory().get_registration_form(request).to_json(),  # pylint: disable=http-response-with-content-type-json
                            content_type="application/json")

    @method_decorator(csrf_exempt)
    @method_decorator(ratelimit(key=REAL_IP_KEY, rate=settings.REGISTRATION_RATELIMIT, method='POST', block=False))
    def post(self, request):
        """Create the user's account.

        You must send all required form fields with the request.

        You can optionally send a "course_id" param to indicate in analytics
        events that the user registered while enrolling in a particular course.

        Arguments:
            request (HTTPRequest)

        Returns:
            HttpResponse: 200 on success
            HttpResponse: 400 if the request is not valid.
            HttpResponse: 409 if an account with the given username or email
                address already exists
            HttpResponse: 403 operation not allowed
        """
        should_be_rate_limited = getattr(request, 'limited', False)
        if should_be_rate_limited:
            return JsonResponse({'error_code': 'forbidden-request'}, status=403)

        if is_require_third_party_auth_enabled() and not pipeline.running(request):
            # if request is not running a third-party auth pipeline
            return HttpResponseForbidden(
                "Third party authentication is required to register. Username and password were received instead."
            )

        data = request.POST.copy()
        self._handle_terms_of_service(data)

        if is_auto_generated_username_enabled() and 'username' not in data:
            data['username'] = get_auto_generated_username(data)

        try:
            # .. filter_implemented_name: StudentRegistrationRequested
            # .. filter_type: org.openedx.learning.student.registration.requested.v1
            data = StudentRegistrationRequested.run_filter(form_data=data)
        except StudentRegistrationRequested.PreventRegistration as exc:
            errors = {
                "error_message": [{"user_message": str(exc)}],
            }
            error_code = getattr(exc, "error_code", None)
            return self._create_response(request, errors, status_code=exc.status_code, error_code=error_code)

        response = self._handle_duplicate_email_username(request, data)
        if response:
            return response

        response = self._handle_country_code_validation(request, data)
        if response:
            return response

        response, user = self._create_account(request, data)
        if response:
            return response

        redirect_to, root_url = get_next_url_for_login_page(request, include_host=True)
        redirect_url = get_redirect_url_with_host(root_url, redirect_to)
        authenticated_user = {'username': user.username, 'full_name': user.profile.name, 'user_id': user.id}
        response = self._create_response(
            request, {'authenticated_user': authenticated_user}, status_code=200, redirect_url=redirect_url
        )
        set_logged_in_cookies(request, response, user)
        if not user.is_active and settings.SHOW_ACCOUNT_ACTIVATION_CTA and not settings.MARKETING_EMAILS_OPT_IN:
            response.set_cookie(
                settings.SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME,
                True,
                domain=settings.SESSION_COOKIE_DOMAIN,
                path='/',
                secure=request.is_secure()
            )  # setting the cookie to show account activation dialogue in platform and learning MFE
        mark_user_change_as_expected(user.id)
        return response

    def _handle_country_code_validation(self, request, data):
        # pylint: disable=no-member
        country = data.get('country')
        is_valid_country_code = country in dict(countries).keys()

        errors = {}
        error_code = 'invalid-country'
        error_message = accounts_settings.REQUIRED_FIELD_COUNTRY_MSG
        extra_fields = configuration_helpers.get_value(
            'REGISTRATION_EXTRA_FIELDS',
            getattr(settings, 'REGISTRATION_EXTRA_FIELDS', {})
        )

        if extra_fields.get('country', 'hidden') == 'required' and not is_valid_country_code:
            errors['country'] = [{'user_message': error_message}]
        elif country and not is_valid_country_code:
            errors['country'] = [{'user_message': error_message}]

        if errors:
            return self._create_response(request, errors, status_code=400, error_code=error_code)

    def _handle_duplicate_email_username(self, request, data):
        # pylint: disable=no-member
        # TODO Verify whether this check is needed here - it may be duplicated in user_api.
        email = data.get('email')
        username = data.get('username')
        errors = {}

        error_code = 'duplicate'
        if email is not None and email_exists_or_retired(email):
            error_code += '-email'
            error_message = accounts_settings.AUTHN_EMAIL_CONFLICT_MSG
            errors['email'] = [{'user_message': error_message}]

        if username is not None and username_exists_or_retired(username):
            error_code += '-username'
            error_message = accounts_settings.AUTHN_USERNAME_CONFLICT_MSG
            errors['username'] = [{'user_message': error_message}]
            errors['username_suggestions'] = generate_username_suggestions(username)

        if errors:
            return self._create_response(request, errors, status_code=409, error_code=error_code)

    def _handle_terms_of_service(self, data):
        # Backwards compatibility: the student view expects both
        # terms of service and honor code values.  Since we're combining
        # these into a single checkbox, the only value we may get
        # from the new view is "honor_code".
        # Longer term, we will need to make this more flexible to support
        # open source installations that may have separate checkboxes
        # for TOS, privacy policy, etc.
        if data.get("honor_code") and "terms_of_service" not in data:
            data["terms_of_service"] = data["honor_code"]

    def _create_account(self, request, data):
        response, user = None, None
        try:
            user = create_account_with_params(request, data)
        except AccountValidationError as err:
            errors = {
                err.field: [{"user_message": str(err)}]
            }
            response = self._create_response(request, errors, status_code=409, error_code=err.error_code)
        except ValidationError as err:
            # Should only get field errors from this exception
            assert NON_FIELD_ERRORS not in err.message_dict  # noqa: PT017

            # Error messages are returned as arrays from ValidationError
            error_code = err.message_dict.get('error_code', ['validation-error'])[0]

            # Only return first error for each field
            errors = {
                field: [{"user_message": error} for error in error_list]
                for field, error_list in err.message_dict.items() if field != 'error_code'
            }
            response = self._create_response(request, errors, status_code=400, error_code=error_code)
        except PermissionDenied:
            response = HttpResponseForbidden(_("Account creation not allowed."))

        return response, user

    def _create_response(self, request, response_dict, status_code, redirect_url=None, error_code=None):
        if status_code == 200:
            # keeping this `success` field in for now, as we have outstanding clients expecting this
            response_dict['success'] = True
        if redirect_url:
            response_dict['redirect_url'] = redirect_url
        if error_code:
            response_dict['error_code'] = error_code
            set_custom_attribute('register_error_code', error_code)
        return JsonResponse(response_dict, status=status_code)


# pylint: disable=line-too-long
class RegistrationValidationView(APIView):
    """
        **Use Cases**

            Get validation information about user data during registration.
            Client-side may request validation for any number of form fields,
            and the API will return a conclusion from its analysis for each
            input (i.e. valid or not valid, or a custom, detailed message).

        **Example Requests and Responses**

            - Checks the validity of the username and email inputs separately.
            POST /api/user/v1/validation/registration/
            >>> {
            >>>     "username": "hi_im_new",
            >>>     "email": "newguy101@edx.org"
            >>> }
            RESPONSE
            >>> {
            >>>     "validation_decisions": {
            >>>         "username": "",
            >>>         "email": ""
            >>>     }
            >>> }
            Empty strings indicate that there was no problem with the input.

            - Checks the validity of the password field (its validity depends
              upon both the username and password fields, so we need both). If
              only password is input, we don't check for password/username
              compatibility issues.
            POST /api/user/v1/validation/registration/
            >>> {
            >>>     "username": "myname",
            >>>     "password": "myname"
            >>> }
            RESPONSE
            >>> {
            >>>     "validation_decisions": {
            >>>         "username": "",
            >>>         "password": "Password cannot be the same as the username."
            >>>     }
            >>> }

            - Checks the validity of the username, email, and password fields
              separately, and also tells whether an account exists. The password
              field's validity depends upon both the username and password, and
              the account's existence depends upon both the username and email.
            POST /api/user/v1/validation/registration/
            >>> {
            >>>     "username": "hi_im_new",
            >>>     "email": "cto@edx.org",
            >>>     "password": "p"
            >>> }
            RESPONSE
            >>> {
            >>>     "validation_decisions": {
            >>>         "username": "",
            >>>         "email": "It looks like cto@edx.org belongs to an existing account. Try again with a different email address.",
            >>>         "password": "Password must be at least 2 characters long",
            >>>     }
            >>> }
            In this example, username is valid and (we assume) there is
            a preexisting account with that email. The password also seems
            to contain the username.

            Note that a validation decision is returned *for all* inputs, whether
            positive or negative.

        **Available Handlers**

            "name":
                A handler to check the validity of the user's real name.
            "username":
                A handler to check the validity of usernames.
            "email":
                A handler to check the validity of emails.
            "confirm_email":
                A handler to check whether the confirmation email field matches
                the email field.
            "password":
                A handler to check the validity of passwords; a compatibility
                decision with the username is made if it exists in the input.
            "country":
                A handler to check whether the validity of country fields.
    """

    # This end-point is available to anonymous users, so no authentication is needed.
    authentication_classes = []
    username_suggestions = []

    def name_handler(self, request):
        """ Validates whether fullname is valid """
        name = request.data.get('name')
        validation_error = get_name_validation_error(name)
        if validation_error:
            return validation_error
        self.username_suggestions = generate_username_suggestions(name)
        return validation_error

    def username_handler(self, request):
        """ Validates whether the username is valid. """
        username = request.data.get('username')
        invalid_username_error = get_username_validation_error(username)
        username_exists_error = get_username_existence_validation_error(username)
        if username_exists_error:
            self.username_suggestions = generate_username_suggestions(username)
        # We prefer seeing for invalidity first.
        # Some invalid usernames (like for superusers) may exist.
        return invalid_username_error or username_exists_error

    def email_handler(self, request):
        """ Validates whether the email address is valid. """
        email = request.data.get('email')
        invalid_email_error = get_email_validation_error(email)
        email_exists_error = get_email_existence_validation_error(email)
        # We prefer seeing for invalidity first.
        # Some invalid emails (like a blank one for superusers) may exist.
        return invalid_email_error or email_exists_error

    def confirm_email_handler(self, request):
        """ Confirm email validator """
        email = request.data.get('email')
        confirm_email = request.data.get('confirm_email')
        return get_confirm_email_validation_error(confirm_email, email)

    def password_handler(self, request):
        """ Password validator """
        username = request.data.get('username')
        email = request.data.get('email')
        password = request.data.get('password')
        reset_password_page = request.data.get('reset_password_page', 'false') == 'true'
        return get_password_validation_error(password, username, email, reset_password_page)

    def country_handler(self, request):
        """ Country validator """
        country = request.data.get('country')
        return get_country_validation_error(country)

    validation_handlers = {
        "name": name_handler,
        "username": username_handler,
        "email": email_handler,
        "confirm_email": confirm_email_handler,
        "password": password_handler,
        "country": country_handler
    }

    @method_decorator(
        ratelimit(key=REAL_IP_KEY, rate=settings.REGISTRATION_VALIDATION_RATELIMIT, method='POST', block=True)
    )
    def post(self, request):
        """
        POST /api/user/v1/validation/registration/

        Expects request of the form
        ```
        {
            "name": "Dan the Validator",
            "username": "mslm",
            "email": "mslm@gmail.com",
            "confirm_email": "mslm@gmail.com",
            "password": "password123",
            "country": "PK"
        }
        ```
        where each key is the appropriate form field name and the value is
        user input. One may enter individual inputs if needed. Some inputs
        can get extra verification checks if entered along with others,
        like when the password may not equal the username.
        """
        field_key = request.data.get('form_field_key')
        validation_decisions = {}

        def update_validations(field_name):
            """
            Updates the validation decisions
            """
            validation = self.validation_handlers[field_name](self, request)
            validation_decisions[field_name] = validation

        if field_key and field_key in self.validation_handlers:
            update_validations(field_key)
        else:
            for form_field_key in self.validation_handlers:
                # For every field requiring validation from the client,
                # request a decision for it from the appropriate handler.
                if form_field_key in request.data:
                    update_validations(form_field_key)

        response_dict = {'validation_decisions': validation_decisions}
        if self.username_suggestions:
            response_dict['username_suggestions'] = self.username_suggestions

        return Response(response_dict)
