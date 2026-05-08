"""
Unit tests for preference APIs.
"""


import datetime
from unittest.mock import patch

import ddt
import pytest
from dateutil.parser import parse as parse_datetime
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test.utils import override_settings
from django.urls import reverse
from pytz import common_timezones, utc

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.core.lib.time_zone_utils import get_display_time_zone
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

from ...errors import (  # pylint: disable=unused-import
    CountryCodeError,  # noqa: F401
    PreferenceUpdateError,
    PreferenceValidationError,
    UserNotAuthorized,
    UserNotFound,
)
from ...models import UserOrgTag
from ...preferences.api import (
    delete_user_preference,
    get_country_time_zones,
    get_user_preference,
    get_user_preferences,
    set_user_preference,
    update_email_opt_in,
    update_user_preferences,
)


@skip_unless_lms
class TestPreferenceAPI(CacheIsolationTestCase):
    """
    These tests specifically cover the parts of the API methods that are not covered by test_views.py.
    This includes the specific types of error raised, and default behavior when optional arguments
    are not specified.
    """
    password = "test"

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password=self.password)
        self.different_user = UserFactory.create(password=self.password)
        self.staff_user = UserFactory.create(is_staff=True, password=self.password)
        self.no_such_user = UserFactory.build(password=self.password, username="no_such_user")
        self.test_preference_key = "test_key"
        self.test_preference_value = "test_value"
        set_user_preference(self.user, self.test_preference_key, self.test_preference_value)

    def test_get_user_preference(self):
        """
        Verifies the basic behavior of get_user_preference.
        """
        assert get_user_preference(self.user, self.test_preference_key) == self.test_preference_value
        assert get_user_preference(self.staff_user, self.test_preference_key, username=self.user.username) == \
               self.test_preference_value

    def test_get_user_preference_errors(self):
        """
        Verifies that get_user_preference returns appropriate errors.
        """
        with pytest.raises(UserNotAuthorized):
            get_user_preference(self.user, self.test_preference_key, username="no_such_user")

        with pytest.raises(UserNotFound):
            get_user_preference(self.no_such_user, self.test_preference_key)

        with pytest.raises(UserNotAuthorized):
            get_user_preference(self.different_user, self.test_preference_key, username=self.user.username)

    def test_get_user_preferences(self):
        """
        Verifies the basic behavior of get_user_preferences.
        """
        expected_user_preferences = {
            self.test_preference_key: self.test_preference_value,
        }
        assert get_user_preferences(self.user) == expected_user_preferences
        assert get_user_preferences(self.staff_user, username=self.user.username) == expected_user_preferences

    def test_get_user_preferences_errors(self):
        """
        Verifies that get_user_preferences returns appropriate errors.
        """
        with pytest.raises(UserNotAuthorized):
            get_user_preferences(self.user, username="no_such_user")

        with pytest.raises(UserNotFound):
            get_user_preferences(self.no_such_user)

        with pytest.raises(UserNotAuthorized):
            get_user_preferences(self.different_user, username=self.user.username)

    def test_set_user_preference(self):
        """
        Verifies the basic behavior of set_user_preference.
        """
        test_key = 'ⓟⓡⓔⓕⓔⓡⓔⓝⓒⓔ_ⓚⓔⓨ'
        test_value = 'ǝnןɐʌ_ǝɔuǝɹǝɟǝɹd'
        set_user_preference(self.user, test_key, test_value)
        assert get_user_preference(self.user, test_key) == test_value
        set_user_preference(self.user, test_key, "new_value", username=self.user.username)
        assert get_user_preference(self.user, test_key) == 'new_value'

    @patch('openedx.core.djangoapps.user_api.models.UserPreference.save')
    def test_set_user_preference_errors(self, user_preference_save):
        """
        Verifies that set_user_preference returns appropriate errors.
        """
        with pytest.raises(UserNotAuthorized):
            set_user_preference(self.user, self.test_preference_key, "new_value", username="no_such_user")

        with pytest.raises(UserNotFound):
            set_user_preference(self.no_such_user, self.test_preference_key, "new_value")

        with pytest.raises(UserNotAuthorized):
            set_user_preference(self.staff_user, self.test_preference_key, "new_value", username=self.user.username)

        with pytest.raises(UserNotAuthorized):
            set_user_preference(self.different_user, self.test_preference_key, "new_value", username=self.user.username)

        too_long_key = "x" * 256
        with pytest.raises(PreferenceValidationError) as context_manager:
            set_user_preference(self.user, too_long_key, "new_value")
        errors = context_manager.value.preference_errors
        assert len(list(errors.keys())) == 1
        assert errors[too_long_key] ==\
               {'developer_message': get_expected_validation_developer_message(too_long_key, 'new_value'),
                'user_message': get_expected_key_error_user_message(too_long_key, 'new_value')}

        for empty_value in (None, "", "   "):
            with pytest.raises(PreferenceValidationError) as context_manager:
                set_user_preference(self.user, self.test_preference_key, empty_value)
            errors = context_manager.value.preference_errors
            assert len(list(errors.keys())) == 1
            assert errors[self.test_preference_key] ==\
                   {'developer_message': get_empty_preference_message(self.test_preference_key),
                    'user_message': get_empty_preference_message(self.test_preference_key)}

        user_preference_save.side_effect = [Exception, None]
        with pytest.raises(PreferenceUpdateError) as context_manager:
            set_user_preference(self.user, "new_key_ȻħȺɍłɇs", "new_value_ȻħȺɍłɇs")
        assert context_manager.value.developer_message ==\
               "Save failed for user preference 'new_key_ȻħȺɍłɇs' with value 'new_value_ȻħȺɍłɇs': "
        assert context_manager.value.user_message ==\
               "Save failed for user preference 'new_key_ȻħȺɍłɇs' with value 'new_value_ȻħȺɍłɇs'."

    def test_update_user_preferences(self):
        """
        Verifies the basic behavior of update_user_preferences.
        """
        set_user_preference(self.user, self.test_preference_key, "new_value")
        assert get_user_preference(self.user, self.test_preference_key) == 'new_value'
        set_user_preference(self.user, self.test_preference_key, "new_value", username=self.user.username)
        assert get_user_preference(self.user, self.test_preference_key) == 'new_value'

    def test_update_user_preferences_with_username(self):
        """
        Verifies the basic behavior of update_user_preferences when passed
        username string.
        """
        update_data = {
            self.test_preference_key: "new_value"
        }
        update_user_preferences(self.user, update_data, user=self.user.username)
        assert get_user_preference(self.user, self.test_preference_key) == 'new_value'

    def test_update_user_preferences_with_user(self):
        """
        Verifies the basic behavior of update_user_preferences when passed
        user object.
        """
        update_data = {
            self.test_preference_key: "new_value"
        }
        update_user_preferences(self.user, update_data, user=self.user)
        assert get_user_preference(self.user, self.test_preference_key) == 'new_value'

    @patch('openedx.core.djangoapps.user_api.models.UserPreference.delete')
    @patch('openedx.core.djangoapps.user_api.models.UserPreference.save')
    def test_update_user_preferences_errors(self, user_preference_save, user_preference_delete):
        """
        Verifies that set_user_preferences returns appropriate errors.
        """
        update_data = {
            self.test_preference_key: "new_value"
        }
        with pytest.raises(UserNotAuthorized):
            update_user_preferences(self.user, update_data, user="no_such_user")

        with pytest.raises(UserNotFound):
            update_user_preferences(self.no_such_user, update_data)

        with pytest.raises(UserNotAuthorized):
            update_user_preferences(self.staff_user, update_data, user=self.user.username)

        with pytest.raises(UserNotAuthorized):
            update_user_preferences(self.different_user, update_data, user=self.user.username)

        too_long_key = "x" * 256
        with pytest.raises(PreferenceValidationError) as context_manager:
            update_user_preferences(self.user, {too_long_key: "new_value"})
        errors = context_manager.value.preference_errors
        assert len(list(errors.keys())) == 1
        assert errors[too_long_key] ==\
               {'developer_message': get_expected_validation_developer_message(too_long_key, 'new_value'),
                'user_message': get_expected_key_error_user_message(too_long_key, 'new_value')}

        for empty_value in ("", "   "):
            with pytest.raises(PreferenceValidationError) as context_manager:
                update_user_preferences(self.user, {self.test_preference_key: empty_value})
            errors = context_manager.value.preference_errors
            assert len(list(errors.keys())) == 1
            assert errors[self.test_preference_key] ==\
                   {'developer_message': get_empty_preference_message(self.test_preference_key),
                    'user_message': get_empty_preference_message(self.test_preference_key)}

        user_preference_save.side_effect = [Exception, None]
        with pytest.raises(PreferenceUpdateError) as context_manager:
            update_user_preferences(self.user, {self.test_preference_key: "new_value"})
        assert context_manager.value.developer_message ==\
               "Save failed for user preference 'test_key' with value 'new_value': "
        assert context_manager.value.user_message ==\
               "Save failed for user preference 'test_key' with value 'new_value'."

        user_preference_delete.side_effect = [Exception, None]
        with pytest.raises(PreferenceUpdateError) as context_manager:
            update_user_preferences(self.user, {self.test_preference_key: None})
        assert context_manager.value.developer_message == "Delete failed for user preference 'test_key': "
        assert context_manager.value.user_message == "Delete failed for user preference 'test_key'."

    def test_delete_user_preference(self):
        """
        Verifies the basic behavior of delete_user_preference.
        """
        assert delete_user_preference(self.user, self.test_preference_key)
        set_user_preference(self.user, self.test_preference_key, self.test_preference_value)
        assert delete_user_preference(self.user, self.test_preference_key, username=self.user.username)
        assert not delete_user_preference(self.user, 'no_such_key')

    @patch('openedx.core.djangoapps.user_api.models.UserPreference.delete')
    def test_delete_user_preference_errors(self, user_preference_delete):
        """
        Verifies that delete_user_preference returns appropriate errors.
        """
        with pytest.raises(UserNotAuthorized):
            delete_user_preference(self.user, self.test_preference_key, username="no_such_user")

        with pytest.raises(UserNotFound):
            delete_user_preference(self.no_such_user, self.test_preference_key)

        with pytest.raises(UserNotAuthorized):
            delete_user_preference(self.staff_user, self.test_preference_key, username=self.user.username)

        with pytest.raises(UserNotAuthorized):
            delete_user_preference(self.different_user, self.test_preference_key, username=self.user.username)

        user_preference_delete.side_effect = [Exception, None]
        with pytest.raises(PreferenceUpdateError) as context_manager:
            delete_user_preference(self.user, self.test_preference_key)
        assert context_manager.value.developer_message == "Delete failed for user preference 'test_key': "
        assert context_manager.value.user_message == "Delete failed for user preference 'test_key'."


@ddt.ddt
# HIBP settings are only defined in lms envs but needed for common tests.
@override_settings(ENABLE_AUTHN_REGISTER_HIBP_POLICY=False)
class UpdateEmailOptInTests(ModuleStoreTestCase):
    """
    Test cases to cover API-driven email list opt-in update workflows
    """
    USERNAME = 'claire-underwood'
    PASSWORD = 'ṕáśśẃőŕd'
    EMAIL = 'claire+underwood@example.com'

    def _create_account(self, username, password, email):
        # pylint: disable=missing-docstring
        registration_url = reverse('user_api_registration')
        resp = self.client.post(registration_url, {
            'username': username,
            'email': email,
            'password': password,
            'name': username,
            'honor_code': 'true',
        })
        assert resp.status_code == 200

    @ddt.data(
        # Check that a 27 year old can opt-in
        (27, True, "True"),

        # Check that a 32-year old can opt-out
        (32, False, "False"),

        # Check that someone 14 years old can opt-in
        (14, True, "True"),

        # Check that someone 13 years old cannot opt-in (must have turned 13 before this year)
        (13, True, "False"),

        # Check that someone 12 years old cannot opt-in
        (12, True, "False")
    )
    @ddt.unpack
    @override_settings(EMAIL_OPTIN_MINIMUM_AGE=13)
    def test_update_email_optin(self, age, option, expected_result):
        # Create the course and account.
        course = CourseFactory.create()
        self._create_account(self.USERNAME, self.PASSWORD, self.EMAIL)

        # Set year of birth
        user = User.objects.get(username=self.USERNAME)
        profile = UserProfile.objects.get(user=user)
        year_of_birth = datetime.datetime.now().year - age
        profile.year_of_birth = year_of_birth
        profile.save()

        update_email_opt_in(user, course.id.org, option)
        result_obj = UserOrgTag.objects.get(user=user, org=course.id.org, key='email-optin')
        assert result_obj.value == expected_result

    def test_update_email_optin_no_age_set(self):
        # Test that the API still works if no age is specified.
        # Create the course and account.
        course = CourseFactory.create()
        self._create_account(self.USERNAME, self.PASSWORD, self.EMAIL)

        user = User.objects.get(username=self.USERNAME)

        update_email_opt_in(user, course.id.org, True)
        result_obj = UserOrgTag.objects.get(user=user, org=course.id.org, key='email-optin')
        assert result_obj.value == 'True'

    def test_update_email_optin_anonymous_user(self):
        """Verify that the API raises an exception for a user with no profile."""
        course = CourseFactory.create()
        no_profile_user, __ = User.objects.get_or_create(username="no_profile_user", password=self.PASSWORD)
        with pytest.raises(UserNotFound):
            update_email_opt_in(no_profile_user, course.id.org, True)

    @ddt.data(
        # Check that a 27 year old can opt-in, then out.
        (27, True, False, "False"),

        # Check that a 32-year old can opt-out, then in.
        (32, False, True, "True"),

        # Check that someone 13 years old can opt-in, then out.
        (13, True, False, "False"),

        # Check that someone 12 years old cannot opt-in, then explicitly out.
        (12, True, False, "False")
    )
    @ddt.unpack
    @override_settings(EMAIL_OPTIN_MINIMUM_AGE=13)
    def test_change_email_optin(self, age, option, second_option, expected_result):
        # Create the course and account.
        course = CourseFactory.create()
        self._create_account(self.USERNAME, self.PASSWORD, self.EMAIL)

        # Set year of birth
        user = User.objects.get(username=self.USERNAME)
        profile = UserProfile.objects.get(user=user)
        year_of_birth = datetime.datetime.now(utc).year - age
        profile.year_of_birth = year_of_birth
        profile.save()

        update_email_opt_in(user, course.id.org, option)
        update_email_opt_in(user, course.id.org, second_option)

        result_obj = UserOrgTag.objects.get(user=user, org=course.id.org, key='email-optin')
        assert result_obj.value == expected_result

    def _assert_is_datetime(self, timestamp):
        """
        Internal helper to assert the type of the provided timestamp value
        """
        if not timestamp:
            return False
        try:
            parse_datetime(timestamp)
        except ValueError:
            return False
        else:
            return True


@ddt.ddt
class CountryTimeZoneTest(CacheIsolationTestCase):
    """
    Test cases to validate country code api functionality
    """

    @ddt.data(('ES', ['Africa/Ceuta', 'Atlantic/Canary', 'Europe/Madrid']),
              (None, common_timezones[:10]),
              ('AA', common_timezones[:10]))
    @ddt.unpack
    def test_get_country_time_zones(self, country_code, expected_time_zones):
        """
        Verify that list of common country time zones dictionaries is returned
        An unrecognized country code (e.g. AA) will return the list of common timezones
        """
        expected_dict = [
            {
                'time_zone': time_zone,
                'description': get_display_time_zone(time_zone)
            }
            for time_zone in expected_time_zones
        ]
        country_time_zones_dicts = get_country_time_zones(country_code)[:10]
        assert country_time_zones_dicts == expected_dict


def get_expected_validation_developer_message(preference_key, preference_value):
    """
    Returns the expected dict of validation messages for the specified key.
    """
    return "Value '{preference_value}' not valid for preference '{preference_key}': {error}".format(
        preference_key=preference_key,
        preference_value=preference_value,
        error={
            "key": ["Ensure this field has no more than 255 characters."]
        }
    )


def get_expected_key_error_user_message(preference_key, preference_value):  # pylint: disable=unused-argument
    """
    Returns the expected user message for an invalid key.
    """
    return f"Invalid user preference key '{preference_key}'."


def get_empty_preference_message(preference_key):
    """
    Returns the validation message shown for an empty preference.
    """
    return f"Preference '{preference_key}' cannot be set to an empty value."
