"""Tests for account creation"""

import json
from datetime import datetime
from unittest import mock, skipIf, skipUnless
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ddt
import httpretty
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core import mail
from django.core.cache import cache
from django.test import TransactionTestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from openedx_events.testing import OpenEdxEventsTestMixin
from social_django.models import Partial, UserSocialAuth
from testfixtures import LogCapture

from common.djangoapps.student.helpers import authenticate_new_user
from common.djangoapps.student.tests.factories import AccountRecoveryFactory, UserFactory
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin, simulate_running_pipeline
from common.djangoapps.third_party_auth.tests.utils import (
    ThirdPartyOAuthTestMixin,
    ThirdPartyOAuthTestMixinFacebook,
    ThirdPartyOAuthTestMixinGoogle,
)
from common.djangoapps.util.password_policy_validators import (
    DEFAULT_MAX_PASSWORD_LENGTH,
    create_validator_config,
    password_validators_instruction_texts,
    password_validators_restrictions,
)
from openedx.core.djangoapps.embargo.models import Country, GlobalRestrictedCountry
from openedx.core.djangoapps.site_configuration.helpers import get_value
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration
from openedx.core.djangoapps.user_api.accounts import (
    AUTHN_EMAIL_CONFLICT_MSG,
    AUTHN_EMAIL_INVALID_MSG,
    AUTHN_PASSWORD_COMPROMISED_MSG,
    AUTHN_USERNAME_CONFLICT_MSG,
    EMAIL_BAD_LENGTH_MSG,
    EMAIL_MAX_LENGTH,
    EMAIL_MIN_LENGTH,
    NAME_MAX_LENGTH,
    REQUIRED_FIELD_CONFIRM_EMAIL_MSG,
    REQUIRED_FIELD_COUNTRY_MSG,
    USERNAME_BAD_LENGTH_MSG,
    USERNAME_INVALID_CHARS_ASCII,
    USERNAME_INVALID_CHARS_UNICODE,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
)
from openedx.core.djangoapps.user_api.accounts.api import get_account_settings
from openedx.core.djangoapps.user_api.accounts.tests import testutils
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    RetirementTestCase,
    fake_requested_retirement,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.tests.test_constants import SORTED_COUNTRIES
from openedx.core.djangoapps.user_api.tests.test_helpers import TestCaseForm
from openedx.core.djangoapps.user_api.tests.test_views import UserAPITestCase
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.core.lib.api import test_utils

ENABLE_AUTO_GENERATED_USERNAME = settings.FEATURES.copy()
ENABLE_AUTO_GENERATED_USERNAME['ENABLE_AUTO_GENERATED_USERNAME'] = True


@ddt.ddt
@skip_unless_lms
class RegistrationViewValidationErrorTest(
    OpenEdxEventsTestMixin, ThirdPartyAuthTestMixin, UserAPITestCase, RetirementTestCase
):
    """
    Tests for catching duplicate email and username validation errors within
    the registration end-points of the User API.
    """

    ENABLED_OPENEDX_EVENTS = []

    maxDiff = None

    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"
    NAME = "Bob Smith"
    EDUCATION = "m"
    YEAR_OF_BIRTH = "1998"
    ADDRESS = "123 Fake Street"
    CITY = "Springfield"
    COUNTRY = "US"
    GOALS = "Learn all the things!"

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_registration")

    @mock.patch.dict(settings.FEATURES, {
        "ENABLE_THIRD_PARTY_AUTH": True,
    })
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.register.is_require_third_party_auth_enabled',
        mock.Mock(return_value=True)
    )
    def test_register_public_account_with_only_third_party_auth_failure(self):
        # fails to register for public user if only third party auth is allowed
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        assert response.status_code == 403
        assert response.content == (b"Third party authentication is required to register. "
                                    b"Username and password were received instead.")

    def test_register_retired_email_validation_error(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Initiate retirement for the above user:
        fake_requested_retirement(User.objects.get(username=self.USERNAME))

        # Try to create a second user with the same email address as the retired user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": "someone_else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        assert response.status_code == 409

        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email"
            }
        )

    def test_register_duplicate_retired_username_account_validation_error(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Initiate retirement for the above user.
        fake_requested_retirement(User.objects.get(username=self.USERNAME))

        with mock.patch('openedx.core.djangoapps.user_authn.views.register.do_create_account') as dummy_do_create_acct:
            # do_create_account should *not* be called - the duplicate retired username
            # should be detected before account creation is called.
            dummy_do_create_acct.side_effect = Exception('do_create_account should *not* have been called!')
            # Try to create a second user with the same username.
            response = self.client.post(self.url, {
                "email": "someone+else@example.com",
                "name": "Someone Else",
                "username": self.USERNAME,
                "password": self.PASSWORD,
                "honor_code": "true",
            })

        assert response.status_code == 409

        response_json = json.loads(response.content.decode('utf-8'))
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "error_code": "duplicate-username"
            }
        )

    def test_register_duplicate_email_validation_error(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same email address
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": "someone_else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409

        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email"
            }
        )

    def test_register_duplicate_email_validation_error_with_recovery(self):
        # Register the user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Create recovery object
        user = User.objects.get(email=self.EMAIL)
        account_recovery = AccountRecoveryFactory(user=user)

        # Try to create a user with the recovery email address
        response = self.client.post(self.url, {
            "email": account_recovery.secondary_email,
            "name": "Someone Else",
            "username": "someone_else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409

        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email"
            }
        )

    def test_register_fullname_url_validation_error(self):
        """
        Test for catching invalid full name errors
        """
        response = self.client.post(self.url, {
            "email": "bob@example.com",
            "name": "Bob Smith http://test.com",
            "username": "bob",
            "password": "password",
            "honor_code": "true",
        })
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "name": [{"user_message": 'Enter a valid name'}],
                "error_code": "validation-error"
            }
        )

        # testing for http/https
        response = self.client.post(self.url, {
            "email": "bob@example.com",
            "name": "http://",
            "username": "bob",
            "password": "password",
            "honor_code": "true",
        })
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "name": [{"user_message": 'Enter a valid name'}],
                "error_code": "validation-error"
            }
        )

    def test_register_fullname_max_lenghth_validation_error(self):
        """
        Full name error detection test if the length exceeds 255 characters.
        """
        expected_error_message = f"Your legal name is too long. It must not exceed {NAME_MAX_LENGTH} characters"

        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "x" * 256,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        assert response.status_code == 400

        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "name": [{"user_message": expected_error_message}],
                "error_code": "validation-error"
            }
        )

    def test_register_fullname_html_validation_error(self):
        """
        Test for catching invalid full name errors
        """
        response = self.client.post(self.url, {
            "email": "bob@example.com",
            "name": "<Bob Smith>",
            "username": "bob",
            "password": "password",
            "honor_code": "true",
        })
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                'name': [{'user_message': 'Full Name cannot contain the following characters: < >'}],
                "error_code": "validation-error"
            }
        )

    def test_register_duplicate_username_account_validation_error(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": "someone+else@example.com",
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "error_code": "duplicate-username"
            }
        )

    def test_register_duplicate_username_and_email_validation_errors(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username and email
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email-username"
            }
        )

    def test_duplicate_email_username_error(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username and email
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": self.COUNTRY,
            "honor_code": "true",
        })

        response_json = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 409
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email-username"
            }
        )

    def test_invalid_country_code_error(self):
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": "Invalid country code",
            "honor_code": "true",
        })

        response_json = json.loads(response.content.decode('utf-8'))
        self.assertHttpBadRequest(response)
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "country": [{
                    "user_message": REQUIRED_FIELD_COUNTRY_MSG,
                }],
                "error_code": "invalid-country"
            }
        )


@ddt.ddt
@skip_unless_lms
class RegistrationViewTestV1(
    OpenEdxEventsTestMixin, ThirdPartyAuthTestMixin, UserAPITestCase
):
    """Tests for the registration end-points of the User API. """

    ENABLED_OPENEDX_EVENTS = []

    maxDiff = None

    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"
    NAME = "Bob Smith"
    EDUCATION = "m"
    YEAR_OF_BIRTH = "1998"
    ADDRESS = "123 Fake Street"
    CITY = "Springfield"
    COUNTRY = "US"
    GOALS = "Learn all the things!"
    PROFESSION_OPTIONS = [
        {
            "name": '--',
            "value": '',
            "default": True

        },
        {
            "value": 'software engineer',
            "name": 'Software Engineer',
            "default": False
        },
        {
            "value": 'teacher',
            "name": 'Teacher',
            "default": False
        },
        {
            "value": 'other',
            "name": 'Other',
            "default": False
        }
    ]
    SPECIALTY_OPTIONS = [
        {
            "name": '--',
            "value": '',
            "default": True

        },
        {
            "value": "aerospace",
            "name": "Aerospace",
            "default": False
        },
        {
            "value": 'early education',
            "name": 'Early Education',
            "default": False
        },
        {
            "value": 'n/a',
            "name": 'N/A',
            "default": False
        }
    ]
    link_template = "<a href='/honor' rel='noopener' target='_blank'>{link_label}</a>"

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_registration")

    @ddt.data("get", "post")
    def test_auth_disabled(self, method):
        self.assertAuthDisabled(method, self.url)

    def test_allowed_methods(self):
        self.assertAllowedMethods(self.url, ["GET", "POST", "HEAD", "OPTIONS"])

    def test_put_not_allowed(self):
        response = self.client.put(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_delete_not_allowed(self):
        response = self.client.delete(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_patch_not_allowed(self):
        response = self.client.patch(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_register_form_default_fields(self):
        no_extra_fields_setting = {}

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "name": "email",
                "type": "email",
                "required": True,
                "label": "Email",
                "instructions": "This is what you will use to login.",
                "restrictions": {
                    "min_length": EMAIL_MIN_LENGTH,
                    "max_length": EMAIL_MAX_LENGTH
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "name": "name",
                "type": "text",
                "required": True,
                "label": "Full Name",
                "instructions": "This name will be used on any certificates that you earn.",
                "restrictions": {
                    "max_length": 255
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "name": "username",
                "type": "text",
                "required": True,
                "label": "Public Username",
                "instructions": "The name that will identify you in your courses. It cannot be changed later.",
                "restrictions": {
                    "min_length": USERNAME_MIN_LENGTH,
                    "max_length": USERNAME_MAX_LENGTH
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "placeholder": "",
                "name": "password",
                "type": "password",
                "required": True,
                "label": "Password",
                "instructions": password_validators_instruction_texts(),
                "restrictions": password_validators_restrictions(),
            }
        )

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 2}
        ),
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.UppercaseValidator', {'min_upper': 3}
        ),
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.SymbolValidator', {'min_symbol': 1}
        ),
    ])
    def test_register_form_password_complexity(self):
        no_extra_fields_setting = {}

        # Without enabling password policy
        self._assert_reg_field(
            no_extra_fields_setting,
            {
                'name': 'password',
                'label': 'Password',
                "instructions": password_validators_instruction_texts(),
                "restrictions": password_validators_restrictions(),
            }
        )

        msg = 'Your password must contain at least 2 characters, including ' \
              '3 uppercase letters & 1 symbol.'
        self._assert_reg_field(
            no_extra_fields_setting,
            {
                'name': 'password',
                'label': 'Password',
                'instructions': msg,
                "restrictions": password_validators_restrictions(),
            }
        )

    @override_settings(REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm')
    def test_extension_form_fields(self):
        no_extra_fields_setting = {}

        # Verify other fields didn't disappear for some reason.
        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "name": "email",
                "type": "email",
                "required": True,
                "label": "Email",
                "instructions": "This is what you will use to login.",
                "restrictions": {
                    "min_length": EMAIL_MIN_LENGTH,
                    "max_length": EMAIL_MAX_LENGTH
                },
            }
        )

        self._assert_reg_absent_field(
            no_extra_fields_setting,
            {
                "name": "favorite_editor",
                "type": "select",
                "required": False,
                "label": "Favorite Editor",
                "placeholder": "cat",
                "defaultValue": "vim",
                "errorMessages": {
                    'required': 'This field is required.',
                    'invalid_choice': 'Select a valid choice. %(value)s is not one of the available choices.',
                }
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                "name": "favorite_movie",
                "type": "text",
                "required": True,
                "label": "Fav Flick",
                "placeholder": None,
                "defaultValue": None,
                "errorMessages": {
                    'required': 'Please tell us your favorite movie.',
                    'invalid': "We're pretty sure you made that movie up."
                },
                "restrictions": {
                    "min_length": TestCaseForm.MOVIE_MIN_LEN,
                    "max_length": TestCaseForm.MOVIE_MAX_LEN,
                }
            }
        )

    @ddt.data(
        ('pk', 'PK', 'Bob123', 'Bob123'),
        ('Pk', 'PK', None, ''),
        ('pK', 'PK', 'Bob123@edx.org', 'Bob123_edx_org'),
        ('PK', 'PK', 'Bob123123123123123123123123123123123123', 'Bob123123123123123123123123123'),
        ('us', 'US', 'Bob-1231231&23123+1231(2312312312@3123123123', 'Bob-1231231_23123_1231_2312312'),
    )
    @ddt.unpack
    def test_register_form_third_party_auth_running_google(self, input_country_code, expected_country_code,
                                                           input_username, expected_username):
        no_extra_fields_setting = {}
        country_options = (
            [
                {
                    "name": "--",
                    "value": "",
                    "default": False
                }
            ] + [
                {
                    "value": country_code,
                    "name": str(country_name),
                    "default": country_code == expected_country_code
                }
                for country_code, country_name in SORTED_COUNTRIES
            ]
        )

        provider = self.configure_google_provider(enabled=True)
        with simulate_running_pipeline(
            "openedx.core.djangoapps.user_authn.views.login_form.third_party_auth.pipeline", "google-oauth2",
            email="bob@example.com",
            fullname="Bob",
            username=input_username,
            country=input_country_code
        ):
            self._assert_password_field_hidden(no_extra_fields_setting)
            self._assert_social_auth_provider_present(no_extra_fields_setting, provider)

            # Email should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    "name": "email",
                    "defaultValue": "bob@example.com",
                    "type": "email",
                    "required": True,
                    "label": "Email",
                    "instructions": "This is what you will use to login.",
                    "restrictions": {
                        "min_length": EMAIL_MIN_LENGTH,
                        "max_length": EMAIL_MAX_LENGTH
                    },
                }
            )

            # Full Name should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    "name": "name",
                    "defaultValue": "Bob",
                    "type": "text",
                    "required": True,
                    "label": "Full Name",
                    "instructions": "This name will be used on any certificates that you earn.",
                    "restrictions": {
                        "max_length": NAME_MAX_LENGTH,
                    }
                }
            )

            # Username should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    "name": "username",
                    "defaultValue": expected_username,
                    "type": "text",
                    "required": True,
                    "label": "Public Username",
                    "instructions": "The name that will identify you in your courses. It cannot be changed later.",
                    "restrictions": {
                        "min_length": USERNAME_MIN_LENGTH,
                        "max_length": USERNAME_MAX_LENGTH
                    }
                }
            )

            # Country should be filled in.
            self._assert_reg_field(
                {"country": "required"},
                {
                    "label": "Country or Region of Residence",
                    "name": "country",
                    "defaultValue": expected_country_code,
                    "type": "select",
                    "required": True,
                    "options": country_options,
                    "instructions": "The country or region where you live.",
                    "errorMessages": {
                        "required": "Select your country or region of residence"
                    },
                }
            )

    def test_register_form_level_of_education(self):
        self._assert_reg_field(
            {"level_of_education": "optional"},
            {
                "name": "level_of_education",
                "type": "select",
                "required": False,
                "label": "Highest level of education completed",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "p", "name": "Doctorate", "default": False},
                    {"value": "m", "name": "Master's or professional degree", "default": False},
                    {"value": "b", "name": "Bachelor's degree", "default": False},
                    {"value": "a", "name": "Associate degree", "default": False},
                    {"value": "hs", "name": "Secondary/high school", "default": False},
                    {"value": "jhs", "name": "Junior secondary/junior high/middle school", "default": False},
                    {"value": "el", "name": "Elementary/primary school", "default": False},
                    {"value": "none", "name": "No formal education", "default": False},
                    {"value": "other", "name": "Other education", "default": False},
                ],
                "errorMessages": {
                    "required": "Select the highest level of education you have completed"
                }
            }
        )

    @mock.patch('openedx.core.djangoapps.user_authn.views.registration_form._')
    def test_register_form_level_of_education_translations(self, fake_gettext):
        fake_gettext.side_effect = lambda text: text + ' TRANSLATED'

        self._assert_reg_field(
            {"level_of_education": "optional"},
            {
                "name": "level_of_education",
                "type": "select",
                "required": False,
                "label": "Highest level of education completed TRANSLATED",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "p", "name": "Doctorate TRANSLATED", "default": False},
                    {"value": "m", "name": "Master's or professional degree TRANSLATED", "default": False},
                    {"value": "b", "name": "Bachelor's degree TRANSLATED", "default": False},
                    {"value": "a", "name": "Associate degree TRANSLATED", "default": False},
                    {"value": "hs", "name": "Secondary/high school TRANSLATED", "default": False},
                    {"value": "jhs", "name": "Junior secondary/junior high/middle school TRANSLATED", "default": False},
                    {"value": "el", "name": "Elementary/primary school TRANSLATED", "default": False},
                    {"value": "none", "name": "No formal education TRANSLATED", "default": False},
                    {"value": "other", "name": "Other education TRANSLATED", "default": False},
                ],
                "errorMessages": {
                    "required": "Select the highest level of education you have completed"
                }
            }
        )

    def test_register_form_gender(self):
        self._assert_reg_field(
            {"gender": "optional"},
            {
                "name": "gender",
                "type": "select",
                "required": False,
                "label": "Gender",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "m", "name": "Male", "default": False},
                    {"value": "f", "name": "Female", "default": False},
                    {"value": "o", "name": "Other/Prefer Not to Say", "default": False},
                ],
            }
        )

    @mock.patch('openedx.core.djangoapps.user_authn.views.registration_form._')
    def test_register_form_gender_translations(self, fake_gettext):
        fake_gettext.side_effect = lambda text: text + ' TRANSLATED'

        self._assert_reg_field(
            {"gender": "optional"},
            {
                "name": "gender",
                "type": "select",
                "required": False,
                "label": "Gender TRANSLATED",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "m", "name": "Male TRANSLATED", "default": False},
                    {"value": "f", "name": "Female TRANSLATED", "default": False},
                    {"value": "o", "name": "Other/Prefer Not to Say TRANSLATED", "default": False},
                ],
            }
        )

    def test_register_form_year_of_birth(self):
        this_year = datetime.now(ZoneInfo("UTC")).year
        year_options = (
            [
                {
                    "value": "",
                    "name": "--",
                    "default": True
                }
            ] + [
                {
                    "value": str(year),
                    "name": str(year),
                    "default": False
                }
                for year in range(this_year, this_year - 120, -1)
            ]
        )
        self._assert_reg_field(
            {"year_of_birth": "optional"},
            {
                "name": "year_of_birth",
                "type": "select",
                "required": False,
                "label": "Year of birth",
                "options": year_options,
            }
        )

    def test_register_form_marketing_emails_opt_in_field(self):
        self._assert_reg_field(
            {"marketing_emails_opt_in": "optional"},
            {
                "name": "marketing_emails_opt_in",
                "type": "checkbox",
                "required": False,
                "label": 'I agree that {platform_name} may send me marketing messages.'.format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME,
                ),
                "exposed": True,
                "defaultValue": True,
            }
        )

    def test_register_form_profession_without_profession_options(self):
        self._assert_reg_field(
            {"profession": "required"},
            {
                "name": "profession",
                "type": "text",
                "required": True,
                "label": "Profession",
                "errorMessages": {
                    "required": "Enter your profession"
                }
            }
        )

    @with_site_configuration(
        configuration={
            "EXTRA_FIELD_OPTIONS": {"profession": ["Software Engineer", "Teacher", "Other"]}
        }
    )
    def test_register_form_profession_with_profession_options(self):
        self._assert_reg_field(
            {"profession": "required"},
            {
                "name": "profession",
                "type": "select",
                "required": True,
                "label": "Profession",
                "options": self.PROFESSION_OPTIONS,
                "errorMessages": {
                    "required": "Select your profession"
                },
            }
        )

    def test_register_form_specialty_without_specialty_options(self):
        self._assert_reg_field(
            {"specialty": "required"},
            {
                "name": "specialty",
                "type": "text",
                "required": True,
                "label": "Specialty",
                "errorMessages": {
                    "required": "Enter your specialty"
                }
            }
        )

    @with_site_configuration(
        configuration={
            "EXTRA_FIELD_OPTIONS": {"specialty": ["Aerospace", "Early Education", "N/A"]}
        }
    )
    def test_register_form_specialty_with_specialty_options(self):
        self._assert_reg_field(
            {"specialty": "required"},
            {
                "name": "specialty",
                "type": "select",
                "required": True,
                "label": "Specialty",
                "options": self.SPECIALTY_OPTIONS,
                "errorMessages": {
                    "required": "Select your specialty"
                },
            }
        )

    def test_registration_form_mailing_address(self):
        self._assert_reg_field(
            {"mailing_address": "optional"},
            {
                "name": "mailing_address",
                "type": "textarea",
                "required": False,
                "label": "Mailing address",
                "errorMessages": {
                    "required": "Enter your mailing address"
                }
            }
        )

    def test_registration_form_goals(self):
        self._assert_reg_field(
            {"goals": "optional"},
            {
                "name": "goals",
                "type": "textarea",
                "required": False,
                "label": "Tell us why you're interested in {platform_name}".format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME
                ),
                "errorMessages": {
                    "required": "Tell us your goals"
                }
            }
        )

    def test_registration_form_city(self):
        self._assert_reg_field(
            {"city": "optional"},
            {
                "name": "city",
                "type": "text",
                "required": False,
                "label": "City",
                "errorMessages": {
                    "required": "Enter your city"
                }
            }
        )

    def test_registration_form_state(self):
        self._assert_reg_field(
            {"state": "optional"},
            {
                "name": "state",
                "type": "text",
                "required": False,
                "label": "State/Province/Region",
            }
        )

    def test_registration_form_country(self):
        country_options = (
            [
                {
                    "name": "--",
                    "value": "",
                    "default": True
                }
            ] + [
                {
                    "value": country_code,
                    "name": str(country_name),
                    "default": False
                }
                for country_code, country_name in SORTED_COUNTRIES
            ]
        )
        self._assert_reg_field(
            {"country": "required"},
            {
                "label": "Country or Region of Residence",
                "name": "country",
                "type": "select",
                "instructions": "The country or region where you live.",
                "required": True,
                "options": country_options,
                "errorMessages": {
                    "required": "Select your country or region of residence"
                },
            }
        )

    def test_registration_form_confirm_email(self):
        pass

    @override_settings(
        MKTG_URLS={"ROOT": "https://www.test.com/", "HONOR": "honor"},
    )
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": True})
    def test_registration_honor_code_mktg_site_enabled(self):
        link_template = "<a href='https://www.test.com/honor' rel='noopener' target='_blank'>{link_label}</a>"
        link_template2 = "<a href='#' rel='noopener' target='_blank'>{link_label}</a>"
        link_label = "Terms of Service and Honor Code"
        link_label2 = "Privacy Policy"
        self._assert_reg_field(
            {"honor_code": "required"},
            {
                "label": ("By creating an account, you agree to the {spacing}"
                          "{link_label} {spacing}"
                          "and you acknowledge that {platform_name} and each Member process your "
                          "personal data in accordance {spacing}"
                          "with the {link_label2}.").format(
                    platform_name=settings.PLATFORM_NAME,
                    link_label=link_template.format(link_label=link_label),
                    link_label2=link_template2.format(link_label=link_label2),
                    spacing=' ' * 18
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "plaintext",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} {link_label}".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME,
                        link_label=link_label
                    )
                }
            }
        )

    @override_settings(MKTG_URLS_LINK_MAP={"HONOR": "honor"})
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": False})
    def test_registration_honor_code_mktg_site_disabled(self):
        link_template = "<a href='/privacy' rel='noopener' target='_blank'>{link_label}</a>"
        link_label = "Terms of Service and Honor Code"
        link_label2 = "Privacy Policy"
        self._assert_reg_field(
            {"honor_code": "required"},
            {
                "label": ("By creating an account, you agree to the {spacing}"
                          "{link_label} {spacing}"
                          "and you acknowledge that {platform_name} and each Member process your "
                          "personal data in accordance {spacing}"
                          "with the {link_label2}.").format(
                    platform_name=settings.PLATFORM_NAME,
                    link_label=self.link_template.format(link_label=link_label),
                    link_label2=link_template.format(link_label=link_label2),
                    spacing=' ' * 18
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "plaintext",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} {link_label}".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME,
                        link_label=link_label
                    )
                }
            }
        )

    @override_settings(MKTG_URLS={
        "ROOT": "https://www.test.com/",
        "HONOR": "honor",
        "TOS": "tos",
    })
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": True})
    def test_registration_separate_terms_of_service_mktg_site_enabled(self):
        # Honor code field should say ONLY honor code,
        # not "terms of service and honor code"
        link_label = 'Honor Code'
        link_template = "<a href='https://www.test.com/honor' rel='noopener' target='_blank'>{link_label}</a>"
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} {link_label}".format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME,
                    link_label=link_template.format(link_label=link_label)
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} {link_label}".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME,
                        link_label=link_label
                    )
                }
            }
        )

        # Terms of service field should also be present
        link_label = "Terms of Service"
        link_template = "<a href='https://www.test.com/tos' rel='noopener' target='_blank'>{link_label}</a>"
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} {link_label}".format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME,
                    link_label=link_template.format(link_label=link_label)
                ),
                "name": "terms_of_service",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} {link_label}".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME,
                        link_label=link_label
                    )
                }
            }
        )

    @override_settings(MKTG_URLS_LINK_MAP={"HONOR": "honor", "TOS": "tos"})
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": False})
    def test_registration_separate_terms_of_service_mktg_site_disabled(self):
        # Honor code field should say ONLY honor code,
        # not "terms of service and honor code"
        link_label = 'Honor Code'
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} {link_label}".format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME,
                    link_label=self.link_template.format(link_label=link_label)
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} Honor Code".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

        link_label = 'Terms of Service'
        # Terms of service field should also be present
        link_template = "<a href='/tos' rel='noopener' target='_blank'>{link_label}</a>"
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} {link_label}".format(  # noqa: UP032
                    platform_name=settings.PLATFORM_NAME,
                    link_label=link_template.format(link_label=link_label)
                ),
                "name": "terms_of_service",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} Terms of Service".format(  # noqa: UP032
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_FIELD_ORDER=None,
        REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm',
    )
    def test_field_order(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]
        assert field_names == [
            "email",
            "name",
            "username",
            "password",
            "city",
            "state",
            "country",
            "gender",
            "year_of_birth",
            "level_of_education",
            "mailing_address",
            "goals",
            "honor_code",
            "favorite_movie",
        ]

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_FIELD_ORDER=[
            "name",
            "username",
            "email",
            "confirm_email",
            "password",
            "first_name",
            "last_name",
            "city",
            "state",
            "country",
            "gender",
            "year_of_birth",
            "level_of_education",
            "company",
            "title",
            "job_title",
            "mailing_address",
            "goals",
            "honor_code",
            "terms_of_service",
            "specialty",
            "profession",
        ],
    )
    def test_field_order_override(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]
        assert field_names == ['name', 'username', 'email', 'password', 'city', 'state', 'country', 'gender',
                               'year_of_birth', 'level_of_education', 'mailing_address', 'goals', 'honor_code']

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm',
        REGISTRATION_FIELD_ORDER=[
            "name",
            "confirm_email",
            "password",
            "first_name",
            "last_name",
            "gender",
            "year_of_birth",
            "level_of_education",
            "company",
            "title",
            "mailing_address",
            "goals",
            "honor_code",
            "terms_of_service",
        ],
    )
    def test_field_order_invalid_override(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]

        assert field_names == [
            "name",
            "password",
            "gender",
            "year_of_birth",
            "level_of_education",
            "mailing_address",
            "goals",
            "honor_code",
            "city",
            "country",
            "email",
            "favorite_movie",
            "state",
            "username",
        ]

    def test_register(self):
        # Create a new registration
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)
        assert settings.EDXMKTG_LOGGED_IN_COOKIE_NAME in self.client.cookies
        assert settings.EDXMKTG_USER_INFO_COOKIE_NAME in self.client.cookies

        user = User.objects.get(username=self.USERNAME)
        request = RequestFactory().get('/url')
        request.user = user
        account_settings = get_account_settings(request)[0]

        assert self.USERNAME == account_settings["username"]
        assert self.EMAIL == account_settings["email"]
        assert not account_settings["is_active"]
        assert self.NAME == account_settings["name"]

        # Verify that we've been logged in
        # by trying to access a page that requires authentication
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

    @override_settings(REGISTRATION_EXTRA_FIELDS={
        "level_of_education": "optional",
        "gender": "optional",
        "year_of_birth": "optional",
        "mailing_address": "optional",
        "goals": "optional",
        "country": "required",
    })
    def test_register_with_profile_info(self):
        # Register, providing lots of demographic info
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "level_of_education": self.EDUCATION,
            "mailing_address": self.ADDRESS,
            "year_of_birth": self.YEAR_OF_BIRTH,
            "goals": self.GOALS,
            "country": self.COUNTRY,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Verify the user's account
        user = User.objects.get(username=self.USERNAME)
        request = RequestFactory().get('/url')
        request.user = user
        account_settings = get_account_settings(request)[0]

        assert account_settings["level_of_education"] == self.EDUCATION
        assert account_settings["mailing_address"] == self.ADDRESS
        assert account_settings["year_of_birth"] == int(self.YEAR_OF_BIRTH)
        assert account_settings["goals"] == self.GOALS
        assert account_settings["country"] == self.COUNTRY

    @override_settings(REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm')
    @mock.patch('openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm.DUMMY_STORAGE', new_callable=dict)
    @mock.patch(
        'openedx.core.djangoapps.user_api.tests.test_helpers.DummyRegistrationExtensionModel',
    )
    def test_with_extended_form(self, dummy_model, storage_dict):
        dummy_model_instance = mock.Mock()
        dummy_model.return_value = dummy_model_instance
        # Create a new registration
        assert storage_dict == {}
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
            "favorite_movie": "Inception",
            "favorite_editor": "cat",
        })
        self.assertHttpOK(response)
        assert settings.EDXMKTG_LOGGED_IN_COOKIE_NAME in self.client.cookies
        assert settings.EDXMKTG_USER_INFO_COOKIE_NAME in self.client.cookies

        user = User.objects.get(username=self.USERNAME)
        request = RequestFactory().get('/url')
        request.user = user
        account_settings = get_account_settings(request)[0]

        assert self.USERNAME == account_settings["username"]
        assert self.EMAIL == account_settings["email"]
        assert not account_settings["is_active"]
        assert self.NAME == account_settings["name"]

        assert storage_dict == {'favorite_movie': "Inception", "favorite_editor": "cat"}
        assert dummy_model_instance.user == user

        # Verify that we've been logged in
        # by trying to access a page that requires authentication
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

    def test_activation_email(self):
        # Register, which should trigger an activation email
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Verify that the activation email was sent
        assert len(mail.outbox) == 1
        sent_email = mail.outbox[0]
        assert sent_email.to == [self.EMAIL]
        assert sent_email.subject == \
               f'Action Required: Activate your {settings.PLATFORM_NAME} account'
        assert f'high-quality {settings.PLATFORM_NAME} courses' in sent_email.body

    @ddt.data(
        {"email": ""},
        {"email": "invalid"},
        {"name": ""},
        {"username": ""},
        {"username": "a"},
        {"password": ""},
    )
    def test_register_invalid_input(self, invalid_fields):
        # Initially, the field values are all valid
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
        }

        # Override the valid fields, making the input invalid
        data.update(invalid_fields)

        # Attempt to create the account, expecting an error response
        response = self.client.post(self.url, data)
        self.assertHttpBadRequest(response)

    @ddt.data("email", "name", "username", "password", "country")
    def test_register_missing_required_field(self, missing_field):
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": self.COUNTRY,
        }

        del data[missing_field]

        # Send a request missing a field
        response = self.client.post(self.url, data)
        self.assertHttpBadRequest(response)

    @override_settings(REGISTRATION_EXTRA_FIELDS={"country": "required"})
    def test_register_missing_country_required_field(self):
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": self.COUNTRY,
        }
        del data['country']

        response = self.client.post(self.url, data)
        response_json = json.loads(response.content.decode('utf-8'))

        self.assertHttpBadRequest(response)
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "country": [{
                    "user_message": REQUIRED_FIELD_COUNTRY_MSG,
                }],
                "error_code": "invalid-country"
            }
        )

    @override_settings(REGISTRATION_EXTRA_FIELDS={"country": "required"})
    def test_register_invalid_country_required_field(self):
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": "Invalid country code",
        }

        response = self.client.post(self.url, data)
        response_json = json.loads(response.content.decode('utf-8'))

        self.assertHttpBadRequest(response)
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "country": [{
                    "user_message": REQUIRED_FIELD_COUNTRY_MSG,
                }],
                "error_code": "invalid-country"
            }
        )

    def test_register_duplicate_email(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same email address
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": "someone_else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email"
            }
        )

    def test_register_duplicate_username(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": "someone+else@example.com",
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "error_code": "duplicate-username"
            }
        )

    def test_register_duplicate_username_and_email(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        username_suggestions = response_json.pop('username_suggestions')
        assert len(username_suggestions) == 3
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "email": [{
                    "user_message": AUTHN_EMAIL_CONFLICT_MSG,
                }],
                "error_code": "duplicate-email-username"
            }
        )

    @override_settings(REGISTRATION_EXTRA_FIELDS={"honor_code": "hidden", "terms_of_service": "hidden"})
    def test_register_hidden_honor_code_and_terms_of_service(self):
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
        })
        self.assertHttpOK(response)

    def test_missing_fields(self):
        response = self.client.post(
            self.url,
            {
                "email": self.EMAIL,
                "name": self.NAME,
                "honor_code": "true",
            }
        )
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{"user_message": USERNAME_BAD_LENGTH_MSG}],
                "password": [{"user_message": "This field is required."}],
                "error_code": "validation-error"
            }
        )

    def test_country_overrides(self):
        """Test that overridden countries are available in country list."""
        # Retrieve the registration form description
        with override_settings(REGISTRATION_EXTRA_FIELDS={"country": "required"}):
            response = self.client.get(self.url)
            self.assertHttpOK(response)

        self.assertContains(response, 'Kosovo')

    def test_password_with_spaces(self):
        """Test that spaces are stripped correctly from password while creating an account."""
        unstripped_password = self.PASSWORD + '  '
        with mock.patch(
            'openedx.core.djangoapps.user_authn.views.register.authenticate_new_user',
            wraps=authenticate_new_user
        ) as mock_authenticate_new_user:
            self.client.post(self.url, {
                "email": self.EMAIL,
                "name": self.NAME,
                "username": self.USERNAME,
                "password": unstripped_password,
                "honor_code": "true",
            })

            mock_authenticate_new_user.assert_called_with(
                mock_authenticate_new_user.call_args[0][0],  # get request object from mock
                self.USERNAME,
                unstripped_password.strip()
            )

    def test_create_account_not_allowed(self):
        """
        Test case to check user creation is forbidden when ALLOW_PUBLIC_ACCOUNT_CREATION feature flag is turned off
        """

        def _side_effect_for_get_value(value, default=None):
            """
            returns a side_effect with given return value for a given value
            """
            if value == 'ALLOW_PUBLIC_ACCOUNT_CREATION':
                return False
            else:
                return get_value(value, default)

        with mock.patch('openedx.core.djangoapps.site_configuration.helpers.get_value') as mock_get_value:
            mock_get_value.side_effect = _side_effect_for_get_value
            response = self.client.post(self.url, {"email": self.EMAIL, "username": self.USERNAME})
            assert response.status_code == 403

    @override_settings(FEATURES=ENABLE_AUTO_GENERATED_USERNAME)
    def test_register_with_auto_generated_username(self):
        """
        Test registration functionality with auto-generated username.

        This method tests the registration process when auto-generated username
        feature is enabled. It creates a new user account, verifies that the user
        account settings are correctly set, and checks if the user is successfully
        logged in after registration.
        """
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        user = User.objects.get(email=self.EMAIL)
        request = RequestFactory().get('/url')
        request.user = user
        account_settings = get_account_settings(request)[0]

        assert self.EMAIL == account_settings["email"]
        assert not account_settings["is_active"]
        assert self.NAME == account_settings["name"]

        # Verify that we've been logged in
        # by trying to access a page that requires authentication
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

    @override_settings(FEATURES=ENABLE_AUTO_GENERATED_USERNAME)
    def test_register_with_empty_name(self):
        """
        Test registration field validations when ENABLE_AUTO_GENERATED_USERNAME is enabled.

        Sends a POST request to the registration endpoint with empty name field.
        Expects a 400 Bad Request response with the corresponding validation error message for the name field.
        """
        response = self.client.post(self.url, {
            "email": "bob@example.com",
            "name": "",
            "password": "password",
            "honor_code": "true",
        })
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "name": [{"user_message": 'Your legal name must be a minimum of one character long'}],
                "error_code": "validation-error"
            }
        )

    @override_settings(FEATURES=ENABLE_AUTO_GENERATED_USERNAME)
    @mock.patch('openedx.core.djangoapps.user_authn.views.utils._get_username_prefix')
    @mock.patch('openedx.core.djangoapps.user_authn.views.utils.random.choices')
    @mock.patch('openedx.core.djangoapps.user_authn.views.utils.datetime')
    @mock.patch('openedx.core.djangoapps.user_authn.views.utils.get_auto_generated_username')
    def test_register_autogenerated_duplicate_username(self,
                                                       mock_get_auto_generated_username,
                                                       mock_datetime,
                                                       mock_choices,
                                                       mock_get_username_prefix):
        """
        Test registering a user with auto-generated username where a duplicate username conflict occurs.

        Mocks various utilities to control the auto-generated username process and verifies the response content
        when a duplicate username conflict happens during user registration.
        """
        mock_datetime.now.return_value.strftime.return_value = '24 03'
        mock_choices.return_value = ['X', 'Y', 'Z', 'A']  # Fixed random string for testing

        mock_get_username_prefix.return_value = None

        current_year_month = f"{datetime.now().year % 100}{datetime.now().month:02d}_"
        random_string = 'XYZA'
        expected_username = current_year_month + random_string
        mock_get_auto_generated_username.return_value = expected_username

        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)
        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": "someone+else@example.com",
            "name": "Someone Else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })

        assert response.status_code == 409
        response_json = json.loads(response.content.decode('utf-8'))
        response_json.pop('username_suggestions')
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "username": [{
                    "user_message": AUTHN_USERNAME_CONFLICT_MSG,
                }],
                "error_code": "duplicate-username"
            }
        )

    def _assert_fields_match(self, actual_field, expected_field):
        """
        Assert that the actual field and the expected field values match.
        """
        assert actual_field is not None, "Could not find field {name}".format(name=expected_field["name"])

        for key in expected_field:
            assert actual_field[key] == expected_field[key], \
                "Expected {expected} for {key} but got {actual} instead".format(  # noqa: UP032
                    key=key, actual=actual_field[key], expected=expected_field[key])

    def _populate_always_present_fields(self, field):
        """
        Populate field dictionary with keys and values that are always present.
        """
        defaults = [
            ("label", ""),
            ("instructions", ""),
            ("placeholder", ""),
            ("defaultValue", ""),
            ("restrictions", {}),
            ("errorMessages", {}),
        ]
        field.update({
            key: value
            for key, value in defaults if key not in field
        })

    def _assert_reg_field(self, extra_fields_setting, expected_field):
        """
        Retrieve the registration form description from the server and
        verify that it contains the expected field.

        Args:
            extra_fields_setting (dict): Override the Django setting controlling
                which extra fields are displayed in the form.
            expected_field (dict): The field definition we expect to find in the form.

        Raises:
            AssertionError
        """
        # Add in fields that are always present
        self._populate_always_present_fields(expected_field)

        # Retrieve the registration form description
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra_fields_setting):
            response = self.client.get(self.url)
            self.assertHttpOK(response)

        # Verify that the form description matches what we'd expect
        form_desc = json.loads(response.content.decode('utf-8'))

        actual_field = None
        for field in form_desc["fields"]:
            if field["name"] == expected_field["name"]:
                actual_field = field
                break

        self._assert_fields_match(actual_field, expected_field)

    def _assert_reg_absent_field(self, extra_fields_setting, expected_absent_field: str):
        """
        Retrieve the registration form description from the server and
        verify that it not contains the expected absent field.

        Args:
            extra_fields_setting (dict): Override the Django setting controlling
                which extra fields are displayed in the form.
            expected_absent_field (str): The field name we expect to be absent in the form.

        Raises:
            AssertionError
        """
        # Retrieve the registration form description
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra_fields_setting):
            response = self.client.get(self.url)
            self.assertHttpOK(response)

        # Verify that the form description matches what we'd expect
        form_desc = json.loads(response.content.decode('utf-8'))

        current_present_field_names = [field["name"] for field in form_desc["fields"]]
        assert expected_absent_field not in current_present_field_names, \
            "Expected absent field {expected}".format(expected=expected_absent_field)  # noqa: UP032

    def _assert_password_field_hidden(self, field_settings):
        self._assert_reg_field(field_settings, {
            "name": "password",
            "type": "hidden",
            "required": False
        })

    def _assert_social_auth_provider_present(self, field_settings, backend):
        self._assert_reg_field(field_settings, {
            "name": "social_auth_provider",
            "type": "hidden",
            "required": False,
            "defaultValue": backend.name
        })


@ddt.ddt
class RegistrationViewTestV2(RegistrationViewTestV1):
    """
    Test for registration api V2
    """

    # pylint: disable=test-inherits-tests

    def setUp(self):  # pylint: disable=arguments-differ
        super(RegistrationViewTestV1, self).setUp()  # pylint: disable=bad-super-call
        self.url = reverse("user_api_registration_v2")

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm',
        REGISTRATION_FIELD_ORDER=[
            "name",
            "confirm_email",
            "password",
            "first_name",
            "last_name",
            "gender",
            "year_of_birth",
            "level_of_education",
            "company",
            "title",
            "mailing_address",
            "goals",
            "honor_code",
            "terms_of_service",
        ],
    )
    def test_field_order_invalid_override(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]

        assert field_names == [
            "name",
            "confirm_email",
            "password",
            "gender",
            "year_of_birth",
            "level_of_education",
            "mailing_address",
            "goals",
            "honor_code",
            "city",
            "country",
            "email",
            "favorite_movie",
            "state",
            "username",
        ]

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_FIELD_ORDER=[
            "name",
            "username",
            "email",
            "confirm_email",
            "password",
            "first_name",
            "last_name",
            "city",
            "state",
            "country",
            "gender",
            "year_of_birth",
            "level_of_education",
            "company",
            "title",
            "job_title",
            "mailing_address",
            "goals",
            "honor_code",
            "terms_of_service",
            "specialty",
            "profession",
        ],
    )
    def test_field_order_override(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]
        assert field_names == ['name', 'username', 'email', 'confirm_email',
                               'password', 'city', 'state', 'country',
                               'gender', 'year_of_birth', 'level_of_education',
                               'mailing_address', 'goals', 'honor_code']

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_FIELD_ORDER=None,
        REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm',
    )
    def test_field_order(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]
        assert field_names == [
            "email",
            "name",
            "username",
            "password",
            "confirm_email",
            "city",
            "state",
            "country",
            "gender",
            "year_of_birth",
            "level_of_education",
            "mailing_address",
            "goals",
            "honor_code",
            "favorite_movie",
        ]

    @override_settings(
        ENABLE_COPPA_COMPLIANCE=True,
        REGISTRATION_EXTRA_FIELDS={
            "level_of_education": "optional",
            "gender": "optional",
            "year_of_birth": "optional",
            "mailing_address": "optional",
            "goals": "optional",
            "city": "optional",
            "state": "optional",
            "country": "required",
            "honor_code": "required",
            "confirm_email": "required",
        },
        REGISTRATION_FIELD_ORDER=None,
        REGISTRATION_EXTENSION_FORM='openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm',
    )
    def test_year_of_birth_field_with_feature_flag(self):
        """
        Test that year of birth is not returned when ENABLE_COPPA_COMPLIANCE is
        set to True.
        """
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content.decode('utf-8'))
        field_names = [field["name"] for field in form_desc["fields"]]
        assert field_names == [
            "email",
            "name",
            "username",
            "password",
            "confirm_email",
            "city",
            "state",
            "country",
            "gender",
            "level_of_education",
            "mailing_address",
            "goals",
            "honor_code",
            "favorite_movie",
        ]

    def test_registration_form_confirm_email(self):
        self._assert_reg_field(
            {"confirm_email": "required"},
            {
                "name": "confirm_email",
                "type": "email",
                "required": True,
                "label": "Confirm Email",
                "errorMessages": {
                    "required": "The email addresses do not match",
                }
            }
        )

    def _assert_redirect_url(self, response, expected_redirect_url):
        """
        Assert that the redirect URL is in the response and has the expected value.

        Assumes that response content is well-formed JSON
        (you can call `_assert_response` first to assert this).
        """
        response_dict = json.loads(response.content.decode('utf-8'))
        assert 'redirect_url' in response_dict, (
            "Response JSON unexpectedly does not have redirect_url: {!r}".format(  # noqa: UP032
                response_dict
            )
        )
        assert response_dict['redirect_url'] == expected_redirect_url

    @ddt.data(
        # Default redirect is dashboard.
        {
            'next_url': None,
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/dashboard',
        },
        # Added root url in next .
        {
            'next_url': '/harmless-relative-page',
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/harmless-relative-page',
        },
        # An absolute URL to a non-whitelisted domain is not an acceptable redirect.
        {
            'next_url': 'https://evil.sketchysite',
            'course_id': None,
            'expected_redirect': settings.LMS_ROOT_URL + '/dashboard',
        },
        # An absolute URL to a whitelisted domain is acceptable.
        {
            'next_url': 'https://openedx.service/coolpage',
            'course_id': None,
            'expected_redirect': 'https://openedx.service/coolpage',
        },
        # If course_id is provided, redirect to finish_auth with dashboard as next.
        {
            'next_url': None,
            'course_id': 'coursekey',
            'expected_redirect': f'{settings.LMS_ROOT_URL}/account/finish_auth?course_id=coursekey&next=%2Fdashboard',
        },
        # If valid course_id AND next_url are provided, redirect to finish_auth with
        # provided next URL.
        {
            'next_url': 'freshpage',
            'course_id': 'coursekey',
            'expected_redirect': (
                settings.LMS_ROOT_URL + '/account/finish_auth?course_id=coursekey&next=freshpage'
            )
        },
        # If course_id is provided with invalid next_url, redirect to finish_auth with
        # course_id and dashboard as next URL.
        {
            'next_url': 'http://scam.scam',
            'course_id': 'coursekey',
            'expected_redirect': f'{settings.LMS_ROOT_URL}/account/finish_auth?course_id=coursekey&next=%2Fdashboard',
        },
    )
    @ddt.unpack
    @override_settings(LOGIN_REDIRECT_WHITELIST=['openedx.service'])
    @skip_unless_lms
    def test_register_success_with_redirect(self, next_url, course_id, expected_redirect):
        expected_response = {
            'username': self.USERNAME,
            'full_name': self.NAME,
            'user_id': 1
        }
        post_params = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        }

        if next_url:
            post_params['next'] = next_url
        if course_id:
            post_params['course_id'] = course_id

        response = self.client.post(
            self.url,
            post_params,
            HTTP_ACCEPT='*/*',
        )
        self._assert_redirect_url(response, expected_redirect)
        assert response.status_code == 200

        # Check that authenticated user details are also returned in
        # the response for successful registration
        decoded_response = json.loads(response.content.decode('utf-8'))
        assert decoded_response['authenticated_user'] == expected_response

    @mock.patch('openedx.core.djangoapps.user_authn.views.register._record_is_marketable_attribute')
    def test_logs_for_error_when_setting_is_marketable_attribute(self, set_is_marketable_attr):
        """
        Test that if some error occurs while setting is_marketable attribute, error
        is logged and that it doesn't affect the user registration workflow.
        """
        set_is_marketable_attr.side_effect = Exception('BOOM!')
        post_params = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        }

        with LogCapture() as logger:
            response = self.client.post(
                self.url,
                post_params,
                HTTP_ACCEPT='*/*',
            )
            logger.check_present(
                (
                    'edx.student',
                    'ERROR',
                    'Error while setting is_marketable attribute.'
                )
            )

            assert response.status_code == 200

    @override_settings(
        ENABLE_AUTHN_REGISTER_HIBP_POLICY=True
    )
    @mock.patch('eventtracking.tracker.emit')
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.check_pwned_password',
        mock.Mock(return_value={
            'vulnerability': 'yes',
            'frequency': 3,
            'user_request_page': 'registration',
        })
    )
    def test_register_error_with_pwned_password(self, emit):
        post_params = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        }
        response = self.client.post(
            self.url,
            post_params,
            HTTP_ACCEPT='*/*',
        )
        emit.assert_called_with(
            'edx.bi.user.pwned.password.status',
            {
                'frequency': 3,
                'vulnerability': 'yes',
                'user_request_page': 'registration',
            })
        assert response.status_code == 400

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_register_with_disabled_country(self):
        """
        Test case to check user registration is forbidden when registration is disabled for a country
        """
        country = Country.objects.create(country="KP")
        GlobalRestrictedCountry.objects.create(country=country)
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
            "country": "KP",
        })
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {'country':
                [
                    {
                        'user_message': 'Registration from this country is not allowed due to restrictions.'
                    }
                ], 'error_code': 'validation-error'}
        )

    @patch.dict(settings.FEATURES, {'EMBARGO': False})
    def test_registration_allowed_when_embargo_disabled(self):
        """
        Ensures that user registration proceeds normally even for restricted countries
        when the EMBARGO feature flag is disabled.
        """
        country = Country.objects.create(country="KP")
        GlobalRestrictedCountry.objects.create(country=country)

        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
            "country": "KP",
        })

        self.assertEqual(response.status_code, 200)  # noqa: PT009


@httpretty.activate
@ddt.ddt
class ThirdPartyRegistrationTestMixin(
    OpenEdxEventsTestMixin, ThirdPartyOAuthTestMixin, CacheIsolationTestCase
):
    """
    Tests for the User API registration endpoint with 3rd party authentication.
    """
    CREATE_USER = False

    ENABLED_OPENEDX_EVENTS = []

    ENABLED_CACHES = ['default']

    __test__ = False

    def setUp(self):
        super().setUp()
        self.url = reverse('user_api_registration')

    def tearDown(self):
        super().tearDown()
        Partial.objects.all().delete()

    def data(self, user=None):
        """Returns the request data for the endpoint."""
        return {
            "provider": self.BACKEND,
            "access_token": self.access_token,
            "client_id": self.client_id,
            "honor_code": "true",
            "country": "US",
            "username": user.username if user else "test_username",
            "name": user.first_name if user else "test name",
            "email": user.email if user else "test@test.com"
        }

    def _assert_existing_user_error(self, response):
        """Assert that the given response was an error with the given status_code and error code."""
        assert response.status_code == 409
        errors = json.loads(response.content.decode('utf-8'))
        for conflict_attribute in ["username", "email"]:
            if conflict_attribute == 'username':
                error_message = AUTHN_USERNAME_CONFLICT_MSG
            else:
                error_message = AUTHN_EMAIL_CONFLICT_MSG
            assert conflict_attribute in errors
            assert error_message == errors[conflict_attribute][0]["user_message"]

    def _assert_access_token_error(self, response, expected_error_message, error_code):
        """Assert that the given response was an error for the access_token field with the given error message."""
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "access_token": [{"user_message": expected_error_message}],
                "error_code": error_code
            }
        )

    def _assert_third_party_session_expired_error(self, response, expected_error_message):
        """Assert that given response is an error due to third party session expiry"""
        assert response.status_code == 400
        response_json = json.loads(response.content.decode('utf-8'))
        self.assertDictEqual(  # noqa: PT009
            response_json,
            {
                "session_expired": [{"user_message": expected_error_message}],
                "error_code": "tpa-session-expired",
            }
        )

    def _verify_user_existence(self, user_exists, social_link_exists, user_is_active=None, username=None):
        """Verifies whether the user object exists."""
        users = User.objects.filter(username=(username if username else "test_username"))
        assert users.exists() == user_exists
        if user_exists:
            assert users[0].is_active == user_is_active
            self.assertEqual(  # noqa: PT009
                UserSocialAuth.objects.filter(user=users[0], provider=self.BACKEND).exists(),
                social_link_exists
            )
        else:
            assert UserSocialAuth.objects.count() == 0

    def test_success(self):
        self._verify_user_existence(user_exists=False, social_link_exists=False)

        self._setup_provider_response(success=True)
        response = self.client.post(self.url, self.data())
        assert response.status_code == 200

        self._verify_user_existence(user_exists=True, social_link_exists=True, user_is_active=False)

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_with_disabled_country(self):
        """
        Test case to check user registration is forbidden when registration is restricted for a country
        """
        self._verify_user_existence(user_exists=False, social_link_exists=False)
        self._setup_provider_response(success=True)
        country_obj = Country.objects.create(country="US")
        GlobalRestrictedCountry.objects.create(country=country_obj)
        response = self.client.post(self.url, self.data())
        assert response.status_code == 400
        assert response.json() == {
            'country': [
                {
                    'user_message': 'Registration from this country is not allowed due to restrictions.'
                }
            ], 'error_code': 'validation-error'
        }
        self._verify_user_existence(user_exists=False, social_link_exists=False, user_is_active=False)

    @patch.dict(settings.FEATURES, {'EMBARGO': False})
    def test_with_disabled_country_when_embargo_disabled(self):
        """
        Ensures that user registration proceeds normally even for restricted countries
        when the EMBARGO feature flag is disabled.
        """
        self._verify_user_existence(user_exists=False, social_link_exists=False)
        self._setup_provider_response(success=True)
        country_obj = Country.objects.create(country="US")
        GlobalRestrictedCountry.objects.create(country=country_obj)
        response = self.client.post(self.url, self.data())
        assert response.status_code == 200
        self._verify_user_existence(user_exists=True, social_link_exists=True, user_is_active=False)

    def test_unlinked_active_user(self):
        user = UserFactory()
        response = self.client.post(self.url, self.data(user))
        self._assert_existing_user_error(response)
        self._verify_user_existence(
            user_exists=True, social_link_exists=False, user_is_active=True, username=user.username
        )

    def test_unlinked_inactive_user(self):
        user = UserFactory(is_active=False)
        response = self.client.post(self.url, self.data(user))
        self._assert_existing_user_error(response)
        self._verify_user_existence(
            user_exists=True, social_link_exists=False, user_is_active=False, username=user.username
        )

    def test_user_already_registered(self):
        self._setup_provider_response(success=True)
        user = UserFactory()
        UserSocialAuth.objects.create(user=user, provider=self.BACKEND, uid=self.social_uid)
        response = self.client.post(self.url, self.data(user))
        self._assert_existing_user_error(response)
        self._verify_user_existence(
            user_exists=True, social_link_exists=True, user_is_active=True, username=user.username
        )

    def test_social_user_conflict(self):
        self._setup_provider_response(success=True)
        user = UserFactory()
        UserSocialAuth.objects.create(user=user, provider=self.BACKEND, uid=self.social_uid)
        response = self.client.post(self.url, self.data())
        self._assert_access_token_error(
            response,
            "The provided access_token is already associated with another user.",
            "tpa-token-already-associated"
        )
        self._verify_user_existence(
            user_exists=True, social_link_exists=True, user_is_active=True, username=user.username
        )

    def test_invalid_token(self):
        self._setup_provider_response(success=False)
        response = self.client.post(self.url, self.data())
        self._assert_access_token_error(response, "The provided access_token is not valid.", "tpa-invalid-access-token")
        self._verify_user_existence(user_exists=False, social_link_exists=False)

    def test_missing_token(self):
        data = self.data()
        data.pop("access_token")
        response = self.client.post(self.url, data)
        self._assert_access_token_error(
            response,
            f"An access_token is required when passing value ({self.BACKEND}) for provider.",
            "tpa-missing-access-token"
        )
        self._verify_user_existence(user_exists=False, social_link_exists=False)

    def test_expired_pipeline(self):
        """
        Test that there is an error and account is not created
        when request is made for account creation using third (Google, Facebook etc) party with pipeline
        getting expired using browser (not mobile application).

        NOTE: We are NOT using actual pipeline here so pipeline is always expired in this environment.
        we don't have to explicitly expire pipeline.
        """
        data = self.data()
        # provider is sent along request when request is made from mobile application
        data.pop("provider")
        # to identify that request is made using browser
        data.update({"social_auth_provider": "Google"})
        response = self.client.post(self.url, data)
        self._assert_third_party_session_expired_error(
            response,
            "Registration using {provider} has timed out.".format(provider="Google")
        )
        self._verify_user_existence(user_exists=False, social_link_exists=False)


@skipUnless(settings.FEATURES.get("ENABLE_THIRD_PARTY_AUTH"), "third party auth not enabled")
class TestFacebookRegistrationView(
    ThirdPartyRegistrationTestMixin, ThirdPartyOAuthTestMixinFacebook, TransactionTestCase
):
    """Tests the User API registration endpoint with Facebook authentication."""

    ENABLED_OPENEDX_EVENTS = []

    __test__ = True

    def test_social_auth_exception(self):
        """
        According to the do_auth method in social_core.backends.facebook.py,
        the Facebook API sometimes responds back a JSON with just False as value.
        """
        self._setup_provider_response_with_body(200, json.dumps("false"))
        response = self.client.post(self.url, self.data())
        self._assert_access_token_error(response, "The provided access_token is not valid.", "tpa-invalid-access-token")
        self._verify_user_existence(user_exists=False, social_link_exists=False)


@skipUnless(settings.FEATURES.get("ENABLE_THIRD_PARTY_AUTH"), "third party auth not enabled")
class TestGoogleRegistrationView(
    ThirdPartyRegistrationTestMixin, ThirdPartyOAuthTestMixinGoogle, TransactionTestCase
):
    """Tests the User API registration endpoint with Google authentication."""

    ENABLED_OPENEDX_EVENTS = []

    __test__ = True


@ddt.ddt
class RegistrationValidationViewTests(OpenEdxEventsTestMixin, test_utils.ApiTestCase):
    """
    Tests for validity of user data in registration forms.
    """

    ENABLED_OPENEDX_EVENTS = []

    endpoint_name = 'registration_validation'
    path = reverse(endpoint_name)

    def setUp(self):
        super().setUp()
        cache.clear()

    def get_validation_response(self, data):
        return self.client.post(self.path, data)

    def get_validation_decision(self, response):
        return response.data.get('validation_decisions', {})

    def get_username_suggestions(self, response):
        return response.data.get('username_suggestions', [])

    def assertValidationDecision(self, data, decision, validate_suggestions=False):
        response = self.get_validation_response(data)
        assert self.get_validation_decision(response) == decision
        if validate_suggestions:
            assert len(self.get_username_suggestions(response)) == 3

    def assertNotValidationDecision(self, data, decision):
        response = self.get_validation_response(data)
        assert self.get_validation_decision(response) != decision

    def test_no_decision_for_empty_request(self):
        self.assertValidationDecision(
            {},
            {}
        )

    def test_no_decision_for_invalid_request(self):
        self.assertValidationDecision(
            {'invalid_field': 'random_user_data'},
            {}
        )

    @ddt.data(
        ['name', list(testutils.VALID_NAMES)],
        ['email', list(testutils.VALID_EMAILS)],
        ['password', list(testutils.VALID_PASSWORDS)],
        ['username', list(testutils.VALID_USERNAMES)],
        ['country', list(testutils.VALID_COUNTRIES)],
    )
    @ddt.unpack
    # HIBP settings are only defined in lms envs but needed for common tests.
    @override_settings(
        ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY=False,
        ENABLE_AUTHN_REGISTER_HIBP_POLICY=False,
    )
    def test_positive_validation_decision(self, form_field_name, user_data):
        """
        Test if {0} as any item in {1} gives a positive validation decision.
        """
        self.assertValidationDecision(
            {form_field_name: user_data},
            {form_field_name: ''}
        )

    @ddt.data(
        # Skip None type for invalidity checks.
        ['name', testutils.INVALID_NAMES[1:]],
        ['email', testutils.INVALID_EMAILS[1:]],
        ['password', testutils.INVALID_PASSWORDS[1:]],
        ['username', testutils.INVALID_USERNAMES[1:]],
        ['country', testutils.INVALID_COUNTRIES[1:]],
    )
    @ddt.unpack
    def test_negative_validation_decision(self, form_field_name, user_data):
        """
        Test if {0} as any item in {1} gives a negative validation decision.
        """
        self.assertNotValidationDecision(
            {form_field_name: user_data},
            {form_field_name: ''}
        )

    @ddt.data(
        ['username', 'username@email.com', False],  # No conflict
        ['user', 'username@email.com', True],  # Username conflict
        ['username', 'user@email.com', False],  # Email conflict
        ['user', 'user@email.com', True]  # Both conflict
    )
    @ddt.unpack
    def test_existence_conflict(self, username, email, validate_suggestions):
        """
        Test if username '{0}' and email '{1}' have conflicts with
        username 'user' and email 'user@email.com'.
        """
        user = UserFactory.create(username='user', email='user@email.com')
        self.assertValidationDecision(
            {
                'username': username,
                'email': email
            },
            {
                # pylint: disable=no-member
                "username": AUTHN_USERNAME_CONFLICT_MSG if username == user.username else '',
                # pylint: disable=no-member
                "email": AUTHN_EMAIL_CONFLICT_MSG if email == user.email else ''
            },
            validate_suggestions
        )

    @ddt.data('', ('e' * EMAIL_MAX_LENGTH) + '@email.com')
    def test_email_bad_length_validation_decision(self, email):
        self.assertValidationDecision(
            {'email': email},
            {'email': EMAIL_BAD_LENGTH_MSG}
        )

    def test_email_generically_invalid_validation_decision(self):
        self.assertValidationDecision(
            {'email': 'email'},
            # pylint: disable=no-member
            {'email': AUTHN_EMAIL_INVALID_MSG}
        )

    def test_confirm_email_matches_email(self):
        email = 'user@email.com'
        self.assertValidationDecision(
            {'email': email, 'confirm_email': email},
            {'email': '', 'confirm_email': ''}
        )

    @ddt.data('', 'users@other.email')
    def test_confirm_email_doesnt_equal_email(self, confirm_email):
        self.assertValidationDecision(
            {'email': 'user@email.com', 'confirm_email': confirm_email},
            {'email': '', 'confirm_email': str(REQUIRED_FIELD_CONFIRM_EMAIL_MSG)}
        )

    @ddt.data(
        'u' * (USERNAME_MIN_LENGTH - 1),
        'u' * (USERNAME_MAX_LENGTH + 1)
    )
    def test_username_bad_length_validation_decision(self, username):
        self.assertValidationDecision(
            {'username': username},
            {'username': str(USERNAME_BAD_LENGTH_MSG)}
        )

    @skipUnless(settings.FEATURES.get("ENABLE_UNICODE_USERNAME"), "Unicode usernames disabled.")
    @ddt.data(*testutils.INVALID_USERNAMES_UNICODE)
    def test_username_invalid_unicode_validation_decision(self, username):
        self.assertValidationDecision(
            {'username': username},
            {'username': str(USERNAME_INVALID_CHARS_UNICODE)}
        )

    @skipIf(settings.FEATURES.get("ENABLE_UNICODE_USERNAME"), "Unicode usernames enabled.")
    @ddt.data(*testutils.INVALID_USERNAMES_ASCII)
    def test_username_invalid_ascii_validation_decision(self, username):
        self.assertValidationDecision(
            {'username': username},
            {"username": str(USERNAME_INVALID_CHARS_ASCII)}
        )

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 4}
        )
    ])
    def test_password_empty_validation_decision(self):
        # 2 is the default setting for minimum length found in openedx/envs/common.py
        # under AUTH_PASSWORD_VALIDATORS.MinimumLengthValidator
        msg = 'This password is too short. It must contain at least 4 characters.'
        self.assertValidationDecision(
            {'password': ''},
            {"password": msg}
        )

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config(
            'common.djangoapps.util.password_policy_validators.MinimumLengthValidator', {'min_length': 4}
        )
    ])
    def test_password_bad_min_length_validation_decision(self):
        password = 'p'
        # 2 is the default setting for minimum length found in openedx/envs/common.py
        # under AUTH_PASSWORD_VALIDATORS.MinimumLengthValidator
        msg = 'This password is too short. It must contain at least 4 characters.'
        self.assertValidationDecision(
            {'password': password},
            {"password": msg}
        )

    def test_password_bad_max_length_validation_decision(self):
        password = 'p' * DEFAULT_MAX_PASSWORD_LENGTH
        # 75 is the default setting for maximum length found in openedx/envs/common.py
        # under AUTH_PASSWORD_VALIDATORS.MaximumLengthValidator
        msg = 'This password is too long. It must contain no more than 75 characters.'
        self.assertValidationDecision(
            {'password': password},
            {"password": msg}
        )

    def test_password_equals_username_validation_decision(self):
        self.assertValidationDecision(
            {"username": "somephrase", "password": "somephrase"},
            {"username": "", "password": "The password is too similar to the username."}
        )

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'registration_proxy',
            }
        }
    )
    def test_rate_limiting_registration_view(self):
        """
        Confirm rate limits work as expected for registration
        end point /api/user/v1/validation/registration/. Note
        that drf's rate limiting makes use of the default cache
        to enforce limits; that's why this test needs a "real"
        default cache (as opposed to the usual-for-tests DummyCache)
        """
        for _ in range(int(settings.REGISTRATION_VALIDATION_RATELIMIT.split('/')[0])):
            response = self.request_without_auth('post', self.path)
            assert response.status_code != 403
        response = self.request_without_auth('post', self.path)
        assert response.status_code == 403

    def test_single_field_validation(self):
        """
        Test that if `is_authn_mfe` is provided in request along with form_field_key, only
        error message for that field is returned.
        """
        UserFactory.create(username='user', email='user@email.com')
        # using username and email that have conflicts but sending form_field_key will return
        # validation for only email
        self.assertValidationDecision(
            {'username': 'user', 'email': 'user@email.com', 'is_authn_mfe': True, 'form_field_key': 'email'},
            {'email': AUTHN_EMAIL_CONFLICT_MSG}
        )

    @mock.patch('eventtracking.tracker.emit')
    @mock.patch(
        'openedx.core.djangoapps.user_api.accounts.api.check_pwned_password',
        mock.Mock(return_value={
            'vulnerability': 'yes',
            'frequency': 3,
            'user_request_page': 'registration',
        })
    )
    # HIBP settings are only defined in lms envs but needed for tests here.
    @override_settings(
        ENABLE_AUTHN_REGISTER_HIBP_POLICY=True,
        ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY=True,
        HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD=3.0,
    )
    def test_pwned_password_and_emit_track_event(self, emit):
        self.assertValidationDecision(
            {'password': 'testtest12'},
            {'password': AUTHN_PASSWORD_COMPROMISED_MSG}
        )
        emit.assert_called_with(
            'edx.bi.user.pwned.password.status',
            {
                'frequency': 3,
                'vulnerability': 'yes',
                'user_request_page': 'registration',
            })
