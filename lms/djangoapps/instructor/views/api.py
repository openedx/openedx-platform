"""
Instructor Dashboard API views

JSON views which the instructor dashboard requests.

Many of these GETs may become PUTs in the future.
"""

import csv
import datetime
import json
import logging
import string
import random
import re

import dateutil
import pytz
import edx_api_doc_tools as apidocs
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django_countries.fields import Country
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import QueryDict, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_when.api import get_date_for_block
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx.core.djangoapps.course_groups.cohorts import get_cohort_by_name
from rest_framework.exceptions import MethodNotAllowed
from rest_framework import serializers, status  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.permissions import IsAdminUser, IsAuthenticated, BasePermission  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.response import Response  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.views import APIView  # lint-amnesty, pylint: disable=wrong-import-order
from submissions import api as sub_api  # installed from the edx-submissions repository  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student import auth
from common.djangoapps.student.api import is_user_enrolled_in_course
from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    DEFAULT_TRANSITION_STATE,
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    EntranceExamConfiguration,
    ManualEnrollmentAudit,
    Registration,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    UserProfile,
    get_user_by_username_or_email,
    is_email_retired,
)
from common.djangoapps.student.roles import CourseFinanceAdminRole, CourseSalesAdminRole
from common.djangoapps.util.file import (
    FileValidationException,
    course_and_time_based_filename_generator,
    store_uploaded_file,
)
from common.djangoapps.util.json_request import JsonResponse, JsonResponseBadRequest
from common.djangoapps.util.views import require_global_staff  # pylint: disable=unused-import
from lms.djangoapps.bulk_email.api import is_bulk_email_feature_enabled, create_course_email
from lms.djangoapps.certificates import api as certs_api
from lms.djangoapps.course_home_api.toggles import course_home_mfe_progress_tab_is_active
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor import enrollment
from lms.djangoapps.instructor.access import ROLES, allow_access, list_with_level, revoke_access, update_forum_role
from lms.djangoapps.instructor.constants import INVOICE_KEY
from lms.djangoapps.instructor.enrollment import (
    enroll_email,
    get_email_params,
    get_user_email_language,
    send_beta_role_email,
    send_mail_to_student,
    unenroll_email,
)
from lms.djangoapps.instructor.views.instructor_task_helpers import extract_email_features, extract_task_features
from lms.djangoapps.instructor_analytics import basic as instructor_analytics_basic, csvs as instructor_analytics_csvs
from lms.djangoapps.instructor_task import api as task_api
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError, QueueConnectionError
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import ReportStore
from lms.djangoapps.instructor.views.serializer import (
    AccessSerializer,
    BlockDueDateSerializer,
    CertificateSerializer,
    CertificateStatusesSerializer,
    ForumRoleNameSerializer,
    ListInstructorTaskInputSerializer,
    ModifyAccessSerializer,
    RoleNameSerializer,
    SendEmailSerializer,
    ShowUnitExtensionsSerializer,
    ShowStudentExtensionSerializer,
    StudentAttemptsSerializer,
    UserSerializer,
    UniqueStudentIdentifierSerializer,
    ProblemResetSerializer,
    UpdateForumRoleMembershipSerializer,
    RescoreEntranceExamSerializer,
    OverrideProblemScoreSerializer,
    StudentsUpdateEnrollmentSerializer,
    ResetEntranceExamAttemptsSerializer
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups.cohorts import add_user_to_cohort, is_course_cohorted
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.djangoapps.django_comment_common.models import (
    CourseDiscussionSettings,
    Role,
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from openedx.core.lib.courses import get_course_by_id
from openedx.core.lib.api.serializers import CourseKeyField
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url
from .tools import (
    DashboardError,
    dump_block_extensions,
    dump_student_extensions,
    find_unit,
    get_student_from_identifier,
    keep_field_private,
    parse_datetime,
    set_due_date_extension,
    strip_if_string,
)
from .. import permissions

log = logging.getLogger(__name__)

TASK_SUBMISSION_OK = 'created'

SUCCESS_MESSAGE_TEMPLATE = _("The {report_type} report is being created. "
                             "To view the status of the report, see Pending Tasks below.")


def common_exceptions_400(func):
    """
    Catches common exceptions and renders matching 400 errors.
    (decorator without arguments)
    """

    def wrapped(request, *args, **kwargs):
        use_json = (request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                    request.META.get("HTTP_ACCEPT", "").startswith("application/json"))
        try:
            return func(request, *args, **kwargs)
        except User.DoesNotExist:
            message = _('User does not exist.')
        except MultipleObjectsReturned:
            message = _('Found a conflict with given identifier. Please try an alternative identifier')
        except (AlreadyRunningError, QueueConnectionError, AttributeError) as err:
            message = str(err)

        if use_json:
            return JsonResponseBadRequest(message)
        else:
            return HttpResponseBadRequest(message)

    return wrapped


def require_course_permission(permission):
    """
    Decorator with argument that requires a specific permission of the requesting
    user. If the requirement is not satisfied, returns an
    HttpResponseForbidden (403).

    Assumes that request is in args[0].
    Assumes that course_id is in kwargs['course_id'].
    """
    def decorator(func):
        def wrapped(*args, **kwargs):
            request = args[0]
            course = get_course_by_id(CourseKey.from_string(kwargs['course_id']))

            if request.user.has_perm(permission, course):
                return func(*args, **kwargs)
            else:
                return HttpResponseForbidden()
        return wrapped
    return decorator


def require_sales_admin(func):
    """
    Decorator for checking sales administrator access before executing an HTTP endpoint. This decorator
    is designed to be used for a request based action on a course. It assumes that there will be a
    request object as well as a course_id attribute to leverage to check course level privileges.

    If the user does not have privileges for this operation, this will return HttpResponseForbidden (403).
    """
    def wrapped(request, course_id):

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            log.error("Unable to find course with course key %s", course_id)
            return HttpResponseNotFound()

        access = auth.user_has_role(request.user, CourseSalesAdminRole(course_key))

        if access:
            return func(request, course_id)
        else:
            return HttpResponseForbidden()
    return wrapped


def require_finance_admin(func):
    """
    Decorator for checking finance administrator access before executing an HTTP endpoint. This decorator
    is designed to be used for a request based action on a course. It assumes that there will be a
    request object as well as a course_id attribute to leverage to check course level privileges.

    If the user does not have privileges for this operation, this will return HttpResponseForbidden (403).
    """
    def wrapped(request, course_id):

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            log.error("Unable to find course with course key %s", course_id)
            return HttpResponseNotFound()

        access = auth.user_has_role(request.user, CourseFinanceAdminRole(course_key))

        if access:
            return func(request, course_id)
        else:
            return HttpResponseForbidden()
    return wrapped


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class RegisterAndEnrollStudents(APIView):
    """
    Create new account and Enroll students in this course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_ENROLL

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):  # pylint: disable=too-many-statements
        """
        Create new account and Enroll students in this course.
        Passing a csv file that contains a list of students.
        Order in csv should be the following email = 0; username = 1; name = 2; country = 3.
        If there are more than 4 columns in the csv: cohort = 4, course mode = 5.
        Requires staff access.

        -If the email address and username already exists and the user is enrolled in the course,
        do nothing (including no email gets sent out)

        -If the email address already exists, but the username is different,
        match on the email address only and continue to enroll the user in the course using the email address
        as the matching criteria. Note the change of username as a warning message (but not a failure).
        Send a standard enrollment email which is the same as the existing manual enrollment

        -If the username already exists (but not the email), assume it is a different user and fail
        to create the new account.
        The failure will be messaged in a response in the browser.
        """
        if not configuration_helpers.get_value(
            'ALLOW_AUTOMATED_SIGNUPS',
            settings.FEATURES.get('ALLOW_AUTOMATED_SIGNUPS', False),
        ):
            return HttpResponseForbidden()

        course_id = CourseKey.from_string(course_id)
        warnings = []
        row_errors = []
        general_errors = []

        # email-students is a checkbox input type; will be present in POST if checked, absent otherwise
        notify_by_email = 'email-students' in request.POST

        # for white labels we use 'shopping cart' which uses CourseMode.HONOR as
        # course mode for creating course enrollments.
        if CourseMode.is_white_label(course_id):
            default_course_mode = CourseMode.HONOR
        else:
            default_course_mode = None

        # Allow bulk enrollments in all non-expired course modes including "credit" (which is non-selectable)
        valid_course_modes = set(map(lambda x: x.slug, CourseMode.modes_for_course(
            course_id=course_id,
            only_selectable=False,
            include_expired=False,
        )))

        if 'students_list' in request.FILES:  # lint-amnesty, pylint: disable=too-many-nested-blocks
            students = []

            try:
                upload_file = request.FILES.get('students_list')
                if upload_file.name.endswith('.csv'):
                    students = list(csv.reader(upload_file.read().decode('utf-8-sig').splitlines()))
                    course = get_course_by_id(course_id)
                else:
                    general_errors.append({
                        'username': '', 'email': '',
                        'response': _(
                            'Make sure that the file you upload is in CSV format with no '
                            'extraneous characters or rows.')
                    })

            except Exception:  # pylint: disable=broad-except
                general_errors.append({
                    'username': '', 'email': '', 'response': _('Could not read uploaded file.')
                })
            finally:
                upload_file.close()

            generated_passwords = []
            # To skip fetching cohorts from the DB while iterating on students,
            # {<cohort name>: CourseUserGroup}
            cohorts_cache = {}
            already_warned_not_cohorted = False
            extra_fields_is_enabled = configuration_helpers.get_value(
                'ENABLE_AUTOMATED_SIGNUPS_EXTRA_FIELDS',
                settings.FEATURES.get('ENABLE_AUTOMATED_SIGNUPS_EXTRA_FIELDS', False),
            )

            # Iterate each student in the uploaded csv file.
            for row_num, student in enumerate(students, 1):

                # Verify that we have the expected number of columns in every row
                # but allow for blank lines.
                if not student:
                    continue

                if extra_fields_is_enabled:
                    is_valid_csv = 4 <= len(student) <= 6
                    error = _('Data in row #{row_num} must have between four and six columns: '
                              'email, username, full name, country, cohort, and course mode. '
                              'The last two columns are optional.').format(row_num=row_num)
                else:
                    is_valid_csv = len(student) == 4
                    error = _('Data in row #{row_num} must have exactly four columns: '
                              'email, username, full name, and country.').format(row_num=row_num)

                if not is_valid_csv:
                    general_errors.append({
                        'username': '',
                        'email': '',
                        'response': error
                    })
                    continue

                # Extract each column, handle optional columns if they exist.
                email, username, name, country, *optional_cols = student
                if optional_cols:
                    optional_cols.append(default_course_mode)
                    cohort_name, course_mode, *_tail = optional_cols
                else:
                    cohort_name = None
                    course_mode = None

                if not Country(country).name:
                    row_errors.append({
                        'username': username,
                        'email': email,
                        'response': _(
                            'Invalid country: {country}. '
                            'Please enter a valid country code. e.g., US, GB'
                        ).format(country=country)
                    })
                    continue

                # Validate cohort name, and get the cohort object.  Skip if course
                # is not cohorted.
                cohort = None

                if cohort_name and not already_warned_not_cohorted:
                    if not is_course_cohorted(course_id):
                        row_errors.append({
                            'username': username,
                            'email': email,
                            'response': _('Course is not cohorted but cohort provided. '
                                          'Ignoring cohort assignment for all users.')
                        })
                        already_warned_not_cohorted = True
                    elif cohort_name in cohorts_cache:
                        cohort = cohorts_cache[cohort_name]
                    else:
                        # Don't attempt to create cohort or assign student if cohort
                        # does not exist.
                        try:
                            cohort = get_cohort_by_name(course_id, cohort_name)
                        except CourseUserGroup.DoesNotExist:
                            row_errors.append({
                                'username': username,
                                'email': email,
                                'response': _('Cohort name not found: {cohort}. '
                                              'Ignoring cohort assignment for '
                                              'all users.').format(cohort=cohort_name)
                            })
                        cohorts_cache[cohort_name] = cohort

                # Validate course mode.
                if not course_mode:
                    course_mode = default_course_mode

                if (course_mode is not None
                        and course_mode not in valid_course_modes):
                    # If `default is None` and the user is already enrolled,
                    # `CourseEnrollment.change_mode()` will not update the mode,
                    # hence two error messages.
                    if default_course_mode is None:
                        err_msg = _(
                            'Invalid course mode: {mode}. Falling back to the '
                            'default mode, or keeping the current mode in case the '
                            'user is already enrolled.'
                        ).format(mode=course_mode)
                    else:
                        err_msg = _(
                            'Invalid course mode: {mode}.  Failling back to '
                            '{default_mode}, or resetting to {default_mode} in case '
                            'the user is already enrolled.'
                        ).format(mode=course_mode, default_mode=default_course_mode)
                    row_errors.append({
                        'username': username,
                        'email': email,
                        'response': err_msg,
                    })
                    course_mode = default_course_mode

                email_params = get_email_params(course, True, secure=request.is_secure())
                try:
                    validate_email(email)  # Raises ValidationError if invalid
                except ValidationError:
                    row_errors.append({
                        'username': username,
                        'email': email,
                        'response': _('Invalid email {email_address}.').format(email_address=email)
                    })
                else:
                    if User.objects.filter(email=email).exists():
                        # Email address already exists. assume it is the correct user
                        # and just register the user in the course and send an enrollment email.
                        user = User.objects.get(email=email)

                        # see if it is an exact match with email and username
                        # if it's not an exact match then just display a warning message, but continue onwards
                        if not User.objects.filter(email=email, username=username).exists():
                            warning_message = _(
                                'An account with email {email} exists but the provided username {username} '
                                'is different. Enrolling anyway with {email}.'
                            ).format(email=email, username=username)

                            warnings.append({
                                'username': username, 'email': email, 'response': warning_message
                            })
                            log.warning('email %s already exist', email)
                        else:
                            log.info(
                                "user already exists with username '%s' and email '%s'",
                                username,
                                email
                            )

                        # enroll a user if it is not already enrolled.
                        if not is_user_enrolled_in_course(user, course_id):
                            # Enroll user to the course and add manual enrollment audit trail
                            create_manual_course_enrollment(
                                user=user,
                                course_id=course_id,
                                mode=course_mode,
                                enrolled_by=request.user,
                                reason='Enrolling via csv upload',
                                state_transition=UNENROLLED_TO_ENROLLED,
                            )
                            enroll_email(
                                course_id=course_id,
                                student_email=email,
                                auto_enroll=True,
                                message_students=notify_by_email,
                                message_params=email_params,
                            )
                        else:
                            # update the course mode if already enrolled
                            existing_enrollment = CourseEnrollment.get_enrollment(user, course_id)
                            if existing_enrollment.mode != course_mode:
                                existing_enrollment.change_mode(mode=course_mode)
                        if cohort:
                            try:
                                add_user_to_cohort(cohort, user)
                            except ValueError:
                                # user already in this cohort; ignore
                                pass
                    elif is_email_retired(email):
                        # We are either attempting to enroll a retired user or create a new user with an email which is
                        # already associated with a retired account.  Simply block these attempts.
                        row_errors.append({
                            'username': username,
                            'email': email,
                            'response': _('Invalid email {email_address}.').format(email_address=email),
                        })
                        log.warning('Email address %s is associated with a retired user, so course enrollment was ' +  # lint-amnesty, pylint: disable=logging-not-lazy
                                    'blocked.', email)
                    else:
                        # This email does not yet exist, so we need to create a new account
                        # If username already exists in the database, then create_and_enroll_user
                        # will raise an IntegrityError exception.
                        password = generate_unique_password(generated_passwords)
                        errors = create_and_enroll_user(
                            email,
                            username,
                            name,
                            country,
                            password,
                            course_id,
                            course_mode,
                            request.user,
                            email_params,
                            email_user=notify_by_email,
                        )
                        row_errors.extend(errors)
                        if cohort:
                            try:
                                add_user_to_cohort(cohort, email)
                            except ValueError:
                                # user already in this cohort; ignore
                                # NOTE: Checking this here may be unnecessary if we can prove that a
                                # new user will never be
                                # automatically assigned to a cohort from the above.
                                pass
                            except ValidationError:
                                row_errors.append({
                                    'username': username,
                                    'email': email,
                                    'response': _('Invalid email {email_address}.').format(email_address=email),
                                })

        else:
            general_errors.append({
                'username': '', 'email': '', 'response': _('File is not attached.')
            })

        results = {
            'row_errors': row_errors,
            'general_errors': general_errors,
            'warnings': warnings
        }
        return JsonResponse(results)


def generate_random_string(length):
    """
    Create a string of random characters of specified length
    """
    chars = [
        char for char in string.ascii_uppercase + string.digits + string.ascii_lowercase
        if char not in 'aAeEiIoOuU1l'
    ]
    return ''.join(random.choice(chars) for i in range(length))


def generate_unique_password(generated_passwords, password_length=12):
    """
    generate a unique password for each student.
    """

    password = generate_random_string(password_length)
    while password in generated_passwords:
        password = generate_random_string(password_length)

    generated_passwords.append(password)

    return password


def create_user_and_user_profile(email, username, name, country, password):
    """
    Create a new user, add a new Registration instance for letting user verify its identity and create a user profile.

    :param email: user's email address
    :param username: user's username
    :param name: user's name
    :param country: user's country
    :param password: user's password

    :return: User instance of the new user.
    """
    user = User.objects.create_user(username, email, password)
    reg = Registration()
    reg.register(user)

    profile = UserProfile(user=user)
    profile.name = name
    profile.country = country
    profile.save()

    return user


def create_manual_course_enrollment(user, course_id, mode, enrolled_by, reason, state_transition):
    """
    Create course enrollment for the given student and create manual enrollment audit trail.

    :param user: User who is to enroll in course
    :param course_id: course identifier of the course in which to enroll the user.
    :param mode: mode for user enrollment, e.g. 'honor', 'audit' etc.
    :param enrolled_by: User who made the manual enrollment entry (usually instructor or support)
    :param reason: Reason behind manual enrollment
    :param state_transition: state transition denoting whether student enrolled from un-enrolled,
            un-enrolled from enrolled etc.
    :return CourseEnrollment instance.
    """
    enrollment_obj = CourseEnrollment.enroll(user, course_id, mode=mode)
    ManualEnrollmentAudit.create_manual_enrollment_audit(
        enrolled_by, user.email, state_transition, reason, enrollment_obj
    )

    log.info('user %s enrolled in the course %s', user.username, course_id)
    return enrollment_obj


def create_and_enroll_user(
    email,
    username,
    name,
    country,
    password,
    course_id,
    course_mode,
    enrolled_by,
    email_params,
    email_user=True,
):
    """
    Create a new user and enroll him/her to the given course, return list of errors in the following format
        Error format:
            each error is key-value pait dict with following key-value pairs.
            1. username: username of the user to enroll
            1. email: email of the user to enroll
            1. response: readable error message

    :param email: user's email address
    :param username: user's username
    :param name: user's name
    :param country: user's country
    :param password: user's password
    :param course_id: course identifier of the course in which to enroll the user.
    :param course_mode: mode for user enrollment, e.g. 'honor', 'audit' etc.
    :param enrolled_by: User who made the manual enrollment entry (usually instructor or support)
    :param email_params: information to send to the user via email
    :param email_user: If True and it's a new user, an email will be sent to
                       them upon account creation.
    :return: list of errors
    """
    errors = []
    try:
        with transaction.atomic():
            # Create a new user
            user = create_user_and_user_profile(email, username, name, country, password)

            # Enroll user to the course and add manual enrollment audit trail
            create_manual_course_enrollment(
                user=user,
                course_id=course_id,
                mode=course_mode,
                enrolled_by=enrolled_by,
                reason='Enrolling via csv upload',
                state_transition=UNENROLLED_TO_ENROLLED,
            )
    except IntegrityError:
        errors.append({
            'username': username,
            'email': email,
            'response': _('Username {user} already exists.').format(user=username)
        })
    except Exception as ex:  # pylint: disable=broad-except
        log.exception(type(ex).__name__)
        errors.append({
            'username': username, 'email': email, 'response': type(ex).__name__,
        })
    else:
        if email_user:
            try:
                # It's a new user, an email will be sent to each newly created user.
                email_params.update({
                    'message_type': 'account_creation_and_enrollment',
                    'email_address': email,
                    'user_id': user.id,
                    'password': password,
                    'platform_name': configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME),
                })
                send_mail_to_student(email, email_params)
            except Exception as ex:  # pylint: disable=broad-except
                log.exception(
                    f"Exception '{type(ex).__name__}' raised while sending email to new user."
                )
                errors.append({
                    'username': username,
                    'email': email,
                    'response':
                        _("Error '{error}' while sending email to new user (user email={email}). "
                          "Without the email student would not be able to login. "
                          "Please contact support for further information.").format(
                              error=type(ex).__name__, email=email
                        ),
                })
            else:
                log.info('email sent to new created user at %s', email)

    return errors


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class StudentsUpdateEnrollmentView(APIView):
    """
    API view to enroll or unenroll students in a course.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_ENROLL

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Handle POST request to enroll or unenroll students.

        Parameters:
        - action (str): 'enroll' or 'unenroll'
        - identifiers (str): comma/newline separated emails or usernames
        - auto_enroll (bool): auto-enroll in verified track if applicable
        - email_students (bool): whether to send enrollment emails
        - reason (str, optional): reason for enrollment change

        Returns:
        - JSON response with action, auto_enroll flag, and enrollment results.
         """
        response_payload = self._process_student_enrollment(
            user=request.user,
            course_id=course_id,
            data=request.data,
            secure=request.is_secure()
        )
        return JsonResponse(response_payload)

    def _process_student_enrollment(self, user, course_id, data, secure):  # pylint: disable=too-many-statements
        """
        Core logic for enrolling or unenrolling students.

        :param user: User making the request
        :param course_id: Course identifier
        :param data: Request data containing action, identifiers, etc.
        :param secure: Whether the request is secure (HTTPS)
        """

        # Validate request data with serializer
        serializer = StudentsUpdateEnrollmentSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        # Extract validated data
        action = serializer.validated_data['action']
        identifiers_raw = serializer.validated_data['identifiers']
        auto_enroll = serializer.validated_data['auto_enroll']
        email_students = serializer.validated_data['email_students']
        reason = serializer.validated_data.get('reason')

        # Parse identifiers
        identifiers = _split_input_list(identifiers_raw)

        course_key = CourseKey.from_string(course_id)

        enrollment_obj = None
        state_transition = DEFAULT_TRANSITION_STATE

        email_params = {}
        if email_students:
            course = get_course_by_id(course_key)
            email_params = get_email_params(course, auto_enroll, secure=secure)

        results = []

        for identifier in identifiers:  # pylint: disable=too-many-nested-blocks
            identified_user = None
            email = None
            language = None

            try:
                identified_user = get_student_from_identifier(identifier)
            except User.DoesNotExist:
                email = identifier
            else:
                email = identified_user.email
                language = get_user_email_language(identified_user)

            try:
                validate_email(email)  # Raises ValidationError if invalid

                if action == 'enroll':
                    before, after, enrollment_obj = enroll_email(
                        course_key, email, auto_enroll, email_students, {**email_params}, language=language
                    )
                    before_enrollment = before.to_dict()['enrollment']
                    before_user_registered = before.to_dict()['user']
                    before_allowed = before.to_dict()['allowed']
                    after_enrollment = after.to_dict()['enrollment']
                    after_allowed = after.to_dict()['allowed']

                    if before_user_registered:
                        if after_enrollment:
                            if before_enrollment:
                                state_transition = ENROLLED_TO_ENROLLED
                            elif before_allowed:
                                state_transition = ALLOWEDTOENROLL_TO_ENROLLED
                            else:
                                state_transition = UNENROLLED_TO_ENROLLED
                    elif after_allowed:
                        state_transition = UNENROLLED_TO_ALLOWEDTOENROLL

                elif action == 'unenroll':
                    before, after = unenroll_email(
                        course_key, email, email_students, {**email_params}, language=language
                    )
                    before_enrollment = before.to_dict()['enrollment']
                    before_allowed = before.to_dict()['allowed']
                    enrollment_obj = (
                        CourseEnrollment.get_enrollment(identified_user, course_key)
                        if identified_user else None
                    )

                    if before_enrollment:
                        state_transition = ENROLLED_TO_UNENROLLED
                    elif before_allowed:
                        state_transition = ALLOWEDTOENROLL_TO_UNENROLLED
                    else:
                        state_transition = UNENROLLED_TO_UNENROLLED

            except ValidationError:
                results.append({
                    'identifier': identifier,
                    'invalidIdentifier': True,
                })
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Error while processing student")
                log.exception(exc)
                results.append({
                    'identifier': identifier,
                    'error': True,
                })
            else:
                ManualEnrollmentAudit.create_manual_enrollment_audit(
                    identified_user, email, state_transition, reason, enrollment_obj
                )
                results.append({
                    'identifier': identifier,
                    'before': before.to_dict(),
                    'after': after.to_dict(),
                })

        return {
            'action': action,
            'auto_enroll': auto_enroll,
            'results': results,
        }


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class BulkBetaModifyAccess(DeveloperErrorViewMixin, APIView):
    """
    Enroll or unenroll users in beta testing program.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_BETATEST
    serializer_class = ModifyAccessSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Query parameters:
        - identifiers is string containing a list of emails and/or usernames separated by
          anything split_input_list can handle.
        - action is one of ['add', 'remove']
        """
        course_id = CourseKey.from_string(course_id)
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return JsonResponse({'message': serializer.errors}, status=400)

        action = serializer.validated_data['action']
        identifiers = serializer.validated_data['identifiers']
        email_students = serializer.validated_data['email_students']
        auto_enroll = serializer.validated_data['auto_enroll']

        results = []
        rolename = 'beta'
        course = get_course_by_id(course_id)

        email_params = {}
        if email_students:
            secure = request.is_secure()
            email_params = get_email_params(course, auto_enroll=auto_enroll, secure=secure)

        for identifier in identifiers:
            try:
                error = False
                user_does_not_exist = False
                user = get_student_from_identifier(identifier)
                user_active = user.is_active

                if action == 'add':
                    allow_access(course, user, rolename)
                elif action == 'remove':
                    revoke_access(course, user, rolename)
                else:
                    return HttpResponseBadRequest(strip_tags(
                        f"Unrecognized action '{action}'"
                    ))
            except User.DoesNotExist:
                error = True
                user_does_not_exist = True
                user_active = None
            # catch and log any unexpected exceptions
            # so that one error doesn't cause a 500.
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Error while #{}ing student")
                log.exception(exc)
                error = True
            else:
                # If no exception thrown, see if we should send an email
                if email_students:
                    send_beta_role_email(action, user, email_params)
                # See if we should autoenroll the student
                if auto_enroll:
                    # Check if student is already enrolled
                    if not is_user_enrolled_in_course(user, course_id):
                        CourseEnrollment.enroll(user, course_id)

            finally:
                # Tabulate the action result of this email address
                results.append({
                    'identifier': identifier,
                    'error': error,  # pylint: disable=used-before-assignment
                    'userDoesNotExist': user_does_not_exist,  # pylint: disable=used-before-assignment
                    'is_active': user_active  # pylint: disable=used-before-assignment
                })

        response_payload = {
            'action': action,
            'results': results,
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ModifyAccess(APIView):
    """
    Modify staff/instructor access of other user.
    Requires instructor access.

    NOTE: instructors cannot remove their own instructor access.

    Query parameters:
    unique_student_identifier is the target user's username or email
    rolename is one of ['instructor', 'staff', 'beta', 'ccx_coach']
    action is one of ['allow', 'revoke']
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EDIT_COURSE_ACCESS
    serializer_class = AccessSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Modify staff/instructor access of other user.
        Requires instructor access.
        """
        course_id = CourseKey.from_string(course_id)
        course = get_course_with_access(
            request.user, 'instructor', course_id, depth=None
        )
        serializer_data = AccessSerializer(data=request.data)
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        user = serializer_data.validated_data.get('unique_student_identifier')
        if not user:
            response_payload = {
                'unique_student_identifier': request.data.get('unique_student_identifier'),
                'userDoesNotExist': True,
            }
            return JsonResponse(response_payload)

        if not user.is_active:
            response_payload = {
                'unique_student_identifier': user.username,
                'inactiveUser': True,
            }
            return JsonResponse(response_payload)

        rolename = serializer_data.data['rolename']
        action = serializer_data.data['action']

        if rolename not in ROLES:
            error = strip_tags(f"unknown rolename '{rolename}'")
            log.error(error)
            return HttpResponseBadRequest(error)

        # disallow instructors from removing their own instructor access.
        if rolename == 'instructor' and user == request.user and action != 'allow':
            response_payload = {
                'unique_student_identifier': user.username,
                'rolename': rolename,
                'action': action,
                'removingSelfAsInstructor': True,
            }
            return JsonResponse(response_payload)

        if action == 'allow':
            allow_access(course, user, rolename)
            if not is_user_enrolled_in_course(user, course_id):
                CourseEnrollment.enroll(user, course_id)
        elif action == 'revoke':
            revoke_access(course, user, rolename)
        else:
            return HttpResponseBadRequest(strip_tags(
                f"unrecognized action u'{action}'"
            ))

        response_payload = {
            'unique_student_identifier': user.username,
            'rolename': rolename,
            'action': action,
            'success': 'yes',
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListCourseRoleMembersView(APIView):
    """
    View to list instructors and staff for a specific course.
    Requires the user to have instructor access.

    rolename is one of ['instructor', 'staff', 'beta', 'ccx_coach']

    Returns JSON of the form {
        "course_id": "some/course/id",
        "staff": [
            {
                "username": "staff1",
                "email": "staff1@example.org",
                "first_name": "Joe",
                "last_name": "Shmoe",
            }
        ]
    }
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EDIT_COURSE_ACCESS

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Handles POST request to list instructors and staff.

        Args:
            request (HttpRequest): The request object containing user data.
            course_id (str): The ID of the course to list instructors and staff for.

        Returns:
            Response: A Response object containing the list of instructors and staff or an error message.

        Raises:
            Http404: If the course does not exist.
        """
        course_id = CourseKey.from_string(course_id)
        course = get_course_with_access(
            request.user, 'instructor', course_id, depth=None
        )
        role_serializer = RoleNameSerializer(data=request.data)
        role_serializer.is_valid(raise_exception=True)
        rolename = role_serializer.data['rolename']

        users = list_with_level(course.id, rolename)
        serializer = UserSerializer(users, many=True)

        response_payload = {
            'course_id': str(course_id),
            rolename: serializer.data,
        }

        return Response(response_payload, status=status.HTTP_200_OK)


class ProblemResponseReportPostParamsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes that POST parameters for the report generation API.
    """
    problem_locations = serializers.ListSerializer(
        child=serializers.CharField(
            help_text=_(
                "A usage key location for a section or a problem. "
                "If the location is a block that contains other blocks, (such as the course, "
                "section, subsection, or unit blocks) then all blocks under that block will be "
                "included in the report."
            ),
        ),
        required=True,
        allow_empty=False,
        help_text=_(
            "A list of usage keys for the blocks to include in the report. "
        )
    )
    problem_types_filter = serializers.ListSerializer(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        help_text=_(
            "A list of problem/block types to generate the report for. "
            "This field can be omitted if the report should include details of all"
            "block types. "
        ),
    )


class ProblemResponsesReportStatusSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes the response of the problem response report generation API.
    """
    status = serializers.CharField(help_text=_("User-friendly text describing current status of report generation."))
    task_id = serializers.UUIDField(
        help_text=_(
            "A unique id for the report generation task. "
            "It can be used to query the latest report generation status."
        )
    )


@view_auth_classes()
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ProblemResponseReportInitiate(DeveloperErrorViewMixin, APIView):
    """
    Initiate generation of a CSV file containing all student answers
    to a given problem.
    """

    @apidocs.schema(
        parameters=[
            apidocs.path_parameter(
                'course_id',
                str,
                description="ID of the course for which report is to be generate.",
            ),
        ],
        body=ProblemResponseReportPostParamsSerializer,
        responses={
            200: ProblemResponsesReportStatusSerializer,
            400: _(
                "The provided parameters were invalid. Make sure you've provided at least "
                "one valid usage key for `problem_locations`."
            ),
            401: _("The requesting user is not authenticated."),
            403: _("The requesting user lacks access to the course."),
        }
    )
    @transaction.non_atomic_requests
    @method_decorator(require_course_permission(permissions.CAN_RESEARCH))
    def post(self, request, course_id):
        """
        Initiate generation of a CSV file containing all student answers
        to a given problem.

        **Example requests**

            POST /api/instructor/v1/reports/{course_id}/generate/problem_responses {
                "problem_locations": [
                    "{usage_key1}",
                    "{usage_key2}",
                    "{usage_key3}"
                ]
            }
            POST /api/instructor/v1/reports/{course_id}/generate/problem_responses {
                "problem_locations": ["{usage_key}"],
                "problem_types_filter": ["problem"]
            }

        **POST Parameters**

        A POST request can include the following parameters:

        * problem_location: A list of usage keys for the blocks to include in
          the report. If the location is a block that contains other blocks,
          (such as the course, section, subsection, or unit blocks) then all
          blocks under that block will be included in the report.
        * problem_types_filter: Optional. A comma-separated list of block types
          to include in the report. If set, only blocks of the specified types
          will be included in the report.

        To get data on all the poll and survey blocks in a course, you could
        POST the usage key of the course for `problem_location`, and
        "poll, survey" as the value for `problem_types_filter`.


        **Example Response:**
        If initiation is successful (or generation task is already running):
        ```json
        {
            "status": "The problem responses report is being created. ...",
            "task_id": "4e49522f-31d9-431a-9cff-dd2a2bf4c85a"
        }
        ```

        Responds with BadRequest if any of the provided problem locations are faulty.
        """
        params = ProblemResponseReportPostParamsSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        problem_locations = params.validated_data.get('problem_locations')
        problem_types_filter = params.validated_data.get('problem_types_filter')
        if problem_types_filter:
            problem_types_filter = ','.join(problem_types_filter)
        return _get_problem_responses(
            request,
            course_id=course_id,
            problem_locations=problem_locations,
            problem_types_filter=problem_types_filter,
        )


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetProblemResponses(DeveloperErrorViewMixin, APIView):
    """
    Initiate generation of a CSV file containing all student answers
    to a given problem.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Initiate generation of a CSV file containing all student answers
        to a given problem.

        **Example requests**

            POST /courses/{course_id}/instructor/api/get_problem_responses {
                "problem_location": "{usage_key1},{usage_key2},{usage_key3}""
            }
            POST /courses/{course_id}/instructor/api/get_problem_responses {
                "problem_location": "{usage_key}",
                "problem_types_filter": "problem"
            }

            **POST Parameters**

            A POST request can include the following parameters:

            * problem_location: A comma-separated list of usage keys for the blocks
              to include in the report. If the location is a block that contains
              other blocks, (such as the course, section, subsection, or unit blocks)
              then all blocks under that block will be included in the report.
            * problem_types_filter: Optional. A comma-separated list of block types
              to include in the repot. If set, only blocks of the specified types will
              be included in the report.

            To get data on all the poll and survey blocks in a course, you could
            POST the usage key of the course for `problem_location`, and
            "poll, survey" as the value for `problem_types_filter`.


        **Example Response:**
        If initiation is successful (or generation task is already running):
        ```json
        {
            "status": "The problem responses report is being created. ...",
            "task_id": "4e49522f-31d9-431a-9cff-dd2a2bf4c85a"
        }
        ```

        Responds with BadRequest if any of the provided problem locations are faulty.
        """
        # A comma-separated list of problem locations
        # The name of the POST parameter is `problem_location` (not pluralised) in
        # order to preserve backwards compatibility with existing third-party
        # scripts.

        problem_locations = request.POST.get('problem_location', '').split(',')
        # A comma-separated list of block types
        problem_types_filter = request.POST.get('problem_types_filter')

        return _get_problem_responses(
            request,
            course_id=course_id,
            problem_locations=problem_locations,
            problem_types_filter=problem_types_filter,
        )


@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@common_exceptions_400
def _get_problem_responses(request, *, course_id, problem_locations, problem_types_filter):
    """
    Shared code for new DRF and old APIS for problem response report generation.
    """
    course_key = CourseKey.from_string(course_id)
    report_type = _('problem responses')

    try:
        for problem_location in problem_locations:
            UsageKey.from_string(problem_location).map_into_course(course_key)
    except InvalidKeyError:
        return JsonResponseBadRequest(_("Could not find problem with this location."))

    task = task_api.submit_calculate_problem_responses_csv(
        request, course_key, ','.join(problem_locations), problem_types_filter,
    )
    success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)

    return JsonResponse({"status": success_status, "task_id": task.task_id})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class GetGradingConfig(APIView):
    """
    Respond with json which contains a html formatted grade summary.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Post method to return grading config.
        """
        course_id = CourseKey.from_string(course_id)
        course = get_course_by_id(course_id)
        grading_config_summary = instructor_analytics_basic.dump_grading_context(course)
        response_payload = {
            'course_id': str(course_id),
            'grading_config_summary': grading_config_summary,
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetIssuedCertificates(APIView):
    """
    Responds with JSON if CSV is not required. contains a list of issued certificates.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ISSUED_CERTIFICATES

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Arguments: course_id
        Returns:
            {"certificates": [{course_id: xyz, mode: 'honor'}, ...]}
        """
        return self.all_issued_certificates(request, course_id)

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def get(self, request, course_id):
        return self.all_issued_certificates(request, course_id)

    def all_issued_certificates(self, request, course_id):
        """
        common method for both post and get. This method will return all issued certificates.
        """
        course_key = CourseKey.from_string(course_id)
        csv_required = request.GET.get('csv', 'false')

        query_features = ['course_id', 'mode', 'total_issued_certificate', 'report_run_date']
        query_features_names = [
            ('course_id', _('CourseID')),
            ('mode', _('Certificate Type')),
            ('total_issued_certificate', _('Total Certificates Issued')),
            ('report_run_date', _('Date Report Run'))
        ]
        certificates_data = instructor_analytics_basic.issued_certificates(course_key, query_features)
        if csv_required.lower() == 'true':
            __, data_rows = instructor_analytics_csvs.format_dictlist(certificates_data, query_features)
            return instructor_analytics_csvs.create_csv_response(
                'issued_certificates.csv',
                [col_header for __, col_header in query_features_names],
                data_rows
            )
        else:
            response_payload = {
                'certificates': certificates_data,
                'queried_features': query_features,
                'feature_names': dict(query_features_names)
            }
            return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetStudentsFeatures(DeveloperErrorViewMixin, APIView):
    """
    Respond with json which contains a summary of all enrolled students profile information.

    Responds with JSON
        {"students": [{-student-info-}, ...]}

    TO DO accept requests for different attribute sets.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id, csv=False):  # pylint: disable=redefined-outer-name
        """
        Handle POST requests to retrieve student profile information for a specific course.

        Args:
            request: The HTTP request object.
            course_id: The ID of the course for which to retrieve student information.
            csv: Optional; if 'csv' is present in the URL, it indicates that the response should be in CSV format.
            Defaults to None.

        Returns:
            Response: A JSON response containing student profile information, or CSV if the `csv` parameter is provided.
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)
        report_type = _('enrolled learner profile')
        available_features = instructor_analytics_basic.get_available_features(course_key)

        # Allow for sites to be able to define additional columns.
        # Note that adding additional columns has the potential to break
        # the student profile report due to a character limit on the
        # asynchronous job input which in this case is a JSON string
        # containing the list of columns to include in the report.
        # TODO: Refactor the student profile report code to remove the list of columns
        # that should be included in the report from the asynchronous job input.
        # We need to clone the list because we modify it below
        query_features = list(configuration_helpers.get_value('student_profile_download_fields', []))

        if not query_features:
            query_features = [
                'id', 'username', 'name', 'email', 'language', 'location',
                'year_of_birth', 'gender', 'level_of_education', 'mailing_address',
                'goals', 'enrollment_mode', 'last_login', 'date_joined', 'external_user_key',
                'enrollment_date',
            ]

        additional_attributes = configuration_helpers.get_value_for_org(
            course_key.org,
            "additional_student_profile_attributes"
        )
        if additional_attributes:
            # Fail fast: must be list/tuple of strings.
            if not isinstance(additional_attributes, (list, tuple)):
                return JsonResponseBadRequest(
                    _('Invalid additional student attribute configuration: expected list of strings, got {type}.')
                    .format(type=type(additional_attributes).__name__)
                )
            if not all(isinstance(v, str) for v in additional_attributes):
                return JsonResponseBadRequest(
                    _('Invalid additional student attribute configuration: all entries must be strings.')
                )
            # Reject empty string entries explicitly.
            if any(v == '' for v in additional_attributes):
                return JsonResponseBadRequest(
                    _('Invalid additional student attribute configuration: empty attribute names are not allowed.')
                )
            # Validate each attribute is in available_features; allow duplicates as provided.
            invalid = [v for v in additional_attributes if v not in available_features]
            if invalid:
                return JsonResponseBadRequest(
                    _('Invalid additional student attributes: {attrs}').format(
                        attrs=', '.join(invalid)
                    )
                )
            query_features.extend(additional_attributes)

        # Provide human-friendly and translatable names for these features. These names
        # will be displayed in the table generated in data_download.js. It is not (yet)
        # used as the header row in the CSV, but could be in the future.
        query_features_names = {
            'id': _('User ID'),
            'username': _('Username'),
            'name': _('Name'),
            'email': _('Email'),
            'language': _('Language'),
            'location': _('Location'),
            'year_of_birth': _('Birth Year'),
            'gender': _('Gender'),
            'level_of_education': _('Level of Education'),
            'mailing_address': _('Mailing Address'),
            'goals': _('Goals'),
            'enrollment_mode': _('Enrollment Mode'),
            'last_login': _('Last Login'),
            'date_joined': _('Date Joined'),
            'external_user_key': _('External User Key'),
            'enrollment_date': _('Enrollment Date'),
        }

        if additional_attributes:
            for attr in additional_attributes:
                if attr not in query_features_names:
                    formatted_name = attr.replace('_', ' ').title()
                    # pylint: disable-next=translation-of-non-string
                    query_features_names[attr] = _(formatted_name)

        for field in settings.PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS:
            keep_field_private(query_features, field)
            query_features_names.pop(field, None)

        if is_course_cohorted(course.id):
            # Translators: 'Cohort' refers to a group of students within a course.
            query_features.append('cohort')
            query_features_names['cohort'] = _('Cohort')

        if course.teams_enabled:
            query_features.append('team')
            query_features_names['team'] = _('Team')

        # For compatibility reasons, city and country should always appear last.
        query_features.append('city')
        query_features_names['city'] = _('City')
        query_features.append('country')
        query_features_names['country'] = _('Country')

        if not csv:
            student_data = instructor_analytics_basic.enrolled_students_features(course_key, query_features)
            response_payload = {
                'course_id': str(course_key),
                'students': student_data,
                'students_count': len(student_data),
                'queried_features': query_features,
                'feature_names': query_features_names,
                'available_features': available_features,
            }
            return JsonResponse(response_payload)

        else:
            try:
                task_api.submit_calculate_students_features_csv(
                    request,
                    course_key,
                    query_features
                )
                success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
            except Exception as e:
                raise self.api_error(status.HTTP_400_BAD_REQUEST, str(e), 'Requested task is already running')

            return JsonResponse({"status": success_status})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetStudentsWhoMayEnroll(DeveloperErrorViewMixin, APIView):
    """
    Initiate generation of a CSV file containing information about
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Initiate generation of a CSV file containing information about
         students who may enroll in a course.

        Responds with JSON
            {"status": "... status message ..."}
        """
        course_key = CourseKey.from_string(course_id)
        query_features = ['email']
        report_type = _('enrollment')
        try:
            task_api.submit_calculate_may_enroll_csv(request, course_key, query_features)
            success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
        except Exception as e:
            raise self.api_error(status.HTTP_400_BAD_REQUEST, str(e), 'Requested task is already running')

        return JsonResponse({"status": success_status})

    def get(self, request, *args, **kwargs):
        raise MethodNotAllowed('GET')


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetInactiveEnrolledStudents(DeveloperErrorViewMixin, APIView):
    """
    Initiate generation of a CSV file containing information about
    students who are enrolled in a course but have inactive account.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Initiate generation of a CSV file containing information about
        students who are enrolled in a course but have inactive account.

        Responds with JSON
            {"status": "... status message ..."}
        """
        course_key = CourseKey.from_string(course_id)
        query_features = ["email"]
        report_type = _("inactive enrollment")
        try:
            task_api.submit_calculate_inactive_enrolled_students_csv(
                request, course_key, query_features
            )
            success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
        except Exception as e:
            raise self.api_error(
                status.HTTP_400_BAD_REQUEST, str(e), "Requested task is already running"
            )

        return JsonResponse({"status": success_status})

    def get(self, request, *args, **kwargs):
        raise MethodNotAllowed("GET")


def _cohorts_csv_validator(file_storage, file_to_validate):
    """
    Verifies that the expected columns are present in the CSV used to add users to cohorts.
    """
    with file_storage.open(file_to_validate) as f:
        reader = csv.reader(f.read().decode('utf-8-sig').splitlines())

        try:
            fieldnames = next(reader)
        except StopIteration:
            fieldnames = []
        msg = None
        if "cohort" not in fieldnames:
            msg = _("The file must contain a 'cohort' column containing cohort names.")
        elif "email" not in fieldnames and "username" not in fieldnames:
            msg = _("The file must contain a 'username' column, an 'email' column, or both.")
        if msg:
            raise FileValidationException(msg)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class AddUsersToCohorts(DeveloperErrorViewMixin, APIView):
    """
    View method that accepts an uploaded file (using key "uploaded-file")
    containing cohort assignments for users. This method spawns a celery task
    to do the assignments, and a CSV file with results is provided via data downloads.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.ASSIGN_TO_COHORTS

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        This method spawns a celery task to do the assignments, and a CSV file with results
         is provided via data downloads.
        """

        course_key = CourseKey.from_string(course_id)

        try:
            __, filename = store_uploaded_file(
                request, 'uploaded-file', ['.csv'],
                course_and_time_based_filename_generator(course_key, "cohorts"),
                max_file_size=2000000,  # limit to 2 MB
                validator=_cohorts_csv_validator
            )
            # The task will assume the default file storage.
            task_api.submit_cohort_students(request, course_key, filename)
        except (FileValidationException, PermissionDenied, ValueError) as err:
            return JsonResponse({"error": str(err)}, status=400)

        return JsonResponse()


# The non-atomic decorator is required because this view calls a celery
# task which uses the 'outer_atomic' context manager.
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CohortCSV(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Submit a CSV file to assign users to cohorts

    **Example Requests**:

        POST /api/cohorts/v1/courses/{course_id}/users/

    **Response Values**
        * Empty as this is executed asynchronously.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, IsAdminUser)

    def post(self, request, course_key_string):
        """
        View method that accepts an uploaded file (using key "uploaded-file")
        containing cohort assignments for users. This method spawns a celery task
        to do the assignments, and a CSV file with results is provided via data downloads.
        """
        course_key = CourseKey.from_string(course_key_string)
        try:
            __, file_name = store_uploaded_file(
                request, 'uploaded-file', ['.csv'],
                course_and_time_based_filename_generator(course_key, 'cohorts'),
                max_file_size=2000000,  # limit to 2 MB
                validator=_cohorts_csv_validator,
                is_private=True
            )
            task_api.submit_cohort_students(request, course_key, file_name)
        except (FileValidationException, ValueError) as e:
            raise self.api_error(status.HTTP_400_BAD_REQUEST, str(e), 'failed-validation')

        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetCourseSurveyResults(DeveloperErrorViewMixin, APIView):
    """
    get the survey results report for the particular course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.ENROLLMENT_REPORT

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        method to return survey results report for the particular course.
        """
        course_key = CourseKey.from_string(course_id)
        report_type = _('survey')
        task_api.submit_course_survey_report(request, course_key)
        success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)

        return JsonResponse({"status": success_status})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetProctoredExamResults(DeveloperErrorViewMixin, APIView):
    """
    get the proctored exam results report for the particular course.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EXAM_RESULTS

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        get the proctored exam results report for the particular course.
        """
        try:
            course_key = CourseKey.from_string(course_id)
            report_type = _('proctored exam results')
            task_api.submit_proctored_exam_results_report(request, course_key)
            success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
            return JsonResponse({"status": success_status})
        except (AlreadyRunningError, QueueConnectionError, AttributeError) as error:
            # Return a 400 status code with the error message
            return JsonResponse({"error": str(error)}, status=400)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GetAnonIds(APIView):
    """
    Respond with 2-column CSV output of user-id, anonymized-user-id.
    This API processes the incoming request to generate a CSV file containing
    two columns: `user-id` and `anonymized-user-id`. The CSV is returned as a
    response to the client.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Handle POST request to generate a CSV output.

        Returns:
            Response: A CSV file with two columns: `user-id` and `anonymized-user-id`.
        """
        report_type = _('Anonymized User IDs')
        success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
        task_api.generate_anonymous_ids(request, course_id)
        return JsonResponse({"status": success_status})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class GetStudentEnrollmentStatus(APIView):
    """
    Get the enrollment status of a student.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.VIEW_ENROLLMENTS

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Permission: Limited to staff access.
        Takes query parameter unique_student_identifier
        """
        error = ''
        mode = None
        is_active = None

        course_id = CourseKey.from_string(course_id)
        unique_student_identifier = request.data.get("unique_student_identifier")

        serializer_data = UniqueStudentIdentifierSerializer(data=request.data)
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        user = serializer_data.validated_data.get('unique_student_identifier')
        if user:
            mode, is_active = CourseEnrollment.enrollment_mode_for_user(user, course_id)

        if user and mode:
            if is_active:
                enrollment_status = _('Enrollment status for {student}: active').format(student=user)
            else:
                enrollment_status = _('Enrollment status for {student}: inactive').format(student=user)
        else:
            email = user.email if user else unique_student_identifier
            allowed = CourseEnrollmentAllowed.may_enroll_and_unenrolled(course_id)
            if allowed and email in [cea.email for cea in allowed]:
                enrollment_status = _('Enrollment status for {student}: pending').format(student=email)
            else:
                enrollment_status = _('Enrollment status for {student}: never enrolled').format(student=email)

        response_payload = {
            'course_id': str(course_id),
            'error': error,
            'enrollment_status': enrollment_status
        }

        return JsonResponse(response_payload)


class StudentProgressUrlSerializer(serializers.Serializer):
    """Serializer for course renders"""
    unique_student_identifier = serializers.CharField(write_only=True)
    course_id = CourseKeyField(required=False)
    progress_url = serializers.SerializerMethodField()

    def get_progress_url(self, obj):    # pylint: disable=unused-argument
        """
        Return the progress URL for the student.
        Args:
            obj (dict): The dictionary containing data for the serializer.
        Returns:
            str: The URL for the progress of the student in the course.
        """
        user = get_student_from_identifier(obj.get('unique_student_identifier'))
        course_id = obj.get('course_id')  # Adjust based on your data structure

        if course_home_mfe_progress_tab_is_active(course_id):
            progress_url = get_learning_mfe_home_url(course_id, url_fragment='progress')
            if user is not None:
                progress_url += '/{}/'.format(user.id)
        else:
            progress_url = reverse('student_progress', kwargs={'course_id': str(course_id), 'student_id': user.id})

        return progress_url


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class StudentProgressUrl(APIView):
    """
    Get the progress url of a student.
    Limited to staff access.

    Takes query parameter unique_student_identifier and if the student exists
    returns e.g. {
        'progress_url': '/../...'
    }
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    serializer_class = StudentProgressUrlSerializer
    permission_name = permissions.ENROLLMENT_REPORT

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """Post method for validating incoming data and generating progress URL"""
        data = {
            'course_id': course_id,
            'unique_student_identifier': request.data.get('unique_student_identifier')
        }
        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ResetStudentAttempts(DeveloperErrorViewMixin, APIView):
    """
    Resets a students attempts counter or starts a task to reset all students
    attempts counters. Optionally deletes student state for a problem. Limited
    to staff access. Some sub-methods limited to instructor access.
    """
    http_method_names = ['post']
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GIVE_STUDENT_EXTENSION
    serializer_class = StudentAttemptsSerializer

    @method_decorator(ensure_csrf_cookie)
    @transaction.non_atomic_requests
    def post(self, request, course_id):
        """
        Takes some of the following query parameters
        - problem_to_reset is a urlname of a problem
        - unique_student_identifier is an email or username
        - all_students is a boolean
            requires instructor access
            mutually exclusive with delete_module
            mutually exclusive with delete_module
        - delete_module is a boolean
            requires instructor access
            mutually exclusive with all_students
        """
        course_id = CourseKey.from_string(course_id)
        serializer_data = self.serializer_class(data=request.data)

        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        course = get_course_with_access(
            request.user, 'staff', course_id, depth=None
        )

        all_students = serializer_data.validated_data.get('all_students')

        if all_students and not has_access(request.user, 'instructor', course):
            return HttpResponseForbidden("Requires instructor access.")

        problem_to_reset = strip_if_string(serializer_data.validated_data.get('problem_to_reset'))
        student_identifier = request.POST.get('unique_student_identifier', None)
        student = serializer_data.validated_data.get('unique_student_identifier')
        delete_module = serializer_data.validated_data.get('delete_module')

        # parameter combinations
        if all_students and student:
            return HttpResponseBadRequest(
                "all_students and unique_student_identifier are mutually exclusive."
            )
        if all_students and delete_module:
            return HttpResponseBadRequest(
                "all_students and delete_module are mutually exclusive."
            )

        try:
            module_state_key = UsageKey.from_string(problem_to_reset).map_into_course(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest()

        response_payload = {}
        response_payload['problem_to_reset'] = problem_to_reset

        if student:
            try:
                enrollment.reset_student_attempts(
                    course_id,
                    student,
                    module_state_key,
                    requesting_user=request.user,
                    delete_module=delete_module
                )
            except StudentModule.DoesNotExist:
                return HttpResponseBadRequest(_("Module does not exist."))
            except sub_api.SubmissionError:
                # Trust the submissions API to log the error
                error_msg = _("An error occurred while deleting the score.")
                return HttpResponse(error_msg, status=500)
            response_payload['student'] = student_identifier

        elif all_students:
            try:
                task_api.submit_reset_problem_attempts_for_all_students(request, module_state_key)
                response_payload['task'] = TASK_SUBMISSION_OK
                response_payload['student'] = 'All Students'
            except Exception:  # pylint: disable=broad-except
                error_msg = _("An error occurred while attempting to reset for all students.")
                return HttpResponse(error_msg, status=500)
        else:
            return HttpResponseBadRequest()

        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ResetStudentAttemptsForEntranceExam(DeveloperErrorViewMixin, APIView):
    """
    Resets a students attempts counter or starts a task to reset all students
    attempts counters for entrance exam. Optionally deletes student state for
    entrance exam. Limited to staff access. Some sub-methods limited to instructor access.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GIVE_STUDENT_EXTENSION

    serializer_class = ResetEntranceExamAttemptsSerializer

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Resets a student's entrance exam attempts or
        deletes entrance exam state.

        Parameters (in request.data):
            - unique_student_identifier (str, optional):
                Email or username of the student. If provided, must exist.
            - all_students (bool, optional):
                If True, applies to all students. Mutually exclusive with
                unique_student_identifier and delete_module.
            - delete_module (bool, optional):
                If True, deletes entrance exam state for the student. Mutually
                exclusive with all_students.

        Behavior:
            - At least one of unique_student_identifier, all_students, or
              delete_module must be provided.
            - If unique_student_identifier is provided but does not exist,
              returns a validation error.
            - If mutually exclusive parameters are provided, returns a
              validation error.
            - Requires staff access; instructor access required for
              all_students or delete_module actions.
            - Returns a JSON response with the task status and student
              identifier.
        """
        course_id = CourseKey.from_string(course_id)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        course = get_course_with_access(
            request.user, 'staff', course_id, depth=None
        )

        if not course.entrance_exam_id:
            return HttpResponseBadRequest(
                _("Course has no entrance exam section.")
            )

        student_identifier = serializer.initial_data.get('unique_student_identifier')
        student = serializer.validated_data.get('unique_student_identifier')
        all_students = serializer.validated_data.get('all_students')
        delete_module = serializer.validated_data.get('delete_module')

        # instructor authorization
        if all_students or delete_module:
            if not has_access(request.user, 'instructor', course):
                return HttpResponseForbidden(_("Requires instructor access."))

        try:
            entrance_exam_key = UsageKey.from_string(course.entrance_exam_id).map_into_course(course_id)
            if delete_module:
                task_api.submit_delete_entrance_exam_state_for_student(
                    request,
                    entrance_exam_key,
                    student
                )
            else:
                task_api.submit_reset_problem_attempts_in_entrance_exam(
                    request,
                    entrance_exam_key,
                    student
                )
        except InvalidKeyError:
            return HttpResponseBadRequest(_("Course has no valid entrance exam section."))

        response_payload = {'student': student_identifier or _('All Students'), 'task': TASK_SUBMISSION_OK}
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class RescoreProblem(DeveloperErrorViewMixin, APIView):
    """
    Starts a background process a students attempts counter. Optionally deletes student state for a problem.
    Rescore for all students is limited to instructor access.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.OVERRIDE_GRADES
    serializer_class = ProblemResetSerializer

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Takes either of the following query parameters
            - problem_to_reset is a urlname of a problem
            - unique_student_identifier is an email or username
            - all_students is a boolean

        all_students and unique_student_identifier cannot both be present.
        """

        course_id = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, 'staff', course_id)

        serializer_data = self.serializer_class(data=request.data)

        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        problem_to_reset = serializer_data.validated_data.get("problem_to_reset")
        all_students = serializer_data.validated_data.get("all_students")
        only_if_higher = serializer_data.validated_data.get("only_if_higher")

        student = serializer_data.validated_data.get("unique_student_identifier")
        student_identifier = request.data.get("unique_student_identifier")

        if all_students and not has_access(request.user, 'instructor', course):
            return HttpResponseForbidden("Requires instructor access.")

        if not (problem_to_reset and (all_students or student)):
            return HttpResponseBadRequest("Missing query parameters.")

        if all_students and student:
            return HttpResponseBadRequest(
                "Cannot rescore with all_students and unique_student_identifier."
            )

        try:
            module_state_key = UsageKey.from_string(problem_to_reset).map_into_course(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest("Unable to parse problem id")

        response_payload = {'problem_to_reset': problem_to_reset}

        if student:
            response_payload['student'] = student_identifier
            try:
                task_api.submit_rescore_problem_for_student(
                    request,
                    module_state_key,
                    student,
                    only_if_higher,
                )
            except NotImplementedError as exc:
                return HttpResponseBadRequest(str(exc))
            except ItemNotFoundError as exc:
                return HttpResponseBadRequest(f"{module_state_key} not found")

        elif all_students:
            try:
                task_api.submit_rescore_problem_for_all_students(
                    request,
                    module_state_key,
                    only_if_higher,
                )
            except NotImplementedError as exc:
                return HttpResponseBadRequest(str(exc))
            except ItemNotFoundError as exc:
                return HttpResponseBadRequest(f"{module_state_key} not found")
        else:
            return HttpResponseBadRequest()

        response_payload['task'] = TASK_SUBMISSION_OK
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class OverrideProblemScoreView(DeveloperErrorViewMixin, APIView):
    """
    DRF view to override a student's score for a specific problem.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.OVERRIDE_GRADES
    serializer_class = OverrideProblemScoreSerializer

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Takes the following query parameters:
            - problem_to_reset: a urlname of a problem
            - unique_student_identifier: an email or username
            - score: the score to override with
        Returns a response indicating the success or failure of the operation.
        If the user does not have permission to override scores for the problem,
        a 403 Forbidden response is returned.
        If the problem cannot be found or parsed, a 400 Bad Request response is returned.
        If the score override is successful, a 200 OK response is returned with the task status
        and the problem and student identifiers in the response payload.
        """

        serializer_data = self.serializer_class(data=request.data)
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        course_key = CourseKey.from_string(course_id)
        problem_to_reset = serializer_data.validated_data['problem_to_reset']
        score = serializer_data.validated_data['score']
        student = serializer_data.validated_data['unique_student_identifier']
        student_identifier = request.data.get('unique_student_identifier')

        try:
            usage_key = UsageKey.from_string(problem_to_reset).map_into_course(course_key)
            block = modulestore().get_item(usage_key)
        except InvalidKeyError:
            return Response(
                {"error": f"Unable to parse problem id {problem_to_reset}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ItemNotFoundError:
            return Response(
                {"error": f"Unable to find problem id {problem_to_reset}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not has_access(request.user, "staff", block):
            return Response(
                {
                    "error": _(
                        "User {user_id} does not have permission to "
                        "override scores for problem {problem_to_reset}."
                    ).format(
                        user_id=request.user.id,
                        problem_to_reset=problem_to_reset
                    )
                },
                status=status.HTTP_403_FORBIDDEN
            )

        response_payload = {
            'problem_to_reset': problem_to_reset,
            'student': student_identifier
        }
        try:
            task_api.submit_override_score(
                request,
                usage_key,
                student,
                score,
            )
        except NotImplementedError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response_payload['task'] = TASK_SUBMISSION_OK
        return Response(response_payload)


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class RescoreEntranceExamView(DeveloperErrorViewMixin, APIView):
    """
    Starts a background process for a student's attempts counter for entrance exam.
    Optionally deletes student state for a problem. Limited to instructor access.

    Takes either of the following parameters:
        - unique_student_identifier: an email or username
        - all_students: a boolean

    all_students and unique_student_identifier cannot both be present.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.RESCORE_EXAMS
    serializer_class = RescoreEntranceExamSerializer

    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Initiates a Celery task to rescore the entrance exam for a student or all students.
        """
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        course_id = CourseKey.from_string(course_id)
        course = get_course_with_access(
            request.user, 'staff', course_id, depth=None
        )

        if not course.entrance_exam_id:
            return Response(
                {"error": _("Course has no entrance exam section.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        student_identifier = data.get('unique_student_identifier')
        only_if_higher = data.get('only_if_higher')
        all_students = data.get('all_students', False)
        student = None

        if student_identifier:
            student = get_student_from_identifier(student_identifier)

        if all_students and student:
            return Response(
                {"error": _("Cannot rescore with all_students and unique_student_identifier.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            entrance_exam_key = UsageKey.from_string(course.entrance_exam_id).map_into_course(course_id)
        except InvalidKeyError:
            return Response(
                {"error": _("Course has no valid entrance exam section.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        response_payload = {
            'student': student_identifier if student else _("All Students"),
            'task': TASK_SUBMISSION_OK
        }

        task_api.submit_rescore_entrance_exam_for_student(
            request, entrance_exam_key, student, only_if_higher,
        )

        return Response(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListBackgroundEmailTasks(DeveloperErrorViewMixin, APIView):
    """
    List background email tasks.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EMAIL

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        List background email tasks.
        """
        course_id = CourseKey.from_string(course_id)
        task_type = InstructorTaskTypes.BULK_COURSE_EMAIL
        # Specifying for the history of a single task type
        tasks = task_api.get_instructor_task_history(
            course_id,
            task_type=task_type
        )

        response_payload = {
            'tasks': list(map(extract_task_features, tasks)),
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListEmailContent(APIView):
    """
    List the content of bulk emails sent
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EMAIL

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        List the content of bulk emails sent for a specific course.

        Args:
            request (HttpRequest): The HTTP request object.
            course_id (str): The ID of the course for which to list the bulk emails.

        Returns:
            HttpResponse: A response object containing the list of bulk email contents.
        """
        course_id = CourseKey.from_string(course_id)
        task_type = InstructorTaskTypes.BULK_COURSE_EMAIL
        # First get tasks list of bulk emails sent
        emails = task_api.get_instructor_task_history(course_id, task_type=task_type)

        response_payload = {
            'emails': list(map(extract_email_features, emails)),
        }
        return JsonResponse(response_payload)


class InstructorTaskSerializerV2(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes the format of a single instructor task.
    """
    status = serializers.CharField(help_text=_("Current status of task."))
    task_type = serializers.CharField(help_text=_("Identifies the kind of task being performed, e.g. rescoring."))
    task_id = serializers.CharField(help_text=_("The celery ID for the task."))
    created = serializers.DateTimeField(help_text=_("The date and time when the task was created."))
    task_input = serializers.DictField(
        help_text=_(
            "The input parameters for the task. The format and content of this "
            "data will depend on the kind of task being performed. For instance"
            "it may contain the problem locations for a problem resources task.")
    )
    requester = serializers.CharField(help_text=_("The username of the user who initiated this task."))
    task_state = serializers.CharField(help_text=_("The last knows state of the celery task."))
    duration_sec = serializers.CharField(help_text=_("Task duration information, if known"))
    task_message = serializers.CharField(help_text=_("User-friendly task status information, if available."))


class InstructorTasksListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer to describe the response of the instructor tasks list API.
    """
    tasks = serializers.ListSerializer(
        child=InstructorTaskSerializerV2(),
        help_text=_("List of instructor tasks.")
    )


@view_auth_classes()
class InstructorTasks(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Lists currently running instructor tasks

    **Parameters**
       - With no arguments, lists running tasks.
       - `problem_location_str` lists task history for problem
       - `problem_location_str` and `unique_student_identifier` lists task
           history for problem AND student (intersection)

    **Example Requests**:

        GET /courses/{course_id}/instructor/api/v0/tasks

    **Response Values**
        {
          "tasks": [
            {
              "status": "Incomplete",
              "task_type": "grade_problems",
              "task_id": "2519ff31-22d9-4a62-91e2-55495895b355",
              "created": "2019-01-15T18:00:15.902470+00:00",
              "task_input": "{}",
              "duration_sec": "unknown",
              "task_message": "No status information available",
              "requester": "staff",
              "task_state": "PROGRESS"
            }
          ]
        }
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="ID for the course whose tasks need to be listed.",
            ),
            apidocs.string_parameter(
                'problem_location_str',
                apidocs.ParameterLocation.QUERY,
                description="Filter instructor tasks to this problem location.",
            ),
            apidocs.string_parameter(
                'unique_student_identifier',
                apidocs.ParameterLocation.QUERY,
                description="Filter tasks to a singe problem and a single student. "
                            "Must be used in combination with `problem_location_str`.",
            ),
        ],
        responses={
            200: InstructorTasksListSerializer,
            401: _("The requesting user is not authenticated."),
            403: _("The requesting user lacks access to the course."),
            404: _("The requested course does not exist."),
        }
    )
    def get(self, request, course_id):
        """
        List instructor tasks filtered by `course_id`.

        **Use Cases**

        Lists currently running instructor tasks

        **Parameters**
           - With no arguments, lists running tasks.
           - `problem_location_str` lists task history for problem
           - `problem_location_str` and `unique_student_identifier` lists task
               history for problem AND student (intersection)

        **Example Requests**:

            GET /courses/{course_id}/instructor/api/v0/tasks

        **Response Values**
        ```json
            {
              "tasks": [
                {
                  "status": "Incomplete",
                  "task_type": "grade_problems",
                  "task_id": "2519ff31-22d9-4a62-91e2-55495895b355",
                  "created": "2019-01-15T18:00:15.902470+00:00",
                  "task_input": "{}",
                  "duration_sec": "unknown",
                  "task_message": "No status information available",
                  "requester": "staff",
                  "task_state": "PROGRESS"
                }
              ]
            }
        ```
        """
        return _list_instructor_tasks(request=request, course_id=course_id)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListInstructorTasks(APIView):
    """
    List instructor tasks.

    Takes optional query parameters.
        - With no arguments, lists running tasks.
        - `problem_location_str` lists task history for problem
        - `problem_location_str` and `unique_student_identifier` lists task
            history for problem AND student (intersection)
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.SHOW_TASKS
    serializer_class = ListInstructorTaskInputSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        List instructor tasks.
        """
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        return _list_instructor_tasks(
            request=request, course_id=course_id, serialize_data=serializer.validated_data
        )


@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.SHOW_TASKS)
def _list_instructor_tasks(request, course_id, serialize_data=None):
    """
    List instructor tasks.

    Internal function with common code for both DRF and and tradition views.
    """
    # This method is also used by other APIs with the GET method.
    # The query_params attribute is utilized for GET requests,
    # where parameters are passed as query strings.

    course_id = CourseKey.from_string(course_id)
    if serialize_data is not None:
        problem_location_str = strip_if_string(serialize_data.get('problem_location_str', False))
        student = serialize_data.get('unique_student_identifier', None)
    else:
        params = getattr(request, 'query_params', request.POST)
        problem_location_str = strip_if_string(params.get('problem_location_str', False))
        student = params.get('unique_student_identifier', None)

    if student is not None:
        student = get_student_from_identifier(student)

    if student and not problem_location_str:
        return HttpResponseBadRequest(
            "unique_student_identifier must accompany problem_location_str"
        )

    if problem_location_str:
        try:
            module_state_key = UsageKey.from_string(problem_location_str).map_into_course(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest()
        if student:
            # Specifying for a single student's history on this problem
            tasks = task_api.get_instructor_task_history(course_id, module_state_key, student)
        else:
            # Specifying for single problem's history
            tasks = task_api.get_instructor_task_history(course_id, module_state_key)
    else:
        # If no problem or student, just get currently running tasks
        tasks = task_api.get_running_instructor_tasks(course_id)

    response_payload = {
        'tasks': list(map(extract_task_features, tasks)),
    }
    return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListEntranceExamInstructorTasks(APIView):
    """
    List entrance exam related instructor tasks.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.SHOW_TASKS

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        List entrance exam related instructor tasks.

        Takes either of the following query parameters
            - unique_student_identifier is an email or username
            - all_students is a boolean
        """
        course_id = CourseKey.from_string(course_id)
        course = get_course_by_id(course_id)
        student = request.POST.get('unique_student_identifier', None)
        if student is not None:
            student = get_student_from_identifier(student)

        try:
            entrance_exam_key = UsageKey.from_string(course.entrance_exam_id).map_into_course(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest(_("Course has no valid entrance exam section."))
        if student:
            # Specifying for a single student's entrance exam history
            tasks = task_api.get_entrance_exam_instructor_task_history(
                course_id,
                entrance_exam_key,
                student
            )
        else:
            # Specifying for all student's entrance exam history
            tasks = task_api.get_entrance_exam_instructor_task_history(
                course_id,
                entrance_exam_key
            )

        response_payload = {
            'tasks': list(map(extract_task_features, tasks)),
        }
        return JsonResponse(response_payload)


class ReportDownloadSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes a single report download.
    """
    url = serializers.URLField(help_text=_("URL from which report can be downloaded."))
    name = serializers.CharField(help_text=_("Name of report."))
    link = serializers.CharField(help_text=_("HTML anchor tag that contains the name and link."))


class ReportDownloadsListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes the response of the report downloads list API.
    """
    downloads = serializers.ListSerializer(
        child=ReportDownloadSerializer(help_text="Report Download"),
        help_text=_("List of report downloads"),
    )


@view_auth_classes()
class ReportDownloads(DeveloperErrorViewMixin, APIView):
    """
    API view to list report downloads for a course.
    """

    @apidocs.schema(parameters=[
        apidocs.string_parameter(
            'course_id',
            apidocs.ParameterLocation.PATH,
            description=_("ID for the course whose reports need to be listed."),
        ),
        apidocs.string_parameter(
            'report_name',
            apidocs.ParameterLocation.QUERY,
            description=_(
                "Filter results to only return details of for the report with the specified name."
            ),
        ),
    ], responses={
        200: ReportDownloadsListSerializer,
        401: _("The requesting user is not authenticated."),
        403: _("The requesting user lacks access to the course."),
        404: _("The requested course does not exist."),
    })
    def get(self, request, course_id):
        """
        List report CSV files that are available for download for this course.

        **Use Cases**

        Lists reports available for download

        **Example Requests**:

            GET /api/instructor/v1/reports/{course_id}

        **Response Values**
        ```json
        {
            "downloads": [
                {
                    "url": "https://1.mock.url",
                    "link": "<a href=\"https://1.mock.url\">mock_file_name_1</a>",
                    "name": "mock_file_name_1"
                }
            ]
        }
        ```

        The report name will depend on the type of report generated. For example a
        problem responses report for an entire course might be called:

            edX_DemoX_Demo_Course_student_state_from_block-v1_edX+DemoX+Demo_Course+type@course+block@course_2021-04-30-0918.csv
        """  # pylint: disable=line-too-long
        return _list_report_downloads(request=request, course_id=course_id)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListReportDownloads(APIView):

    """
    List grade CSV files that are available for download for this course.

    Takes the following query parameters:
    - (optional) report_name - name of the report
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):

        return _list_report_downloads(request=request, course_id=course_id)


@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.CAN_RESEARCH)
def _list_report_downloads(request, course_id):
    """
    List grade CSV files that are available for download for this course.

    Internal function with common code shared between DRF and functional views.
    """
    course_id = CourseKey.from_string(course_id)
    report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
    report_name = getattr(request, 'query_params', request.POST).get("report_name", None)

    response_payload = {
        'downloads': [
            dict(name=name, url=url, link=HTML('<a href="{}">{}</a>').format(HTML(url), Text(name)))
            for name, url in report_store.links_for(course_id) if report_name is None or name == report_name
        ]
    }
    return JsonResponse(response_payload)


@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.CAN_RESEARCH)
@require_finance_admin
def list_financial_report_downloads(_request, course_id):
    """
    List grade CSV files that are available for download for this course.
    """
    course_id = CourseKey.from_string(course_id)
    report_store = ReportStore.from_config(config_name='FINANCIAL_REPORTS')

    response_payload = {
        'downloads': [
            dict(name=name, url=url, link=HTML('<a href="{}">{}</a>').format(HTML(url), Text(name)))
            for name, url in report_store.links_for(course_id)
        ]
    }
    return JsonResponse(response_payload)


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ExportOra2DataView(DeveloperErrorViewMixin, APIView):
    """
    Pushes a Celery task which will aggregate ora2 responses for a course into a .csv
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Initiates a task to export Open Response Assessment (ORA) data for a course.

        Args:
            request: The HTTP request object
            course_id: The ID of the course for which to export ORA data

        Returns:
            Response: A JSON response containing the status message indicating the task has been initiated
        """
        course_key = CourseKey.from_string(course_id)
        report_type = _('ORA data')

        try:
            task_api.submit_export_ora2_data(request, course_key)
            success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
            return Response({"status": success_status})
        except (AlreadyRunningError, QueueConnectionError, AttributeError) as err:
            return JsonResponse({"error": str(err)}, status=400)


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ExportOra2SummaryView(DeveloperErrorViewMixin, APIView):
    """
    Pushes a Celery task which will aggregate a summary of students' progress in ora2 tasks for a course into a .csv
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    def post(self, request, course_id):
        """
        Initiates a Celery task to generate an ORA summary report for the specified course.

        Args:
            request: The HTTP request object
            course_id: The string representation of the course key

        Returns:
            Response: A JSON response with a status message indicating the report generation has started
        """
        course_key = CourseKey.from_string(course_id)
        report_type = _('ORA summary')
        try:
            task_api.submit_export_ora2_summary(request, course_key)
            success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)
            return Response({"status": success_status})
        except (AlreadyRunningError, QueueConnectionError, AttributeError) as err:
            return JsonResponse({"error": str(err)}, status=400)


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ExportOra2SubmissionFilesView(DeveloperErrorViewMixin, APIView):
    """
    Pushes a Celery task which will download and compress all submission
    files (texts, attachments) into a zip archive.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Initiates a task to export all ORA2 submission files for a course.
        Returns a JSON response indicating the export task has been started.
        """
        course_key = CourseKey.from_string(course_id)
        try:
            task_api.submit_export_ora2_submission_files(request, course_key)
            return Response({
                "status": _("Attachments archive is being created.")
            })
        except (AlreadyRunningError, QueueConnectionError, AttributeError) as err:
            return JsonResponse({"error": str(err)}, status=400)


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CalculateGradesCsvView(DeveloperErrorViewMixin, APIView):
    """
    Initiates a Celery task to calculate grades CSV.
    AlreadyRunningError is raised if the course's grades are already being updated.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Initiates a Celery task to calculate grades CSV.
        """
        report_type = _('grade')
        course_key = CourseKey.from_string(course_id)
        task_api.submit_calculate_grades_csv(request, course_key)
        success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)

        return Response({"status": success_status})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class ProblemGradeReport(DeveloperErrorViewMixin, APIView):
    """
    Request a CSV showing students' grades for all problems in the course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CAN_RESEARCH

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Request a CSV showing students' grades for all problems in the
        course.

        AlreadyRunningError is raised if the course's grades are already being
        updated.
        """
        course_key = CourseKey.from_string(course_id)
        report_type = _('problem grade')
        task_api.submit_problem_grade_report(request, course_key)
        success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=report_type)

        return JsonResponse({"status": success_status})


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ListForumMembers(APIView):
    """
    Lists forum members of a certain rolename.
    Limited to staff access.

    The requesting user must be at least staff.
    Staff forum admins can access all roles EXCEPT for FORUM_ROLE_ADMINISTRATOR
        which is limited to instructors.

    """
    permission_classes = (
        IsAuthenticated, permissions.InstructorPermission, permissions.ForumAdminRequiresInstructorAccess
    )
    permission_name = permissions.VIEW_FORUM_MEMBERS
    serializer_class = ForumRoleNameSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Handle the POST request to list forum members with a certain role name for the given course.

        Args:
            request (HttpRequest): The request object containing the data sent by the client.
            course_id (int): The ID of the course for which the role is being assigned or managed.

        Returns:
            Response: The Json constians lists of members.

        Raises:
            ValidationError: If the provided `rolename` is not valid according to the serializer.
        """
        course_id = CourseKey.from_string(course_id)
        course_discussion_settings = CourseDiscussionSettings.get(course_id)

        role_serializer = ForumRoleNameSerializer(
            data=request.data,
            context={
                'course_discussion_settings': course_discussion_settings,
                'course_id': course_id
            }
        )

        role_serializer.is_valid(raise_exception=True)
        rolename = role_serializer.data['rolename']

        response_payload = {
            'course_id': str(course_id),
            rolename: role_serializer.data.get('users'),
            'division_scheme': course_discussion_settings.division_scheme,
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class SendEmail(DeveloperErrorViewMixin, APIView):
    """
    Send an email to self, staff, cohorts, or everyone involved in a course.
    """
    http_method_names = ['post']
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.EMAIL
    serializer_class = SendEmailSerializer

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
        Query Parameters:
        - 'send_to' specifies what group the email should be sent to
           Options are defined by the CourseEmail model in
           lms/djangoapps/bulk_email/models.py
        - 'subject' specifies email's subject
        - 'message' specifies email's content
        """
        course_id = CourseKey.from_string(course_id)
        course_overview = CourseOverview.get_from_id(course_id)

        if not is_bulk_email_feature_enabled(course_id):
            log.warning(f"Email is not enabled for course {course_id}")
            return HttpResponseForbidden("Email is not enabled for this course.")

        serializer_data = self.serializer_class(data=request.data)
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        # Skipping serializer validation to avoid potential disruptions.
        # The API handles numerous input variations, and changes here could introduce breaking issues.

        targets = json.loads(request.POST.get("send_to"))

        subject = serializer_data.validated_data.get("subject")
        message = serializer_data.validated_data.get("message")
        # optional, this is a date and time in the form of an ISO8601 string
        schedule = serializer_data.validated_data.get("schedule", "")

        schedule_dt = None
        if schedule:
            try:
                # convert the schedule from a string to a datetime, then check if its a
                # valid future date and time, dateutil
                # will throw a ValueError if the schedule is no good.
                schedule_dt = dateutil.parser.parse(schedule).replace(tzinfo=pytz.utc)
                if schedule_dt < datetime.datetime.now(pytz.utc):
                    raise ValueError("the requested schedule is in the past")
            except ValueError as value_error:
                error_message = (
                    f"Error occurred creating a scheduled bulk email task. Schedule provided: '{schedule}'. Error: "
                    f"{value_error}"
                )
                log.error(error_message)
                return HttpResponseBadRequest(error_message)

        # Retrieve the customized email "from address" and email template from site configuration for the c
        # ourse/partner.
        # If there is no site configuration enabled for the current site then we use system defaults for both.
        from_addr = _get_branded_email_from_address(course_overview)
        template_name = _get_branded_email_template(course_overview)

        # Create the CourseEmail object. This is saved immediately so that any transaction that has been
        # pending up to this point will also be committed.
        try:
            email = create_course_email(
                course_id,
                request.user,
                targets,
                subject,
                message,
                template_name=template_name,
                from_addr=from_addr,
            )
        except ValueError as err:
            return HttpResponseBadRequest(repr(err))

        # Submit the task, so that the correct InstructorTask object gets created (for monitoring purposes)
        task_api.submit_bulk_course_email(request, course_id, email.id, schedule_dt)

        response_payload = {
            'course_id': str(course_id),
            'success': True,
        }

        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class UpdateForumRoleMembership(APIView):
    """
    Modify a user's forum role in a course.

    Permissions:
    - Must be authenticated.
    - Must be instructor or (staff + forum admin).
    - Only instructors can grant FORUM_ROLE_ADMINISTRATOR.

    Request (POST body):
    {
        "unique_student_identifier": "user@example.com",
        "rolename": "FORUM_ROLE_MODERATOR",
        "action": "allow"  or "revoke"
    }

    """
    permission_classes = (
        IsAuthenticated,
        permissions.InstructorPermission,
        permissions.ForumAdminRequiresInstructorAccess
    )
    permission_name = permissions.EDIT_FORUM_ROLES
    serializer_class = UpdateForumRoleMembershipSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Handles role modification requests for a forum user.

        Query parameters:
        - `email` is the target users email
        - `rolename` is one of [FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_GROUP_MODERATOR,
            FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA]
        - `action` is one of ['allow', 'revoke']
        """
        course_id = CourseKey.from_string(course_id)
        serializer_data = UpdateForumRoleMembershipSerializer(data=request.data)

        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        user = serializer_data.validated_data.get('unique_student_identifier')
        if not user:
            return JsonResponse({'error': 'User does not exist.'}, status=400)

        rolename = serializer_data.data['rolename']
        action = serializer_data.data['action']

        if action == 'allow' and not is_user_enrolled_in_course(user, course_id):
            CourseEnrollment.enroll(user, course_id)

        try:
            update_forum_role(course_id, user, rolename, action)
        except Role.DoesNotExist:
            return HttpResponseBadRequest("Role does not exist.")

        return Response(
            {
                "course_id": str(course_id),
                "action": action,
            },
            status=status.HTTP_200_OK,
        )


@require_POST
def get_user_invoice_preference(request, course_id):  # lint-amnesty, pylint: disable=unused-argument
    """
    Gets invoice copy user's preferences.
    """
    invoice_copy_preference = True
    invoice_preference_value = get_user_preference(request.user, INVOICE_KEY)
    if invoice_preference_value is not None:
        invoice_copy_preference = invoice_preference_value == 'True'

    return JsonResponse({
        'invoice_copy': invoice_copy_preference
    })


def _display_unit(unit):
    """
    Gets string for displaying unit to user.
    """
    name = getattr(unit, 'display_name', None)
    if name:
        return f'{name} ({str(unit.location)})'
    else:
        return str(unit.location)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ChangeDueDate(APIView):
    """
    Grants a due date extension to a student for a particular unit.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GIVE_STUDENT_EXTENSION
    serializer_class = BlockDueDateSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Grants a due date extension to a student for a particular unit.

        params:
            url (str): The URL related to the block that needs the due date update.
            due_datetime (str): The new due date and time for the block.
            student (str): The email or username of the student whose access is being modified.
        """
        serializer_data = self.serializer_class(data=request.data)
        if not serializer_data.is_valid():
            return JsonResponseBadRequest({'error': _('All fields must be filled out')})

        student = serializer_data.validated_data.get('student')
        if not student:
            response_payload = {
                'error': _(
                    'Could not find student matching identifier: {student}'
                ).format(student=request.data.get("student"))
            }
            return JsonResponse(response_payload, status=status.HTTP_404_NOT_FOUND)

        due_datetime = serializer_data.validated_data.get('due_datetime')
        try:
            due_date = parse_datetime(due_datetime)
        except DashboardError:
            return JsonResponseBadRequest({'error': _('The extension due date and time format is incorrect')})

        course = get_course_by_id(CourseKey.from_string(course_id))
        unit = find_unit(course, serializer_data.validated_data.get('url'))
        reason = strip_tags(serializer_data.validated_data.get('reason', ''))
        try:
            set_due_date_extension(course, unit, student, due_date, request.user, reason=reason)
        except Exception as error:  # pylint: disable=broad-except
            return JsonResponseBadRequest({'error': str(error)})

        return JsonResponse(_(
            'Successfully changed due date for student {0} for {1} '
            'to {2}').format(student.profile.name, _display_unit(unit),
                             due_date.strftime('%Y-%m-%d %H:%M')))


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ResetDueDate(APIView):
    """
    Rescinds a due date extension for a student on a particular unit.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GIVE_STUDENT_EXTENSION
    serializer_class = BlockDueDateSerializer

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        reset a due date extension to a student for a particular unit.
        params:
            url (str): The URL related to the block that needs the due date update.
            student (str): The email or username of the student whose access is being modified.
            reason (str): Optional param.
        """
        serializer_data = self.serializer_class(data=request.data, context={'disable_due_datetime': True})
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        student = serializer_data.validated_data.get('student')
        if not student:
            response_payload = {
                'error': f'Could not find student matching identifier: {request.data.get("student")}'
            }
            return JsonResponse(response_payload)

        course = get_course_by_id(CourseKey.from_string(course_id))
        unit = find_unit(course, serializer_data.validated_data.get('url'))
        reason = strip_tags(serializer_data.validated_data.get('reason', ''))

        version = getattr(course, 'course_version', None)

        original_due_date = get_date_for_block(course_id, unit.location, published_version=version)

        try:
            set_due_date_extension(course, unit, student, None, request.user, reason=reason)
            if not original_due_date:
                # It's possible the normal due date was deleted after an extension was granted:
                return JsonResponse(
                    _("Successfully removed invalid due date extension (unit has no due date).")
                )

            original_due_date_str = original_due_date.strftime('%Y-%m-%d %H:%M')
            return JsonResponse(_(
                'Successfully reset due date for student {0} for {1} '
                'to {2}').format(student.profile.name, _display_unit(unit),
                                 original_due_date_str))

        except Exception as error:  # pylint: disable=broad-except
            return JsonResponse({'error': str(error)}, status=400)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ShowUnitExtensionsView(APIView):
    """
    API view to retrieve a list of students who have due date extensions
    for a specific unit in a course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    serializer_class = ShowUnitExtensionsSerializer
    permission_name = permissions.GIVE_STUDENT_EXTENSION

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Shows all of the students which have due date extensions for the given unit.
        """
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        url = serializer.validated_data['url']
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key)

        try:
            unit = find_unit(course, url)
            data = dump_block_extensions(course, unit)
            return Response(data)

        except DashboardError as error:
            return error.response()


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class ShowStudentExtensions(APIView):
    """
    Shows all of the due date extensions granted to a particular student in a
    particular course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    serializer_class = ShowStudentExtensionSerializer
    permission_name = permissions.GIVE_STUDENT_EXTENSION

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Handles POST requests to retrieve due date extensions for a specific student
        within a specified course.

        Parameters:
        - `request`: The HTTP request object containing user-submitted data.
        - `course_id`: The ID of the course for which the extensions are being queried.

        Data expected in the request:
        - `student`: A required field containing the identifier of the student for whom
          the due date extensions are being retrieved. This data is extracted from the
          request body.

        Returns:
        - A JSON response containing the details of the due date extensions granted to
          the specified student in the specified course.
        """
        data = {
            'student': request.data.get('student')
        }
        serializer_data = self.serializer_class(data=data)

        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        student = serializer_data.validated_data.get('student')
        if not student:
            response_payload = f'Could not find student matching identifier: {request.data.get("student")}'
            return JsonResponse({'error': response_payload}, status=400)

        course = get_course_by_id(CourseKey.from_string(course_id))
        return Response(dump_student_extensions(course, student))


def _split_input_list(str_list):
    """
    Separate out individual student email from the comma, or space separated string.

    e.g.
    in: "Lorem@ipsum.dolor, sit@amet.consectetur\nadipiscing@elit.Aenean\r convallis@at.lacus\r, ut@lacinia.Sed"
    out: ['Lorem@ipsum.dolor', 'sit@amet.consectetur', 'adipiscing@elit.Aenean', 'convallis@at.lacus', 'ut@lacinia.Sed']

    `str_list` is a string coming from an input text area
    returns a list of separated values
    """

    new_list = re.split(r'[\n\r\s,]', str_list)
    new_list = [s.strip() for s in new_list]
    new_list = [s for s in new_list if s != '']

    return new_list


def _instructor_dash_url(course_key, section=None):
    """Return the URL for a section in the instructor dashboard.

    Arguments:
        course_key (CourseKey)

    Keyword Arguments:
        section (str): The name of the section to load.

    Returns:
        unicode: The URL of a section in the instructor dashboard.

    """
    url = reverse('instructor_dashboard', kwargs={'course_id': str(course_key)})
    if section is not None:
        url += f'#view-{section}'
    return url


class HasCertificateActionPermission(BasePermission):
    """
    DRF permission class to validate course-level certificate task permissions
    based on the `action` URL parameter.
    """

    permission_map = {
        'toggle': permissions.ENABLE_CERTIFICATE_GENERATION,
        'generate': permissions.START_CERTIFICATE_GENERATION,
        'regenerate': permissions.START_CERTIFICATE_REGENERATION,
    }

    def has_permission(self, request, view):
        """
        Check whether the user has permission to perform the requested certificate action
        on the specified course.
        """
        course_id = view.kwargs.get('course_id')
        action = view.kwargs.get('action')

        if not course_id or not action:
            return False

        required_perm = self.permission_map.get(action)
        if required_perm is None:
            return False

        try:
            course_key = CourseKey.from_string(course_id)
        except (ValueError, TypeError):
            return False

        return request.user.has_perm(required_perm, course_key)


def toggle_certificate_generation(request, course_id):
    """
    Enable or disable student-generated certificates for a course.

    Based on the value of the POST field `certificates-enabled`, this function
    updates the course setting to allow or prevent students from generating their
    own certificates. This function assumes that permission checks
    have already been performed.

    Args:
        request (HttpRequest): The incoming POST request.
        course_id (str): The course identifier in string format.

    Returns:
        HttpResponseRedirect: Redirects back to the instructor dashboard
        (certificates section) after updating the course setting.
    """
    course_key = CourseKey.from_string(course_id)
    is_enabled = (request.POST.get('certificates-enabled', 'false') == 'true')
    certs_api.set_cert_generation_enabled(course_key, is_enabled)
    return redirect(_instructor_dash_url(course_key, section='certificates'))


def start_certificate_generation(request, course_id):
    """
    Initiates the generation of certificates for all enrolled students in the course.

    This function triggers an asynchronous background task that generates certificates
    for every student enrolled in the specified course. It returns a response payload
    containing a confirmation message and the task ID for tracking the task's progress.

    Args:
        request (HttpRequest): The HTTP request object.
        course_key (CourseKey): The course identifier for which to generate certificates.

    Returns:
        dict: A dictionary with a success message and the task ID.
    """
    course_key = CourseKey.from_string(course_id)
    task = task_api.generate_certificates_for_students(request, course_key)

    return {
        "message": _(
            "Certificate generation task for all students of this course has been started. "
            "You can view the status of the generation task in the \"Pending Tasks\" section."
        ),
        "task_id": task.task_id
    }


def start_certificate_regeneration(request, course_id, certificates_statuses):
    """
    Initiates regeneration of certificates for students based on given certificate statuses.

    This function triggers a background task that regenerates certificates for students
    whose certificates match the provided list of statuses.

    Args:
        request (HttpRequest): The HTTP request object.
        course_key (CourseKey): The identifier of the course for which certificates are being regenerated.
        certificates_statuses (list[str]): A list of certificate statuses to filter the affected certificates.

    Returns:
        dict: A dictionary with a success message and success status.
    """
    course_key = CourseKey.from_string(course_id)
    task_api.regenerate_certificates(request, course_key, certificates_statuses)

    return {
        'message': _(
            'Certificate regeneration task has been started. '
            'You can view the status of the generation task in the "Pending Tasks" section.'
        ),
        'success': True
    }


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CertificateTask(DeveloperErrorViewMixin, APIView):
    """
    API endpoint for handling certificate-related administrative tasks for a given course.

    Supported actions:
    - "toggle": Enable or disable self-generated certificates.
    - "generate": Initiate certificate generation for all enrolled students.
    - "regenerate": Regenerate certificates based on selected certificate statuses.

    URL pattern:
        POST /courses/{course_id}/instructor/api/certificates/{action}/

    The `action` path parameter determines the task to perform.
    The request must be authenticated and the user must have the appropriate permission for the action.
    """
    permission_classes = [IsAuthenticated, HasCertificateActionPermission]

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id, action=None):
        """
        Handles POST requests for certificate actions.

        Depending on the `action` parameter, different tasks are performed:

        Args:
            request (HttpRequest): The HTTP request object.
            course_id (str): The ID of the course on which to perform the action.
            action (str, optional): The certificate task to perform. Must be one of:
                - "toggle": Enable or disable certificates for the course. No additional
                            parameters are required.
                - "generate": Generate certificates for eligible learners. No additional
                            parameters are required.
                - "regenerate": Regenerate certificates for learners. Requires an additional
                            parameter in the request body:
                    - `statuses` (list of str): List of certificate statuses to regenerate
                            (e.g., ["downloaded", "issued"]).

       Returns:
            Response: A DRF Response object containing a success message or error details.
            If the `action` is invalid, returns HTTP 400 with an error message.

       Example request body for `regenerate` action:
        {
            "statuses": ["downloaded", "issued"]
        }
        """
        if action == "toggle":
            return self._handle_toggle(request, course_id)
        elif action == "generate":
            return self._handle_generate(request, course_id)
        elif action == "regenerate":
            return self._handle_regenerate(request, course_id)
        else:
            return Response(
                {"error": f"Invalid action: {action}"},
                status=status.HTTP_400_BAD_REQUEST
            )

    def _handle_toggle(self, request, course_id):
        """Handle certificate generation toggle."""
        # TODO: Update this to return a proper API response (e.g., {"enabled": true})
        return toggle_certificate_generation(request, course_id)

    def _handle_generate(self, request, course_id):
        """Handle certificate generation for all students."""
        payload = start_certificate_generation(request, course_id)
        return Response(payload, status=status.HTTP_200_OK)

    def _handle_regenerate(self, request, course_id):
        """Handle certificate regeneration based on status."""
        # Validate and extract certificate statuses from the request
        serializer = CertificateStatusesSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'message': _(
                    'Please select certificate statuses that '
                    'lie with in "certificate_statuses" entry in POST data.'
                )},
                status=status.HTTP_400_BAD_REQUEST
            )
        statuses = serializer.validated_data['certificate_statuses']
        payload = start_certificate_regeneration(request, course_id, statuses)
        return Response(payload, status=status.HTTP_200_OK)


@require_course_permission(permissions.ENABLE_CERTIFICATE_GENERATION)
@require_POST
def enable_certificate_generation(request, course_id=None):
    """
    View to toggle self-generated certificate availability for a course.

    This endpoint is protected by course-level permission checks and allows
    enabling or disabling student-generated certificates. The logic is handled
    by `toggle_certificate_generation`.

    Args:
        request (HttpRequest): The incoming POST request.
        course_id (str): The course identifier in string format.

    Returns:
        HttpResponseRedirect: Redirects to the instructor dashboard after update.
    """
    return toggle_certificate_generation(request, course_id)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class MarkStudentCanSkipEntranceExam(APIView):
    """
    Mark a student to skip entrance exam.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.ALLOW_STUDENT_TO_BYPASS_ENTRANCE_EXAM

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Takes `unique_student_identifier` as required POST parameter.
        """
        course_id = CourseKey.from_string(course_id)
        student_identifier = request.data.get("unique_student_identifier")

        serializer_data = UniqueStudentIdentifierSerializer(data=request.data)
        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        student = serializer_data.validated_data.get('unique_student_identifier')
        if not student:
            response_payload = f'Could not find student matching : {student_identifier}'
            return JsonResponse({'error': response_payload}, status=400)

        __, created = EntranceExamConfiguration.objects.get_or_create(user=student, course_id=course_id)
        if created:
            message = _('This student (%s) will skip the entrance exam.') % student_identifier
        else:
            message = _('This student (%s) is already allowed to skip the entrance exam.') % student_identifier
        response_payload = {
            'message': message,
        }
        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class StartCertificateGeneration(DeveloperErrorViewMixin, APIView):
    """
    Start generating certificates for all students enrolled in given course.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.START_CERTIFICATE_GENERATION

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
         Generating certificates for all students enrolled in given course.
        """
        payload = start_certificate_generation(request, course_id=course_id)
        return JsonResponse(payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class StartCertificateRegeneration(DeveloperErrorViewMixin, APIView):
    """
    Start regenerating certificates for students whose certificate statuses lie with in 'certificate_statuses'
    entry in POST data.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.START_CERTIFICATE_REGENERATION
    serializer_class = CertificateStatusesSerializer
    http_method_names = ['post']

    @method_decorator(transaction.non_atomic_requests, name='dispatch')
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        certificate_statuses 'certificate_statuses' in POST data.
        """
        serializer = self.serializer_class(data=request.data)

        if not serializer.is_valid():
            return JsonResponse(
                {'message': _('Please select certificate statuses from the list only.')},
                status=400
            )

        certificates_statuses = serializer.validated_data['certificate_statuses']
        payload = start_certificate_regeneration(request, course_id, certificates_statuses)
        return JsonResponse(payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CertificateExceptionView(DeveloperErrorViewMixin, APIView):
    """
    Add/Remove students to/from the certificate allowlist.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CERTIFICATE_EXCEPTION_VIEW
    serializer_class = CertificateSerializer
    http_method_names = ['post', 'delete']

    @method_decorator(transaction.non_atomic_requests, name='dispatch')
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Add certificate exception for a student.
        """
        return self._handle_certificate_exception(request, course_id, action="post")

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def delete(self, request, course_id):
        """
        Remove certificate exception for a student.
        """
        return self._handle_certificate_exception(request, course_id, action="delete")

    def _handle_certificate_exception(self, request, course_id, action):
        """
        Handles adding or removing certificate exceptions.
        """
        course_key = CourseKey.from_string(course_id)
        try:
            data = request.data
        except Exception:  # pylint: disable=broad-except
            return JsonResponse(
                {
                    'success': False,
                    'message':
                        _('The record is not in the correct format. Please add a valid username or email address.')},
                status=400
            )

        # Extract and validate the student information
        student, error_response = self._get_and_validate_user(data)

        if error_response:
            return error_response

        try:
            if action == "post":
                exception = add_certificate_exception(course_key, student, data)
                return JsonResponse(exception)
            elif action == "delete":
                remove_certificate_exception(course_key, student)
                return JsonResponse({}, status=204)
        except ValueError as error:
            return JsonResponse({'success': False, 'message': str(error)}, status=400)

    def _get_and_validate_user(self, raw_data):
        """
        Extracts the user data from the request and validates the student.
        """
        # This is only happening in case of delete.
        # because content-type is coming as x-www-form-urlencoded from front-end.
        if isinstance(raw_data, QueryDict):
            raw_data = list(raw_data.keys())[0]
            try:
                raw_data = json.loads(raw_data)
            except Exception as error:  # pylint: disable=broad-except
                return None, JsonResponse({'success': False, 'message': str(error)}, status=400)

        try:
            user_data = raw_data.get('user_name', '') or raw_data.get('user_email', '')
        except ValueError as error:
            return None, JsonResponse({'success': False, 'message': str(error)}, status=400)

        serializer_data = self.serializer_class(data={'user': user_data})
        if not serializer_data.is_valid():
            return None, JsonResponse({'success': False, 'message': serializer_data.errors}, status=400)

        student = serializer_data.validated_data.get('user')
        if not student:
            response_payload = f'{user_data} does not exist in the LMS. Please check your spelling and retry.'
            return None, JsonResponse({'success': False, 'message': response_payload}, status=400)

        return student, None


def add_certificate_exception(course_key, student, certificate_exception):
    """
    Add a certificate exception.

    Raises ValueError in case Student is already allowlisted or if they appear on the block list.

    :param course_key: identifier of the course whose certificate exception will be added.
    :param student: User object whose certificate exception will be added.
    :param certificate_exception: A dict object containing certificate exception info.
    :return: Allowlist item in dict format containing certificate exception info.
    """
    log.info(f"Request received to add an allowlist entry for student {student.id} in course {course_key}")

    # Check if the learner is actively enrolled in the course-run
    if not is_user_enrolled_in_course(student, course_key):
        raise ValueError(
            _("Student {user} is not enrolled in this course. Please check your spelling and retry.")
            .format(user=student.username)
        )

    # Check if the learner is blocked from receiving certificates in this course run.
    if certs_api.is_certificate_invalidated(student, course_key):
        raise ValueError(
            _("Student {user} is already on the certificate invalidation list and cannot be added to the certificate "
              "exception list.").format(user=student.username)
        )

    if certs_api.is_on_allowlist(student, course_key):
        raise ValueError(
            _("Student (username/email={user}) already in certificate exception list.").format(user=student.username)
        )

    certificate_allowlist_entry, __ = certs_api.create_or_update_certificate_allowlist_entry(
        student,
        course_key,
        certificate_exception.get("notes", "")
    )

    generated_certificate = certs_api.get_certificate_for_user(student.username, course_key, False)

    exception = dict({
        'id': certificate_allowlist_entry.id,
        'user_email': student.email,
        'user_name': student.username,
        'user_id': student.id,
        'certificate_generated': generated_certificate and generated_certificate.created_date.strftime("%B %d, %Y"),
        'created': certificate_allowlist_entry.created.strftime("%B %d, %Y"),
    })

    return exception


def remove_certificate_exception(course_key, student):
    """
    Remove certificate exception for given course and student from the certificate allowlist.

    Raises ValueError if an error occurs during removal of the allowlist entry.

    :param course_key: identifier of the course whose certificate exception needs to be removed.
    :param student: User object whose certificate exception needs to be removed.
    :return:
    """
    result = certs_api.remove_allowlist_entry(student, course_key)
    if not result:
        raise ValueError(
            _("Error occurred removing the allowlist entry for student {student}. Please refresh the page "
              "and try again").format(student=student.username)
        )


def parse_request_data_and_get_user(request):
    """
        Parse request data into Certificate Exception and User object.
        Certificate Exception is the dict object containing information about certificate exception.

    :param request:
    :param course_key: Course Identifier of the course for whom to process certificate exception
    :return: key-value pairs containing certificate exception data and User object
    """
    certificate_exception = parse_request_data(request)

    user = certificate_exception.get('user_name', '') or certificate_exception.get('user_email', '')
    if not user:
        raise ValueError(_('Student username/email field is required and can not be empty. '
                           'Kindly fill in username/email and then press "Add to Exception List" button.'))
    db_user = get_student(user)

    return certificate_exception, db_user


def parse_request_data(request):
    """
    Parse and return request data, raise ValueError in case of invalid JSON data.

    :param request: HttpRequest request object.
    :return: dict object containing parsed json data.
    """
    try:
        data = json.loads(request.body.decode('utf8') or '{}')
    except ValueError:
        raise ValueError(_('The record is not in the correct format. Please add a valid username or email address.'))  # lint-amnesty, pylint: disable=raise-missing-from

    return data


def get_student(username_or_email):
    """
    Retrieve and return User object from db, raise ValueError
    if user is does not exists or is not enrolled in the given course.

    :param username_or_email: String containing either user name or email of the student.
    :param course_key: CourseKey object identifying the current course.
    :return: User object
    """
    try:
        student = get_user_by_username_or_email(username_or_email)
    except ObjectDoesNotExist:
        raise ValueError(_("{user} does not exist in the LMS. Please check your spelling and retry.").format(  # lint-amnesty, pylint: disable=raise-missing-from
            user=username_or_email
        ))

    return student


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class GenerateCertificateExceptions(DeveloperErrorViewMixin, APIView):
    """
    Generate Certificate for students on the allowlist.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GENERATE_CERTIFICATE_EXCEPTIONS

    @method_decorator(transaction.non_atomic_requests)
    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id, generate_for=None):
        """
        :param request: HttpRequest object,
        :param course_id: course identifier of the course for whom to generate certificates
        :param generate_for: string to identify whether to generate certificates for 'all' or 'new'
                additions to the allowlist
        :return: JsonResponse object containing success/failure message and certificate exception data
        """
        course_key = CourseKey.from_string(course_id)

        if generate_for == 'all':
            # Generate Certificates for all allowlisted students
            students = 'all_allowlisted'

        elif generate_for == 'new':
            students = 'allowlisted_not_generated'

        else:
            # Invalid data, generate_for must be present for all certificate exceptions
            return JsonResponse(
                {
                    'success': False,
                    'message': _('Invalid data, generate_for must be "new" or "all".'),
                },
                status=400
            )

        task_api.generate_certificates_for_students(request, course_key, student_set=students)
        response_payload = {
            'success': True,
            'message': _('Certificate generation started for students on the allowlist.'),
        }

        return JsonResponse(response_payload)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
class GenerateBulkCertificateExceptions(APIView):
    """
    Adds students to the certificate allowlist using data from the uploaded CSV file.
    """

    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.GENERATE_BULK_CERTIFICATE_EXCEPTIONS

    @method_decorator(ensure_csrf_cookie)
    def post(self, request, course_id):
        """
        Arguments:
            request (WSGIRequest): Django HTTP request object.
            course_id (string): Course-Run key
        Returns:
            dict:
            {
                general_errors: [errors related to csv file e.g. csv uploading, csv attachment, content reading, etc. ],
                row_errors: {
                    data_format_error: [users/data in csv file that are not well formatted],
                    user_not_exist: [users that cannot be found in the LMS],
                    user_already_allowlisted: [users that already appear on the allowlist of this course-run],
                    user_not_enrolled: [users that are not currently enrolled in this course-run],
                    user_on_certificate_invalidation_list: [users that have an active certificate invalidation in this
                                                        course-run]
                },
                success: [list of users sucessfully added to the certificate allowlist]
            }
        """
        user_index = 0
        notes_index = 1
        row_errors_key = [
            'data_format_error',
            'user_not_exist',
            'user_already_allowlisted',
            'user_not_enrolled',
            'user_on_certificate_invalidation_list'
        ]
        course_key = CourseKey.from_string(course_id)
        students, general_errors, success = [], [], []
        row_errors = {key: [] for key in row_errors_key}

        def build_row_errors(key, _user, row_count):
            """
            inner method to build dict of csv data as row errors.
            """
            row_errors[key].append(_('user "{user}" in row# {row}').format(user=_user, row=row_count))

        if 'students_list' in request.FILES:
            try:
                upload_file = request.FILES.get('students_list')
                if upload_file.name.endswith('.csv'):
                    students = list(csv.reader(upload_file.read().decode('utf-8-sig').splitlines()))
                else:
                    general_errors.append(_('Make sure that the file you upload is in CSV format with no '
                                            'extraneous characters or rows.'))
            except Exception:  # pylint: disable=broad-except
                general_errors.append(_('Could not read uploaded file.'))
            finally:
                upload_file.close()

            row_num = 0
            for student in students:
                row_num += 1
                # verify that we have exactly two column in every row either email or username and notes but allow for
                # blank lines
                if len(student) != 2:
                    if student:
                        build_row_errors('data_format_error', student[user_index], row_num)
                        log.info(f'Invalid data/format in csv row# {row_num}')
                    continue

                user = student[user_index]
                try:
                    user = get_user_by_username_or_email(user)
                except ObjectDoesNotExist:
                    build_row_errors('user_not_exist', user, row_num)
                    log.info(f'Student {user} does not exist')
                else:
                    # make sure learner doesn't have an active certificate invalidation
                    if certs_api.is_certificate_invalidated(user, course_key):
                        build_row_errors('user_on_certificate_invalidation_list', user, row_num)
                        log.warning(f'Student {user.id} is blocked from receiving a Certificate in Course {course_key}')
                    # make sure learner isn't already on the allowlist
                    elif certs_api.is_on_allowlist(user, course_key):
                        build_row_errors('user_already_allowlisted', user, row_num)
                        log.warning(f'Student {user.id} already appears on the allowlist in Course {course_key}.')
                    # make sure user is enrolled in course
                    elif not is_user_enrolled_in_course(user, course_key):
                        build_row_errors('user_not_enrolled', user, row_num)
                        log.warning(f'Student {user.id} is not enrolled in Course {course_key}')
                    else:
                        certs_api.create_or_update_certificate_allowlist_entry(
                            user,
                            course_key,
                            notes=student[notes_index]
                        )
                        success.append(_('user "{username}" in row# {row}').format(username=user.username, row=row_num))
        else:
            general_errors.append(_('File is not attached.'))

        results = {
            'general_errors': general_errors,
            'row_errors': row_errors,
            'success': success
        }
        return JsonResponse(results)


@method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True), name='dispatch')
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CertificateInvalidationView(APIView):
    """
    Invalidate/Re-Validate students to/from certificate.

    :param request: HttpRequest object
    :param course_id: course identifier of the course for whom to add/remove certificates exception.
    :return: JsonResponse object with success/error message or certificate invalidation data.
    """
    permission_classes = (IsAuthenticated, permissions.InstructorPermission)
    permission_name = permissions.CERTIFICATE_INVALIDATION_VIEW
    serializer_class = CertificateSerializer
    http_method_names = ['post', 'delete']

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def post(self, request, course_id):
        """
         Invalidate/Re-Validate students to/from certificate.
        """
        course_key = CourseKey.from_string(course_id)
        # Validate request data and return error response in case of invalid data
        serializer_data = self.serializer_class(data=request.data)
        if not serializer_data.is_valid():
            # return HttpResponseBadRequest(reason=serializer_data.errors)
            return JsonResponse({'message': serializer_data.errors}, status=400)

        student = serializer_data.validated_data.get('user')
        notes = serializer_data.validated_data.get('notes')

        if not student:
            invalid_user = request.data.get('user')
            response_payload = f'{invalid_user} does not exist in the LMS. Please check your spelling and retry.'

            return JsonResponse({'message': response_payload}, status=400)

        try:
            certificate = _get_certificate_for_user(course_key, student)
        except Exception as ex:  # pylint: disable=broad-except
            return JsonResponse({'message': str(ex)}, status=400)

        # Invalidate certificate of the given student for the course course
        try:
            if certs_api.is_on_allowlist(student, course_key):
                log.warning(f"Invalidating certificate for student {student.id} in course {course_key} failed. "
                            "Student is currently on the allowlist.")
                raise ValueError(
                    _("The student {student} appears on the Certificate Exception list in course {course}. Please "
                      "remove them from the Certificate Exception list before attempting to invalidate their "
                      "certificate.").format(student=student, course=course_key)
                )
            certificate_invalidation = invalidate_certificate(
                request,
                certificate,
                notes,
                student
            )

        except ValueError as error:
            return JsonResponse({'message': str(error)}, status=400)
        return JsonResponse(certificate_invalidation)

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(transaction.non_atomic_requests)
    def delete(self, request, course_id):
        """
        Invalidate/Re-Validate students to/from certificate.
        """
        # Re-Validate student certificate for the course course
        course_key = CourseKey.from_string(course_id)
        try:
            data = json.loads(self.request.body.decode('utf8') or '{}')
        except Exception:  # pylint: disable=broad-except
            data = {}

        serializer_data = self.serializer_class(data=data)

        if not serializer_data.is_valid():
            return HttpResponseBadRequest(reason=serializer_data.errors)

        student = serializer_data.validated_data.get('user')

        try:
            certificate = _get_certificate_for_user(course_key, student)
        except Exception as ex:  # pylint: disable=broad-except
            return JsonResponse({'message': str(ex)}, status=400)

        try:
            re_validate_certificate(request, course_key, certificate, student)
        except ValueError as error:
            return JsonResponse({'message': str(error)}, status=400)

        return JsonResponse({}, status=204)


def invalidate_certificate(request, generated_certificate, notes, student):
    """
    Invalidate given GeneratedCertificate and add CertificateInvalidation record for future reference or re-validation.

    :param request: HttpRequest object
    :param generated_certificate: GeneratedCertificate object, the certificate we want to invalidate
    :param notes: notes values.
    :param student: User object, this user is tied to the generated_certificate we are going to invalidate
    :return: dict object containing updated certificate invalidation data.
    """
    # Check if the learner already appears on the certificate invalidation list
    if certs_api.is_certificate_invalidated(student, generated_certificate.course_id):
        raise ValueError(
            _("Certificate of {user} has already been invalidated. Please check your spelling and retry.").format(
                user=student.username,
            )
        )

    # Verify that the learner's certificate is valid before invalidating
    if not generated_certificate.is_valid():
        raise ValueError(
            _("Certificate for student {user} is already invalid, kindly verify that certificate was generated "
              "for this student and then proceed.").format(user=student.username)
        )

    # Add CertificateInvalidation record for future reference or re-validation
    certificate_invalidation = certs_api.create_certificate_invalidation_entry(
        generated_certificate,
        request.user,
        notes,
    )

    # Invalidate the certificate
    generated_certificate.invalidate(source='certificate_invalidation_list')

    return {
        'id': certificate_invalidation.id,
        'user': student.username,
        'invalidated_by': certificate_invalidation.invalidated_by.username,
        'created': certificate_invalidation.created.strftime("%B %d, %Y"),
        'notes': notes,
    }


@common_exceptions_400
def re_validate_certificate(request, course_key, generated_certificate, student):
    """
    Remove certificate invalidation from db and start certificate generation task for this student.
    Raises ValueError if certificate invalidation is present.

    :param request: HttpRequest object
    :param course_key: CourseKey object identifying the current course.
    :param generated_certificate: GeneratedCertificate object of the student for the given course
    """
    log.info(f"Attempting to revalidate certificate for student {student.id} in course {course_key}")

    certificate_invalidation = certs_api.get_certificate_invalidation_entry(generated_certificate)
    if not certificate_invalidation:
        raise ValueError(_("Certificate Invalidation does not exist, Please refresh the page and try again."))  # lint-amnesty, pylint: disable=raise-missing-from

    certificate_invalidation.deactivate()

    task_api.generate_certificates_for_students(
        request, course_key, student_set="specific_student", specific_student_id=student.id
    )


def _get_boolean_param(request, param_name):
    """
    Returns the value of the boolean parameter with the given
    name in the POST request. Handles translation from string
    values to boolean values.
    """
    return request.POST.get(param_name, False) in ['true', 'True', True]


def _create_error_response(request, msg):
    """
    Creates the appropriate error response for the current request,
    in JSON form.
    """
    return JsonResponse({"error": msg}, 400)


def _get_student_from_request_data(request_data):
    """
    Attempts to retrieve the student information from the incoming request data.

    :param request: HttpRequest object
    :param course_key: CourseKey object identifying the current course.
    """
    user = request_data.get("user")
    if not user:
        raise ValueError(
            _('Student username/email field is required and can not be empty. '
              'Kindly fill in username/email and then press "Invalidate Certificate" button.')
        )

    return get_student(user)


def _get_certificate_for_user(course_key, student):
    """
    Attempt to retrieve certificate information for a learner in a given course-run.

    Raises a ValueError if a certificate cannot be retrieved for the learner. This will prompt an informative message
    to be displayed on the instructor dashboard.
    """
    log.info(f"Retrieving certificate for student {student.id} in course {course_key}")
    certificate = certs_api.get_certificate_for_user(student.username, course_key, False)
    if not certificate:
        raise ValueError(_(
            "The student {student} does not have certificate for the course {course}. Kindly verify student "
            "username/email and the selected course are correct and try again.").format(
                student=student.username, course=course_key.course)
        )

    return certificate


def _get_branded_email_from_address(course_overview):
    """
    Checks and retrieves a customized "from address", if one exists for the course/org. This is the email address that
    learners will see the message coming from.

    Args:
        course_overview (CourseOverview): The course overview instance for the course-run.

    Returns:
        String: The customized "from address" to be used in messages sent by the bulk course email tool for this
                course or org.
    """
    from_addr = configuration_helpers.get_value('course_email_from_addr')
    if isinstance(from_addr, dict):
        # If course_email_from_addr is a dict, we are customizing the email template for each organization that has
        # courses on the site. The dict maps from addresses by org allowing us to find the correct from address to use
        # here.
        from_addr = from_addr.get(course_overview.display_org_with_default)

    return from_addr


def _get_branded_email_template(course_overview):
    """
    Checks and retrieves the custom email template, if one exists for the course/org, to style messages sent by the bulk
    course email tool.

    Args:
        course_overview (CourseOverview): The course overview instance for the course-run.

    Returns:
        String: The name of the custom email template to use for this course or org.
    """
    template_name = configuration_helpers.get_value('course_email_template_name')
    if isinstance(template_name, dict):
        # If course_email_template_name is a dict, we are customizing the email template for each organization that has
        # courses on the site. The dict maps template names by org allowing us to find the correct template to use here.
        template_name = template_name.get(course_overview.display_org_with_default)

    return template_name
