"""
Utility functions, constants, etc. for testing.
"""


from common.djangoapps.util.password_policy_validators import DEFAULT_MAX_PASSWORD_LENGTH
from openedx.core.djangoapps.user_api.accounts import EMAIL_MAX_LENGTH, USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH

INVALID_NAMES = [
    None,
    '',
    'http://',
    'https://',
    '<html_name>',
    'https://www.example.com',
    'Valid name http://www.example.com',
    'Valid name <tag>',
]

INVALID_USERNAMES_ASCII = [
    '$invalid-ascii$',
    'invalid-fŕáńḱ',
    '@invalid-ascii@'
]

INVALID_USERNAMES_UNICODE = [
    'invalid-unicode_fŕáńḱ',
]

INVALID_USERNAMES = [
    None,
    '',
    'a',
    'a' * (USERNAME_MAX_LENGTH + 1),
] + INVALID_USERNAMES_ASCII + INVALID_USERNAMES_UNICODE

INVALID_EMAILS = [
    None,
    '',
    'a',
    'no_domain',
    'no+domain',
    '@',
    '@domain.com',
    'test@no_extension',
    'fŕáńḱ@example.com',

    # Long email -- subtract the length of the @domain
    # except for one character (so we exceed the max length limit)
    '{user}@example.com'.format(
        user=('e' * (EMAIL_MAX_LENGTH - 11))
    )
]

INVALID_PASSWORDS = [
    None,
    '',
    'a',
    'a' * (DEFAULT_MAX_PASSWORD_LENGTH + 1),
]

INVALID_COUNTRIES = [
    None,
    "",
    "--"
]

VALID_NAMES = [
    'Validation Bot',
    'Validation Bot'
]

VALID_USERNAMES_UNICODE = [
    'Enchanté',
    'username_with_@',
    'username with spaces',
    'eastern_arabic_numbers_١٢٣',
]

VALID_USERNAMES = [
    'username',
    'a' * USERNAME_MIN_LENGTH,
    'a' * USERNAME_MAX_LENGTH,
    '-' * USERNAME_MIN_LENGTH,
    '-' * USERNAME_MAX_LENGTH,
    '_username_',
    '-username-',
    '-_username_-'
]

VALID_EMAILS = [
    'has@domain.com'
]

VALID_PASSWORDS = [
    'good_password_339',
]

VALID_COUNTRIES = [
    'PK',
    'Pakistan',
    'US'
]
