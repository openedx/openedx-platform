"""
Helpers for the student app.
"""


import json
import logging
import mimetypes
import urllib.parse
from collections import OrderedDict
from datetime import datetime

from completion.exceptions import UnavailableCompletionData
from completion.utilities import get_key_to_last_completed_block
from django.conf import settings
from django.contrib.auth import load_backend
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.validators import ValidationError
from django.db import IntegrityError, ProgrammingError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _
from pytz import UTC, timezone

from common.djangoapps import third_party_auth
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import (
    CourseEnrollment,
    LinkedInAddToProfileConfiguration,
    Registration,
    UserAttribute,
    UserProfile,
    email_exists_or_retired,
    unique_id_for_user,
    username_exists_or_retired,
)
from common.djangoapps.util.password_policy_validators import normalize_password
from lms.djangoapps.certificates.api import (
    auto_certificate_generation_enabled,
    certificate_status_for_student,
    certificates_viewable_for_course,
    get_certificate_url,
    has_html_certificates_enabled,
    has_self_generated_certificates_enabled,
)
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.course_home_api.dates.serializers import DateSummarySerializer
from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from lms.djangoapps.courseware.courses import get_course_date_blocks, get_course_with_access
from lms.djangoapps.courseware.date_summary import TodaysDate
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor import access
from lms.djangoapps.verify_student.models import VerificationDeadline
from lms.djangoapps.verify_student.services import IDVerificationService
from lms.djangoapps.verify_student.utils import is_verification_expiring_soon, verification_for_datetime
from openedx.core.djangoapps.content.block_structure.exceptions import UsageKeyNotInBlockStructure
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_themes
from openedx.core.djangoapps.user_authn.utils import is_safe_login_or_logout_redirect
from openedx.core.lib.time_zone_utils import get_time_zone_offset
from xmodule.data import CertificatesDisplayBehaviors  # pylint: disable=wrong-import-order

# Enumeration of per-course verification statuses
# we display on the student dashboard.
VERIFY_STATUS_NEED_TO_VERIFY = "verify_need_to_verify"
VERIFY_STATUS_SUBMITTED = "verify_submitted"
VERIFY_STATUS_RESUBMITTED = "re_verify_submitted"
VERIFY_STATUS_APPROVED = "verify_approved"
VERIFY_STATUS_MISSED_DEADLINE = "verify_missed_deadline"
VERIFY_STATUS_NEED_TO_REVERIFY = "verify_need_to_reverify"

DISABLE_UNENROLL_CERT_STATES = [
    'generating',
    'downloadable',
]
EMAIL_EXISTS_MSG_FMT = _("An account with the Email '{email}' already exists.")
USERNAME_EXISTS_MSG_FMT = _("An account with the Public Username '{username}' already exists.")


log = logging.getLogger(__name__)


def check_verify_status_by_course(user, course_enrollments):
    """
    Determine the per-course verification statuses for a given user.

    The possible statuses are:
        * VERIFY_STATUS_NEED_TO_VERIFY: The student has not yet submitted photos for verification.
        * VERIFY_STATUS_SUBMITTED: The student has submitted photos for verification,
          but has have not yet been approved.
        * VERIFY_STATUS_RESUBMITTED: The student has re-submitted photos for re-verification while
          they still have an active but expiring ID verification
        * VERIFY_STATUS_APPROVED: The student has been successfully verified.
        * VERIFY_STATUS_MISSED_DEADLINE: The student did not submit photos within the course's deadline.
        * VERIFY_STATUS_NEED_TO_REVERIFY: The student has an active verification, but it is
            set to expire before the verification deadline for the course.

    It is is also possible that a course does NOT have a verification status if:
        * The user is not enrolled in a verified mode, meaning that the user didn't pay.
        * The course does not offer a verified mode.
        * The user submitted photos but an error occurred while verifying them.
        * The user submitted photos but the verification was denied.

    In the last two cases, we rely on messages in the sidebar rather than displaying
    messages for each course.

    Arguments:
        user (User): The currently logged-in user.
        course_enrollments (list[CourseEnrollment]): The courses the user is enrolled in.

    Returns:
        dict: Mapping of course keys verification status dictionaries.
            If no verification status is applicable to a course, it will not
            be included in the dictionary.
            The dictionaries have these keys:
                * status (str): One of the enumerated status codes.
                * days_until_deadline (int): Number of days until the verification deadline.
                * verification_good_until (str): Date string for the verification expiration date.

    """
    status_by_course = {}

    # If integrity signature is enabled, this is a no-op because IDV is not required
    if settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE'):
        return status_by_course

    # Retrieve all verifications for the user, sorted in descending
    # order by submission datetime
    verifications = IDVerificationService.verifications_for_user(user)

    # Check whether the user has an active or pending verification attempt
    has_active_or_pending = IDVerificationService.user_has_valid_or_pending(user)

    # Retrieve expiration_datetime of most recent approved verification
    expiration_datetime = IDVerificationService.get_expiration_datetime(user, ['approved'])
    verification_expiring_soon = is_verification_expiring_soon(expiration_datetime)

    # Retrieve verification deadlines for the enrolled courses
    course_deadlines = VerificationDeadline.deadlines_for_enrollments(
        CourseEnrollment.enrollments_for_user(user)
    )
    recent_verification_datetime = None

    for enrollment in course_enrollments:

        # If the user hasn't enrolled as verified, then the course
        # won't display state related to its verification status.
        if enrollment.mode in CourseMode.VERIFIED_MODES:

            # Retrieve the verification deadline associated with the course.
            # This could be None if the course doesn't have a deadline.
            deadline = course_deadlines.get(enrollment.course_id)

            relevant_verification = verification_for_datetime(deadline, verifications)

            # Picking the max verification datetime on each iteration only with approved status
            if relevant_verification is not None and relevant_verification.status == "approved":
                recent_verification_datetime = max(
                    recent_verification_datetime if recent_verification_datetime is not None
                    else relevant_verification.expiration_datetime,
                    relevant_verification.expiration_datetime
                )

            # By default, don't show any status related to verification
            status = None
            should_display = True

            # Check whether the user was approved or is awaiting approval
            if relevant_verification is not None:
                should_display = relevant_verification.should_display_status_to_user()

                if relevant_verification.status == "approved":
                    if verification_expiring_soon:
                        status = VERIFY_STATUS_NEED_TO_REVERIFY
                    else:
                        status = VERIFY_STATUS_APPROVED
                elif relevant_verification.status == "submitted":
                    if verification_expiring_soon:
                        status = VERIFY_STATUS_RESUBMITTED
                    else:
                        status = VERIFY_STATUS_SUBMITTED

            # If the user didn't submit at all, then tell them they need to verify
            # If the deadline has already passed, then tell them they missed it.
            # If they submitted but something went wrong (error or denied),
            # then don't show any messaging next to the course, since we already
            # show messages related to this on the left sidebar.
            submitted = (
                relevant_verification is not None and
                relevant_verification.status not in ["created", "ready"]
            )
            if status is None and not submitted:
                if deadline is None or deadline > datetime.now(UTC):
                    if IDVerificationService.user_is_verified(user) and verification_expiring_soon:
                        # The user has an active verification, but the verification
                        # is set to expire within "EXPIRING_SOON_WINDOW" days (default is 4 weeks).
                        # Tell the student to reverify.
                        status = VERIFY_STATUS_NEED_TO_REVERIFY
                    elif not IDVerificationService.user_is_verified(user):
                        status = VERIFY_STATUS_NEED_TO_VERIFY
                else:
                    # If a user currently has an active or pending verification,
                    # then they may have submitted an additional attempt after
                    # the verification deadline passed.  This can occur,
                    # for example, when the support team asks a student
                    # to reverify after the deadline so they can receive
                    # a verified certificate.
                    # In this case, we still want to show them as "verified"
                    # on the dashboard.
                    if has_active_or_pending:
                        status = VERIFY_STATUS_APPROVED

                    # Otherwise, the student missed the deadline, so show
                    # them as "honor" (the kind of certificate they will receive).
                    else:
                        status = VERIFY_STATUS_MISSED_DEADLINE

            # Set the status for the course only if we're displaying some kind of message
            # Otherwise, leave the course out of the dictionary.
            if status is not None:
                days_until_deadline = None

                now = datetime.now(UTC)
                if deadline is not None and deadline > now:
                    days_until_deadline = (deadline - now).days

                status_by_course[enrollment.course_id] = {
                    'status': status,
                    'days_until_deadline': days_until_deadline,
                    'should_display': should_display,
                }

    if recent_verification_datetime:
        for key, value in status_by_course.items():  # pylint: disable=unused-variable  # noqa: B007
            status_by_course[key]['verification_good_until'] = recent_verification_datetime.strftime("%m/%d/%Y")

    return status_by_course


# Query string parameters that can be passed to the "finish_auth" view to manage
# things like auto-enrollment.
POST_AUTH_PARAMS = ('course_id', 'enrollment_action', 'course_mode', 'email_opt_in', 'purchase_workflow')


def get_redirect_url_with_host(root_url, redirect_to):
    """
    Adds host to the redirect url
    """
    (_, netloc, path, query, fragment) = list(urllib.parse.urlsplit(redirect_to))
    if not netloc:
        parse_root_url = urllib.parse.urlsplit(root_url)
        redirect_to = urllib.parse.urlunsplit((parse_root_url.scheme, parse_root_url.netloc, path, query, fragment))

    return redirect_to


def get_next_url_for_login_page(request, include_host=False):
    """
    Determine the URL to redirect to following login/registration/third_party_auth

    The user is currently on a login or registration page.
    If 'course_id' is set, or other POST_AUTH_PARAMS, we will need to send the user to the
    /account/finish_auth/ view following login, which will take care of auto-enrollment in
    the specified course.

    Otherwise, we go to the ?next= query param or the configured custom
    redirection url (the default behaviour is to go to /dashboard).

    If THIRD_PARTY_AUTH_HINT is set, then `tpa_hint=<hint>` is added as a query parameter.

    This works with both GET and POST requests.
    """
    request_params = request.GET if request.method == 'GET' else request.POST
    redirect_to = _get_redirect_to(
        request_host=request.get_host(),
        request_headers=request.META,
        request_params=request_params,
        request_is_https=request.is_secure(),
    )
    root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    if not redirect_to:
        if settings.ROOT_URLCONF == 'lms.urls':
            login_redirect_url = configuration_helpers.get_value('DEFAULT_REDIRECT_AFTER_LOGIN')

            if login_redirect_url:
                try:
                    redirect_to = reverse(login_redirect_url)
                except NoReverseMatch:
                    log.warning(
                        'Default redirect after login doesn\'t exist: %(login_redirect_url)r. '
                        'Check the value set on DEFAULT_REDIRECT_AFTER_LOGIN configuration variable.',
                        {"login_redirect_url": login_redirect_url}
                    )

            # If redirect url isn't set, reverse to dashboard
            if not redirect_to:
                # Tries reversing the LMS dashboard if the url doesn't exist
                redirect_to = reverse('dashboard')

        elif settings.ROOT_URLCONF == 'cms.urls':
            redirect_to = reverse('home')
            scheme = "https" if settings.HTTPS == "on" else "http"
            root_url = f'{scheme}://{settings.CMS_BASE}'

    if any(param in request_params for param in POST_AUTH_PARAMS):
        # Before we redirect to next/dashboard, we need to handle auto-enrollment:
        params = [(param, request_params[param]) for param in POST_AUTH_PARAMS if param in request_params]

        params.append(('next', redirect_to))  # After auto-enrollment, user will be sent to payment page or to this URL
        redirect_to = '{}?{}'.format(reverse('finish_auth'), urllib.parse.urlencode(params))
        # Note: if we are resuming a third party auth pipeline, then the next URL will already
        # be saved in the session as part of the pipeline state. That URL will take priority
        # over this one.

    # Append a tpa_hint query parameter, if one is configured
    tpa_hint = configuration_helpers.get_value(
        "THIRD_PARTY_AUTH_HINT",
        settings.FEATURES.get("THIRD_PARTY_AUTH_HINT", '')
    )
    if tpa_hint:
        # Don't add tpa_hint if we're already in the TPA pipeline (prevent infinite loop),
        # and don't overwrite any existing tpa_hint params (allow tpa_hint override).
        running_pipeline = third_party_auth.pipeline.get(request)
        (scheme, netloc, path, query, fragment) = list(urllib.parse.urlsplit(redirect_to))
        if not running_pipeline and 'tpa_hint' not in query:
            params = urllib.parse.parse_qs(query)
            params['tpa_hint'] = [tpa_hint]
            query = urllib.parse.urlencode(params, doseq=True)
            redirect_to = urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))

    if include_host:
        return redirect_to, root_url

    return redirect_to


def _get_redirect_to(request_host, request_headers, request_params, request_is_https):
    """
    Determine the redirect url and return if safe

    Arguments:
        request_host (str)
        request_headers (dict)
        request_params (QueryDict)
        request_is_https (bool)

    Returns: str
        redirect url if safe else None
    """
    redirect_to = request_params.get('next')
    header_accept = request_headers.get('HTTP_ACCEPT', '')
    accepts_text_html = any(
        mime_type in header_accept
        for mime_type in ['*/*', 'text/*', 'text/html']
    )

    # If we get a redirect parameter, make sure it's safe i.e. not redirecting outside our domain.
    # Also make sure that it is not redirecting to a static asset and redirected page is web page
    # not a static file. As allowing assets to be pointed to by "next" allows 3rd party sites to
    # get information about a user on edx.org. In any such case drop the parameter.
    if redirect_to:
        mime_type, _ = mimetypes.guess_type(redirect_to, strict=False)
        safe_redirect = is_safe_login_or_logout_redirect(
            redirect_to=redirect_to,
            request_host=request_host,
            dot_client_id=request_params.get('client_id'),
            require_https=request_is_https,
        )
        if not safe_redirect:
            log.warning(
                "Unsafe redirect parameter detected after login page: '%(redirect_to)s'",
                {"redirect_to": redirect_to}
            )
            redirect_to = None
        elif not accepts_text_html:
            log.info(
                "Redirect to non html content '%(content_type)s' detected from '%(user_agent)s'"
                " after login page: '%(redirect_to)s'",
                {
                    "redirect_to": redirect_to, "content_type": header_accept,
                    "user_agent": request_headers.get('HTTP_USER_AGENT', '')
                }
            )
            redirect_to = None
        elif mime_type:
            log.warning(
                "Redirect to url path with specified file type '%(mime_type)s' not allowed: '%(redirect_to)s'",
                {"redirect_to": redirect_to, "mime_type": mime_type}
            )
            redirect_to = None
        elif settings.STATIC_URL in redirect_to:
            log.warning(
                "Redirect to static content detected after login page: '%(redirect_to)s'",
                {"redirect_to": redirect_to}
            )
            redirect_to = None
        else:
            themes = get_themes()
            next_path = urllib.parse.urlparse(redirect_to).path
            for theme in themes:
                if theme.theme_dir_name in next_path:
                    log.warning(
                        "Redirect to theme content detected after login page: '%(redirect_to)s'",
                        {"redirect_to": redirect_to}
                    )
                    redirect_to = None
                    break

    return redirect_to


def create_or_set_user_attribute_created_on_site(user, site):
    """
    Create or Set UserAttribute indicating the site the user account was created on.
    User maybe created on 'courses.edx.org', or a white-label site. Due to the very high
    traffic on this table we now ignore the default site (eg. 'courses.edx.org') and
    code which consumes this attribute should assume a 'created_on_site' which doesn't exist
    belongs to the default site.
    """
    if site and site.id != settings.SITE_ID:
        UserAttribute.set_user_attribute(user, 'created_on_site', site.domain)


# We want to allow inactive users to log in only when their account is first created
NEW_USER_AUTH_BACKEND = 'django.contrib.auth.backends.AllowAllUsersModelBackend'

# Disable this warning because it doesn't make sense to completely refactor tests to appease Pylint
# pylint: disable=logging-format-interpolation


def authenticate_new_user(request, username, password):
    """
    Immediately after a user creates an account, we log them in. They are only
    logged in until they close the browser. They can't log in again until they click
    the activation link from the email.
    """
    password = normalize_password(password)
    backend = load_backend(NEW_USER_AUTH_BACKEND)
    user = backend.authenticate(request=request, username=username, password=password)
    if not user:
        log.warning(f"Unable to authenticate user: {username}")
    user.backend = NEW_USER_AUTH_BACKEND
    return user


class AccountValidationError(Exception):
    """
    Used in account creation views to raise exceptions with details about specific invalid fields
    """
    def __init__(self, message, field, error_code=None):
        super().__init__(message)
        self.field = field
        self.error_code = error_code


def cert_info(user, enrollment):
    """
    Get the certificate info needed to render the dashboard section for the given
    student and course.

    Arguments:
        user (User): A user.
        enrollment (CourseEnrollment): A course enrollment.

    Returns:
        See _cert_info
    """
    return _cert_info(
        user,
        enrollment,
        certificate_status_for_student(user, enrollment.course_overview.id),
    )


def _cert_info(user, enrollment, cert_status):
    """
    Implements the logic for cert_info -- split out for testing.

    TODO: replace with a method that lives in the certificates app and combines this logic with
     lms.djangoapps.certificates.api.can_show_certificate_message and
     lms.djangoapps.courseware.views.get_cert_data

    Arguments:
        user (User): A user.
        enrollment (CourseEnrollment): A course enrollment.
        cert_status (dict): dictionary containing information about certificate status for the user

    Returns:
        dictionary containing:
            'status': one of 'generating', 'downloadable', 'notpassing', 'restricted', 'auditing',
                'processing', 'unverified', 'unavailable', or 'certificate_earned_but_not_available'
            'show_survey_button': bool
            'can_unenroll': if status allows for unenrollment

        The dictionary may also contain:
            'linked_in_url': url to add cert to LinkedIn profile
            'survey_url': url, only if course_overview.end_of_course_survey_url is not None
            'show_cert_web_view': bool if html web certs are enabled and there is an active web cert
            'cert_web_view_url': url if html web certs are enabled and there is an active web cert
            'download_url': url to download a cert
            'grade': if status is in 'generating', 'downloadable', 'notpassing', 'restricted',
                'auditing', or 'unverified'
    """
    # simplify the status for the template using this lookup table
    template_state = {
        CertificateStatuses.generating: 'generating',
        CertificateStatuses.downloadable: 'downloadable',
        CertificateStatuses.notpassing: 'notpassing',
        CertificateStatuses.restricted: 'restricted',
        CertificateStatuses.auditing: 'auditing',
        CertificateStatuses.audit_passing: 'auditing',
        CertificateStatuses.audit_notpassing: 'auditing',
        CertificateStatuses.unverified: 'unverified',
    }

    certificate_earned_but_not_available_status = 'certificate_earned_but_not_available'
    default_status = 'processing'

    default_info = {
        'status': default_status,
        'show_survey_button': False,
        'can_unenroll': True,
    }

    if cert_status is None or enrollment is None:
        return default_info

    course_overview = enrollment.course_overview if enrollment else None
    status = template_state.get(cert_status['status'], default_status)
    is_hidden_status = status in ('processing', 'generating', 'notpassing', 'auditing')

    if _is_certificate_earned_but_not_available(course_overview, status):
        status = certificate_earned_but_not_available_status

    if (
        course_overview.certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO and
        is_hidden_status
    ):
        return default_info

    if not CourseMode.is_eligible_for_certificate(enrollment.mode, status=status):
        return default_info

    if course_overview and access.is_beta_tester(user, course_overview.id):
        # Beta testers are not eligible for a course certificate
        return default_info

    status_dict = {
        'status': status,
        'mode': cert_status.get('mode', None),
        'linked_in_url': None,
        'can_unenroll': status not in DISABLE_UNENROLL_CERT_STATES,
    }

    if status != default_status and course_overview.end_of_course_survey_url is not None:
        status_dict.update({
            'show_survey_button': True,
            'survey_url': process_survey_link(course_overview.end_of_course_survey_url, user)})
    else:
        status_dict['show_survey_button'] = False

    if status == 'downloadable':
        # showing the certificate web view button if certificate is downloadable state and feature flags are enabled.
        if has_html_certificates_enabled(course_overview):
            if course_overview.has_any_active_web_certificate:
                status_dict.update({
                    'show_cert_web_view': True,
                    'cert_web_view_url': get_certificate_url(course_id=course_overview.id, uuid=cert_status['uuid'])
                })
            elif cert_status['download_url']:
                status_dict['download_url'] = cert_status['download_url']
            else:
                # don't show download certificate button if we don't have an active certificate for course
                status_dict['status'] = 'unavailable'
        elif 'download_url' not in cert_status:
            log.warning(
                "User %s has a downloadable cert for %s, but no download url",
                user.username,
                course_overview.id
            )
            return default_info
        else:
            status_dict['download_url'] = cert_status['download_url']

            # If enabled, show the LinkedIn "add to profile" button
            # Clicking this button sends the user to LinkedIn where they
            # can add the certificate information to their profile.
            linkedin_config = LinkedInAddToProfileConfiguration.current()
            if linkedin_config.is_enabled():
                status_dict['linked_in_url'] = linkedin_config.add_to_profile_url(
                    course_overview, cert_status.get('mode'), cert_status['download_url'],
                )

    if status in {'generating', 'downloadable', 'notpassing', 'restricted', 'auditing', 'unverified'}:
        cert_grade_percent = -1
        persisted_grade_percent = -1
        persisted_grade = CourseGradeFactory().read(user, course=course_overview, create_if_needed=False)
        if persisted_grade is not None:
            persisted_grade_percent = persisted_grade.percent

        if 'grade' in cert_status:
            cert_grade_percent = float(cert_status['grade'])

        if cert_grade_percent == -1 and persisted_grade_percent == -1:
            # Note: as of 11/20/2012, we know there are students in this state-- cs169.1x,
            # who need to be regraded (we weren't tracking 'notpassing' at first).
            # We can add a log.warning here once we think it shouldn't happen.
            return default_info
        grades_input = [cert_grade_percent, persisted_grade_percent]
        max_grade = (
            None
            if all(grade is None for grade in grades_input)
            else max(filter(lambda x: x is not None, grades_input))
        )
        status_dict['grade'] = str(max_grade)

        # If the grade is passing, the status is one of these statuses, and request certificate
        # is enabled for a course then we need to provide the option to the learner
        cert_gen_enabled = (
            has_self_generated_certificates_enabled(course_overview.id) or
            auto_certificate_generation_enabled()
        )
        passing_grade = persisted_grade and persisted_grade.passed
        if (
            status_dict['status'] != CertificateStatuses.downloadable and
            cert_gen_enabled and
            passing_grade and
            course_overview.has_any_active_web_certificate
        ):
            status_dict['status'] = CertificateStatuses.requesting

    return status_dict


def _is_certificate_earned_but_not_available(course_overview, status):
    """
    Returns True if the user is passing the course, but the certificate is not visible due to display behavior or
    available date

    Params:
        course_overview (CourseOverview): The course to check we're checking the certificate for
        status (str): The certificate status the user has in the course

    Returns:
        (bool): True if the user earned the certificate but it's hidden due to display behavior, else False

    """
    return (
        not certificates_viewable_for_course(course_overview)
        and CertificateStatuses.is_passing_status(status)
        and course_overview.certificates_display_behavior in (
            CertificatesDisplayBehaviors.END_WITH_DATE,
            CertificatesDisplayBehaviors.END
        )
    )


def process_survey_link(survey_link, user):
    """
    If {UNIQUE_ID} appears in the link, replace it with a unique id for the user.
    Currently, this is sha1(user.username).  Otherwise, return survey_link.
    """
    return survey_link.format(UNIQUE_ID=unique_id_for_user(user))


def do_create_account(form, custom_form=None):
    """
    Given cleaned post variables, create the User and UserProfile objects, as well as the
    registration for this user.

    Returns a tuple (User, UserProfile, Registration).

    Note: this function is also used for creating test users.
    """
    # Check if ALLOW_PUBLIC_ACCOUNT_CREATION flag turned off to restrict user account creation
    if not configuration_helpers.get_value(
            'ALLOW_PUBLIC_ACCOUNT_CREATION',
            settings.FEATURES.get('ALLOW_PUBLIC_ACCOUNT_CREATION', True)
    ):
        raise PermissionDenied()

    errors = {}
    errors.update(form.errors)
    if custom_form:
        errors.update(custom_form.errors)

    if errors:
        raise ValidationError(errors)

    proposed_username = form.cleaned_data["username"]
    user = User(
        username=proposed_username,
        email=form.cleaned_data["email"],
        is_active=False
    )
    password = normalize_password(form.cleaned_data["password"])
    user.set_password(password)
    registration = Registration()

    # TODO: Rearrange so that if part of the process fails, the whole process fails.
    # Right now, we can have e.g. no registration e-mail sent out and a zombie account
    try:
        with transaction.atomic():
            user.save()
            if custom_form:
                custom_model = custom_form.save(commit=False)
                custom_model.user = user
                custom_model.save()
    except IntegrityError:
        # Figure out the cause of the integrity error
        # TODO duplicate email is already handled by form.errors above as a ValidationError.
        # The checks for duplicate email/username should occur in the same place with an
        # AccountValidationError and a consistent user message returned (i.e. both should
        # return "It looks like {username} belongs to an existing account. Try again with a
        # different username.")
        if username_exists_or_retired(user.username):  # pylint: disable=no-else-raise
            raise AccountValidationError(  # pylint: disable=raise-missing-from  # noqa: B904
                USERNAME_EXISTS_MSG_FMT.format(username=proposed_username),
                field="username",
                error_code='duplicate-username',
            )
        elif email_exists_or_retired(user.email):
            raise AccountValidationError(  # pylint: disable=raise-missing-from  # noqa: B904
                _("An account with the Email '{email}' already exists.").format(email=user.email),
                field="email",
                error_code='duplicate-email'
            )
        else:
            raise

    registration.register(user)

    profile_fields = [
        "name", "level_of_education", "gender", "mailing_address", "city", "country", "goals",
        "year_of_birth"
    ]
    profile = UserProfile(
        user=user,
        **{key: form.cleaned_data.get(key) for key in profile_fields}
    )
    extended_profile = form.cleaned_extended_profile
    if extended_profile:
        profile.meta = json.dumps(extended_profile)
    try:
        profile.save()
    except Exception:
        log.exception(f"UserProfile creation failed for user {user.id}.")
        raise

    return user, profile, registration


def get_resume_urls_for_enrollments(user, enrollments):
    '''
    For a given user, return a list of urls to the user's last completed block in
    a course run for each course run in the user's enrollments.

    Arguments:
        user: the user object for which we want resume course urls
        enrollments (list): a list of user enrollments

    Returns:
        resume_course_urls (OrderedDict): an OrderedDict of urls
            key: CourseKey
            value: url to the last completed block
                if the value is '', then the user has not completed any blocks in the course run
    '''
    resume_course_urls = OrderedDict()
    for enrollment in enrollments:
        try:
            block_key = get_key_to_last_completed_block(user, enrollment.course_id)
            try:
                block_data = get_course_blocks(user, block_key)
            except UsageKeyNotInBlockStructure:
                url_to_block = ''
            else:
                if block_key in block_data:
                    url_to_block = reverse(
                        'jump_to',
                        kwargs={'course_id': enrollment.course_id, 'location': block_key}
                    )
                else:
                    url_to_block = ''
        except UnavailableCompletionData:
            url_to_block = ''
        resume_course_urls[enrollment.course_id] = url_to_block
    return resume_course_urls


def does_user_profile_exist(user):
    """
    Check if user has an associated profile.
    Ignore errors and return False in case of errors.
    """
    try:
        return hasattr(user, 'profile')
    except (ProgrammingError, ObjectDoesNotExist):
        return False


def user_has_passing_grade_in_course(enrollment):
    """
    Check to see if a user has passing grade for a course
    """
    try:
        user = enrollment.user
        course = enrollment.course_overview
        course_grade = CourseGradeFactory().read(user, course, create_if_needed=False)
        if course_grade:
            return course_grade.passed
    except AttributeError:
        pass
    return False


def get_instructors(course_run, marketing_root_url):
    """
    Get course instructors.
    """
    if course_run is None:
        raise ValueError("missing course_run")
    instructors = []
    staff = course_run.get('staff', [])
    for instructor in staff:
        instructor = {
            'name': f"{instructor.get('given_name')} {instructor.get('family_name')}",
            'profile_image_url': instructor.get('profile_image_url'),
            'organization_name': (instructor.get('position').get('organization_name')
                                  if instructor.get('position') else ''),
            'bio_url': f"{marketing_root_url}/bio/{instructor.get('slug')}"
        }
        instructors.append(instructor)

    return instructors


def _prepare_date_block(block, block_date, user_timezone):
    """
    Prepare date block which include assignment related data for this date
    """
    timezone_offset = get_time_zone_offset(user_timezone, block_date)
    block = {
        'title': block.get('title', ''),
        'assignment_type': block.get('assignment_type', '') or '',
        'assignment_count': 0,
        'link': block.get('link', ''),
        'date': block_date,
        'due_date': block_date.strftime("%a, %b %d, %Y"),
        'due_time': (f'{block_date.strftime("%H:%M %p")} GMT{timezone_offset}' if block.get('assignment_type') else '')
    }
    return block


def _remove_date_key_from_course_dates(course_data):
    """
    Remove date key from course dates list
    """
    _ = course_data.pop('date')
    return course_data


def get_course_dates_for_email(user, course_id, request):
    """
    Getting nearest dates from today one would be before today and one
    would be after today.
    """
    user_timezone_locale = user_timezone_locale_prefs(request)
    user_timezone = timezone(user_timezone_locale['user_timezone'] or str(UTC))

    course = get_course_with_access(user, 'load', course_id)
    date_blocks = get_course_date_blocks(course, user, request, include_access=True, include_past_dates=True)
    date_blocks = [block for block in date_blocks if not isinstance(block, TodaysDate)]
    blocks = DateSummarySerializer(date_blocks, many=True).data

    today = datetime.now(user_timezone)
    course_date = {
        'title': '',
        'assignment_type': '',
        'link': '',
        'assignment_count': 0,
        'date': '',
        'due_date': today.strftime("%a, %b %d, %Y"),
        'due_time': ''
    }
    course_date_list = [{**course_date, }, {**course_date, 'date': today}, {**course_date}]
    for block in blocks:
        block_date = datetime.strptime(block.get('date')[:19], '%Y-%m-%dT%H:%M:%S')
        block_date = block_date.replace(tzinfo=UTC)
        block_date = block_date.astimezone(user_timezone)

        if block_date < today:
            if block_date == course_date_list[0]['date'] and block.get('assignment_type'):
                course_date_list[0]['assignment_count'] += 1
            else:
                course_date_list[0].update(_prepare_date_block(block, block_date, user_timezone))

        if block_date == today:
            if block.get('assignment_type') and course_date_list[1]['assignment_type'] != '':
                course_date_list[1]['assignment_count'] += 1
            else:
                course_date_list[1].update(_prepare_date_block(block, block_date, user_timezone))

        if block_date > today:
            if block_date == course_date_list[2]['date'] and block.get('assignment_type'):
                course_date_list[2]['assignment_count'] += 1
            if course_date_list[2]['date'] == '':
                course_date_list[2].update(_prepare_date_block(block, block_date, user_timezone))

    course_date_list = list(map(_remove_date_key_from_course_dates, course_date_list))
    return course_date_list
