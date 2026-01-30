"""  # lint-amnesty, pylint: disable=cyclic-import
Student Views
"""


import datetime
import logging
import urllib.parse
import uuid
from collections import namedtuple
import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.validators import ValidationError, validate_email
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import Signal, receiver  # lint-amnesty, pylint: disable=unused-import
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect
from django.template.context_processors import csrf
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie  # lint-amnesty, pylint: disable=unused-import
from django.views.decorators.http import require_GET, require_http_methods, require_POST  # lint-amnesty, pylint: disable=unused-import
from edx_ace import ace
from edx_ace.recipient import Recipient
from edx_django_utils import monitoring as monitoring_utils
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser  # lint-amnesty, pylint: disable=wrong-import-order
from eventtracking import tracker
# Note that this lives in LMS, so this dependency should be refactored.
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated

from common.djangoapps.student.toggles import should_redirect_to_courseware_after_enrollment
from common.djangoapps.track import views as track_views
from lms.djangoapps.bulk_email.models import Optout
from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.courseware.courses import get_courses, sort_by_announcement, sort_by_start_date
from common.djangoapps.edxmako.shortcuts import marketing_link, render_to_response, render_to_string  # lint-amnesty, pylint: disable=unused-import
from common.djangoapps.entitlements.models import CourseEntitlement
from common.djangoapps.student.helpers import get_next_url_for_login_page, get_redirect_url_with_host
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.catalog.utils import get_programs_with_type
from openedx.core.djangoapps.embargo import api as embargo_api
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.programs.models import ProgramsApiConfig  # lint-amnesty, pylint: disable=unused-import
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming import helpers as theming_helpers
from openedx.core.djangoapps.user_api.preferences import api as preferences_api
from openedx.core.djangoapps.user_authn.tasks import send_activation_email
from openedx.core.djangoapps.user_authn.toggles import (
    should_redirect_to_authn_microfrontend,
    is_auto_generated_username_enabled
)
from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
from openedx.features.discounts.applicability import FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG
from openedx.features.enterprise_support.utils import is_enterprise_learner
from common.djangoapps.student.email_helpers import generate_activation_email_context
from common.djangoapps.student.helpers import DISABLE_UNENROLL_CERT_STATES, cert_info
from common.djangoapps.student.message_types import AccountActivation, EmailChange, EmailChangeConfirmation, RecoveryEmailCreate  # lint-amnesty, pylint: disable=line-too-long
from common.djangoapps.student.models import (  # lint-amnesty, pylint: disable=unused-import
    AccountRecovery,
    CourseEnrollment,
    EnrollmentNotAllowed,
    PendingEmailChange,  # unimport:skip
    PendingSecondaryEmailChange,
    Registration,
    RegistrationCookieConfiguration,
    UnenrollmentNotAllowed,
    UserAttribute,
    UserProfile,
    UserSignupSource,
    UserStanding,
    create_comments_service_user,
    email_exists_or_retired
)
from common.djangoapps.student.signals import REFUND_ORDER
from common.djangoapps.util.db import outer_atomic
from common.djangoapps.util.json_request import JsonResponse
from common.djangoapps.student.signals import USER_EMAIL_CHANGED
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from edx_toggles.toggles import WaffleFlag

log = logging.getLogger("edx.student")

AUDIT_LOG = logging.getLogger("audit")
ReverifyInfo = namedtuple(
    'ReverifyInfo',
    'course_id course_name course_number date status display'
)
SETTING_CHANGE_INITIATED = 'edx.user.settings.change_initiated'
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
USER_ACCOUNT_ACTIVATED = 'edx.user.account.activated'

# .. toggle_name: user_authn.enable_ses_for_account_activation
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Route account activation emails via SES using ACE.
# .. toggle_use_cases: opt_in, temporary
# .. toggle_creation_date: 2026-01-22
# .. toggle_target_removal_date: None
# .. toggle_warning: This toggle controls email delivery routing and should be enabled cautiously.
ENABLE_SES_FOR_ACCOUNT_ACTIVATION = WaffleFlag(
    'user_authn.enable_ses_for_account_activation',
    __name__,
)


def csrf_token(context):
    """
    A csrf token that can be included in a form.
    """
    token = context.get('csrf_token', '')
    if token == 'NOTPROVIDED':
        return ''
    return (HTML('<div style="display:none"><input type="hidden"'
                 ' name="csrfmiddlewaretoken" value="{}" /></div>').format(Text(token)))


# NOTE: This view is not linked to directly--it is called from
# branding/views.py:index(), which is cached for anonymous users.
# This means that it should always return the same thing for anon
# users. (in particular, no switching based on query params allowed)
def index(request, extra_context=None, user=AnonymousUser()):
    """
    Render the edX main page.

    extra_context is used to allow immediate display of certain modal windows, eg signup.
    """
    if extra_context is None:
        extra_context = {}

    courses = get_courses(user)

    if configuration_helpers.get_value(
        "ENABLE_COURSE_SORTING_BY_START_DATE",
        settings.FEATURES["ENABLE_COURSE_SORTING_BY_START_DATE"],
    ):
        courses = sort_by_start_date(courses)
    else:
        courses = sort_by_announcement(courses)

    context = {'courses': courses}

    context['homepage_overlay_html'] = configuration_helpers.get_value('homepage_overlay_html')

    # This appears to be an unused context parameter, at least for the master templates...
    context['show_partners'] = configuration_helpers.get_value('show_partners', True)

    # TO DISPLAY A YOUTUBE WELCOME VIDEO
    # 1) Change False to True
    context['show_homepage_promo_video'] = configuration_helpers.get_value('show_homepage_promo_video', False)

    # Maximum number of courses to display on the homepage.
    context['homepage_course_max'] = configuration_helpers.get_value(
        'HOMEPAGE_COURSE_MAX', settings.HOMEPAGE_COURSE_MAX
    )

    # 2) Add your video's YouTube ID (11 chars, eg "123456789xX"), or specify via site configuration
    # Note: This value should be moved into a configuration setting and plumbed-through to the
    # context via the site configuration workflow, versus living here
    youtube_video_id = configuration_helpers.get_value('homepage_promo_video_youtube_id', "your-youtube-id")
    context['homepage_promo_video_youtube_id'] = youtube_video_id

    # allow for theme override of the courses list
    context['courses_list'] = theming_helpers.get_template_path('courses_list.html')

    # Insert additional context for use in the template
    context.update(extra_context)

    # Add marketable programs to the context.
    context['programs_list'] = get_programs_with_type(request.site, include_hidden=False)

    return render_to_response('index.html', context)


def show_auto_generated_username(username):
    """
    Check if the auto-generated username is valid based on the specified pattern.

    Parameters:
    - username (str): The username to be checked.

    Returns:
    - bool: True if the username is valid and the auto-generated username check is enabled, False otherwise.
    """
    if not is_auto_generated_username_enabled():
        return False

    pattern = r'^[A-Z]{1,2}_\d{4}_[A-Z0-9]+$'
    return bool(re.match(pattern, username))


def compose_activation_email(
    user, user_registration=None, route_enabled=False, profile_name='', redirect_url=None, registration_flow=False
):
    """
    Construct all the required params for the activation email
    through celery task
    """
    if user_registration is None:
        user_registration = Registration.objects.get(user=user)

    message_context = generate_activation_email_context(user, user_registration)
    message_context.update({
        'confirm_activation_link': _get_activation_confirmation_link(message_context['key'], redirect_url),
        'is_enterprise_learner': is_enterprise_learner(user),
        'is_first_purchase_discount_overridden': FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG.is_enabled(),
        'route_enabled': route_enabled,
        'routed_user': user.username,
        'routed_user_email': user.email,
        'routed_profile_name': profile_name,
        'registration_flow': registration_flow,
        'show_auto_generated_username': show_auto_generated_username(user.username),
    })

    if route_enabled:
        dest_addr = settings.FEATURES['REROUTE_ACTIVATION_EMAIL']
    else:
        dest_addr = user.email

    msg = AccountActivation().personalize(
        recipient=Recipient(user.id, dest_addr),
        language=preferences_api.get_user_preference(user, LANGUAGE_KEY),
        user_context=message_context,
    )
    if ENABLE_SES_FOR_ACCOUNT_ACTIVATION.is_enabled():
        log.info(
            "ACCOUNT ACTIVATION EMAIL | SES flag=%s | override_channel=%s",
            ENABLE_SES_FOR_ACCOUNT_ACTIVATION.is_enabled(),
            msg.options.get('override_default_channel')
        )
        msg.options.update({
            'transactional': True,
            'override_default_channel': 'django_email',
            'from_address': configuration_helpers.get_value(
                'ACTIVATION_EMAIL_FROM_ADDRESS'
            ) or configuration_helpers.get_value(
                'email_from_address', settings.DEFAULT_FROM_EMAIL
            ),
        })

    return msg


def _get_activation_confirmation_link(activation_key, redirect_url=None):
    """
    Helper function to build an activation confirmation URL given an activation_key.
    The confirmation URL will include a "?next={redirect_url}" query if redirect_url
    is not null.
    """
    root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    confirmation_link = '{root_url}/activate/{activation_key}'.format(
        root_url=root_url,
        activation_key=activation_key,
    )
    if not redirect_url:
        return confirmation_link

    scheme, netloc, path, params, _, fragment = urllib.parse.urlparse(confirmation_link)
    query = urllib.parse.urlencode({'next': redirect_url})
    return urllib.parse.urlunparse((scheme, netloc, path, params, query, fragment))


def compose_and_send_activation_email(
    user, profile, user_registration=None, redirect_url=None, registration_flow=False,
):
    """
    Construct all the required params and send the activation email
    through celery task

    Arguments:
        user: current logged-in user
        profile: profile object of the current logged-in user
        user_registration: registration of the current logged-in user
        redirect_url: The URL to redirect to after successful activation
        registration_flow: Is the request coming from registration workflow
    """
    route_enabled = settings.FEATURES.get('REROUTE_ACTIVATION_EMAIL')

    msg = compose_activation_email(
        user, user_registration, route_enabled, profile.name, redirect_url, registration_flow
    )
    from_address = configuration_helpers.get_value('ACTIVATION_EMAIL_FROM_ADDRESS') or (
        configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
    )

    try:
        send_activation_email.delay(str(msg), from_address)
    except Exception:  # pylint: disable=broad-except
        log.exception(f'Activation email task failed for user {user.id}.')


@login_required
def course_run_refund_status(request, course_id):
    """
    Get Refundable status for a course.

    Arguments:
        request: The request object.
        course_id (str): The unique identifier for the course.

    Returns:
        Json response.

    """

    try:
        course_key = CourseKey.from_string(course_id)
        course_enrollment = CourseEnrollment.get_enrollment(request.user, course_key)

    except InvalidKeyError:
        logging.exception("The course key used to get refund status caused InvalidKeyError during look up.")

        return JsonResponse({'course_refundable_status': ''}, status=406)

    refundable_status = course_enrollment.refundable()
    logging.info(f"Course refund status for course {course_id} is {refundable_status}")

    return JsonResponse({'course_refundable_status': refundable_status}, status=200)


def _update_email_opt_in(request, org):
    """
    Helper function used to hit the profile API if email opt-in is enabled.
    """

    email_opt_in = request.POST.get('email_opt_in')
    if email_opt_in is not None:
        email_opt_in_boolean = email_opt_in == 'true'
        preferences_api.update_email_opt_in(request.user, org, email_opt_in_boolean)


@transaction.non_atomic_requests
@require_POST
@outer_atomic()
def change_enrollment(request, check_access=True):
    """
    Modify the enrollment status for the logged-in user.

    TODO: This is lms specific and does not belong in common code.

    The request parameter must be a POST request (other methods return 405)
    that specifies course_id and enrollment_action parameters. If course_id or
    enrollment_action is not specified, if course_id is not valid, if
    enrollment_action is something other than "enroll" or "unenroll", if
    enrollment_action is "enroll" and enrollment is closed for the course, or
    if enrollment_action is "unenroll" and the user is not enrolled in the
    course, a 400 error will be returned. If the user is not logged in, 403
    will be returned; it is important that only this case return 403 so the
    front end can redirect the user to a registration or login page when this
    happens. This function should only be called from an AJAX request, so
    the error messages in the responses should never actually be user-visible.

    Args:
        request (`Request`): The Django request object

    Keyword Args:
        check_access (boolean): If True, we check that an accessible course actually
            exists for the given course_key before we enroll the student.
            The default is set to False to avoid breaking legacy code or
            code with non-standard flows (ex. beta tester invitations), but
            for any standard enrollment flow you probably want this to be True.

    Returns:
        Response

    """
    # Get the user
    user = request.user

    # Ensure the user is authenticated
    if not user.is_authenticated:
        return HttpResponseForbidden()

    # Ensure we received a course_id
    action = request.POST.get("enrollment_action")
    if 'course_id' not in request.POST:
        return HttpResponseBadRequest(_("Course id not specified"))

    try:
        course_id = CourseKey.from_string(request.POST.get("course_id"))
    except InvalidKeyError:
        log.warning(
            "User %s tried to %s with invalid course id: %s",
            user.username,
            action,
            request.POST.get("course_id"),
        )
        return HttpResponseBadRequest(_("Invalid course id"))

    # Allow us to monitor performance of this transaction on a per-course basis since we often roll-out features
    # on a per-course basis.
    monitoring_utils.set_custom_attribute('course_id', str(course_id))

    if action == "enroll":
        # Make sure the course exists
        # We don't do this check on unenroll, or a bad course id can't be unenrolled from
        if not modulestore().has_course(course_id):
            log.warning(
                "User %s tried to enroll in non-existent course %s",
                user.username,
                course_id
            )
            return HttpResponseBadRequest(_("Course id is invalid"))

        # Record the user's email opt-in preference
        if settings.FEATURES.get('ENABLE_MKTG_EMAIL_OPT_IN'):
            _update_email_opt_in(request, course_id.org)

        available_modes = CourseMode.modes_for_course_dict(course_id)

        # Check whether the user is blocked from enrolling in this course
        # This can occur if the user's IP is on a global blacklist
        # or if the user is enrolling in a country in which the course
        # is not available.
        redirect_url = embargo_api.redirect_if_blocked(request, course_id)
        if redirect_url:
            return HttpResponse(redirect_url)

        if CourseEntitlement.check_for_existing_entitlement_and_enroll(user=user, course_run_key=course_id):
            return HttpResponse(make_learning_mfe_courseware_url(course_id))

        # Check that auto enrollment is allowed for this course
        # (= the course is NOT behind a paywall)
        if CourseMode.can_auto_enroll(course_id):
            # Enroll the user using the default mode (audit)
            # We're assuming that users of the course enrollment table
            # will NOT try to look up the course enrollment model
            # by its slug.  If they do, it's possible (based on the state of the database)
            # for no such model to exist, even though we've set the enrollment type
            # to "audit".
            try:
                enroll_mode = CourseMode.auto_enroll_mode(course_id, available_modes)
                if enroll_mode:
                    CourseEnrollment.enroll(user, course_id, check_access=check_access, mode=enroll_mode)
            except EnrollmentNotAllowed as exc:
                return HttpResponseBadRequest(str(exc))
            except Exception:  # pylint: disable=broad-except
                return HttpResponseBadRequest(_("Could not enroll"))

        # If we have more than one course mode or professional ed is enabled,
        # then send the user to the choose your track page.
        # (In the case of no-id-professional/professional ed, this will redirect to a page that
        # funnels users directly into the verification / payment flow)
        if CourseMode.has_verified_mode(available_modes) or CourseMode.has_professional_mode(available_modes):
            return HttpResponse(
                reverse("course_modes_choose", kwargs={'course_id': str(course_id)})
            )

        if should_redirect_to_courseware_after_enrollment():
            return HttpResponse(make_learning_mfe_courseware_url(course_id))
        else:
            return HttpResponse()
    elif action == "unenroll":
        if configuration_helpers.get_value(
            "DISABLE_UNENROLLMENT",
            settings.FEATURES.get("DISABLE_UNENROLLMENT")
        ):
            return HttpResponseBadRequest(_("Unenrollment is currently disabled"))

        enrollment = CourseEnrollment.get_enrollment(user, course_id)
        if not enrollment:
            return HttpResponseBadRequest(_("You are not enrolled in this course"))

        certificate_info = cert_info(user, enrollment)
        if certificate_info.get('status') in DISABLE_UNENROLL_CERT_STATES:
            return HttpResponseBadRequest(_("Your certificate prevents you from unenrolling from this course"))

        try:
            CourseEnrollment.unenroll(user, course_id)
        except UnenrollmentNotAllowed as exc:
            return HttpResponseBadRequest(str(exc))

        log.info("User %s unenrolled from %s; sending REFUND_ORDER", user.username, course_id)
        REFUND_ORDER.send(sender=None, course_enrollment=enrollment)
        return HttpResponse()
    else:
        return HttpResponseBadRequest(_("Enrollment action is invalid"))


@require_GET
@login_required
@ensure_csrf_cookie
def manage_user_standing(request):
    """
    Renders the view used to manage user standing. Also displays a table
    of user accounts that have been disabled and who disabled them.
    """
    if not request.user.is_staff:
        raise Http404
    all_disabled_accounts = UserStanding.objects.filter(
        account_status=UserStanding.ACCOUNT_DISABLED
    )

    all_disabled_users = [standing.user for standing in all_disabled_accounts]

    headers = ['username', 'account_changed_by']
    rows = []
    for user in all_disabled_users:
        row = [user.username, user.standing.changed_by]
        rows.append(row)

    context = {'headers': headers, 'rows': rows}

    return render_to_response("manage_user_standing.html", context)


@require_POST
@login_required
@ensure_csrf_cookie
def disable_account_ajax(request):
    """
    Ajax call to change user standing. Endpoint of the form
    in manage_user_standing.html
    """
    if not request.user.is_staff:
        raise Http404
    username = request.POST.get('username')
    context = {}
    if username is None or username.strip() == '':
        context['message'] = _('Please enter a username')
        return JsonResponse(context, status=400)

    account_action = request.POST.get('account_action')
    if account_action is None:
        context['message'] = _('Please choose an option')
        return JsonResponse(context, status=400)

    username = username.strip()
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        context['message'] = _("User with username {} does not exist").format(username)
        return JsonResponse(context, status=400)
    else:
        user_account, _success = UserStanding.objects.get_or_create(
            user=user, defaults={'changed_by': request.user},
        )
        if account_action == 'disable':
            user_account.account_status = UserStanding.ACCOUNT_DISABLED
            context['message'] = _("Successfully disabled {}'s account").format(username)
            log.info("%s disabled %s's account", request.user, username)
        elif account_action == 'reenable':
            user_account.account_status = UserStanding.ACCOUNT_ENABLED
            context['message'] = _("Successfully reenabled {}'s account").format(username)
            log.info("%s reenabled %s's account", request.user, username)
        else:
            context['message'] = _("Unexpected account status")
            return JsonResponse(context, status=400)
        user_account.changed_by = request.user
        user_account.standing_last_changed_at = datetime.datetime.now(ZoneInfo("UTC"))
        user_account.save()

    return JsonResponse(context)


@receiver(post_save, sender=User)
def user_signup_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Handler that saves the user Signup Source when the user is created
    """
    if 'created' in kwargs and kwargs['created']:
        site = configuration_helpers.get_value('SITE_NAME')
        if site:
            user_signup_source = UserSignupSource(user=kwargs['instance'], site=site)
            user_signup_source.save()
            log.info('user {} originated from a white labeled "Microsite"'.format(kwargs['instance'].id))


@ensure_csrf_cookie
def activate_account(request, key):
    """
    When link in activation e-mail is clicked
    """
    # If request is in Studio call the appropriate view
    if theming_helpers.get_project_root_name().lower() == 'cms':
        monitoring_utils.set_custom_attribute('student_activate_account', 'cms')
        return activate_account_studio(request, key)

    # TODO: Use custom attribute to determine if there are any `activate_account` calls for cms in Production.
    # If not, the templates wouldn't be needed for cms, but we still need a way to activate for cms tests.
    monitoring_utils.set_custom_attribute('student_activate_account', 'lms')
    activation_message_type = None

    activated_or_confirmed = 'confirmed' if settings.MARKETING_EMAILS_OPT_IN else 'activated'
    account_or_email = 'email' if settings.MARKETING_EMAILS_OPT_IN else 'account'

    invalid_message = HTML(_(
        '{html_start}Your {account_or_email} could not be {activated_or_confirmed}{html_end}'
        'Something went wrong, please <a href="{support_url}">contact support</a> to resolve this issue.'
    )).format(
        account_or_email=account_or_email,
        activated_or_confirmed=activated_or_confirmed,
        support_url=configuration_helpers.get_value(
            'ACTIVATION_EMAIL_SUPPORT_LINK', settings.ACTIVATION_EMAIL_SUPPORT_LINK
        ) or settings.SUPPORT_SITE_LINK,
        html_start=HTML('<p class="message-title">'),
        html_end=HTML('</p>'),
    )

    show_account_activation_popup = None
    try:
        registration = Registration.objects.get(activation_key=key)
    except (Registration.DoesNotExist, Registration.MultipleObjectsReturned):
        activation_message_type = 'error'
        messages.error(
            request,
            invalid_message,
            extra_tags='account-activation aa-icon'
        )
    else:
        if request.user.is_authenticated and request.user.id != registration.user.id:
            activation_message_type = 'error'
            messages.error(
                request,
                invalid_message,
                extra_tags='account-activation aa-icon'
            )
        elif registration.user.is_active:
            activation_message_type = 'info'
            messages.info(
                request,
                HTML(_(
                    '{html_start}This {account_or_email} has already been {activated_or_confirmed}.{html_end}'
                )).format(
                    account_or_email=account_or_email,
                    activated_or_confirmed=activated_or_confirmed,
                    html_start=HTML('<p class="message-title">'),
                    html_end=HTML('</p>'),
                ),
                extra_tags='account-activation aa-icon',
            )
        else:
            registration.activate()
            # Success message for logged in users.
            message = _('{html_start}Success{html_end} You have {activated_or_confirmed} your {account_or_email}.')

            tracker.emit(
                USER_ACCOUNT_ACTIVATED,
                {
                    "user_id": registration.user.id,
                    "activation_timestamp": registration.activation_timestamp
                }
            )

            if not request.user.is_authenticated:
                # Success message for logged out users
                message = _(
                    '{html_start}Success! You have {activated_or_confirmed} your {account_or_email}.{html_end}'
                    'You will now receive email updates and alerts from us related to'
                    ' the courses you are enrolled in. Sign In to continue.'
                )

            # Add message for later use.
            activation_message_type = 'success'
            messages.success(
                request,
                HTML(message).format(
                    account_or_email=account_or_email,
                    activated_or_confirmed=activated_or_confirmed,
                    html_start=HTML('<p class="message-title">'),
                    html_end=HTML('</p>'),
                ),
                extra_tags='account-activation aa-icon',
            )
            show_account_activation_popup = request.COOKIES.get(settings.SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME, None)

    # If a safe `next` parameter is provided in the request
    # and it's not the same as the dashboard, redirect there.
    # The `get_next_url_for_login_page()` function will only return a safe redirect URL.
    # If the provided `next` URL is not safe, that function will fill `redirect_to`
    # with a value of `reverse('dashboard')`.
    redirect_url = None
    if request.GET.get('next'):
        redirect_to, root_login_url = get_next_url_for_login_page(request, include_host=True)

        # Don't automatically redirect authenticated users to the redirect_url
        # if the `next` value is either:
        # 1. "/dashboard" or
        # 2. "https://{LMS_ROOT_URL}/dashboard" (which we might provide as a value from the AuthN MFE)
        if redirect_to not in (
            root_login_url + reverse('dashboard'),
            reverse('dashboard')
        ):
            redirect_url = get_redirect_url_with_host(root_login_url, redirect_to)

    if should_redirect_to_authn_microfrontend() and not request.user.is_authenticated:
        params = {'account_activation_status': activation_message_type}
        if redirect_url:
            params['next'] = redirect_url
        url_path = '/login?{}'.format(urllib.parse.urlencode(params))
        return redirect(settings.AUTHN_MICROFRONTEND_URL + url_path)

    response = redirect(redirect_url) if redirect_url and is_enterprise_learner(request.user) else redirect('dashboard')
    if show_account_activation_popup:
        response.delete_cookie(
            settings.SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME,
            domain=settings.SESSION_COOKIE_DOMAIN,
            path='/',
        )
    return response


@ensure_csrf_cookie
def activate_account_studio(request, key):
    """
    When link in activation e-mail is clicked and the link belongs to studio.
    """
    try:
        registration = Registration.objects.get(activation_key=key)
    except (Registration.DoesNotExist, Registration.MultipleObjectsReturned):
        return render_to_response(
            "registration/activation_invalid.html",
            {'csrf': csrf(request)['csrf_token']}
        )
    else:
        user_logged_in = request.user.is_authenticated
        already_active = True
        if not registration.user.is_active:
            registration.activate()
            already_active = False

        return render_to_response(
            "registration/activation_complete.html",
            {
                'user_logged_in': user_logged_in,
                'already_active': already_active
            }
        )


def validate_new_email(user, new_email):
    """
    Given a new email for a user, does some basic verification of the new address If any issues are encountered
    with verification a ValueError will be thrown.
    """
    try:
        validate_email(new_email)
    except ValidationError:
        raise ValueError(_('Valid e-mail address required.'))  # lint-amnesty, pylint: disable=raise-missing-from

    if new_email == user.email:
        raise ValueError(_('Old email is the same as the new email.'))


def validate_secondary_email(user, new_email):
    """
    Enforce valid email addresses.
    """

    from openedx.core.djangoapps.user_api.accounts.api import get_email_validation_error, \
        get_secondary_email_validation_error

    if get_email_validation_error(new_email):
        raise ValueError(_('Valid e-mail address required.'))

    # Make sure that if there is an active recovery email address, that is not the same as the new one.
    if hasattr(user, "account_recovery"):
        if user.account_recovery.is_active and new_email == user.account_recovery.secondary_email:
            raise ValueError(_('Old email is the same as the new email.'))

    # Make sure that secondary email address is not same as user's primary email.
    if new_email == user.email:
        raise ValueError(_('Cannot be same as your sign in email address.'))

    message = get_secondary_email_validation_error(new_email)
    if message:
        raise ValueError(message)


def do_email_change_request(user, new_email, activation_key=None, secondary_email_change_request=False):
    """
    Given a new email for a user, does some basic verification of the new address and sends an activation message
    to the new address. If any issues are encountered with verification or sending the message, a ValueError will
    be thrown.
    """
    # if activation_key is not passing as an argument, generate a random key
    if not activation_key:
        activation_key = uuid.uuid4().hex

    confirm_link = reverse('confirm_email_change', kwargs={'key': activation_key, })

    if secondary_email_change_request:
        PendingSecondaryEmailChange.objects.update_or_create(
            user=user,
            defaults={
                'new_secondary_email': new_email,
                'activation_key': activation_key,
            }
        )
        confirm_link = reverse('activate_secondary_email', kwargs={'key': activation_key})
    else:
        PendingEmailChange.objects.update_or_create(
            user=user,
            defaults={
                'new_email': new_email,
                'activation_key': activation_key,
            }
        )

    use_https = theming_helpers.get_current_request().is_secure()

    site = Site.objects.get_current()
    message_context = get_base_template_context(site)
    message_context.update({
        'old_email': user.email,
        'new_email': new_email,
        'confirm_link': '{protocol}://{site}{link}'.format(
            protocol='https' if use_https else 'http',
            site=configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME),
            link=confirm_link,
        ),
    })

    if secondary_email_change_request:
        msg = RecoveryEmailCreate().personalize(
            recipient=Recipient(user.id, new_email),
            language=preferences_api.get_user_preference(user, LANGUAGE_KEY),
            user_context=message_context,
        )
    else:
        msg = EmailChange().personalize(
            recipient=Recipient(user.id, new_email),
            language=preferences_api.get_user_preference(user, LANGUAGE_KEY),
            user_context=message_context,
        )

    try:
        ace.send(msg)
        log.info("Email activation link sent to user [%s].", new_email)
    except Exception:
        from_address = configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
        log.error('Unable to send email activation link to user from "%s"', from_address, exc_info=True)
        raise ValueError(_('Unable to send email activation link. Please try again later.'))  # lint-amnesty, pylint: disable=raise-missing-from

    if not secondary_email_change_request:
        # When the email address change is complete, a "edx.user.settings.changed" event will be emitted.
        # But because changing the email address is multi-step, we also emit an event here so that we can
        # track where the request was initiated.
        tracker.emit(
            SETTING_CHANGE_INITIATED,
            {
                "setting": "email",
                "old": message_context['old_email'],
                "new": message_context['new_email'],
                "user_id": user.id,
            }
        )


@ensure_csrf_cookie
def activate_secondary_email(request, key):
    """
    This is called when the activation link is clicked. We activate the secondary email
    for the requested user.
    """
    try:
        pending_secondary_email_change = PendingSecondaryEmailChange.objects.get(activation_key=key)
    except PendingSecondaryEmailChange.DoesNotExist:
        return render_to_response("invalid_email_key.html", {})

    try:
        account_recovery = pending_secondary_email_change.user.account_recovery
    except AccountRecovery.DoesNotExist:
        account_recovery = AccountRecovery(user=pending_secondary_email_change.user)

    try:
        account_recovery.update_recovery_email(pending_secondary_email_change.new_secondary_email)
    except ValidationError:
        return render_to_response("secondary_email_change_failed.html", {
            'secondary_email': pending_secondary_email_change.new_secondary_email
        })

    pending_secondary_email_change.delete()

    return render_to_response("secondary_email_change_successful.html")


@ensure_csrf_cookie
def confirm_email_change(request, key):
    """
    User requested a new e-mail. This is called when the activation
    link is clicked. We confirm with the old e-mail, and update
    """
    with transaction.atomic():
        try:
            pec = PendingEmailChange.objects.get(activation_key=key)
        except PendingEmailChange.DoesNotExist:
            response = render_to_response("invalid_email_key.html", {})
            transaction.set_rollback(True)
            return response

        user = pec.user
        address_context = {
            'old_email': user.email,
            'new_email': pec.new_email
        }

        if len(User.objects.filter(email=pec.new_email)) != 0:
            response = render_to_response("email_exists.html", {})
            transaction.set_rollback(True)
            return response

        use_https = request.is_secure()
        if settings.FEATURES['ENABLE_MKTG_SITE']:
            contact_link = marketing_link('CONTACT')
        else:
            contact_link = '{protocol}://{site}{link}'.format(
                protocol='https' if use_https else 'http',
                site=configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME),
                link=reverse('contact'),
            )

        site = Site.objects.get_current()
        message_context = get_base_template_context(site)
        message_context.update({
            'old_email': user.email,
            'new_email': pec.new_email,
            'contact_link': contact_link,
            'from_address': configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL),
        })

        msg = EmailChangeConfirmation().personalize(
            recipient=Recipient(user.id, user.email),
            language=preferences_api.get_user_preference(user, LANGUAGE_KEY),
            user_context=message_context,
        )

        u_prof = UserProfile.objects.get(user=user)
        meta = u_prof.get_meta()
        if 'old_emails' not in meta:
            meta['old_emails'] = []
        meta['old_emails'].append([user.email, datetime.datetime.now(ZoneInfo("UTC")).isoformat()])
        u_prof.set_meta(meta)
        u_prof.save()
        # Send it to the old email...
        try:
            ace.send(msg)
        except Exception:  # pylint: disable=broad-except
            log.warning('Unable to send confirmation email to old address', exc_info=True)
            response = render_to_response("email_change_failed.html", {'email': user.email})
            transaction.set_rollback(True)
            return response

        user.email = pec.new_email
        user.save()
        pec.delete()
        # And send it to the new email...
        msg.recipient = Recipient(user.id, pec.new_email)
        try:
            ace.send(msg)
        except Exception:  # pylint: disable=broad-except
            log.warning('Unable to send confirmation email to new address', exc_info=True)
            response = render_to_response("email_change_failed.html", {'email': pec.new_email})
            transaction.set_rollback(True)
            return response

        response = render_to_response("email_change_successful.html", address_context)

        USER_EMAIL_CHANGED.send(sender=None, user=user, request=request)
        return response


@api_view(['POST'])
@authentication_classes((
    JwtAuthentication,
    BearerAuthenticationAllowInactiveUser,
    SessionAuthenticationAllowInactiveUser,
))
@permission_classes((IsAuthenticated,))
def change_email_settings(request):
    """
    Modify logged-in user's setting for receiving emails from a course.
    """
    user = request.user

    course_id = request.data.get("course_id")
    receive_emails = request.data.get("receive_emails")
    course_key = CourseKey.from_string(course_id)

    if receive_emails:
        optout_object = Optout.objects.filter(user=user, course_id=course_key)
        if optout_object:
            optout_object.delete()
        log.info(
            "User %s (%s) opted in to receive emails from course %s",
            user.username,
            user.email,
            course_id,
        )
        track_views.server_track(
            request,
            "change-email-settings",
            {"receive_emails": "yes", "course": course_id},
            page='dashboard',
        )
    else:
        Optout.objects.get_or_create(user=user, course_id=course_key)
        log.info(
            "User %s (%s) opted out of receiving emails from course %s",
            user.username,
            user.email,
            course_id,
        )
        track_views.server_track(
            request,
            "change-email-settings",
            {"receive_emails": "no", "course": course_id},
            page='dashboard',
        )

    return JsonResponse({"success": True})
