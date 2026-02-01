"""
An API for retrieving user account information.

For additional information and historical context, see:
https://openedx.atlassian.net/wiki/display/TNL/User+API
"""

import datetime
import logging
from functools import wraps

from zoneinfo import ZoneInfo
from consent.models import DataSharingConsent
from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, logout
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import transaction
from django.utils.translation import gettext as _
from django_ratelimit.core import is_ratelimited
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from edx_ace import ace
from edx_ace.recipient import Recipient
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from enterprise.models import EnterpriseCourseEnrollment, EnterpriseCustomerUser, PendingEnterpriseCustomerUser
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import UnsupportedMediaType
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from wiki.models import ArticleRevision
from wiki.models.pluginbase import RevisionPluginRevision

from common.djangoapps.track import segment
from common.djangoapps.entitlements.models import CourseEntitlement
from common.djangoapps.student.models import (  # lint-amnesty, pylint: disable=unused-import
    CourseEnrollmentAllowed,
    LoginFailures,
    ManualEnrollmentAudit,
    PendingEmailChange,
    PendingNameChange,
    User,
    UserProfile,
    get_potentially_retired_user_by_username,
    get_retired_email_by_email,
    get_retired_username_by_username,
    is_email_retired,
    is_username_retired,
)
from common.djangoapps.student.models_api import confirm_name_change, do_name_change_request, get_pending_name_change
from lms.djangoapps.certificates.api import clear_pii_from_certificate_records_for_user
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest
from openedx.core.djangoapps.course_groups.models import UnregisteredLearnerCohortAssignments
from openedx.core.djangoapps.credit.models import CreditRequest, CreditRequirementStatus
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.profile_images.images import remove_profile_images
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names, set_has_profile_image
from openedx.core.djangoapps.user_api.accounts.utils import handle_retirement_cancellation
from openedx.core.djangoapps.user_authn.exceptions import AuthFailedError
from openedx.core.lib.api.authentication import BearerAuthentication, BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser

from ..errors import AccountUpdateError, AccountValidationError, UserNotAuthorized, UserNotFound
from ..message_types import DeletionNotificationMessage
from ..models import (
    RetirementState,
    RetirementStateError,
    UserOrgTag,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)
from .api import get_account_settings, update_account_settings
from .permissions import (
    CanCancelUserRetirement,
    CanDeactivateUser,
    CanGetAccountInfo,
    CanReplaceUsername,
    CanRetireUser,
)
from .serializers import (
    PendingNameChangeSerializer,
    UserRetirementPartnerReportSerializer,
    UserRetirementStatusSerializer,
    UserSearchEmailSerializer,
)
from .signals import USER_RETIRE_LMS_CRITICAL, USER_RETIRE_LMS_MISC, USER_RETIRE_MAILINGS
from .utils import create_retirement_request_and_deactivate_account, username_suffix_generator

# This is a temporary import path while we transition from integrated_channels to channel_integrations
if getattr(settings, 'ENABLE_LEGACY_INTEGRATED_CHANNELS', True):
    from integrated_channels.degreed.models import DegreedLearnerDataTransmissionAudit
    from integrated_channels.sap_success_factors.models import SapSuccessFactorsLearnerDataTransmissionAudit
else:
    from channel_integrations.degreed2.models import Degreed2LearnerDataTransmissionAudit \
        as DegreedLearnerDataTransmissionAudit
    from channel_integrations.sap_success_factors.models import SapSuccessFactorsLearnerDataTransmissionAudit

log = logging.getLogger(__name__)

USER_PROFILE_PII = {
    "name": "",
    "meta": "",
    "location": "",
    "year_of_birth": None,
    "gender": None,
    "mailing_address": None,
    "city": None,
    "country": None,
    "bio": None,
    "phone_number": None,
}


def request_requires_username(function):
    """
    Requires that a ``username`` key containing a truthy value exists in
    the ``request.data`` attribute of the decorated function.
    """

    @wraps(function)
    def wrapper(self, request):  # pylint: disable=missing-docstring
        username = request.data.get("username", None)
        if not username:
            return Response(status=status.HTTP_404_NOT_FOUND, data={"message": "The user was not specified."})
        return function(self, request)

    return wrapper


account_get_me_return_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "username": openapi.Schema(type=openapi.TYPE_STRING),
    },
)


# pylint: disable=line-too-long
class AccountViewSet(ViewSet):
    """View or update a user's account information."""

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, CanGetAccountInfo)
    parser_classes = (
        JSONParser,
        MergePatchParser,
    )
    account_user_get_responses = {
        status.HTTP_200_OK: account_get_me_return_schema,
        status.HTTP_401_UNAUTHORIZED: "",
    }

    @swagger_auto_schema(
        responses=account_user_get_responses,
    )
    def get(self, request):
        """Return an authenticated user's username

        **Example Requests**

            GET /api/user/v1/me[?view=shared]
        """
        return Response({"username": request.user.username})

    def list(self, request):
        """Return a list of user details objects

        **Example Requests**

            GET /api/user/v1/accounts?usernames={username1,username2}[?view=shared]

            GET /api/user/v1/accounts?email={user_email} (staff only)

            GET /api/user/v1/accounts?lms_user_id={user_email} (staff only)

        **Responses**

        If no user exists with the specified username, or email, an HTTP 404 "Not Found" response is returned.

        If the user makes the request for her own account, or makes a request for another account and has "is_staff" access, an HTTP 200 "OK" response is returned.

        The response consists of a list of one or more  user objects, in the same format as is returned for `GET /user/v1/accounts/{username}`.
        """
        usernames = request.GET.get("username")
        user_email = request.GET.get("email")
        lms_user_id = request.GET.get("lms_user_id")
        search_usernames = []

        if usernames:
            search_usernames = usernames.strip(",").split(",")
        elif user_email:
            if is_email_retired(user_email):
                can_cancel_retirement = True
                retirement_id = None
                earliest_datetime = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=settings.COOL_OFF_DAYS)
                try:
                    retirement_status = UserRetirementStatus.objects.get(
                        created__gt=earliest_datetime,
                        created__lt=datetime.datetime.now(ZoneInfo("UTC")),
                        original_email=user_email,
                    )
                    retirement_id = retirement_status.id
                except UserRetirementStatus.DoesNotExist:
                    can_cancel_retirement = False

                context = {
                    "error_msg": accounts.RETIRED_EMAIL_MSG,
                    "can_cancel_retirement": can_cancel_retirement,
                    "retirement_id": retirement_id,
                }

                return Response(context, status=status.HTTP_404_NOT_FOUND)
            user_email = user_email.strip("")
            try:
                user = User.objects.get(email=user_email)
            except (UserNotFound, User.DoesNotExist):
                return Response(status=status.HTTP_404_NOT_FOUND)
            search_usernames = [user.username]
        elif lms_user_id:
            try:
                user = User.objects.get(id=lms_user_id)
            except (UserNotFound, User.DoesNotExist):
                return Response(status=status.HTTP_404_NOT_FOUND)
            except ValueError:
                return Response(status=status.HTTP_400_BAD_REQUEST)
            search_usernames = [user.username]
        try:
            account_settings = get_account_settings(request, search_usernames, view=request.query_params.get("view"))
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(account_settings)

    def search_emails(self, request):
        """Return information about users associated with a list of email addresses

        **Example Requests**

        POST /api/user/v1/accounts/search_emails

            {
                "emails": ["edx@example.com", "staff@example.com"]
            }

        **Response**

        If no `emails` key is present in the request, or the user does not have "is_staff" access, an HTTP 404 "Not Found" response is returned.

        If the has "is_staff" access, an HTTP 200 "OK" response is returned. The response contains the following values.

            [
                {
                    "username": "edx",
                    "email": "edx@example.com",
                    "id": 3,
                },
                {
                    "username": "staff",
                    "email": "staff@example.com",
                    "id": 8,
                }
            ]
        """
        if not request.user.is_staff:
            return Response(
                {"developer_message": "not_found", "user_message": "Not Found"}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            user_emails = request.data["emails"]
        except KeyError as error:
            error_message = f"{error} field is required"
            return Response(
                {"developer_message": error_message, "user_message": error_message}, status=status.HTTP_400_BAD_REQUEST
            )
        users = User.objects.filter(email__in=user_emails)
        data = UserSearchEmailSerializer(users, many=True).data
        return Response(data)

    def retrieve(self, request, username):
        """Retrieve a single detailed user object

        **Example Requests**

            GET /api/user/v1/accounts/{username}/

        **Response**

        If no user exists with the specified username, or email, an HTTP 404 "Not Found" response is returned.

        If the user makes the request for her own account, or makes a request for another account and has "is_staff" access, an HTTP 200 "OK" response is returned. The response contains the following values.

        * `id`: numerical lms user id in db
        * `activation_key`: auto-genrated activation key when signed up via email
        * `bio`: null or textual representation of user biographical information ("about me").
        * `country`: An ISO 3166 country code or null.
        * `date_joined`: The date the account was created, in the string format provided by datetime. For example, "2014-08-26T17:52:11Z".
        * `last_login`: The latest date the user logged in, in the string datetime format.
        * `email`: Email address for the user. New email addresses must be confirmed via a confirmation email, so GET does not reflect the change until the address has been confirmed.
        * `secondary_email`: A secondary email address for the user. Unlike the email field, GET will reflect the latest update to this field even if changes have yet to be confirmed.
        * `verified_name`: Approved verified name of the learner present in name affirmation plugin
        * `extended_profile`: A list of objects with the keys `field_name` and `field_value`, returning any populated `extended_profile_fields` configured in the **Site Configuration**
        * `gender`: One of the following values:
            * null
            * "f"
            * "m"
            * "o"
        * `goals`: The textual representation of the user's goals, or null.
        * `is_active`: Boolean representation of whether a user is active.
        * `language`: The user's preferred language, or null.
        * `language_proficiencies`: Array of language preferences. Each preference is a JSON object with the following keys:
            * "code": string ISO 639-1 language code e.g. "en".
        * `level_of_education`: One of the following values:
            * "p": PhD or Doctorate
            * "m": Master's or professional degree
            * "b": Bachelor's degree
            * "a": Associate's degree
            * "hs": Secondary/high school
            * "jhs": Junior secondary/junior high/middle school
            * "el": Elementary/primary school
            * "none": None
            * "o": Other
            * null: The user did not enter a value
        * `mailing_address`: The textual representation of the user's mailing address, or null.
        * `name`: The full name of the user.
        * `profile_image`: A JSON representation of a user's profile image information. This representation has the following keys.
            * "has_image": Boolean indicating whether the user has a profile image.
            * "image_url_*": Absolute URL to various sizes of a user's profile image, where '*' matches a representation of the corresponding image size, such as 'small', 'medium', 'large', and 'full'. These are configurable via PROFILE_IMAGE_SIZES_MAP.
        * `requires_parental_consent`: True if the user is a minor requiring parental consent.
        * `social_links`: Array of social links, sorted alphabetically by "platform". Each preference is a JSON object with the following keys:
            * "platform": A particular social platform, ex: 'facebook'
            * "social_link": The link to the user's profile on the particular platform
        * `username`: The username associated with the account.
        * `year_of_birth`: The year the user was born, as an integer, or null.
        * `account_privacy`: The user's setting for sharing her personal profile. Possible values are "all_users", "private", or "custom".  If "custom", the user has selectively chosen a subset of shareable fields to make visible to others via the User Preferences API.
        * `phone_number`: The phone number for the user. String of numbers with an optional `+` sign at the start.
        * `pending_name_change`: If the user has an active name change request, returns the requested name.

        For all text fields, plain text instead of HTML is supported. The data is stored exactly as specified. Clients must HTML escape rendered values to avoid script injections.

        If a user who does not have "is_staff" access requests account information for a different user, only a subset of these fields is returned. The returned fields depend on the `ACCOUNT_VISIBILITY_CONFIGURATION` configuration setting and the visibility preference of the user for whom data is requested.

        A user can view which account fields they have shared with other users by requesting their own username and providing the "view=shared" URL parameter.
        """
        try:
            account_settings = get_account_settings(request, [username], view=request.query_params.get("view"))
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(account_settings[0])

    def partial_update(self, request, username):
        """Update user account or profile information

        **Example Requests**


            Content-Type: application/merge-patch+json

            PATCH /api/user/v1/accounts/{username}

        **Request Body

            {
              "level_of_education": "m",
              "extended_profile":
                [
                    {"field_name": "favorite_beatle", "field_value": {"name": "ringo"}},
                    {"field_name": "conlangs_spoken", "field_value":["Láadan", "Rikchik", "Lojban"]}
                ]
            }

        **Notes regarding `social_links`**

        Requested updates to social_links are automatically merged with previously set links. That is, any newly introduced platforms are add to the previous list. Updated links to pre-existing platforms replace their values in the previous list. Pre-existing platforms can be removed by setting the value of the social_link to an empty string ("").

        **Response Values for PATCH**

        Users can only modify their own account information. If the requesting user does not have the specified username and has staff access, the request returns an HTTP 403 "Forbidden" response. If the requesting user does not have staff access, the request returns an HTTP 404 "Not Found" response to avoid revealing the existence of the account.

        If no user exists with the specified username, an HTTP 404 "Not Found" response is returned.

        If "application/merge-patch+json" is not the specified content type, a 415 "Unsupported Media Type" response is returned.

        If validation errors prevent the update, this method returns a 400 "Bad Request" response that includes a "field_errors" field that lists all error messages. This will happen if an attempt is made to edit any read-only fields.

        If a failure at the time of the update prevents the update, a 400 "Bad Request" error is returned. The JSON collection contains specific errors.

        If the update is successful, updated user account data is returned.
        """
        if request.content_type != MergePatchParser.media_type:
            raise UnsupportedMediaType(request.content_type)

        for key, limit in [
            ('email', settings.EMAIL_CHANGE_RATE_LIMIT),
            ('secondary_email', settings.SECONDARY_EMAIL_RATE_LIMIT)
        ]:
            if request.data.get(key) and limit:
                if is_ratelimited(
                    request=request, group=f"{key}_change_rate_limit", key="user",
                    rate=limit, increment=True,
                ):
                    return Response({"error": "Too many requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            with transaction.atomic():
                update_account_settings(request.user, request.data, username=username)
                account_settings = get_account_settings(request, [username])[0]
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except AccountValidationError as err:
            return Response({"field_errors": err.field_errors}, status=status.HTTP_400_BAD_REQUEST)
        except AccountUpdateError as err:
            return Response(
                {"developer_message": err.developer_message, "user_message": err.user_message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(account_settings)


# pylint: enable=line-too-long


class NameChangeView(ViewSet):
    """
    Viewset to manage profile name change requests.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def create(self, request):
        """
        POST /api/user/v1/accounts/name_change/

        Request a profile name change. This creates a PendingNameChange to be verified later,
        rather than updating the user's profile name directly.

        Example request:
            {
                "name": "Jon Doe"
            }
        """
        user = request.user
        new_name = request.data.get("name", None)
        rationale = f"Name change requested through account API by {user.username}"

        serializer = PendingNameChangeSerializer(data={"new_name": new_name})

        if serializer.is_valid():
            pending_name_change = do_name_change_request(user, new_name, rationale)[0]
            if pending_name_change:
                return Response(status=status.HTTP_201_CREATED)
            else:
                return Response(
                    {"new_name": "The profile name given was identical to the current name."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(status=status.HTTP_400_BAD_REQUEST, data=serializer.errors)

    def confirm(self, request, username):
        """
        POST /api/user/v1/account/name_change/{username}/confirm

        Confirm a name change request for the specified user, and update their profile name.
        """
        if not request.user.is_staff:
            return Response(status=status.HTTP_403_FORBIDDEN)

        user_model = get_user_model()
        user = user_model.objects.get(username=username)
        pending_name_change = get_pending_name_change(user)

        if pending_name_change:
            confirm_name_change(user, pending_name_change)
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            return Response(status=status.HTTP_404_NOT_FOUND)


class AccountDeactivationView(APIView):
    """
    Account deactivation viewset. Currently only supports POST requests.
    Only admins can deactivate accounts.
    """

    permission_classes = (permissions.IsAuthenticated, CanDeactivateUser)

    def post(self, request, username):
        """
        POST /api/user/v1/accounts/{username}/deactivate/

        Marks the user as having no password set for deactivation purposes.
        """
        user = User.objects.get(username=username)
        segment.identify(user.id, {"is_disabled": "true"})
        _set_unusable_password(user)
        return Response(get_account_settings(request, [username])[0])


class DeactivateLogoutView(APIView):
    """
    POST /api/user/v1/accounts/deactivate_logout/
    {
        "password": "example_password",
    }

    **POST Parameters**

      A POST request must include the following parameter.

      * password: Required. The current password of the user being deactivated.

    **POST Response Values**

     If the request does not specify a username or submits a username
     for a non-existent user, the request returns an HTTP 404 "Not Found"
     response.

     If a user who is not a superuser tries to deactivate a user,
     the request returns an HTTP 403 "Forbidden" response.

     If the specified user is successfully deactivated, the request
     returns an HTTP 204 "No Content" response.

     If an unanticipated error occurs, the request returns an
     HTTP 500 "Internal Server Error" response.

    Allows an LMS user to take the following actions:
    -  Change the user's password permanently to Django's unusable password
    -  Log the user out
    - Create a row in the retirement table for that user
    """

    # BearerAuthentication is added here to support account deletion
    # from the mobile app until it moves to JWT Auth.
    # See mobile roadmap issue https://github.com/openedx/edx-platform/issues/33307.
    authentication_classes = (JwtAuthentication, SessionAuthentication, BearerAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        """
        POST /api/user/v1/accounts/deactivate_logout/

        Marks the user as having no password set for deactivation purposes,
        and logs the user out.
        """

        # Ensure the account deletion is not disable
        enable_account_deletion = configuration_helpers.get_value(
            "ENABLE_ACCOUNT_DELETION", settings.FEATURES.get("ENABLE_ACCOUNT_DELETION", False)
        )

        if not enable_account_deletion:
            return Response(status=status.HTTP_403_FORBIDDEN)

        user_model = get_user_model()
        try:
            # Get the username from the request and check that it exists
            verify_user_password_response = self._verify_user_password(request)
            if verify_user_password_response.status_code != status.HTTP_204_NO_CONTENT:
                return verify_user_password_response
            with transaction.atomic():
                user_email = request.user.email
                create_retirement_request_and_deactivate_account(request.user)

                try:
                    # Send notification email to user
                    site = Site.objects.get_current()
                    notification_context = get_base_template_context(site)
                    notification_context.update({"full_name": request.user.profile.name})
                    language_code = request.user.preferences.model.get_value(
                        request.user, LANGUAGE_KEY, default=settings.LANGUAGE_CODE
                    )
                    notification = DeletionNotificationMessage().personalize(
                        recipient=Recipient(lms_user_id=0, email_address=user_email),
                        language=language_code,
                        user_context=notification_context,
                    )
                    ace.send(notification)
                except Exception as exc:
                    log.exception("Error sending out deletion notification email")
                    raise exc

                # Log the user out.
                logout(request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except KeyError:
            log.exception(f"Username not specified {request.user}")
            return Response("Username not specified.", status=status.HTTP_404_NOT_FOUND)
        except user_model.DoesNotExist:
            log.exception(f'The user "{request.user.username}" does not exist.')
            return Response(f'The user "{request.user.username}" does not exist.', status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception(f"500 error deactivating account {exc}")
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _verify_user_password(self, request):
        """
        If the user is logged in and we want to verify that they have submitted the correct password
        for a major account change (for example, retiring this user's account).

        Args:
            request (HttpRequest): A request object where the password should be included in the POST fields.
        """
        try:
            self._check_excessive_login_attempts(request.user)
            user = authenticate(username=request.user.username, password=request.POST["password"], request=request)
            if user:
                if LoginFailures.is_feature_enabled():
                    LoginFailures.clear_lockout_counter(user)
                return Response(status=status.HTTP_204_NO_CONTENT)
            else:
                self._handle_failed_authentication(request.user)
        except AuthFailedError as err:
            log.exception(f"The user password to deactivate was incorrect. {request.user.username}")
            return Response(str(err), status=status.HTTP_403_FORBIDDEN)
        except Exception as err:  # pylint: disable=broad-except
            return Response(f"Could not verify user password: {err}", status=status.HTTP_400_BAD_REQUEST)

    def _check_excessive_login_attempts(self, user):
        """
        See if account has been locked out due to excessive login failures
        """
        if user and LoginFailures.is_feature_enabled():
            if LoginFailures.is_user_locked_out(user):
                raise AuthFailedError(
                    _("This account has been temporarily locked due to excessive login failures. Try again later.")
                )

    def _handle_failed_authentication(self, user):
        """
        Handles updating the failed login count, inactive user notifications, and logging failed authentications.
        """
        if user and LoginFailures.is_feature_enabled():
            LoginFailures.increment_lockout_counter(user)

        raise AuthFailedError(_("Email or password is incorrect."))


def _set_unusable_password(user):
    """
    Helper method for the shared functionality of setting a user's
    password to the unusable password, thus deactivating the account.
    """
    user.set_unusable_password()
    user.save()


class AccountRetirementPartnerReportView(ViewSet):
    """
    Provides API endpoints for managing partner reporting of retired
    users.
    """

    DELETION_COMPLETED_KEY = "deletion_completed"
    ORGS_CONFIG_KEY = "orgs_config"
    ORGS_CONFIG_ORG_KEY = "org"
    ORGS_CONFIG_FIELD_HEADINGS_KEY = "field_headings"
    ORIGINAL_EMAIL_KEY = "original_email"
    ORIGINAL_NAME_KEY = "original_name"
    STUDENT_ID_KEY = "student_id"

    permission_classes = (
        permissions.IsAuthenticated,
        CanRetireUser,
    )
    parser_classes = (JSONParser,)
    serializer_class = UserRetirementStatusSerializer

    @staticmethod
    def _get_orgs_for_user(user):
        """
        Returns a set of orgs that the user has enrollments with
        """
        orgs = set()
        for enrollment in user.courseenrollment_set.all():
            org = enrollment.course_id.org

            # Org can conceivably be blank or this bogus default value
            if org and org != "outdated_entry":
                orgs.add(org)
        return orgs

    def retirement_partner_report(self, request):  # pylint: disable=unused-argument
        """
        POST /api/user/v1/accounts/retirement_partner_report/

        Returns the list of UserRetirementPartnerReportingStatus users
        that are not already being processed and updates their status
        to indicate they are currently being processed.
        """
        retirement_statuses = UserRetirementPartnerReportingStatus.objects.filter(is_being_processed=False).order_by(
            "id"
        )

        retirements = []
        for retirement_status in retirement_statuses:
            retirements.append(self._get_retirement_for_partner_report(retirement_status))

        serializer = UserRetirementPartnerReportSerializer(retirements, many=True)

        retirement_statuses.update(is_being_processed=True)

        return Response(serializer.data)

    def _get_retirement_for_partner_report(self, retirement_status):
        """
        Get the retirement for this retirement_status. The retirement info will be included in the partner report.
        """
        retirement = {
            "user_id": retirement_status.user.pk,
            "original_username": retirement_status.original_username,
            AccountRetirementPartnerReportView.ORIGINAL_EMAIL_KEY: retirement_status.original_email,
            AccountRetirementPartnerReportView.ORIGINAL_NAME_KEY: retirement_status.original_name,
            "orgs": self._get_orgs_for_user(retirement_status.user),
            "created": retirement_status.created,
        }

        return retirement

    @request_requires_username
    def retirement_partner_status_create(self, request):
        """
        PUT /api/user/v1/accounts/retirement_partner_report/

        ```
        {
            'username': 'user_to_retire'
        }
        ```

        Creates a UserRetirementPartnerReportingStatus object for the given user
        as part of the retirement pipeline.
        """
        username = request.data["username"]

        try:
            retirement = UserRetirementStatus.get_retirement_for_retirement_action(username)
            orgs = self._get_orgs_for_user(retirement.user)

            if orgs:
                UserRetirementPartnerReportingStatus.objects.get_or_create(
                    user=retirement.user,
                    defaults={
                        "original_username": retirement.original_username,
                        "original_email": retirement.original_email,
                        "original_name": retirement.original_name,
                    },
                )

            return Response(status=status.HTTP_204_NO_CONTENT)
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

    def retirement_partner_cleanup(self, request):
        """
        POST /api/user/v1/accounts/retirement_partner_report_cleanup/

        [{'original_username': 'user1'}, {'original_username': 'user2'}, ...]

        Deletes UserRetirementPartnerReportingStatus objects for a list of users
        that have been reported on.
        """
        usernames = [u["original_username"] for u in request.data]

        if not usernames:
            return Response("No original_usernames given.", status=status.HTTP_400_BAD_REQUEST)

        retirement_statuses = UserRetirementPartnerReportingStatus.objects.filter(
            is_being_processed=True, original_username__in=usernames
        )

        # Need to de-dupe usernames that differ only by case to find the exact right match
        retirement_statuses_clean = [rs for rs in retirement_statuses if rs.original_username in usernames]

        # During a narrow window learners were able to re-use a username that had been retired if
        # they altered the capitalization of one or more characters. Therefore we can have more
        # than one row returned here (due to our MySQL collation being case-insensitive), and need
        # to disambiguate them in Python, which will respect case in the comparison.
        if len(usernames) != len(retirement_statuses_clean):
            return Response(
                "{} original_usernames given, {} found!\n"
                "Given usernames:\n{}\n"
                "Found UserRetirementReportingStatuses:\n{}".format(
                    len(usernames),
                    len(retirement_statuses_clean),
                    usernames,
                    ", ".join([rs.original_username for rs in retirement_statuses_clean]),
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        retirement_statuses.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class CancelAccountRetirementStatusView(ViewSet):
    """
    Provides API endpoints for canceling retirement process for a user's account.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        CanCancelUserRetirement,
    )

    def cancel_retirement(self, request):
        """
        POST /api/user/v1/accounts/cancel_retirement/

        Cancels the retirement for a user's account.
        This also handles the top level error handling, and permissions.
        """
        try:
            retirement_id = request.data["retirement_id"]
        except KeyError:
            return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": "retirement_id must be specified."})

        try:
            retirement = UserRetirementStatus.objects.get(id=retirement_id)
        except UserRetirementStatus.DoesNotExist:
            return Response(data={"message": "Retirement does not exist!"}, status=status.HTTP_400_BAD_REQUEST)

        if retirement.current_state.state_name != "PENDING":
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": f"Retirement requests can only be cancelled for users in the PENDING state. Current "
                    f"request state for '{retirement.original_username}': "
                    f"{retirement.current_state.state_name}"
                },
            )

        handle_retirement_cancellation(retirement)

        return Response(data={"success": True}, status=status.HTTP_200_OK)


class AccountRetirementStatusView(ViewSet):
    """
    Provides API endpoints for managing the user retirement process.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        CanRetireUser,
    )
    parser_classes = (JSONParser,)
    serializer_class = UserRetirementStatusSerializer

    def retirement_queue(self, request):
        """
        GET /api/user/v1/accounts/retirement_queue/
        {'cool_off_days': 7, 'states': ['PENDING', 'COMPLETE'], 'limit': 500}

        Returns the list of RetirementStatus users in the given states that were
        created in the retirement queue at least `cool_off_days` ago.
        """
        try:
            cool_off_days = int(request.GET["cool_off_days"])
            if cool_off_days < 0:
                raise RetirementStateError("Invalid argument for cool_off_days, must be greater than 0.")

            states = request.GET.getlist("states")
            if not states:
                raise RetirementStateError('Param "states" required with at least one state.')

            state_objs = RetirementState.objects.filter(state_name__in=states)
            if state_objs.count() != len(states):
                found = [s.state_name for s in state_objs]
                raise RetirementStateError(f"Unknown state. Requested: {states} Found: {found}")

            limit = request.GET.get("limit")
            if limit:
                try:
                    limit_count = int(limit)
                except ValueError:
                    return Response(
                        f"Limit could not be parsed: {limit}, please ensure this is an integer",
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            earliest_datetime = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=cool_off_days)

            retirements = (
                UserRetirementStatus.objects.select_related("user", "current_state", "last_state")
                .filter(current_state__in=state_objs, created__lt=earliest_datetime)
                .order_by("id")
            )
            if limit:
                retirements = retirements[:limit_count]
            serializer = UserRetirementStatusSerializer(retirements, many=True)
            return Response(serializer.data)
        # This should only occur on the int() conversion of cool_off_days at this point
        except ValueError:
            return Response("Invalid cool_off_days, should be integer.", status=status.HTTP_400_BAD_REQUEST)
        except KeyError as exc:
            return Response(f"Missing required parameter: {str(exc)}", status=status.HTTP_400_BAD_REQUEST)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)

    def retirements_by_status_and_date(self, request):
        """
        GET /api/user/v1/accounts/retirements_by_status_and_date/
        ?start_date=2018-09-05&end_date=2018-09-07&state=COMPLETE

        Returns a list of UserRetirementStatusSerializer serialized
        RetirementStatus rows in the given state that were created in the
        retirement queue between the dates given. Date range is inclusive,
        so to get one day you would set both dates to that day.
        """
        try:
            start_date = (
                datetime.datetime.strptime(request.GET["start_date"], "%Y-%m-%d")
                .replace(tzinfo=ZoneInfo("UTC"))
            )
            end_date = datetime.datetime.strptime(request.GET["end_date"], "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
            now = datetime.datetime.now(ZoneInfo("UTC"))
            if start_date > now or end_date > now or start_date > end_date:
                raise RetirementStateError("Dates must be today or earlier, and start must be earlier than end.")

            # Add a day to make sure we get all the way to 23:59:59.999, this is compared "lt" in the query
            # not "lte".
            end_date += datetime.timedelta(days=1)
            state = request.GET["state"]

            state_obj = RetirementState.objects.get(state_name=state)

            retirements = (
                UserRetirementStatus.objects.select_related("user", "current_state", "last_state", "user__profile")
                .filter(current_state=state_obj, created__lt=end_date, created__gte=start_date)
                .order_by("id")
            )
            serializer = UserRetirementStatusSerializer(retirements, many=True)
            return Response(serializer.data)
        # This should only occur on the datetime conversion of the start / end dates.
        except ValueError as exc:
            return Response(f"Invalid start or end date: {str(exc)}", status=status.HTTP_400_BAD_REQUEST)
        except KeyError as exc:
            return Response(f"Missing required parameter: {str(exc)}", status=status.HTTP_400_BAD_REQUEST)
        except RetirementState.DoesNotExist:
            return Response("Unknown retirement state.", status=status.HTTP_400_BAD_REQUEST)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, username):  # pylint: disable=unused-argument
        """
        GET /api/user/v1/accounts/{username}/retirement_status/
        Returns the RetirementStatus of a given user, or 404 if that row
        doesn't exist.
        """
        try:
            user = get_potentially_retired_user_by_username(username)
            retirement = UserRetirementStatus.objects.select_related("user", "current_state", "last_state").get(
                user=user
            )
            serializer = UserRetirementStatusSerializer(instance=retirement)
            return Response(serializer.data)
        except (UserRetirementStatus.DoesNotExist, User.DoesNotExist):
            return Response(status=status.HTTP_404_NOT_FOUND)

    @request_requires_username
    def partial_update(self, request):
        """
        PATCH /api/user/v1/accounts/update_retirement_status/

        ```
        {
            'username': 'user_to_retire',
            'new_state': 'LOCKING_COMPLETE',
            'response': 'User account locked and logged out.'
        }
        ```

        Updates the RetirementStatus row for the given user to the new
        status, and append any messages to the message log.

        Note that this implementation DOES NOT use the "merge patch" implementation seen in AccountViewSet.
        The content type for this request is 'application/json'.
        """
        try:
            username = request.data["username"]
            retirements = UserRetirementStatus.objects.filter(original_username=username)

            # During a narrow window learners were able to re-use a username that had been retired if
            # they altered the capitalization of one or more characters. Therefore we can have more
            # than one row returned here (due to our MySQL collation being case-insensitive), and need
            # to disambiguate them in Python, which will respect case in the comparison.
            retirement = None
            if len(retirements) < 1:  # lint-amnesty, pylint: disable=no-else-raise
                raise UserRetirementStatus.DoesNotExist()
            elif len(retirements) >= 1:
                for r in retirements:
                    if r.original_username == username:
                        retirement = r
                        break
                # UserRetirementStatus was found, but it was the wrong case.
                if retirement is None:
                    raise UserRetirementStatus.DoesNotExist()

            retirement.update_state(request.data)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def cleanup(self, request):
        """
        POST /api/user/v1/accounts/retirement_cleanup/

        ```
        {
            'usernames': ['user1', 'user2', ...]
        }
        ```

        Deletes a batch of retirement requests by username.
        """
        try:
            usernames = request.data["usernames"]

            if not isinstance(usernames, list):
                raise TypeError("Usernames should be an array.")

            complete_state = RetirementState.objects.get(state_name="COMPLETE")
            retirements = UserRetirementStatus.objects.filter(
                original_username__in=usernames, current_state=complete_state
            )

            # Sanity check that they're all valid usernames in the right state
            if len(usernames) != len(retirements):
                raise UserRetirementStatus.DoesNotExist("Not all usernames exist in the COMPLETE state.")

            retirements.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except (RetirementStateError, UserRetirementStatus.DoesNotExist, TypeError) as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LMSAccountRetirementView(ViewSet):
    """
    Provides an API endpoint for retiring a user in the LMS.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        CanRetireUser,
    )
    parser_classes = (JSONParser,)

    @request_requires_username
    def post(self, request):
        """
        POST /api/user/v1/accounts/retire_misc/

        ```
        {
            'username': 'user_to_retire'
        }
        ```

        Retires the user with the given username in the LMS.
        """

        username = request.data["username"]

        try:
            retirement = UserRetirementStatus.get_retirement_for_retirement_action(username)
            RevisionPluginRevision.retire_user(retirement.user)
            ArticleRevision.retire_user(retirement.user)
            PendingNameChange.delete_by_user_value(retirement.user, field="user")
            ManualEnrollmentAudit.retire_manual_enrollments(retirement.user, retirement.retired_email)

            CreditRequest.retire_user(retirement)
            ApiAccessRequest.retire_user(retirement.user)
            CreditRequirementStatus.retire_user(retirement)

            # This signal allows code in higher points of LMS to retire the user as necessary
            USER_RETIRE_LMS_MISC.send(sender=self.__class__, user=retirement.user)

            # This signal allows code in higher points of LMS to unsubscribe the user
            # from various types of mailings.
            USER_RETIRE_MAILINGS.send(
                sender=self.__class__,
                email=retirement.original_email,
                new_email=retirement.retired_email,
                user=retirement.user,
            )
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountRetirementView(ViewSet):
    """
    Provides API endpoint for retiring a user.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        CanRetireUser,
    )
    parser_classes = (JSONParser,)

    @request_requires_username
    def post(self, request):
        """
        POST /api/user/v1/accounts/retire/

        ```
        {
            'username': 'user_to_retire'
        }
        ```

        Retires the user with the given username.  This includes retiring this username, the associated email address,
        and any other PII associated with this user.
        """
        username = request.data["username"]

        try:
            retirement_status = UserRetirementStatus.get_retirement_for_retirement_action(username)
            user = retirement_status.user
            retired_username = retirement_status.retired_username or get_retired_username_by_username(username)
            retired_email = retirement_status.retired_email or get_retired_email_by_email(user.email)
            original_email = retirement_status.original_email

            # Retire core user/profile information
            self.clear_pii_from_userprofile(user)
            self.delete_users_profile_images(user)
            self.delete_users_country_cache(user)

            # Retire user information from any certificate records associated with the learner
            self.clear_pii_from_certificate_records(user)

            # Retire data from Enterprise models
            self.retire_users_data_sharing_consent(username, retired_username)
            self.retire_sapsf_data_transmission(user)
            self.retire_degreed_data_transmission(user)
            self.retire_user_from_pending_enterprise_customer_user(user, retired_email)
            self.retire_entitlement_support_detail(user)

            # Retire misc. models that may contain PII of this user
            PendingEmailChange.delete_by_user_value(user, field="user")
            UserOrgTag.delete_by_user_value(user, field="user")

            # Retire any objects linked to the user via their original email
            CourseEnrollmentAllowed.delete_by_user_value(original_email, field="email")
            UnregisteredLearnerCohortAssignments.delete_by_user_value(original_email, field="email")

            # This signal allows code in higher points of LMS to retire the user as necessary
            USER_RETIRE_LMS_CRITICAL.send(sender=self.__class__, user=user)

            user.first_name = ""
            user.last_name = ""
            user.is_active = False
            user.username = retired_username
            user.save()
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def clear_pii_from_userprofile(user):
        """
        For the given user, sets all of the user's profile fields to some retired value. This also deletes all
        ``SocialLink`` objects associated with this user's profile.
        """
        for model_field, value_to_assign in USER_PROFILE_PII.items():
            setattr(user.profile, model_field, value_to_assign)

        user.profile.save()
        user.profile.social_links.all().delete()

    @staticmethod
    def delete_users_profile_images(user):
        set_has_profile_image(user.username, False)
        names_of_profile_images = get_profile_image_names(user.username)
        remove_profile_images(names_of_profile_images)

    @staticmethod
    def delete_users_country_cache(user):
        cache_key = UserProfile.country_cache_key_name(user.id)
        cache.delete(cache_key)

    @staticmethod
    def retire_users_data_sharing_consent(username, retired_username):
        DataSharingConsent.objects.filter(username=username).update(username=retired_username)

    @staticmethod
    def retire_sapsf_data_transmission(user):  # lint-amnesty, pylint: disable=missing-function-docstring
        for ent_user in EnterpriseCustomerUser.objects.filter(user_id=user.id):
            for enrollment in EnterpriseCourseEnrollment.objects.filter(enterprise_customer_user=ent_user):
                audits = SapSuccessFactorsLearnerDataTransmissionAudit.objects.filter(
                    enterprise_course_enrollment_id=enrollment.id
                )
                audits.update(sapsf_user_id="")

    @staticmethod
    def retire_degreed_data_transmission(user):  # lint-amnesty, pylint: disable=missing-function-docstring
        for ent_user in EnterpriseCustomerUser.objects.filter(user_id=user.id):
            for enrollment in EnterpriseCourseEnrollment.objects.filter(enterprise_customer_user=ent_user):
                audits = DegreedLearnerDataTransmissionAudit.objects.filter(
                    enterprise_course_enrollment_id=enrollment.id
                )
                audits.update(degreed_user_email="")

    @staticmethod
    def retire_user_from_pending_enterprise_customer_user(user, retired_email):
        PendingEnterpriseCustomerUser.objects.filter(user_email=user.email).update(user_email=retired_email)

    @staticmethod
    def retire_entitlement_support_detail(user):
        """
        Updates all CourseEntitleSupportDetail records for the given user to have an empty ``comments`` field.
        """
        for entitlement in CourseEntitlement.objects.filter(user_id=user.id):
            entitlement.courseentitlementsupportdetail_set.all().update(comments="")

    @staticmethod
    def clear_pii_from_certificate_records(user):
        """
        Calls a utility function in the `certificates` Django app responsible for removing PII (name) from any
        certificate records associated with the learner being retired.
        """
        clear_pii_from_certificate_records_for_user(user)


class UsernameReplacementView(APIView):
    """
    WARNING: This API is only meant to be used as part of a larger job that
    updates usernames across all services. DO NOT run this alone or users will
    not match across the system and things will be broken.

    API will receive a list of current usernames and their requested new
    username. If their new username is taken, it will randomly assign a new username.

    This API will be called first, before calling the APIs in other services as this
    one handles the checks on the usernames provided.
    """

    permission_classes = (permissions.IsAuthenticated, CanReplaceUsername)

    def post(self, request):
        """
        POST /api/user/v1/accounts/replace_usernames/
        ```
        {
            "username_mappings": [
                {"current_username_1": "desired_username_1"},
                {"current_username_2": "desired_username_2"}
            ]
        }
        ```

        **POST Parameters**

        A POST request must include the following parameter.

        * username_mappings: Required. A list of objects that map the current username (key)
          to the desired username (value)

        **POST Response Values**

        As long as data validation passes, the request will return a 200 with a new mapping
        of old usernames (key) to new username (value)

        ```
        {
            "successful_replacements": [
                {"old_username_1": "new_username_1"}
            ],
            "failed_replacements": [
                {"old_username_2": "new_username_2"}
            ]
        }
        ```

        """

        # (model_name, column_name)
        MODELS_WITH_USERNAME = (
            ("auth.user", "username"),
            ("consent.DataSharingConsent", "username"),
            ("consent.HistoricalDataSharingConsent", "username"),
            ("credit.CreditEligibility", "username"),
            ("credit.CreditRequest", "username"),
            ("credit.CreditRequirementStatus", "username"),
            ("user_api.UserRetirementPartnerReportingStatus", "original_username"),
            ("user_api.UserRetirementStatus", "original_username"),
        )
        UNIQUE_SUFFIX_LENGTH = getattr(settings, "SOCIAL_AUTH_UUID_LENGTH", 4)

        username_mappings = request.data.get("username_mappings")
        replacement_locations = self._load_models(MODELS_WITH_USERNAME)

        if not self._has_valid_schema(username_mappings):
            raise ValidationError("Request data does not match schema")

        successful_replacements, failed_replacements = [], []

        for username_pair in username_mappings:
            current_username = list(username_pair.keys())[0]
            desired_username = list(username_pair.values())[0]
            new_username = self._generate_unique_username(desired_username, suffix_length=UNIQUE_SUFFIX_LENGTH)
            successfully_replaced = self._replace_username_for_all_models(
                current_username, new_username, replacement_locations
            )
            if successfully_replaced:
                successful_replacements.append({current_username: new_username})
            else:
                failed_replacements.append({current_username: new_username})
        return Response(
            status=status.HTTP_200_OK,
            data={"successful_replacements": successful_replacements, "failed_replacements": failed_replacements},
        )

    def _load_models(self, models_with_fields):
        """Takes tuples that contain a model path and returns the list with a loaded version of the model"""
        try:
            replacement_locations = [(apps.get_model(model), column) for (model, column) in models_with_fields]
        except LookupError:
            log.exception("Unable to load models for username replacement")
            raise
        return replacement_locations

    def _has_valid_schema(self, post_data):
        """Verifies the data is a list of objects with a single key:value pair"""
        if not isinstance(post_data, list):
            return False
        for obj in post_data:
            if not (isinstance(obj, dict) and len(obj) == 1):
                return False
        return True

    def _generate_unique_username(self, desired_username, suffix_length=4):
        """
        Generates a unique username.
        If the desired username is available, that will be returned.
        Otherwise it will generate unique suffixes to the desired username until it is an available username.
        """
        new_username = desired_username
        # Keep checking usernames in case desired_username + random suffix is already taken
        while True:
            if User.objects.filter(username=new_username).exists():
                # adding a dash between user-supplied and system-generated values to avoid weird combinations
                new_username = desired_username + "-" + username_suffix_generator(suffix_length)
            else:
                break
        return new_username

    def _replace_username_for_all_models(self, current_username, new_username, replacement_locations):
        """
        Replaces current_username with new_username for all (model, column) pairs in replacement locations.
        Returns if it was successful or not. Will return successful even if no matching

        TODO: Determine if logs of username are a PII issue.
        """
        try:
            with transaction.atomic():
                num_rows_changed = 0
                for model, column in replacement_locations:
                    num_rows_changed += model.objects.filter(**{column: current_username}).update(
                        **{column: new_username}
                    )
        except Exception as exc:  # pylint: disable=broad-except
            log.exception(
                "Unable to change username from %s to %s. Failed on table %s because %s",
                current_username,
                new_username,
                model.__class__.__name__,  # Retrieves the model name that it failed on
                exc,
            )
            return False
        if num_rows_changed == 0:
            log.info(
                "Unable to change username from %s to %s because %s doesn't exist.",
                current_username,
                new_username,
                current_username,
            )
        else:
            log.info(
                "Successfully changed username from %s to %s.",
                current_username,
                new_username,
            )
        return True
