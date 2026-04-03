""" Views related to auto auth. """


import datetime
import uuid

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import PermissionDenied
from django.core.validators import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.template.context_processors import csrf
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _
from edx_django_utils.user import generate_password  # lint-amnesty, pylint: disable=wrong-import-order
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.helpers import (
    AccountValidationError,
    authenticate_new_user,
    create_or_set_user_attribute_created_on_site,
    do_create_account,
)
from common.djangoapps.student.models import (
    CourseAccessRole,
    CourseEnrollment,
    Registration,
    UserProfile,
    anonymous_id_for_user,
    create_comments_service_user,
)
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.core.djangoapps.django_comment_common.models import assign_role
from openedx.core.djangoapps.user_authn.views.registration_form import AccountCreationForm
from openedx.features.course_experience import course_home_url


def auto_auth(request):  # pylint: disable=too-many-statements
    """
    Create or configure a user account, then log in as that user.

    Enabled only when
    settings.FEATURES['AUTOMATIC_AUTH_FOR_TESTING'] is true.

    Accepts the following querystring parameters:
    * `username`, `email`, and `password` for the user account
    * `full_name` for the user profile (the user's full name; defaults to the username)
    * `staff`: Set to "true" to make the user global staff.
    * `course_id`: Enroll the student in the course with `course_id`
    * `roles`: Comma-separated list of roles to grant the student in the course with `course_id`
    * `no_login`: Define this to create the user but not login
    * `redirect`: Set to "true" will redirect to the `redirect_to` value if set, or
        course home page if course_id is defined, otherwise it will redirect to dashboard
    * `redirect_to`: will redirect to to this url
    * `is_active` : make/update account with status provided as 'is_active'
    * `should_manually_verify`: Whether the created user should have their identification verified
    If username, email, or password are not provided, use
    randomly generated credentials.
    """

    # Generate a unique name to use if none provided
    generated_username = uuid.uuid4().hex[0:30]
    generated_password = generate_password()

    # Use the params from the request, otherwise use these defaults
    username = request.GET.get('username', generated_username)
    password = request.GET.get('password', generated_password)
    email = request.GET.get('email', username + "@example.com")
    full_name = request.GET.get('full_name', username)
    is_staff = _str2bool(request.GET.get('staff', False))
    is_superuser = _str2bool(request.GET.get('superuser', False))
    course_id = request.GET.get('course_id')
    redirect_to = request.GET.get('redirect_to')
    is_active = _str2bool(request.GET.get('is_active', True))

    # Valid modes: audit, credit, honor, no-id-professional, professional, verified
    enrollment_mode = request.GET.get('enrollment_mode', 'honor')

    # Whether to add a manual ID verification record for the user (can
    # be helpful for bypassing certain gated features)
    should_manually_verify = _str2bool(request.GET.get('should_manually_verify', False))

    # Parse roles, stripping whitespace, and filtering out empty strings
    roles = _clean_roles(request.GET.get('roles', '').split(','))
    course_access_roles = _clean_roles(request.GET.get('course_access_roles', '').split(','))

    redirect_when_done = _str2bool(request.GET.get('redirect', '')) or redirect_to
    login_when_done = 'no_login' not in request.GET

    restricted = settings.FEATURES.get('RESTRICT_AUTOMATIC_AUTH', True)
    if is_superuser and restricted:
        return HttpResponseForbidden(_('Superuser creation not allowed'))

    form = AccountCreationForm(
        data={
            'username': username,
            'email': email,
            'password': password,
            'name': full_name,
        },
        tos_required=False
    )

    # Attempt to create the account.
    # If successful, this will return a tuple containing
    # the new user object.
    try:
        user, profile, reg = do_create_account(form)
    except (AccountValidationError, ValidationError):
        if restricted:
            return HttpResponseForbidden(_('Account modification not allowed.'))
        # Attempt to retrieve the existing user.
        user = User.objects.get(username=username)
        user.email = email
        user.set_password(password)
        user.is_active = is_active
        user.save()
        profile = UserProfile.objects.get(user=user)
        reg = Registration.objects.get(user=user)
    except PermissionDenied:
        return HttpResponseForbidden(_('Account creation not allowed.'))

    user.is_staff = is_staff
    user.is_superuser = is_superuser
    user.save()

    if is_active:
        reg.activate()
        reg.save()

    if should_manually_verify:
        ManualVerification.objects.get_or_create(user=user, status="approved")

    # ensure parental consent threshold is met
    year = datetime.date.today().year
    age_limit = settings.PARENTAL_CONSENT_AGE_LIMIT
    profile.year_of_birth = (year - age_limit) - 1
    profile.save()

    create_or_set_user_attribute_created_on_site(user, request.site)

    # Enroll the user in a course
    course_key = None
    if course_id:
        course_key = CourseLocator.from_string(course_id)
        CourseEnrollment.enroll(user, course_key, mode=enrollment_mode)

        # Apply the roles
        for role in roles:
            assign_role(course_key, user, role)

        for role in course_access_roles:
            CourseAccessRole.objects.update_or_create(user=user, course_id=course_key, org=course_key.org, role=role)

    # Log in as the user
    if login_when_done:
        user = authenticate_new_user(request, username, password)
        django_login(request, user)

    create_comments_service_user(user)

    if redirect_when_done:
        if redirect_to:
            # Redirect to page specified by the client
            redirect_url = redirect_to
        elif course_id:
            # Redirect to the course homepage (in LMS) or outline page (in Studio)
            try:
                redirect_url = reverse('course_handler', kwargs={'course_key_string': course_id})  # Studio
            except NoReverseMatch:
                redirect_url = course_home_url(course_key)  # LMS
        else:
            # Redirect to the learner dashboard (in LMS) or homepage (in Studio)
            try:
                redirect_url = reverse('dashboard')
            except NoReverseMatch:
                redirect_url = reverse('home')

        return redirect(redirect_url)
    else:
        response = JsonResponse({
            'created_status': 'Logged in' if login_when_done else 'Created',
            'username': username,
            'email': email,
            'password': password,
            'user_id': user.id,
            'anonymous_id': anonymous_id_for_user(user, None),
        })
    response.set_cookie('csrftoken', csrf(request)['csrf_token'], secure=request.is_secure())
    return response


def _clean_roles(roles):
    """ Clean roles.

    Strips whitespace from roles, and removes empty items.

    Args:
        roles (str[]): List of role names.

    Returns:
        str[]
    """
    roles = [role.strip() for role in roles]
    roles = [role for role in roles if role]
    return roles


def _str2bool(s):
    s = str(s)
    return s.lower() in ('yes', 'true', 't', '1')
