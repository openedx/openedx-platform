""" Password reset logic and views . """
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN, PasswordResetConfirmView
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import ValidationError
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, int_to_base36, urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from edx_ace import ace
from edx_ace.recipient import Recipient
from eventtracking import tracker
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.student.forms import send_account_recovery_email_for_user
from common.djangoapps.student.models import AccountRecovery, LoginFailures
from common.djangoapps.util.json_request import JsonResponse
from common.djangoapps.util.password_policy_validators import normalize_password, validate_password
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.oauth_dispatch.api import destroy_oauth_tokens
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_request, get_current_site
from openedx.core.djangoapps.user_api import accounts, errors, helpers
from openedx.core.djangoapps.user_api.accounts.utils import is_secondary_email_feature_enabled
from openedx.core.djangoapps.user_api.helpers import FormDescription
from openedx.core.djangoapps.user_api.models import UserRetirementRequest
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.djangoapps.user_authn.message_types import PasswordReset, PasswordResetSuccess
from openedx.core.djangoapps.user_authn.toggles import should_redirect_to_authn_microfrontend
from openedx.core.djangoapps.user_authn.utils import check_pwned_password
from openedx.core.djangolib.markup import HTML

POST_EMAIL_KEY = 'openedx.core.djangoapps.util.ratelimit.request_post_email'
REAL_IP_KEY = 'openedx.core.djangoapps.util.ratelimit.real_ip'
SETTING_CHANGE_INITIATED = 'edx.user.settings.change_initiated'
PASSWORD_RESET_INITIATED = 'edx.user.passwordreset.initiated'

# Maintaining this naming for backwards compatibility.
log = logging.getLogger("edx.student")
AUDIT_LOG = logging.getLogger("audit")


def get_user_default_email_params(user):
    """
    Get default email params for the user.
    """
    site = get_current_site()
    message_context = get_base_template_context(site)
    user_language_pref = get_user_preference(user, LANGUAGE_KEY)
    return [message_context, user_language_pref]


def get_password_reset_form():
    """Return a description of the password reset form.

    This decouples clients from the API definition:
    if the API decides to modify the form, clients won't need
    to be updated.

    See `user_api.helpers.FormDescription` for examples
    of the JSON-encoded form description.

    Returns:
        HttpResponse

    """
    form_desc = FormDescription("post", reverse("password_change_request"))

    # Translators: This label appears above a field on the password reset
    # form meant to hold the user's email address.
    email_label = _("Email")

    # Translators: This example email address is used as a placeholder in
    # a field on the password reset form meant to hold the user's email address.
    email_placeholder = _("username@domain.com")

    # Translators: These instructions appear on the password reset form,
    # immediately below a field meant to hold the user's email address.
    # pylint: disable=no-member
    email_instructions = _("The email address you used to register with {platform_name}").format(
        platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
    )

    form_desc.add_field(
        "email",
        field_type="email",
        label=email_label,
        placeholder=email_placeholder,
        instructions=email_instructions,
        restrictions={
            "min_length": accounts.EMAIL_MIN_LENGTH,
            "max_length": accounts.EMAIL_MAX_LENGTH,
        }
    )

    return form_desc


def send_password_reset_success_email(user, request):
    """
    Send an email to user indicating that password reset was successful.

    Arguments:
        user (User): Django User object
        request (HttpRequest): Django request object
    """
    message_context, user_language_preference = get_user_default_email_params(user)
    lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    message_context.update(
        {'login_link': f'{lms_root_url}/login', 'request': request, }
    )

    msg = PasswordResetSuccess(context=message_context).personalize(
        recipient=Recipient(user.id, user.email),
        language=user_language_preference,
        user_context={"name": user.profile.name},
    )
    try:
        ace.send(msg)
    except Exception:  # pylint: disable=broad-except
        log.exception('PasswordResetSuccess: sending email to user [%s] failed.', user.username)


def send_password_reset_email_for_user(user, request, preferred_email=None):
    """
    Send out a password reset email for the given user.

    Arguments:
        user (User): Django User object
        request (HttpRequest): Django request object
        preferred_email (str): Send email to this address if present, otherwise fallback to user's email address.
    """
    message_context, user_language_preference = get_user_default_email_params(user)
    site_name = settings.AUTHN_MICROFRONTEND_DOMAIN if should_redirect_to_authn_microfrontend() \
        else configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME)
    message_context.update({
        'request': request,  # Used by google_analytics_tracking_pixel
        # TODO: This overrides `platform_name` from `get_base_template_context` to make the tests passes
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'reset_link': '{protocol}://{site}{link}?track=pwreset'.format(
            protocol='https' if request.is_secure() else 'http',
            site=site_name,
            link=reverse('password_reset_confirm', kwargs={
                'uidb36': int_to_base36(user.id),
                'token': default_token_generator.make_token(user),
            }),
        )
    })

    msg = PasswordReset().personalize(
        recipient=Recipient(user.id, preferred_email or user.email),
        language=user_language_preference,
        user_context=message_context,
    )
    ace.send(msg)


class PasswordResetFormNoActive(PasswordResetForm):
    """
    A modified version of the default Django password reset form to handle
    unknown or unusable email addresses without leaking data.
    """
    error_messages = {
        'unknown': _("That e-mail address doesn't have an associated "
                     "user account. Are you sure you've registered?"),
        'unusable': _("The user account associated with this e-mail "
                      "address cannot reset the password."),
    }

    is_account_recovery = True
    users_cache = []

    def clean_email(self):
        """
        This is a literal copy from Django 1.4.5's django.contrib.auth.forms.PasswordResetForm
        Except removing the requirement of active users
        Validates that a user exists with the given email address.
        """
        email = self.cleaned_data["email"]
        # The line below contains the only change, removing is_active=True
        self.users_cache = User.objects.filter(email__iexact=email)

        if not self.users_cache and is_secondary_email_feature_enabled():
            # Check if user has entered the secondary email.
            self.users_cache = User.objects.filter(
                id__in=AccountRecovery.objects.filter(secondary_email__iexact=email, is_active=True).values_list('user')
            )
            self.is_account_recovery = not bool(self.users_cache)

        if not self.users_cache:
            raise forms.ValidationError(self.error_messages['unknown'])
        if any((user.password.startswith(UNUSABLE_PASSWORD_PREFIX))
               for user in self.users_cache):
            raise forms.ValidationError(self.error_messages['unusable'])
        return email

    def save(self,  # pylint: disable=arguments-differ
             use_https=False,
             token_generator=default_token_generator,
             request=None,
             **_kwargs):
        """
        Generates a one-use only link for resetting password and sends to the
        user.
        """
        for user in self.users_cache:
            if self.is_account_recovery:
                send_password_reset_email_for_user(user, request)
            else:
                send_account_recovery_email_for_user(user, request, user.account_recovery.secondary_email)


class PasswordResetView(APIView):
    """HTTP end-point for GETting a description of the password reset form. """

    # This end-point is available to anonymous users,
    # so do not require authentication.
    authentication_classes = []

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return HttpResponse(get_password_reset_form().to_json(), content_type="application/json")  # pylint: disable=http-response-with-content-type-json


@helpers.intercept_errors(errors.UserAPIInternalError, ignore_errors=[errors.UserAPIRequestError])
def request_password_change(email, is_secure):
    """Email a single-use link for performing a password reset.

    Users must confirm the password change before we update their information.

    Args:
        email (str): An email address
        orig_host (str): An originating host, extracted from a request with get_host
        is_secure (bool): Whether the request was made with HTTPS

    Returns:
        None

    Raises:
        errors.UserNotFound
        AccountRequestError
        errors.UserAPIInternalError: the operation failed due to an unexpected error.

    """
    # Binding data to a form requires that the data be passed as a dictionary
    # to the Form class constructor.
    form = PasswordResetFormNoActive({'email': email})

    # Validate that a user exists with the given email address.
    if form.is_valid():
        # Generate a single-use link for performing a password reset
        # and email it to the user.
        form.save(
            from_email=configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL),
            use_https=is_secure,
            request=get_current_request(),
        )
    else:
        # No user with the provided email address exists.
        raise errors.UserNotFound


@csrf_exempt
@require_POST
@ratelimit(key=POST_EMAIL_KEY, rate=settings.PASSWORD_RESET_EMAIL_RATE, block=False)
@ratelimit(key=REAL_IP_KEY, rate=settings.PASSWORD_RESET_IP_RATE, block=False)
def password_reset(request):
    """
    Attempts to send a password reset e-mail.
    """
    user = request.user
    # Prefer logged-in user's email
    email = user.email if user.is_authenticated else request.POST.get('email')
    tracker.emit(
        PASSWORD_RESET_INITIATED,
        {
            "user_id": user.id,
            "source": "Logistration Page",
        }
    )
    AUDIT_LOG.info("Password reset initiated for email %s.", email)

    if getattr(request, 'limited', False):
        AUDIT_LOG.warning("Password reset rate limit exceeded for email %s.", email)
        return JsonResponse(
            {
                'success': False,
                'value': _("Your previous request is in progress, please try again in a few moments.")
            },
            status=403
        )

    form = PasswordResetFormNoActive(request.POST)
    if form.is_valid():
        form.save(use_https=request.is_secure(),
                  from_email=configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL),
                  request=request)
        # When password change is complete, a "edx.user.settings.changed" event will be emitted.
        # But because changing the password is multi-step, we also emit an event here so that we can
        # track where the request was initiated.
        tracker.emit(
            SETTING_CHANGE_INITIATED,
            {
                "setting": "password",
                "old": None,
                "new": None,
                "user_id": request.user.id,
            }
        )
        destroy_oauth_tokens(request.user)
    else:
        # bad user? tick the rate limiter counter
        AUDIT_LOG.info("Bad password_reset user passed in.")

    return JsonResponse({
        'success': True,
        'value': render_to_string('registration/password_reset_done.html', {}),
    })


def _uidb36_to_uidb64(uidb36):
    """
    Needed to support old password reset URLs that use base36-encoded user IDs
    https://github.com/django/django/commit/1184d077893ff1bc947e45b00a4d565f3df81776#diff-c571286052438b2e3190f8db8331a92bR231
    Args:
        uidb36: base36-encoded user ID

    Returns: base64-encoded user ID. Otherwise returns a dummy, invalid ID
    """
    try:
        uidb64 = force_str(urlsafe_base64_encode(force_bytes(base36_to_int(uidb36))))
    except ValueError:
        uidb64 = '1'  # dummy invalid ID (incorrect padding for base64)
    return uidb64


class PasswordResetConfirmWrapper(PasswordResetConfirmView):
    """
    A wrapper around django.contrib.auth.views.PasswordResetConfirmView.
      Needed because we want to set the user as active at this step.
      We also optionally do some additional password policy checks.
    """

    def __init__(self):
        self.platform_name = PasswordResetConfirmWrapper._get_platform_name()
        self.validlink = False
        self.user = None
        self.uidb36 = ''
        self.token = ''
        self.uidb64 = ''
        self.uid_int = -1

    def _process_password_reset_success(self, request, token, uidb64, extra_context):  # pylint: disable=missing-function-docstring
        self.user = self.get_user(uidb64)
        form = SetPasswordForm(self.user, request.POST)
        if self.token_generator.check_token(self.user, token) and form.is_valid():
            self.form_valid(form)
            url = reverse('password_reset_complete')
            return HttpResponseRedirect(url)
        else:
            context = self.get_context_data()
            if extra_context is not None:
                context.update(extra_context)
            return self.render_to_response(context)

    def _get_token_from_session(self, request):
        """
        Internal method to get password reset token from session.
        """
        return request.session[INTERNAL_RESET_SESSION_TOKEN]

    @staticmethod
    def _get_platform_name():
        return {"platform_name": configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)}

    def _set_user(self, request):  # pylint: disable=missing-function-docstring
        try:
            self.uid_int = base36_to_int(self.uidb36)
            if request.user.is_authenticated and request.user.id != self.uid_int:
                raise Http404
            self.user = User.objects.get(id=self.uid_int)
        except (ValueError, User.DoesNotExist):
            # if there's any error getting a user, just let django's
            # password_reset_confirm function handle it.
            return super().dispatch(request, uidb64=self.uidb64, token=self.token, extra_context=self.platform_name)

    def _handle_retired_user(self, request):
        """
        method responsible to stop password reset in case user is retired
        """

        context = {
            'validlink': True,
            'form': None,
            'title': _('Password reset unsuccessful'),
            'err_msg': _('Error in resetting your password.'),
        }
        context.update(self.platform_name)
        return TemplateResponse(
            request, 'registration/password_reset_confirm.html', context
        )

    def _validate_password(self, password, request):  # pylint: disable=missing-function-docstring
        try:
            validate_password(password, user=self.user)
        except ValidationError as err:
            context = {
                'validlink': True,
                'form': None,
                'title': _('Password reset unsuccessful'),
                'err_msg': ' '.join(err.messages),
            }
            context.update(self.platform_name)
            return TemplateResponse(
                request, 'registration/password_reset_confirm.html', context
            )

    def _handle_password_reset_failure(self, response):  # pylint: disable=missing-function-docstring
        form_valid = response.context_data['form'].is_valid() if response.context_data['form'] else False
        if not form_valid:
            log.warning(
                'Unable to reset password for user [%s] because form is not valid. '
                'A possible cause is that the user had an invalid reset token',
                self.user.username,
            )
            response.context_data['err_msg'] = _('Error in resetting your password. Please try again.')
            return response

    def _handle_primary_email_update(self, updated_user):  # pylint: disable=missing-function-docstring
        try:
            updated_user.email = updated_user.account_recovery.secondary_email
            updated_user.account_recovery.delete()
            # emit an event that the user changed their secondary email to the primary email
            tracker.emit(
                SETTING_CHANGE_INITIATED,
                {
                    "setting": "email",
                    "old": self.user.email,
                    "new": updated_user.email,
                    "user_id": updated_user.id,
                }
            )
        except ObjectDoesNotExist:
            log.error('Account recovery process initiated without AccountRecovery instance for user {username}'  # noqa: UP032  # pylint: disable=line-too-long
                      .format(username=updated_user.username))

    def _handle_password_creation(self, request, updated_user):
        messages.success(
            request,
            HTML(_(
                '{html_start}Password Creation Complete{html_end}'
                'Your password has been created. {bold_start}{email}{bold_end} is now your primary login email.'
            )).format(
                support_url=configuration_helpers.get_value('SUPPORT_SITE_LINK', settings.SUPPORT_SITE_LINK),
                html_start=HTML('<p class="message-title">'),
                html_end=HTML('</p>'),
                bold_start=HTML('<b>'),
                bold_end=HTML('</b>'),
                email=updated_user.email,
            ),
            extra_tags='account-recovery aa-icon submission-success'
        )

    def post(self, request, *args, **kwargs):
        # We have to make a copy of request.POST because it is a QueryDict object which is immutable until copied.
        # We have to use request.POST because the password_reset_confirm method takes in the request and a user's
        # password is set to the request.POST['new_password1'] field. We have to also normalize the new_password2
        # field so it passes the equivalence check that new_password1 == new_password2
        # In order to switch out of having to do this copy, we would want to move the normalize_password code into
        # a custom User model's set_password method to ensure it is always happening upon calling set_password.
        request.POST = request.POST.copy()
        request.POST['new_password1'] = normalize_password(request.POST['new_password1'])
        request.POST['new_password2'] = normalize_password(request.POST['new_password2'])
        is_account_recovery = 'is_account_recovery' in request.GET

        password = request.POST['new_password1']
        response = self._validate_password(password, request)
        if response:
            return response

        response = self._process_password_reset_success(request, self.token, self.uidb64,
                                                        extra_context=self.platform_name)

        # If password reset was unsuccessful a template response is returned (status_code 200).
        # Check if form is invalid then show an error to the user.
        # Note if password reset was successful we get response redirect (status_code 302).
        password_reset_successful = response.status_code == 302
        if not password_reset_successful:
            return self._handle_password_reset_failure(response)

        updated_user = User.objects.get(id=self.uid_int)
        if is_account_recovery:
            self._handle_primary_email_update(updated_user)

        updated_user.save()
        if password_reset_successful and is_account_recovery:
            self._handle_password_creation(request, updated_user)

        # Handles clearing the failed login counter upon password reset.
        if LoginFailures.is_feature_enabled():
            LoginFailures.clear_lockout_counter(updated_user)

        update_session_auth_hash(request, updated_user)
        send_password_reset_success_email(updated_user, request)
        return response

    def dispatch(self, *args, **kwargs):
        self.uidb36 = kwargs.get('uidb36')
        self.token = kwargs.get('token')
        self.uidb64 = _uidb36_to_uidb64(self.uidb36)

        # User can not get this link unless account recovery feature is enabled.
        if 'is_account_recovery' in self.request.GET and not is_secondary_email_feature_enabled():
            raise Http404

        response = self._set_user(self.request)
        if response:
            return response
        if UserRetirementRequest.has_user_requested_retirement(self.user):
            return self._handle_retired_user(self.request)

        if self.request.method == 'POST':
            # Get actual token from session before processing the POST request.
            # This is needed because django's post process is not called on password reset
            # post request and the correct token needs to be extracted from session.
            self.token = self._get_token_from_session(self.request)
            return self.post(self.request, *args, **kwargs)
        else:
            response = super().dispatch(
                self.request,
                uidb64=self.uidb64,
                token=self.token,
                extra_context=self.platform_name
            )
            if hasattr(response, 'context_data'):
                response_was_successful = response.context_data.get('validlink')
                if response_was_successful and not self.user.is_active:
                    self.user.is_active = True
                    self.user.save()
            return response


def _get_user_from_email(email):
    """
    Find a user using given email and return it.

    Arguments:
        email (str): primary or secondary email address of the user.

    Raises:
        (User.ObjectNotFound): If no user is found with the given email.
        (User.MultipleObjectsReturned): If more than one user is found with the given email.

    Returns:
        User: Django user object.
    """
    try:
        return User.objects.get(email=email)
    except ObjectDoesNotExist:
        return User.objects.filter(
            id__in=AccountRecovery.objects.filter(secondary_email__iexact=email, is_active=True).values_list('user')
        ).get()


@require_POST
@ratelimit(key=POST_EMAIL_KEY, rate=settings.PASSWORD_RESET_EMAIL_RATE, block=False)
@ratelimit(key=REAL_IP_KEY, rate=settings.PASSWORD_RESET_IP_RATE, block=False)
def password_change_request_handler(request):
    """Handle password change requests originating from the account page.

    Uses the Account API to email the user a link to the password reset page.

    Note:
        The next step in the password reset process (confirmation) is currently handled
        by student.views.password_reset_confirm_wrapper, a custom wrapper around Django's
        password reset confirmation view.

    Args:
        request (HttpRequest)

    Returns:
        HttpResponse: 200 if the email was sent successfully
        HttpResponse: 400 if there is no 'email' POST parameter
        HttpResponse: 403 if the client has been rate limited
        HttpResponse: 405 if using an unsupported HTTP method

    Example usage:

        POST /account/password

    """
    user = request.user
    request_from_support_tools = (user.is_staff or user.is_superuser) and request.POST.get('email_from_support_tools')
    if request_from_support_tools:
        email = request.POST.get('email_from_support_tools')
    else:
        # Prefer logged-in user's email
        email = user.email if user.is_authenticated else request.POST.get('email')
    AUDIT_LOG.info("Password reset initiated for email %s.", email)
    tracker.emit(
        PASSWORD_RESET_INITIATED,
        {
            "user_id": user.id,
            "source": "Account API",
        }
    )

    if getattr(request, 'limited', False) and not request_from_support_tools:
        AUDIT_LOG.warning("Password reset rate limit exceeded for email %s.", email)
        return HttpResponse(
            _("Your previous request is in progress, please try again in a few moments."),
            status=403
        )

    if email:
        try:
            request_password_change(email, request.is_secure())
            user = user if not request.POST.get('email_from_support_tools') and user.is_authenticated \
                else _get_user_from_email(email=email)
            destroy_oauth_tokens(user)
        except errors.UserNotFound:
            AUDIT_LOG.info("Invalid password reset attempt")
            # If enabled, send an email saying that a password reset was attempted, but that there is
            # no user associated with the email
            if configuration_helpers.get_value('ENABLE_PASSWORD_RESET_FAILURE_EMAIL',
                                               settings.FEATURES['ENABLE_PASSWORD_RESET_FAILURE_EMAIL']):
                site = get_current_site()
                message_context = get_base_template_context(site)

                message_context.update({
                    'failed': True,
                    'request': request,  # Used by google_analytics_tracking_pixel
                    'email_address': email,
                })

                msg = PasswordReset().personalize(
                    recipient=Recipient(lms_user_id=0, email_address=email),
                    language=settings.LANGUAGE_CODE,
                    user_context=message_context,
                )
                ace.send(msg)
        except errors.UserAPIInternalError as err:
            log.exception('Error occurred during password change for user {email}: {error}'  # noqa: UP032
                          .format(email=email, error=err))
            return HttpResponse(_("Some error occurred during password change. Please try again"), status=500)

        return HttpResponse(status=200)
    else:
        return HttpResponseBadRequest(_("No email address provided."))


def _get_rate(rate):
    """
    Given the request rate string, return a two tuple of:
    <allowed number of requests>, <period of time in seconds>
    """

    requests, duration = rate.split('/')
    num_requests = int(requests)
    num = int(duration[:-1] if duration[:-1] else 1)
    symbol = duration[-1:]
    duration = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[symbol] * num
    return (num_requests, duration)


class ResetTokenValidationThrottle(AnonRateThrottle):
    """
    Setting rate limit for token validation
    """
    rate = settings.RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT

    def parse_rate(self, rate):
        return _get_rate(rate)


class PasswordResetTokenValidation(APIView):  # pylint: disable=missing-class-docstring
    """
    API to validate generated password reset token
    """
    throttle_classes = [ResetTokenValidationThrottle]

    def post(self, request):
        """ HTTP end-point to validate password reset token. """
        is_valid = False
        token = request.data.get('token')
        try:
            token = token.split('-', 1)
            uid_int = base36_to_int(token[0])
            if request.user.is_authenticated and request.user.id != uid_int:
                return Response({'is_valid': is_valid})

            user = User.objects.get(id=uid_int)
            if UserRetirementRequest.has_user_requested_retirement(user):
                return Response({'is_valid': is_valid})

            is_valid = default_token_generator.check_token(user, token[1])
            if is_valid and not user.is_active:
                user.is_active = True
                user.save()
        except Exception:   # pylint: disable=broad-except
            AUDIT_LOG.exception("Invalid password reset confirm token")

        return Response({'is_valid': is_valid})


class PasswordResetThrottle(AnonRateThrottle):
    """
    Setting rate limit for password reset
    """
    rate = settings.RESET_PASSWORD_API_RATELIMIT

    def parse_rate(self, rate):
        return _get_rate(rate)


class LogistrationPasswordResetView(APIView):  # pylint: disable=missing-class-docstring
    """
    API to update new password credentials for a correct token
    """
    throttle_classes = [PasswordResetThrottle]

    def post(self, request, **kwargs):
        """ Reset learner password using passed token and new credentials """

        reset_status = False
        err_msg = _(
            "An error has occurred. Try refreshing the page, or check your internet connection."
        )
        user_id = None
        uidb36 = kwargs.get('uidb36')
        token = kwargs.get('token')

        has_required_values, uid_int = self._check_token_has_required_values(uidb36, token)
        if not has_required_values:
            AUDIT_LOG.exception("Invalid password reset confirm token")
            return Response({'reset_status': reset_status, 'token_invalid': True})

        request.data._mutable = True  # pylint: disable=protected-access
        request.data['new_password1'] = normalize_password(request.data['new_password1'])
        request.data['new_password2'] = normalize_password(request.data['new_password2'])

        password = request.data['new_password1']
        try:
            user = User.objects.get(id=uid_int)
            user_id = user.id
            if not default_token_generator.check_token(user, token):
                AUDIT_LOG.exception(f"Token validation failed for user {user_id}")
                return Response({'reset_status': reset_status, 'token_invalid': True})

            validate_password(password, user=user)

            if settings.ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY:
                # Checks the Pwned Databases for password vulnerability.
                pwned_response = check_pwned_password(password)
                if pwned_response.get('vulnerability', 'no') == 'yes':
                    error_status = {
                        'reset_status': reset_status,
                        'err_msg': accounts.AUTHN_PASSWORD_COMPROMISED_MSG
                    }
                    return Response(error_status)

            form = SetPasswordForm(user, request.data)
            if form.is_valid():
                form.save()
                reset_status = True
                err_msg = ''

                if 'is_account_recovery' in request.GET:
                    try:
                        old_primary_email = user.email
                        user.email = user.account_recovery.secondary_email
                        user.account_recovery.delete()
                        # emit an event that the user changed their secondary email to the primary email
                        tracker.emit(
                            SETTING_CHANGE_INITIATED,
                            {
                                "setting": "email",
                                "old": old_primary_email,
                                "new": user.email,
                                "user_id": user.id,
                            }
                        )
                        user.save()
                    except ObjectDoesNotExist:
                        log.error(
                            f'Account recovery process initiated without AccountRecovery instance for user {user_id}'
                        )

                # Handles clearing the failed login counter upon password reset.
                if LoginFailures.is_feature_enabled():
                    LoginFailures.clear_lockout_counter(user)

                send_password_reset_success_email(user, request)
                update_session_auth_hash(request, user)
        except ValidationError as err:
            AUDIT_LOG.exception(f"Password validation failed for {user_id}")
            error_status = {
                'reset_status': reset_status,
                'err_msg': ' '.join(err.messages)
            }
            return Response(error_status)
        except Exception:   # pylint: disable=broad-except
            AUDIT_LOG.exception(f"Setting new password failed for {user_id}")

        return Response({'reset_status': reset_status, 'err_msg': err_msg})

    def _check_token_has_required_values(self, uidb36, token):
        """
        Helper function to test that token
        string passed has the required kwargs needed
        to process token validation.
        """

        if not uidb36 or not token:
            return False, None
        try:
            uid_int = base36_to_int(uidb36)
        except ValueError:
            return False, None
        return True, uid_int
