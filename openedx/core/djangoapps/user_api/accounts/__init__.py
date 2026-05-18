"""
Account constants
"""

from django.conf import settings
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

# Import signals to ensure they are registered
from . import signals  # noqa: F401, pylint: disable=unused-import

# The maximum length for the bio ("about me") account field
BIO_MAX_LENGTH = 300

# The minimum and maximum length for the name ("full name") account field
NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 255

# The minimum and maximum length for the username account field
USERNAME_MIN_LENGTH = getattr(settings, 'USERNAME_MIN_LENGTH', 2)
# Note: 30 chars is the default for historical reasons. Django uses 150 as the username length since 1.10
USERNAME_MAX_LENGTH = getattr(settings, 'USERNAME_MAX_LENGTH', 30)

# The minimum and maximum length for the email account field
EMAIL_MIN_LENGTH = 3
EMAIL_MAX_LENGTH = 254  # Limit per RFCs is 254

ACCOUNT_VISIBILITY_PREF_KEY = 'account_privacy'

# Indicates the user's preference that all users can view the shareable fields in their account information.
ALL_USERS_VISIBILITY = 'all_users'

# Indicates the user's preference that all their account information be private.
PRIVATE_VISIBILITY = 'private'

# Indicates that the user has custom preferences for the visibility of their account information.
CUSTOM_VISIBILITY = 'custom'

# Prefix prepended to user preferences related to custom account visibility preferences.
VISIBILITY_PREFIX = 'visibility.'

# Translators: This message is shown when the Unicode usernames are NOT allowed.
# It is shown to users who attempt to create a new account using invalid characters
# in the username.
USERNAME_INVALID_CHARS_ASCII = _(
    "Usernames can only contain letters (A-Z, a-z), numerals (0-9), underscores (_), and hyphens (-)."
)

# Translators: This message is shown only when the Unicode usernames are allowed.
# It is shown to users who attempt to create a new account using invalid characters
# in the username.
USERNAME_INVALID_CHARS_UNICODE = _(
    "Usernames can only contain letters, numerals, and @/./+/-/_ characters."
)

# Translators: This message is shown to users who attempt to create a new account using
# an invalid email format.
AUTHN_EMAIL_INVALID_MSG = _('Enter a valid email address')

# Translators: This message is shown to users who attempt to create a new
# account using an username/email associated with an existing account.
EMAIL_CONFLICT_MSG = _(
    "It looks like {email_address} belongs to an existing account. "
    "Try again with a different email address."
)
AUTHN_EMAIL_CONFLICT_MSG = _(  # pylint: disable=translation-of-non-string
    "This email is already associated with an existing account")
RETIRED_EMAIL_MSG = _(
    "This email is associated to a retired account."
)
AUTHN_PASSWORD_COMPROMISED_MSG = _(
    "The password you entered is on a list of known compromised passwords. Please choose a different one."
)
USERNAME_CONFLICT_MSG = _(
    "It looks like {username} belongs to an existing account. "
    "Try again with a different username."
)
AUTHN_USERNAME_CONFLICT_MSG = _("It looks like this username is already taken")

# Translators: This message is shown to users who enter a username/email/password
# with an inappropriate length (too short or too long).
USERNAME_BAD_LENGTH_MSG = format_lazy(
    _("Username must be between {min} and {max} characters long."),
    min=USERNAME_MIN_LENGTH,
    max=USERNAME_MAX_LENGTH,
)
EMAIL_BAD_LENGTH_MSG = format_lazy(
    _("Enter a valid email address that contains at least {min} characters."),
    min=EMAIL_MIN_LENGTH,
)

# These strings are normally not user-facing.
USERNAME_BAD_TYPE_MSG = "Username must be a string"
EMAIL_BAD_TYPE_MSG = "Email must be a string"
PASSWORD_BAD_TYPE_MSG = "Password must be a string"

# Translators: These messages are shown to users who do not enter information
# into the required field or enter it incorrectly.
REQUIRED_FIELD_NAME_MSG = _("Enter your full name")
REQUIRED_FIELD_FIRST_NAME_MSG = _("Enter your first name")
REQUIRED_FIELD_LAST_NAME_MSG = _("Enter your last name")
REQUIRED_FIELD_CONFIRM_EMAIL_MSG = _("The email addresses do not match")
REQUIRED_FIELD_CONFIRM_EMAIL_TEXT_MSG = _("Enter your confirm email")
REQUIRED_FIELD_COUNTRY_MSG = _("Select your country or region of residence")
REQUIRED_FIELD_PROFESSION_SELECT_MSG = _("Select your profession")
REQUIRED_FIELD_SPECIALTY_SELECT_MSG = _("Select your specialty")
REQUIRED_FIELD_PROFESSION_TEXT_MSG = _("Enter your profession")
REQUIRED_FIELD_SPECIALTY_TEXT_MSG = _("Enter your specialty")
REQUIRED_FIELD_STATE_MSG = _("Enter your state")
REQUIRED_FIELD_CITY_MSG = _("Enter your city")
REQUIRED_FIELD_GOALS_MSG = _("Tell us your goals")
REQUIRED_FIELD_LEVEL_OF_EDUCATION_MSG = _("Select the highest level of education you have completed")
REQUIRED_FIELD_YEAR_OF_BIRTH_MSG = _("Select your year of birth")
REQUIRED_FIELD_GENDER_MSG = _("Select your gender")
REQUIRED_FIELD_MAILING_ADDRESS_MSG = _("Enter your mailing address")

# HIBP Strings
AUTHN_LOGIN_BLOCK_HIBP_POLICY_MSG = _(
    'Our system detected that your password is vulnerable. Change your password so that your account stays secure.'
)
AUTHN_LOGIN_NUDGE_HIBP_POLICY_MSG = _(
    'Our system detected that your password is vulnerable. '
    'We recommend you change it so that your account stays secure.'
)
