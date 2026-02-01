# pylint: disable=missing-docstring
"""
Programmatic integration point for User API Accounts sub-application
"""

import datetime
import logging
import re
from typing import Optional

from django import forms
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import ValidationError, validate_email
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language
from eventtracking import tracker
from zoneinfo import ZoneInfo

from common.djangoapps.student import views as student_views
from common.djangoapps.student.models import (
    AccountRecovery,
    User,
    UserProfile,
    email_exists_or_retired,
    username_exists_or_retired
)
from common.djangoapps.util.model_utils import emit_settings_changed_event
from common.djangoapps.util.password_policy_validators import validate_password
from lms.djangoapps.certificates.api import get_certificates_for_user
from lms.djangoapps.certificates.data import CertificateStatuses
from openedx.core.djangoapps.embargo.models import GlobalRestrictedCountry
from openedx.core.djangoapps.enrollments.api import get_verified_enrollments
from openedx.core.djangoapps.user_api import accounts, errors, helpers
from openedx.core.djangoapps.user_api.errors import (
    AccountUpdateError,
    AccountValidationError,
    PreferenceValidationError
)
from openedx.core.djangoapps.user_api.preferences.api import update_user_preferences
from openedx.core.djangoapps.user_authn.utils import check_pwned_password
from openedx.core.djangoapps.user_authn.views.registration_form import validate_name, validate_username
from openedx.core.lib.api.view_utils import add_serializer_errors
from openedx.features.enterprise_support.utils import get_enterprise_readonly_account_fields
from openedx.features.name_affirmation_api.utils import is_name_affirmation_installed

from .serializers import AccountLegacyProfileSerializer, AccountUserSerializer, UserReadOnlySerializer, _visible_fields

name_affirmation_installed = is_name_affirmation_installed()
if name_affirmation_installed:
    # pylint: disable=import-error
    from edx_name_affirmation.name_change_validator import NameChangeValidator

logger = logging.getLogger(__name__)

# Public access point for this function.
visible_fields = _visible_fields


@helpers.intercept_errors(errors.UserAPIInternalError, ignore_errors=[errors.UserAPIRequestError])
def get_account_settings(request, usernames=None, configuration=None, view=None):
    """Returns account information for a user serialized as JSON.

    Note:
        If `request.user.username` != `username`, this method will return differing amounts of information
        based on who `request.user` is and the privacy settings of the user associated with `username`.

    Args:
        request (Request): The request object with account information about the requesting user.
            Only the user with username `username` or users with "is_staff" privileges can get full
            account information. Other users will get the account fields that the user has elected to share.
        usernames (list): Optional list of usernames for the desired account information. If not
            specified, `request.user.username` is assumed.
        configuration (dict): an optional configuration specifying which fields in the account
            can be shared, and the default visibility settings. If not present, the setting value with
            key ACCOUNT_VISIBILITY_CONFIGURATION is used.
        view (str): An optional string allowing "is_staff" users and users requesting their own
            account information to get just the fields that are shared with everyone. If view is
            "shared", only shared account information will be returned, regardless of `request.user`.

    Returns:
         A list of users account details.

    Raises:
         errors.UserNotFound: no user with username `username` exists (or `request.user.username` if
            `username` is not specified)
         errors.UserAPIInternalError: the operation failed due to an unexpected error.

    """
    requesting_user = request.user
    usernames = usernames or [requesting_user.username]

    requested_users = User.objects.select_related('profile').filter(username__in=usernames)
    if not requested_users:
        raise errors.UserNotFound()

    serialized_users = []
    for user in requested_users:
        has_full_access = requesting_user.is_staff or requesting_user.username == user.username
        if has_full_access and view != 'shared':
            admin_fields = settings.ACCOUNT_VISIBILITY_CONFIGURATION.get('admin_fields')
        else:
            admin_fields = None
        serialized_users.append(UserReadOnlySerializer(
            user,
            configuration=configuration,
            custom_fields=admin_fields,
            context={'request': request}
        ).data)

    return serialized_users


@helpers.intercept_errors(errors.UserAPIInternalError, ignore_errors=[errors.UserAPIRequestError])
def update_account_settings(requesting_user, update, username=None, extended_profile_form=None):
    """Update user account information.

    Note:
        It is up to the caller of this method to enforce the contract that this method is only called
        with the user who made the request.

    Arguments:
        requesting_user (User): The user requesting to modify account information. Only the user with username
            'username' has permissions to modify account information.
        update (dict): The updated account field values.
        username (str): Optional username specifying which account should be updated. If not specified,
            `requesting_user.username` is assumed.
        extended_profile_form (Optional[forms.Form]): Optional validated extended profile form instance.

    Raises:
        errors.UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
        errors.UserNotAuthorized: the requesting_user does not have access to change the account
            associated with `username`
        errors.AccountValidationError: the update was not attempted because validation errors were found with
            the supplied update
        errors.AccountUpdateError: the update could not be completed. Note that if multiple fields are updated at the
            same time, some parts of the update may have been successful, even if an errors.AccountUpdateError is
            returned; in particular, the user account (not including e-mail address) may have successfully been updated,
            but then the e-mail change request, which is processed last, may throw an error.
        errors.UserAPIInternalError: the operation failed due to an unexpected error.

    """
    # Get user
    if username is None:
        username = requesting_user.username
    if requesting_user.username != username:
        raise errors.UserNotAuthorized()
    user, user_profile = _get_user_and_profile(username)

    # Validate fields to update
    field_errors = {}
    _validate_read_only_fields(user, update, field_errors)

    user_serializer = AccountUserSerializer(user, data=update)
    legacy_profile_serializer = AccountLegacyProfileSerializer(user_profile, data=update)
    for serializer in user_serializer, legacy_profile_serializer:
        add_serializer_errors(serializer, update, field_errors)

    _validate_email_change(user, update, field_errors)
    _validate_secondary_email(user, update, field_errors)
    if (
        settings.FEATURES.get('EMBARGO', False) and
        GlobalRestrictedCountry.is_country_restricted(update.get('country', ''))
    ):
        field_errors['country'] = {
            'developer_message': 'Country is disabled for registration',
            'user_message': 'This country cannot be selected for user registration'
        }

    old_name = _validate_name_change(user_profile, update, field_errors)
    old_language_proficiencies = _get_old_language_proficiencies_if_updating(user_profile, update)

    if field_errors:
        raise errors.AccountValidationError(field_errors)

    # Save requested changes
    try:
        for serializer in user_serializer, legacy_profile_serializer:
            serializer.save()

        _update_preferences_if_needed(update, requesting_user, user)
        _notify_language_proficiencies_update_if_needed(update, user, user_profile, old_language_proficiencies)
        _store_old_name_if_needed(old_name, user_profile, requesting_user)
        _update_extended_profile_if_needed(update, user_profile, extended_profile_form)
        _update_state_if_needed(update, user_profile)

    except PreferenceValidationError as err:
        raise AccountValidationError(err.preference_errors)  # lint-amnesty, pylint: disable=raise-missing-from
    except (AccountUpdateError, AccountValidationError) as err:
        raise err
    except Exception as err:
        raise AccountUpdateError(  # lint-amnesty, pylint: disable=raise-missing-from
            f"Error thrown when saving account updates: '{str(err)}'"
        )

    _send_email_change_requests_if_needed(update, user)


def _validate_read_only_fields(user, data, field_errors):
    # Check for fields that are not editable. Marking them read-only causes them to be ignored, but we wish to 400.
    read_only_fields = set(data.keys()).intersection(
        # Remove email since it is handled separately below when checking for changing_email.
        (set(AccountUserSerializer.get_read_only_fields()) - {"email"}) |
        set(AccountLegacyProfileSerializer.get_read_only_fields() or set()) |
        get_enterprise_readonly_account_fields(user)
    )

    for read_only_field in read_only_fields:
        field_errors[read_only_field] = {
            "developer_message": "This field is not editable via this API",
            "user_message": _("The '{field_name}' field cannot be edited.").format(field_name=read_only_field)
        }
        del data[read_only_field]


def _validate_email_change(user, data, field_errors):
    # If user has requested to change email, we must call the multi-step process to handle this.
    # It is not handled by the serializer (which considers email to be read-only).
    if "email" not in data:
        return

    if not settings.FEATURES['ALLOW_EMAIL_ADDRESS_CHANGE']:
        raise AccountUpdateError("Email address changes have been disabled by the site operators.")

    new_email = data["email"]
    try:
        student_views.validate_new_email(user, new_email)
    except ValueError as err:
        field_errors["email"] = {
            "developer_message": f"Error thrown from validate_new_email: '{str(err)}'",
            "user_message": str(err)
        }
        return

    # Don't process with sending email to given new email, if it is already associated with
    # an account. User must see same success message with no error.
    # This is so that this endpoint cannot be used to determine if an email is valid or not.
    if email_exists_or_retired(new_email):
        del data["email"]


def _validate_secondary_email(user, data, field_errors):
    if "secondary_email" not in data:
        return

    secondary_email = data["secondary_email"]

    try:
        student_views.validate_secondary_email(user, secondary_email)
    except ValueError as err:
        field_errors["secondary_email"] = {
            "developer_message": f"Error thrown from validate_secondary_email: '{str(err)}'",
            "user_message": str(err)
        }
    else:
        # Don't process with sending email to given new email, if it is already associated with
        # an account. User must see same success message with no error.
        # This is so that this endpoint cannot be used to determine if an email is valid or not.
        if email_exists_or_retired(secondary_email):
            del data["secondary_email"]


def _validate_name_change(user_profile, data, field_errors):
    # If user has requested to change name, store old name because we must update associated metadata
    # after the save process is complete.
    if "name" not in data:
        return None

    old_name = user_profile.name
    new_name = data['name']

    if old_name == new_name:
        return None

    try:
        validate_name(new_name)
    except ValidationError as err:
        field_errors["name"] = {
            "developer_message": f"Error thrown from validate_name: '{err.message}'",
            "user_message": err.message
        }
        return None

    if _does_name_change_require_verification(user_profile, old_name, new_name):
        err_msg = 'This name change requires ID verification.'
        field_errors['name'] = {
            'developer_message': err_msg,
            'user_message': err_msg
        }
        return None

    return old_name


def _does_name_change_require_verification(user_profile, old_name, new_name):
    """
    If name change requires ID verification, do not update it through this API.
    """
    if not name_affirmation_installed:
        return False

    profile_meta = user_profile.get_meta()
    old_names_list = profile_meta['old_names'] if 'old_names' in profile_meta else []

    user = user_profile.user

    # We only want to validate on a list of passing certificates for the learner. A learner may have
    # a certificate in a non-passing status, and we do not have to require ID verification based on certificates
    # that are not passing.
    passing_certs = filter(
        lambda cert: CertificateStatuses.is_passing_status(cert["status"]),
        get_certificates_for_user(user.username)
    )
    num_passing_certs = len(list(passing_certs))

    # We check whether the learner has active verified enrollments because we do not want to
    # require the learner to perform ID verification if the learner is not enrolled in a verified mode
    # in any courses. The learner will not be able to complete ID verification without being enrolled in
    # at least one seat.
    has_verified_enrollments = len(get_verified_enrollments(user.username)) > 0

    validator = NameChangeValidator(old_names_list, num_passing_certs, old_name, new_name)

    return not validator.validate() and has_verified_enrollments


def _get_old_language_proficiencies_if_updating(user_profile, data):
    if "language_proficiencies" in data:
        return list(user_profile.language_proficiencies.values('code'))


def _update_preferences_if_needed(data, requesting_user, user):
    if 'account_privacy' in data:
        update_user_preferences(
            requesting_user, {'account_privacy': data["account_privacy"]}, user
        )


def _notify_language_proficiencies_update_if_needed(data, user, user_profile, old_language_proficiencies):
    if "language_proficiencies" in data:
        new_language_proficiencies = data["language_proficiencies"]
        emit_settings_changed_event(
            user=user,
            db_table=user_profile.language_proficiencies.model._meta.db_table,
            changed_fields={
                "language_proficiencies": (
                    old_language_proficiencies,
                    new_language_proficiencies,
                )
            }
        )


def _update_extended_profile_if_needed(
    data: dict, user_profile: UserProfile, extended_profile_form: Optional[forms.Form]
) -> None:
    """
    Update the extended profile information if present in the data.

    This function handles two types of extended profile updates:
    1. Updates the user profile meta fields with extended_profile data
    2. Saves the extended profile form data to the extended profile model if a validated form is provided

    Args:
        data (dict): Dictionary containing the update data, may include 'extended_profile' key
        user_profile (UserProfile): The UserProfile instance to update
        extended_profile_form (Optional[forms.Form]): The validated extended profile form
            containing extended profile data, or None if no extended profile form is provided

    Note:
        If `extended_profile` is present in data, the function will:
        - Extract `field_name` and `field_value` pairs from extended_profile list
        - Update the `user_profile.meta` dictionary with new values
        - Save the updated user_profile

        If `extended_profile_form` is provided and valid, the function will:
        - Save the form data to the extended profile model
        - Associate the model instance with the user if it's a new instance
        - Log any errors that occur during the save process
    """
    if "extended_profile" in data:
        meta = user_profile.get_meta()
        new_extended_profile = data["extended_profile"]
        for field in new_extended_profile:
            field_name = field["field_name"]
            new_value = field["field_value"]
            meta[field_name] = new_value
        user_profile.set_meta(meta)
        user_profile.save()

    if extended_profile_form:
        try:
            with transaction.atomic():
                # Use commit=False to create the model instance in memory without saving to DB yet.
                # This allows us to set the user field before persisting, which is necessary because:
                # 1. The form validates and creates the instance with form data
                # 2. For new profiles, the user field isn't in the form data
                # 3. We need to assign the user programmatically before the database save
                # 4. If we called save() directly, it would fail with integrity errors for new profiles
                extended_profile = extended_profile_form.save(commit=False)
                if not hasattr(extended_profile, "user") or extended_profile.user is None:
                    extended_profile.user = user_profile.user
                # Now persist the instance with the user field properly set
                extended_profile.save()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error saving extended profile model: %s", e)


def _update_state_if_needed(data, user_profile):
    # If the country was changed to something other than US, remove the state.
    if "country" in data and data['country'] != UserProfile.COUNTRY_WITH_STATES:
        user_profile.state = None
        user_profile.save()


def _store_old_name_if_needed(old_name, user_profile, requesting_user):
    # If the name was changed, store information about the change operation. This is outside of the
    # serializer so that we can store who requested the change.
    if old_name:
        meta = user_profile.get_meta()
        if 'old_names' not in meta:
            meta['old_names'] = []
        meta['old_names'].append([
            old_name,
            f"Name change requested through account API by {requesting_user.username}",
            datetime.datetime.now(ZoneInfo("UTC")).isoformat()
        ])
        user_profile.set_meta(meta)
        user_profile.save()


def _send_email_change_requests_if_needed(data, user):
    new_email = data.get("email")
    if new_email:
        try:
            student_views.do_email_change_request(user, new_email)
        except ValueError as err:
            raise AccountUpdateError(  # lint-amnesty, pylint: disable=raise-missing-from
                f"Error thrown from do_email_change_request: '{str(err)}'",
                user_message=str(err)
            )

    new_secondary_email = data.get("secondary_email")
    if new_secondary_email:
        try:
            student_views.do_email_change_request(
                user=user,
                new_email=new_secondary_email,
                secondary_email_change_request=True,
            )
        except ValueError as err:
            raise AccountUpdateError(  # lint-amnesty, pylint: disable=raise-missing-from
                f"Error thrown from do_email_change_request: '{str(err)}'",
                user_message=str(err)
            )


def get_name_validation_error(name):
    """Get the built-in validation error message for when
    the user's real name is invalid in some way (we wonder how).

    :param name: The proposed user's real name.
    :return: Validation error message.

    """

    def contains_html(value):
        """
        Validator method to check whether name contains html tags
        """
        regex = re.compile('(<|>)', re.UNICODE)
        return bool(regex.search(value))

    def contains_url(value):
        """
        Validator method to check whether full name contains url
        """
        regex = re.findall(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))*', value)
        return bool(regex)

    if name:
        # Validation for the name length
        if len(name) > 255:
            return _("Full name can't be longer than 255 symbols")

        return _('Enter a valid name') if (contains_html(name) or contains_url(name)) else ''
    else:
        return accounts.REQUIRED_FIELD_NAME_MSG


def get_username_validation_error(username):
    """Get the built-in validation error message for when
    the username is invalid in some way.

    :param username: The proposed username (unicode).
    :param default: The message to default to in case of no error.
    :return: Validation error message.

    """
    return _validate(_validate_username, errors.AccountUsernameInvalid, username)


def get_email_validation_error(email):
    """Get the built-in validation error message for when
    the email is invalid in some way.

    :param email: The proposed email (unicode).
    :param default: The message to default to in case of no error.
    :return: Validation error message.

    """
    return _validate(_validate_email, errors.AccountEmailInvalid, email)


def get_secondary_email_validation_error(email):
    """
    Get the built-in validation error message for when the email is invalid in some way.

    Arguments:
        email (str): The proposed email (unicode).
    Returns:
        (str): Validation error message.

    """
    return _validate(_validate_secondary_email_doesnt_exist, errors.AccountEmailAlreadyExists, email)


def get_confirm_email_validation_error(confirm_email, email):
    """Get the built-in validation error message for when
    the confirmation email is invalid in some way.

    :param confirm_email: The proposed confirmation email (unicode).
    :param email: The email to match (unicode).
    :param default: THe message to default to in case of no error.
    :return: Validation error message.

    """
    return _validate(_validate_confirm_email, errors.AccountEmailInvalid, confirm_email, email)


def get_password_validation_error(password, username=None, email=None, reset_password_page=False):
    """Get the built-in validation error message for when
    the password is invalid in some way.

    :param password: The proposed password (unicode).
    :param username: The username associated with the user's account (unicode).
    :param email: The email associated with the user's account (unicode).
    :param reset_password_page: The flag that determines the validation page (bool).
    :return: Validation error message.

    """
    return _validate(_validate_password, errors.AccountPasswordInvalid, password, username, email, reset_password_page)


def get_country_validation_error(country):
    """Get the built-in validation error message for when
    the country is invalid in some way.

    :param country: The proposed country.
    :return: Validation error message.

    """
    return _validate(_validate_country, errors.AccountCountryInvalid, country)


def get_username_existence_validation_error(username):
    """Get the built-in validation error message for when
    the username has an existence conflict.

    :param username: The proposed username (unicode).
    :param default: The message to default to in case of no error.
    :return: Validation error message.

    """
    return _validate(_validate_username_doesnt_exist, errors.AccountUsernameAlreadyExists, username)


def get_email_existence_validation_error(email):
    """Get the built-in validation error message for when
    the email has an existence conflict.

    :param email: The proposed email (unicode).
    :param default: The message to default to in case of no error.
    :return: Validation error message.

    """
    return _validate(_validate_email_doesnt_exist, errors.AccountEmailAlreadyExists, email)


def get_profile_images(user_profile, user, request=None):
    """
    Returns metadata about a user's profile image.

    The output is a dict that looks like:

    {
        "has_image": False,
        "image_url_full": "http://testserver/static/default_500.png",
        "image_url_large": "http://testserver/static/default_120.png",
        "image_url_medium": "http://testserver/static/default_50.png",
        "image_url_small": "http://testserver/static/default_30.png",
    }
    """
    return AccountLegacyProfileSerializer.get_profile_image(user_profile, user, request)


def _get_user_and_profile(username):
    """
    Helper method to return the legacy user and profile objects based on username.
    """
    try:
        existing_user = User.objects.get(username=username)
    except ObjectDoesNotExist:
        raise errors.UserNotFound()  # lint-amnesty, pylint: disable=raise-missing-from

    existing_user_profile, _ = UserProfile.objects.get_or_create(user=existing_user)

    return existing_user, existing_user_profile


def _validate(validation_func, err, *args):
    """Generic validation function that returns default on
    no errors, but the message associated with the err class
    otherwise. Passes all other arguments into the validation function.

    :param validation_func: The function used to perform validation.
    :param err: The error class to catch.
    :param args: The arguments to pass into the validation function.
    :return: Validation error message, or empty string if no error.

    """
    try:
        validation_func(*args)
    except err as validation_err:
        return str(validation_err)
    return ''


def _validate_username(username):
    """Validate the username.

    Arguments:
        username (unicode): The proposed username.

    Returns:
        None

    Raises:
        errors.AccountUsernameInvalid

    """
    try:
        _validate_unicode(username)
        _validate_type(username, str, accounts.USERNAME_BAD_TYPE_MSG)
        _validate_length(
            username,
            accounts.USERNAME_MIN_LENGTH,
            accounts.USERNAME_MAX_LENGTH,
            accounts.USERNAME_BAD_LENGTH_MSG
        )
        with override_language('en'):
            # `validate_username` provides a proper localized message, however the API needs only the English
            # message by convention.
            validate_username(username)
    except (UnicodeError, errors.AccountDataBadType, errors.AccountDataBadLength) as username_err:
        raise errors.AccountUsernameInvalid(str(username_err))
    except ValidationError as validation_err:
        raise errors.AccountUsernameInvalid(validation_err.message)


def _validate_email(email):
    """Validate the format of the email address.

    Arguments:
        email (unicode): The proposed email.

    Returns:
        None

    Raises:
        errors.AccountEmailInvalid

    """
    try:
        _validate_unicode(email)
        _validate_type(email, str, accounts.EMAIL_BAD_TYPE_MSG)
        _validate_length(email, accounts.EMAIL_MIN_LENGTH, accounts.EMAIL_MAX_LENGTH, accounts.EMAIL_BAD_LENGTH_MSG)
        validate_email.message = accounts.AUTHN_EMAIL_INVALID_MSG
        validate_email(email)
    except (UnicodeError, errors.AccountDataBadType, errors.AccountDataBadLength) as invalid_email_err:
        raise errors.AccountEmailInvalid(str(invalid_email_err))
    except ValidationError as validation_err:
        raise errors.AccountEmailInvalid(validation_err.message)


def _validate_confirm_email(confirm_email, email):
    """Validate the confirmation email field.

    :param confirm_email: The proposed confirmation email. (unicode)
    :param email: The email to match. (unicode)
    :return: None

    """
    if not confirm_email or confirm_email != email:
        raise errors.AccountEmailInvalid(accounts.REQUIRED_FIELD_CONFIRM_EMAIL_MSG)


def _validate_password(password, username=None, email=None, reset_password_page=False):
    """Validate the format of the user's password.

    Passwords cannot be the same as the username of the account,
    so we create a temp_user using the username and email to test the password against.
    This user is never saved.

    Arguments:
        password (unicode): The proposed password.
        username (unicode): The username associated with the user's account.
        email (unicode): The email associated with the user's account.
        reset_password_page (bool): The flag that determines the validation page.

    Returns:
        None

    Raises:
        errors.AccountPasswordInvalid

    """
    try:
        _validate_type(password, str, accounts.PASSWORD_BAD_TYPE_MSG)
        temp_user = User(username=username, email=email) if username else None
        validate_password(password, user=temp_user)
    except errors.AccountDataBadType as invalid_password_err:
        raise errors.AccountPasswordInvalid(str(invalid_password_err))
    except ValidationError as validation_err:
        raise errors.AccountPasswordInvalid(' '.join(validation_err.messages))

    if (
        (settings.ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY and reset_password_page) or
        (settings.ENABLE_AUTHN_REGISTER_HIBP_POLICY and not reset_password_page)
    ):
        pwned_properties = check_pwned_password(password)
        if pwned_properties.get('vulnerability', 'no') == 'yes':
            if reset_password_page is False:
                pwned_properties['user_request_page'] = 'registration'
                tracker.emit('edx.bi.user.pwned.password.status', pwned_properties)
            if (
                reset_password_page or
                pwned_properties.get('frequency', 0) >= settings.HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD
            ):
                raise errors.AccountPasswordInvalid(accounts.AUTHN_PASSWORD_COMPROMISED_MSG)


def _validate_country(country):
    """Validate the country selection.

    :param country: The proposed country.
    :return: None

    """
    if country == '' or country == '--':  # lint-amnesty, pylint: disable=consider-using-in
        raise errors.AccountCountryInvalid(accounts.REQUIRED_FIELD_COUNTRY_MSG)


def _validate_username_doesnt_exist(username):
    """Validate that the username is not associated with an existing user.

    :param username: The proposed username (unicode).
    :return: None
    :raises: errors.AccountUsernameAlreadyExists
    """
    if username is not None and username_exists_or_retired(username):
        # lint-amnesty, pylint: disable=translation-of-non-string
        raise errors.AccountUsernameAlreadyExists(_(accounts.AUTHN_USERNAME_CONFLICT_MSG))


def _validate_email_doesnt_exist(email):
    """Validate that the email is not associated with an existing user.

    :param email: The proposed email (unicode).
    :return: None
    :raises: errors.AccountEmailAlreadyExists
    """
    error_message = accounts.AUTHN_EMAIL_CONFLICT_MSG

    if email is not None and email_exists_or_retired(email):
        raise errors.AccountEmailAlreadyExists(_(error_message))  # lint-amnesty, pylint: disable=translation-of-non-string


def _validate_secondary_email_doesnt_exist(email):
    """
    Validate that the email is not associated as a secondary email of an existing user.

    Arguments:
        email (unicode): The proposed email.

    Returns:
        None

    Raises:
        errors.AccountEmailAlreadyExists: Raised if given email address is already associated as another
            user's secondary email.
    """
    if email is not None and AccountRecovery.objects.filter(secondary_email=email).exists():
        # pylint: disable=no-member
        raise errors.AccountEmailAlreadyExists(accounts.EMAIL_CONFLICT_MSG.format(email_address=email))


def _validate_password_works_with_username(password, username=None):
    """Run validation checks on whether the password and username
    go well together.

    An example check is to see whether they are the same.

    :param password: The proposed password (unicode).
    :param username: The username associated with the user's account (unicode).
    :return: None
    :raises: errors.AccountPasswordInvalid
    """
    if password == username:
        raise errors.AccountPasswordInvalid(accounts.PASSWORD_CANT_EQUAL_USERNAME_MSG)  # lint-amnesty, pylint: disable=no-member


def _validate_type(data, type, err):  # lint-amnesty, pylint: disable=redefined-builtin
    """Checks whether the input data is of type. If not,
    throws a generic error message.

    :param data: The data to check.
    :param type: The type to check against.
    :param err: The error message to throw back if data is not of type.
    :return: None
    :raises: errors.AccountDataBadType

    """
    if not isinstance(data, type):
        raise errors.AccountDataBadType(err)


def _validate_length(data, min, max, err):  # lint-amnesty, pylint: disable=redefined-builtin
    """Validate that the data's length is less than or equal to max,
    and greater than or equal to min.

    :param data: The data to do the test on.
    :param min: The minimum allowed length.
    :param max: The maximum allowed length.
    :param err: The error message to throw back if data's length is below min or above max.
    :return: None
    :raises: errors.AccountDataBadLength

    """
    if len(data) < min or len(data) > max:
        raise errors.AccountDataBadLength(err)


def _validate_unicode(data, err="Input not valid unicode"):
    """Checks whether the input data is valid unicode or not.

    :param data: The data to check for unicode validity.
    :param err: The error message to throw back if unicode is invalid.
    :return: None
    :raises: UnicodeError

    """
    try:
        if not isinstance(data, str) and not isinstance(data, str):
            raise UnicodeError(err)
        # In some cases we pass the above, but it's still inappropriate utf-8.
        str(data)
    except UnicodeError:
        raise UnicodeError(err)  # lint-amnesty, pylint: disable=raise-missing-from
