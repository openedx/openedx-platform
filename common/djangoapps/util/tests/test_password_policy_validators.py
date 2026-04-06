"""Tests for util.password_policy_validators module."""


import unittest

import pytest
from ddt import data, ddt, unpack
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.test.utils import override_settings

from common.djangoapps.util.password_policy_validators import (
    create_validator_config,
    password_validators_instruction_texts,
    validate_password,
)


@ddt
class PasswordPolicyValidatorsTestCase(unittest.TestCase):
    """
    Tests for password validator utility functions

    The general framework I went with for testing the validators was to test:
        1) requiring a single check (also checks proper singular message)
        2) requiring multiple instances of the check (also checks proper plural message)
        3) successful check
    """
    def validation_errors_checker(self, password, msg, user=None):
        """
        This helper function is used to check the proper error messages are
        being displayed based on the password and validator.

        Parameters:
            password (unicode): the password to validate on
            user (django.contrib.auth.models.User): user object to use in validation.
                This is an optional parameter unless the validator requires a
                user object.
            msg (str): The expected ValidationError message
        """
        if msg is None:
            validate_password(password, user)
        else:
            with pytest.raises(ValidationError) as cm:
                validate_password(password, user)
            assert msg in ' '.join(cm.value.messages)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 4}
        )
    ])
    def test_unicode_password(self):
        """ Tests that validate_password enforces unicode """
        unicode_str = '𤭮'
        byte_str = unicode_str.encode('utf-8')

        # Sanity checks and demonstration of why this test is useful
        assert len(byte_str) == 4
        assert len(unicode_str) == 1

        # Test length check
        self.validation_errors_checker(byte_str, 'This password is too short. It must contain at least 4 characters.')
        self.validation_errors_checker(byte_str * 4, None)

        # Test badly encoded password
        self.validation_errors_checker(b'\xff\xff', 'Invalid password.')

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 4}
        )
    ])
    def test_password_unicode_normalization(self):
        """ Tests that validate_password normalizes passwords """
        # s ̣ ̇ (s with combining dot below and combining dot above)
        not_normalized_password = '\u0073\u0323\u0307'
        assert len(not_normalized_password) == 3

        # When we normalize we expect the not_normalized password to fail
        # because it should be normalized to '\u1E69' -> ṩ
        self.validation_errors_checker(not_normalized_password,
                                       'This password is too short. It must contain at least 4 characters.')

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 2})],  # lint-amnesty, pylint: disable=line-too-long
            'at least 2 characters.'),

        ([
            create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 2}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.AlphabeticValidator', {'min_alphabetic': 2}),  # lint-amnesty, pylint: disable=line-too-long
        ], 'characters, including 2 letters.'),

        ([
            create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 2}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.AlphabeticValidator', {'min_alphabetic': 2}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.NumericValidator', {'min_numeric': 1}),  # lint-amnesty, pylint: disable=line-too-long
        ], 'characters, including 2 letters & 1 number.'),

        ([
            create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 2}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.UppercaseValidator', {'min_upper': 3}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.NumericValidator', {'min_numeric': 1}),  # lint-amnesty, pylint: disable=line-too-long
            create_validator_config('common.djangoapps.util.password_policy_validators.SymbolValidator', {'min_symbol': 2}),  # lint-amnesty, pylint: disable=line-too-long
        ], 'including 3 uppercase letters & 1 number & 2 symbols.'),
    )
    @unpack
    def test_password_instructions(self, config, msg):
        """ Tests password instructions """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            assert msg in password_validators_instruction_texts()

    @data(
        ('userna', 'username', 'test@example.com', 'The password is too similar to the username.'),
        ('password', 'username', 'password@example.com', 'The password is too similar to the email address.'),
        ('password', 'username', 'test@password.com', 'The password is too similar to the email address.'),
        ('password', 'username', 'test@example.com', None),
    )
    @unpack
    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('django.contrib.auth.password_validation.UserAttributeSimilarityValidator')
    ])
    def test_user_attribute_similarity_validation_errors(self, password, username, email, msg):
        """ Tests validate_password error messages for the UserAttributeSimilarityValidator """
        user = User(username=username, email=email)
        self.validation_errors_checker(password, msg, user)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 1})],  # lint-amnesty, pylint: disable=line-too-long
            '', 'This password is too short. It must contain at least 1 character.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 8})],  # lint-amnesty, pylint: disable=line-too-long
            'd', 'This password is too short. It must contain at least 8 characters.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 8})],  # lint-amnesty, pylint: disable=line-too-long
            'longpassword', None),
    )
    @unpack
    def test_minimum_length_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the MinimumLengthValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.MaximumLengthValidator', {'max_length': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'longpassword', 'This password is too long. It must contain no more than 1 character.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.MaximumLengthValidator', {'max_length': 10})],  # lint-amnesty, pylint: disable=line-too-long
            'longpassword', 'This password is too long. It must contain no more than 10 characters.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.MaximumLengthValidator', {'max_length': 20})],  # lint-amnesty, pylint: disable=line-too-long
            'shortpassword', None),
    )
    @unpack
    def test_maximum_length_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the MaximumLengthValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ('password', 'This password is too common.'),
        ('good_password', None),
    )
    @unpack
    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('django.contrib.auth.password_validation.CommonPasswordValidator')
    ])
    def test_common_password_validation_errors(self, password, msg):
        """ Tests validate_password error messages for the CommonPasswordValidator """
        self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.AlphabeticValidator', {'min_alphabetic': 1})],  # lint-amnesty, pylint: disable=line-too-long
            '12345', 'This password must contain at least 1 letter.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.AlphabeticValidator', {'min_alphabetic': 5})],  # lint-amnesty, pylint: disable=line-too-long
            'test123', 'This password must contain at least 5 letters.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.AlphabeticValidator', {'min_alphabetic': 2})],  # lint-amnesty, pylint: disable=line-too-long
            'password', None),
    )
    @unpack
    def test_alphabetic_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the AlphabeticValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.NumericValidator', {'min_numeric': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'test', 'This password must contain at least 1 number.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.NumericValidator', {'min_numeric': 4})],  # lint-amnesty, pylint: disable=line-too-long
            'test123', 'This password must contain at least 4 numbers.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.NumericValidator', {'min_numeric': 2})],  # lint-amnesty, pylint: disable=line-too-long
            'password123', None),
    )
    @unpack
    def test_numeric_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the NumericValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.UppercaseValidator', {'min_upper': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'lowercase', 'This password must contain at least 1 uppercase letter.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.UppercaseValidator', {'min_upper': 6})],  # lint-amnesty, pylint: disable=line-too-long
            'NOTenough', 'This password must contain at least 6 uppercase letters.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.UppercaseValidator', {'min_upper': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'camelCase', None),
    )
    @unpack
    def test_upper_case_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the UppercaseValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.LowercaseValidator', {'min_lower': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'UPPERCASE', 'This password must contain at least 1 lowercase letter.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.LowercaseValidator', {'min_lower': 4})],  # lint-amnesty, pylint: disable=line-too-long
            'notENOUGH', 'This password must contain at least 4 lowercase letters.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.LowercaseValidator', {'min_lower': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'goodPassword', None),
    )
    @unpack
    def test_lower_case_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the LowercaseValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.PunctuationValidator', {'min_punctuation': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'no punctuation', 'This password must contain at least 1 punctuation mark.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.PunctuationValidator', {'min_punctuation': 7})],  # lint-amnesty, pylint: disable=line-too-long
            'p@$$w0rd$!', 'This password must contain at least 7 punctuation marks.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.PunctuationValidator', {'min_punctuation': 3})],  # lint-amnesty, pylint: disable=line-too-long
            'excl@m@t!on', None),
    )
    @unpack
    def test_punctuation_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the PunctuationValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)

    @data(
        ([create_validator_config('common.djangoapps.util.password_policy_validators.SymbolValidator', {'min_symbol': 1})],  # lint-amnesty, pylint: disable=line-too-long
            'no symbol', 'This password must contain at least 1 symbol.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.SymbolValidator', {'min_symbol': 3})],  # lint-amnesty, pylint: disable=line-too-long
            '☹️boo☹️', 'This password must contain at least 3 symbols.'),

        ([create_validator_config('common.djangoapps.util.password_policy_validators.SymbolValidator', {'min_symbol': 2})],  # lint-amnesty, pylint: disable=line-too-long
            '☪symbols!☹️', None),
    )
    @unpack
    def test_symbol_validation_errors(self, config, password, msg):
        """ Tests validate_password error messages for the SymbolValidator """
        with override_settings(AUTH_PASSWORD_VALIDATORS=config):
            self.validation_errors_checker(password, msg)
