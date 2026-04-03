#!/usr/bin/env python

"""Tests for `user_util` package."""

from types import GeneratorType

import pytest

from openedx.core.lib import user_util

VALID_SALT_LIST_ONE_SALT = ['gsw@&2p)$^p2hdk&ou0e%c=ou80o=%!+tv7(u(ircv@+96jl6$']
VALID_SALT_LIST_THREE_SALTS = [
    '^==!0%=z4s!v7!yl0#+m6-st^*946aop6$0i+hu13&h_$a$vq8',
    'wdwh&#8s@(f=jnlky4up8p0#04t$jp%ip)nfp@de6rr9i)j7nf',
    ')h1^pu8a!rh=%$_4f7sx*5^46ln_pujw6y*s0=dl6i$_#&#io1',
]
VALID_SALT_LIST_FIVE_SALTS = [
    '8rv!7iy4a7mdvs_kudis6&oycj0_b(mj0s^@*e5p)(o+m(c-cb',
    'xp)43m+d_!f!-)c=ki_8oc2w9(^r^umy73%dp@z7sknn#800z$',
    'some_salt_that_is_not_very_random',
    '$=ldtvagk$qwc)cz%2%edaa_id45^(xg*1rs#t0inywla*)3+x',
    '4eyp*!%nz&g@8(tm!236ykbg2xzwcix!=)06q&=d2rh@3n1o+8',
]
VALID_SALT_LISTS = (
    VALID_SALT_LIST_ONE_SALT,
    VALID_SALT_LIST_THREE_SALTS,
    VALID_SALT_LIST_FIVE_SALTS,
)
INVALID_SALT_LIST = (
    'gsw@&2p)$^p2hdk&ou0e%c=ou80o=%!+tv7(u(ircv@+96jl6$',
    None,
    [],
)


#
# Username retirement tests
#
@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_username_to_hash(salt_list):
    username = 'ALearnerUserName'
    retired_username = user_util.get_retired_username(username, salt_list)
    assert retired_username != username
    assert retired_username.startswith('_'.join(user_util.RETIRED_USERNAME_DEFAULT_FMT.split('_')[0:-1]))
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_username.split('_')[-1]) == 40


@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_username_to_hash_is_normalized(salt_list):
    """
    Make sure identical usernames with different cases map to the same retired username.
    """
    username_mixed = 'ALearnerUserName'
    username_lower = username_mixed.lower()
    retired_username_mixed = user_util.get_retired_username(username_mixed, salt_list)
    retired_username_lower = user_util.get_retired_username(username_lower, salt_list)
    # No matter the case of the input username, the retired username hash should be identical.
    assert retired_username_mixed == retired_username_lower


def test_unicode_username_to_hash():
    username = 'ÁĹéáŕńéŕŰśéŕŃáḿéẂíthŰńíćődé'
    retired_username = user_util.get_retired_username(username, VALID_SALT_LIST_ONE_SALT)
    assert retired_username != username
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_username.split('_')[-1]) == 40


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_THREE_SALTS,))
def test_correct_username_hash(salt_list):
    """
    Verify that get_retired_username uses the current salt and returns the expected hash.
    """
    username = 'ALearnerUserName'
    # Valid retired usernames for the above username when using VALID_SALT_LIST_THREE_SALTS.
    valid_retired_usernames = [
        #  pylint: disable=protected-access
        user_util.RETIRED_USERNAME_DEFAULT_FMT.format(user_util._compute_retired_hash(username.lower(), salt))
        for salt in salt_list
    ]
    retired_username = user_util.get_retired_username(username, salt_list)
    assert retired_username == valid_retired_usernames[-1]


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_FIVE_SALTS,))
def test_all_usernames_to_hash(salt_list):
    username = 'ALearnerUserName'
    retired_username_generator = user_util.get_all_retired_usernames(username, salt_list)
    assert isinstance(retired_username_generator, GeneratorType)
    assert len(list(retired_username_generator)) == len(VALID_SALT_LIST_FIVE_SALTS)


@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_username_to_hash_with_different_format(salt_list):
    username = 'ALearnerUserName'
    retired_username_fmt = "{}_is_now_the_retired_username"
    retired_username = user_util.get_retired_username(username, salt_list, retired_username_fmt=retired_username_fmt)
    assert retired_username.endswith('_'.join(retired_username_fmt.split('_')[1:]))
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_username.split('_')[0]) == 40


#
# Email address retirement tests
#
@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_email_to_hash(salt_list):
    email = 'a.learner@example.com'
    retired_email = user_util.get_retired_email(email, salt_list)
    assert retired_email != email
    assert retired_email.startswith('_'.join(user_util.RETIRED_EMAIL_DEFAULT_FMT.split('_')[0:2]))
    assert retired_email.endswith(user_util.RETIRED_EMAIL_DEFAULT_FMT.split('@')[-1])
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_email.split('@')[0]) == len('retired_email_') + 40


@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_email_to_hash_is_normalized(salt_list):
    """
    Make sure identical emails with different cases map to the same retired email.
    """
    email_mixed = 'A.Learner@example.com'
    email_lower = email_mixed.lower()
    retired_email_mixed = user_util.get_retired_email(email_mixed, salt_list)
    retired_email_lower = user_util.get_retired_email(email_lower, salt_list)
    # No matter the case of the input email, the retired email hash should be identical.
    assert retired_email_mixed == retired_email_lower


def test_unicode_email_to_hash():
    email = '🅐.🅛🅔🅐🅡🅝🅔🅡r@example.com'
    retired_email = user_util.get_retired_email(email, VALID_SALT_LIST_ONE_SALT)
    assert retired_email != email
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_email.split('@')[0]) == len('retired_email_') + 40


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_THREE_SALTS,))
def test_correct_email_hash(salt_list):
    """
    Verify that get_retired_email uses the current salt and returns the expected hash.
    """
    email = 'a.learner@example.com'
    # Valid retired emails for the above email address when using VALID_SALT_LIST_THREE_SALTS.
    valid_retired_emails = [
        #  pylint: disable=protected-access
        user_util.RETIRED_EMAIL_DEFAULT_FMT.format(user_util._compute_retired_hash(email.lower(), salt))
        for salt in salt_list
    ]
    retired_email = user_util.get_retired_email(email, salt_list)
    assert retired_email == valid_retired_emails[-1]


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_FIVE_SALTS,))
def test_all_emails_to_hash(salt_list):
    email = 'a.learner@example.com'
    retired_email_generator = user_util.get_all_retired_emails(email, salt_list)
    assert isinstance(retired_email_generator, GeneratorType)
    assert len(list(retired_email_generator)) == len(VALID_SALT_LIST_FIVE_SALTS)


@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_email_to_hash_with_different_format(salt_list):
    email = 'a.learner@example.com'
    retired_email_fmt = "{}_is_now_the_retired_email@devnull.example.com"
    retired_email = user_util.get_retired_email(email, salt_list, retired_email_fmt=retired_email_fmt)
    assert retired_email.endswith('_'.join(retired_email_fmt.split('_')[1:]))
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_email.split('_')[0]) == 40


#
# Bad salt tests.
#
@pytest.mark.parametrize('salt', INVALID_SALT_LIST)
def test_username_to_hash_bad_salt(salt):
    """
    Salts that are *not* lists/tuples should fail.
    """
    with pytest.raises((ValueError, IndexError)):
        _ = user_util.get_retired_username('AnotherLearnerUserName', salt)


#
# External user retirement tests
#

@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_external_key_to_hash(salt_list):
    external_key = '343ni3hr3ifh3fgghg'
    retired_external_key = user_util.get_retired_external_key(external_key, salt_list)
    assert retired_external_key != external_key
    assert retired_external_key.startswith(
        '_'.join(user_util.RETIRED_EXTERNAL_KEY_DEFAULT_FMT.split('_')[0:3])
    )
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_external_key) == len('retired_external_key_') + 40


def test_unicode_external_key_to_hash():
    unicode_external_key = '🅐.🅛🅔🅐🅡🅝🅔🅡'
    retired_external_key = user_util.get_retired_external_key(unicode_external_key, VALID_SALT_LIST_ONE_SALT)
    assert retired_external_key != unicode_external_key
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_external_key) == len('retired_external_key_') + 40


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_THREE_SALTS,))
def test_correct_external_key_hash(salt_list):
    """
    Verify that get_retired_external_key uses the current salt and returns the expected hash.
    """
    external_key = 'S34839GEF3'
    valid_retired_external_keys = [
        #  pylint: disable=protected-access
        user_util.RETIRED_EXTERNAL_KEY_DEFAULT_FMT.format(
            user_util._compute_retired_hash(external_key.lower(), salt)
        )
        for salt in salt_list
    ]
    retired_email = user_util.get_retired_external_key(external_key, salt_list)
    assert retired_email == valid_retired_external_keys[-1]


@pytest.mark.parametrize('salt_list', (VALID_SALT_LIST_FIVE_SALTS,))
def test_all_external_keys_to_hash(salt_list):
    external_key = 'S34839GEF3'
    retired_external_key_generator = user_util.get_all_retired_external_keys(external_key, salt_list)
    assert isinstance(retired_external_key_generator, GeneratorType)
    assert len(list(retired_external_key_generator)) == len(VALID_SALT_LIST_FIVE_SALTS)


@pytest.mark.parametrize('salt_list', VALID_SALT_LISTS)
def test_external_key_to_hash_with_different_format(salt_list):
    external_key = 'S34839GEF3'
    retired_external_key_fmt = "{}_is_now_the_retired_external_key"
    retired_external_key = user_util.get_retired_external_key(
        external_key,
        salt_list,
        retired_external_key_fmt=retired_external_key_fmt
    )
    assert retired_external_key.endswith('_is_now_the_retired_external_key')
    # Since SHA1 is used, the hexadecimal digest length should be 40.
    assert len(retired_external_key.split('_')[0]) == 40
