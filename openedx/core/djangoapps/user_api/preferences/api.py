"""
API for managing user preferences.
"""


import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from django_countries import countries
from pytz import common_timezones, common_timezones_set, country_timezones

from common.djangoapps.student.models import User, UserProfile
from common.djangoapps.track import segment
from openedx.core.lib.time_zone_utils import get_display_time_zone

from ..errors import (  # lint-amnesty, pylint: disable=unused-import
    CountryCodeError,
    PreferenceUpdateError,
    PreferenceValidationError,
    UserAPIInternalError,
    UserAPIRequestError,
    UserNotAuthorized,
    UserNotFound,
)
from ..helpers import intercept_errors, serializer_is_dirty
from ..models import UserOrgTag, UserPreference
from ..serializers import RawUserPreferenceSerializer

log = logging.getLogger(__name__)


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def has_user_preference(requesting_user, preference_key, username=None):
    """
    Returns True if the user has preference with the specified key.

    Args:
        requesting_user (User): The user requesting the user preference check. Only the user with username
            `username` or users with "is_staff" privileges can access the preferences.
        preference_key (str): The key for the user preference.
        username (str): Optional username for which to look up the preferences. If not specified,
            `requesting_user.username` is assumed.

    Returns:
         (bool): Returns True if the user has preference with the specified key and False otherwise.

    Raises:
         UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
         UserNotAuthorized: the requesting_user does not have access to the user preference.
         UserAPIInternalError: the operation failed due to an unexpected error.
    """
    existing_user = _get_authorized_user(requesting_user, username, allow_staff=True)
    return UserPreference.has_value(existing_user, preference_key)


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def get_user_preference(requesting_user, preference_key, username=None):
    """Returns the value of the user preference with the specified key.

    Args:
        requesting_user (User): The user requesting the user preferences. Only the user with username
            `username` or users with "is_staff" privileges can access the preferences.
        preference_key (str): The key for the user preference.
        username (str): Optional username for which to look up the preferences. If not specified,
            `requesting_user.username` is assumed.

    Returns:
         The value for the user preference which is always a string, or None if a preference
         has not been specified.

    Raises:
         UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
         UserNotAuthorized: the requesting_user does not have access to the user preference.
         UserAPIInternalError: the operation failed due to an unexpected error.
    """
    existing_user = _get_authorized_user(requesting_user, username, allow_staff=True)
    return UserPreference.get_value(existing_user, preference_key)


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def get_user_preferences(requesting_user, username=None):
    """Returns all user preferences as a JSON response.

    Args:
        requesting_user (User): The user requesting the user preferences. Only the user with username
            `username` or users with "is_staff" privileges can access the preferences.
        username (str): Optional username for which to look up the preferences. If not specified,
            `requesting_user.username` is assumed.

    Returns:
         A dict containing account fields.

    Raises:
         UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
         UserNotAuthorized: the requesting_user does not have access to the user preference.
         UserAPIInternalError: the operation failed due to an unexpected error.
    """
    existing_user = _get_authorized_user(requesting_user, username, allow_staff=True)
    return UserPreference.get_all_preferences(existing_user)


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def update_user_preferences(requesting_user, update, user=None):
    """Update the user preferences for the given user.

    Note:
        It is up to the caller of this method to enforce the contract that this method is only called
        with the user who made the request.

    Arguments:
        requesting_user (User): The user requesting to modify account information. Only the user with username
            'username' has permissions to modify account information.
        update (dict): The updated account field values.
            Some notes:
                Values are expected to be strings. Non-string values will be converted to strings.
                Null values for a preference will be treated as a request to delete the key in question.
        user (str/User): Optional, either username string or user object specifying which account should be updated.
                If not specified, `requesting_user.username` is assumed.

    Raises:
        UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
        UserNotAuthorized: the requesting_user does not have access to change the account
            associated with `username`
        PreferenceValidationError: the update was not attempted because validation errors were found
        PreferenceUpdateError: the operation failed when performing the update.
        UserAPIInternalError: the operation failed due to an unexpected error.
    """
    if not user or isinstance(user, str):
        user = _get_authorized_user(requesting_user, user)
    else:
        _check_authorized(requesting_user, user.username)

    # First validate each preference setting
    errors = {}
    serializers = {}
    for preference_key in update.keys():
        preference_value = update[preference_key]
        if preference_value is not None:
            preference_value = str(preference_value)
            try:
                serializer = create_user_preference_serializer(user, preference_key, preference_value)
                validate_user_preference_serializer(serializer, preference_key, preference_value)
                serializers[preference_key] = serializer
            except PreferenceValidationError as error:
                preference_error = error.preference_errors[preference_key]
                errors[preference_key] = {
                    "developer_message": preference_error["developer_message"],
                    "user_message": preference_error["user_message"],
                }
    if errors:
        raise PreferenceValidationError(errors)
    # Then perform the patch
    for preference_key in update.keys():
        preference_value = update[preference_key]
        if preference_value is not None:
            preference_value = str(preference_value)
            try:
                serializer = serializers[preference_key]

                if serializer_is_dirty(serializer):
                    serializer.save()
            except Exception as error:
                raise _create_preference_update_error(preference_key, preference_value, error)  # lint-amnesty, pylint: disable=raise-missing-from
        else:
            delete_user_preference(requesting_user, preference_key)


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def set_user_preference(requesting_user, preference_key, preference_value, username=None):
    """Update a user preference for the given username.

    Note:
        It is up to the caller of this method to enforce the contract that this method is only called
        with the user who made the request.

    Arguments:
        requesting_user (User): The user requesting to modify account information. Only the user with username
            'username' has permissions to modify account information.
        preference_key (str): The key for the user preference.
        preference_value (str): The value to be stored. Non-string values are converted to strings.
        username (str): Optional username specifying which account should be updated. If not specified,
            `requesting_user.username` is assumed.

    Raises:
        UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
        UserNotAuthorized: the requesting_user does not have access to change the account
            associated with `username`
        PreferenceValidationError: the update was not attempted because validation errors were found
        PreferenceUpdateError: the operation failed when performing the update.
        UserAPIInternalError: the operation failed due to an unexpected error.
    """
    existing_user = _get_authorized_user(requesting_user, username)
    if preference_value is not None:
        preference_value = str(preference_value)
    serializer = create_user_preference_serializer(existing_user, preference_key, preference_value)
    validate_user_preference_serializer(serializer, preference_key, preference_value)

    if serializer_is_dirty(serializer):
        try:
            serializer.save()
        except Exception as error:
            raise _create_preference_update_error(preference_key, preference_value, error)  # lint-amnesty, pylint: disable=raise-missing-from


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def delete_user_preference(requesting_user, preference_key, username=None):
    """Deletes a user preference on behalf of a requesting user.

    Note:
        It is up to the caller of this method to enforce the contract that this method is only called
        with the user who made the request.

    Arguments:
        requesting_user (User): The user requesting to delete the preference. Only the user with username
            'username' has permissions to delete their own preference.
        preference_key (str): The key for the user preference.
        username (str): Optional username specifying which account should be updated. If not specified,
            `requesting_user.username` is assumed.

    Returns:
        True if the preference was deleted, False if the user did not have a preference with the supplied key.

    Raises:
        UserNotFound: no user with username `username` exists (or `requesting_user.username` if
            `username` is not specified)
        UserNotAuthorized: the requesting_user does not have access to change the account
            associated with `username`
        PreferenceUpdateError: the operation failed when performing the update.
        UserAPIInternalError: the operation failed due to an unexpected error.
    """
    existing_user = _get_authorized_user(requesting_user, username)
    try:
        user_preference = UserPreference.objects.get(user=existing_user, key=preference_key)
    except ObjectDoesNotExist:
        return False

    try:
        user_preference.delete()
    except Exception as error:
        raise PreferenceUpdateError(  # lint-amnesty, pylint: disable=raise-missing-from
            developer_message="Delete failed for user preference '{preference_key}': {error}".format(
                preference_key=preference_key, error=error
            ),
            user_message=_("Delete failed for user preference '{preference_key}'.").format(
                preference_key=preference_key
            ),
        )
    return True


@intercept_errors(UserAPIInternalError, ignore_errors=[UserAPIRequestError])
def update_email_opt_in(user, org, opt_in):
    """Updates a user's preference for receiving org-wide emails.

    Sets a User Org Tag defining the choice to opt in or opt out of organization-wide
    emails.

    Arguments:
        user (User): The user to set a preference for.
        org (str): The org is used to determine the organization this setting is related to.
        opt_in (bool): True if the user is choosing to receive emails for this organization.
            If the user requires parental consent then email-optin is set to False regardless.

    Returns:
        None

    Raises:
         UserNotFound: no user profile exists for the specified user.
    """
    preference, _ = UserOrgTag.objects.get_or_create(
        user=user, org=org, key='email-optin'
    )

    # If the user requires parental consent, then don't allow opt-in
    try:
        user_profile = UserProfile.objects.get(user=user)
    except ObjectDoesNotExist:
        raise UserNotFound()  # lint-amnesty, pylint: disable=raise-missing-from
    if user_profile.requires_parental_consent(
        age_limit=getattr(settings, 'EMAIL_OPTIN_MINIMUM_AGE', 13),
        default_requires_consent=False,
    ):
        opt_in = False

    # Update the preference and save it
    preference.value = str(opt_in)
    try:
        preference.save()
        if hasattr(settings, 'LMS_SEGMENT_KEY') and settings.LMS_SEGMENT_KEY:
            _track_update_email_opt_in(user.id, org, opt_in)
    except IntegrityError as err:
        log.warning(
            f"Could not update organization wide preference due to IntegrityError: {str(err)}"
        )


def _track_update_email_opt_in(user_id, organization, opt_in):
    """Track an email opt-in preference change.

    Arguments:
        user_id (str): The ID of the user making the preference change.
        organization (str): The organization whose emails are being opted into or out of by the user.
        opt_in (bool): Whether the user has chosen to opt-in to emails from the organization.

    Returns:
        None

    """
    event_name = 'edx.bi.user.org_email.opted_in' if opt_in else 'edx.bi.user.org_email.opted_out'
    segment.track(
        user_id,
        event_name,
        {
            'category': 'communication',
            'label': organization
        },
    )


def _get_authorized_user(requesting_user, username=None, allow_staff=False):
    """
    Helper method to return the authorized user for a given username.
    If username is not provided, requesting_user.username is assumed.
    """
    if username is None:
        # If the user is one that has already been stored to the database, use that
        if requesting_user.pk:
            return requesting_user
        else:
            # Otherwise, treat this as a request against a separate user
            username = requesting_user.username

    _check_authorized(requesting_user, username, allow_staff)

    try:
        existing_user = User.objects.get(username=username)
    except ObjectDoesNotExist:
        raise UserNotFound()  # lint-amnesty, pylint: disable=raise-missing-from

    return existing_user


def _check_authorized(requesting_user, username, allow_staff=False):
    """
    Helper method that raises UserNotAuthorized if requesting user
    is not owner user or is not staff if access to staff is given
    (i.e. 'allow_staff' = true)
    """
    if requesting_user.username != username:
        if not requesting_user.is_staff or not allow_staff:
            raise UserNotAuthorized()


def create_user_preference_serializer(user, preference_key, preference_value):
    """Creates a serializer for the specified user preference.

    Arguments:
        user (User): The user whose preference is being serialized.
        preference_key (str): The key for the user preference.
        preference_value (str): The value to be stored. Non-string values will be converted to strings.

    Returns:
        A serializer that can be used to save the user preference.
    """
    try:
        existing_user_preference = UserPreference.objects.get(user=user, key=preference_key)
    except ObjectDoesNotExist:
        existing_user_preference = None
    new_data = {
        "key": preference_key,
        "value": preference_value,
    }
    if existing_user_preference:
        serializer = RawUserPreferenceSerializer(existing_user_preference, data=new_data, partial=True)
    else:
        new_data['user'] = user.id
        serializer = RawUserPreferenceSerializer(data=new_data)
    return serializer


def validate_user_preference_serializer(serializer, preference_key, preference_value):
    """Validates a user preference serializer.

    Arguments:
        serializer (UserPreferenceSerializer): The serializer to be validated.
        preference_key (str): The key for the user preference.
        preference_value (str): The value to be stored. Non-string values will be converted to strings.

    Raises:
        PreferenceValidationError: the supplied key and/or value for a user preference are invalid.
    """
    if preference_value is None or str(preference_value).strip() == '':
        format_string = gettext_noop("Preference '{preference_key}' cannot be set to an empty value.")
        raise PreferenceValidationError({
            preference_key: {
                "developer_message": format_string.format(preference_key=preference_key),
                "user_message": _(format_string).format(preference_key=preference_key)  # lint-amnesty, pylint: disable=translation-of-non-string
            }
        })
    if not serializer.is_valid():
        errors = serializer.errors
        # DRF error messages are of type ErrorDetail and serialize out as such. We want to coerce those
        # messages into the strings only.
        for key in errors:
            errors[key] = [str(el) for el in errors[key]]
        developer_message = "Value '{preference_value}' not valid for preference '{preference_key}': {error}".format(
            preference_key=preference_key, preference_value=preference_value, error=errors
        )
        if "key" in serializer.errors:
            user_message = _("Invalid user preference key '{preference_key}'.").format(
                preference_key=preference_key
            )
        else:
            user_message = _("Value '{preference_value}' is not valid for user preference '{preference_key}'.").format(
                preference_key=preference_key, preference_value=preference_value
            )
        raise PreferenceValidationError({
            preference_key: {
                "developer_message": developer_message,
                "user_message": user_message,
            }
        })
    if preference_key == "time_zone" and preference_value not in common_timezones_set:
        developer_message = gettext_noop("Value '{preference_value}' not valid for preference '{preference_key}': Not in timezone set.")  # pylint: disable=line-too-long
        user_message = gettext_noop("Value '{preference_value}' is not a valid time zone selection.")
        raise PreferenceValidationError({
            preference_key: {
                "developer_message": developer_message.format(
                    preference_key=preference_key, preference_value=preference_value
                ),
                "user_message": user_message.format(preference_key=preference_key, preference_value=preference_value)
            }
        })


def _create_preference_update_error(preference_key, preference_value, error):
    """ Creates a PreferenceUpdateError with developer_message and user_message. """
    return PreferenceUpdateError(
        developer_message="Save failed for user preference '{key}' with value '{value}': {error}".format(
            key=preference_key, value=preference_value, error=error
        ),
        user_message=_("Save failed for user preference '{key}' with value '{value}'.").format(
            key=preference_key, value=preference_value
        ),
    )


def get_country_time_zones(country_code=None):
    """
    Returns a sorted list of time zones commonly used in the specified
    country.  If country_code is None (or unrecognized), or if the country
    has no defined time zones, return a list of all time zones.

    Arguments:
        country_code (str): ISO 3166-1 Alpha-2 country code
    """
    if country_code is None or country_code.upper() not in set(countries.alt_codes):
        return _get_sorted_time_zone_list(common_timezones)

    # We can still get a failure here because there are some countries that are
    # valid, but have no defined timezones in the pytz package (e.g. BV, HM)
    try:
        return _get_sorted_time_zone_list(country_timezones(country_code))
    except KeyError:
        return _get_sorted_time_zone_list(common_timezones)


def _get_sorted_time_zone_list(time_zone_list):
    """
    Returns a list of time zone dictionaries sorted by their display values

    :param time_zone_list (list): pytz time zone list
    """
    return sorted(
        [_get_time_zone_dictionary(time_zone) for time_zone in time_zone_list],
        key=lambda tz_dict: tz_dict['description']
    )


def _get_time_zone_dictionary(time_zone_name):
    """
    Returns a dictionary of time zone information:

        * time_zone: Name of pytz time zone
        * description: Display version of time zone [e.g. US/Pacific (PST, UTC-0800)]

    :param time_zone_name (str): Name of pytz time zone
    """
    return {
        'time_zone': time_zone_name,
        'description': get_display_time_zone(time_zone_name),
    }
