"""
Errors thrown by the various user APIs.
"""


class UserAPIRequestError(Exception):
    """There was a problem with the request to the User API. """
    pass  # pylint: disable=unnecessary-pass


class UserAPIInternalError(Exception):
    """An internal error occurred in the User API. """
    pass  # pylint: disable=unnecessary-pass


class UserNotFound(UserAPIRequestError):
    """The requested user does not exist. """
    pass  # pylint: disable=unnecessary-pass


class UserNotAuthorized(UserAPIRequestError):
    """The user is not authorized to perform the requested action. """
    pass  # pylint: disable=unnecessary-pass


class AccountRequestError(UserAPIRequestError):
    """There was a problem with the request to the account API. """
    pass  # pylint: disable=unnecessary-pass


class AccountUserAlreadyExists(AccountRequestError):
    """User with the same username and/or email already exists. """
    pass  # pylint: disable=unnecessary-pass


class AccountUsernameAlreadyExists(AccountRequestError):
    """User with the same username already exists. """
    pass  # pylint: disable=unnecessary-pass


class AccountEmailAlreadyExists(AccountRequestError):
    """User with the same email already exists. """
    pass  # pylint: disable=unnecessary-pass


class AccountUsernameInvalid(AccountRequestError):
    """The requested username is not in a valid format. """
    pass  # pylint: disable=unnecessary-pass


class AccountEmailInvalid(AccountRequestError):
    """The requested email is not in a valid format. """
    pass  # pylint: disable=unnecessary-pass


class AccountPasswordInvalid(AccountRequestError):
    """The requested password is not in a valid format. """
    pass  # pylint: disable=unnecessary-pass


class AccountCountryInvalid(AccountRequestError):
    """The requested country does not exist. """
    pass  # pylint: disable=unnecessary-pass


class AccountDataBadLength(AccountRequestError):
    """The requested account data is either too short or too long. """
    pass  # pylint: disable=unnecessary-pass


class AccountDataBadType(AccountRequestError):
    """The requested account data is of the wrong type. """
    pass  # pylint: disable=unnecessary-pass


class AccountUpdateError(AccountRequestError):
    """
    An update to the account failed. More detailed information is present in developer_message,
    and depending on the type of error encountered, there may also be a non-null user_message field.
    """
    def __init__(self, developer_message, user_message=None):  # pylint: disable=super-init-not-called
        self.developer_message = developer_message
        self.user_message = user_message


class AccountValidationError(AccountRequestError):
    """
    Validation issues were found with the supplied data. More detailed information is present in field_errors,
    a dict with specific information about each field that failed validation. For each field,
    there will be at least a developer_message describing the validation issue, and possibly
    also a user_message.
    """
    def __init__(self, field_errors):  # pylint: disable=super-init-not-called
        self.field_errors = field_errors


class PreferenceRequestError(UserAPIRequestError):
    """There was a problem with a preference request."""
    pass  # pylint: disable=unnecessary-pass


class PreferenceValidationError(PreferenceRequestError):
    """
    Validation issues were found with the supplied data. More detailed information is present
    in preference_errors, a dict with specific information about each preference that failed
    validation. For each preference, there will be at least a developer_message describing
    the validation issue, and possibly also a user_message.
    """
    def __init__(self, preference_errors):
        self.preference_errors = preference_errors
        super().__init__(preference_errors)


class PreferenceUpdateError(PreferenceRequestError):
    """
    An update to a user preference failed. More detailed information is present in developer_message,
    and depending on the type of error encountered, there may also be a non-null user_message field.
    """
    def __init__(self, developer_message, user_message=None):
        self.developer_message = developer_message
        self.user_message = user_message
        super().__init__(developer_message)


class CountryCodeError(ValueError):
    """There was a problem with the country code"""
    pass  # pylint: disable=unnecessary-pass
