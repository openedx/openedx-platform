"""
Unit tests for preference APIs.
"""


import json
from unittest.mock import patch

import ddt
from django.test.testcases import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ...accounts.tests.test_views import UserAPITestCase
from ..api import set_user_preference
from .test_api import get_expected_key_error_user_message, get_expected_validation_developer_message

TOO_LONG_PREFERENCE_KEY = "x" * 256


@ddt.ddt
@skip_unless_lms
class TestPreferencesAPI(UserAPITestCase):
    """
    Unit tests /api/user/v1/accounts/{username}/
    """
    def setUp(self):
        super().setUp()
        self.url_endpoint_name = "preferences_api"
        self.url = reverse(self.url_endpoint_name, kwargs={'username': self.user.username})

    def test_anonymous_access(self):
        """
        Test that an anonymous client (not logged in) cannot call GET or PATCH.
        """
        self.send_get(self.anonymous_client, expected_status=401)
        self.send_patch(self.anonymous_client, {}, expected_status=401)

    def test_unsupported_methods(self):
        """
        Test that DELETE, POST, and PUT are not supported.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        assert 405 == self.client.put(self.url).status_code
        assert 405 == self.client.post(self.url).status_code
        assert 405 == self.client.delete(self.url).status_code

    def test_get_different_user(self):
        """
        Test that a client (logged in) cannot get the preferences information for a different client.
        """
        self.different_client.login(username=self.different_user.username, password=TEST_PASSWORD)
        self.send_get(self.different_client, expected_status=403)

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_get_unknown_user(self, api_client, username):
        """
        Test that requesting a user who does not exist returns a 404 for staff users, but 403 for others.
        """
        client = self.login_client(api_client, username)
        response = client.get(reverse(self.url_endpoint_name, kwargs={'username': "does_not_exist"}))
        assert (404 if (username == 'staff_user') else 403) == response.status_code

    def test_get_preferences_default(self):
        """
        Test that a client (logged in) can get her own preferences information (verifying the default
        state before any preferences are stored).
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_get(self.client)
        assert {} == response.data

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_get_preferences(self, api_client, user):
        """
        Test that a client (logged in) can get her own preferences information.
        """
        # Create some test preferences values.
        set_user_preference(self.user, "dict_pref", {"int_key": 10})
        set_user_preference(self.user, "string_pref", "value")
        set_user_preference(self.user, "time_zone", "Asia/Tokyo")

        # Log in the client and do the GET.
        client = self.login_client(api_client, user)
        response = self.send_get(client)
        assert {'dict_pref': "{'int_key': 10}", 'string_pref': 'value', 'time_zone': 'Asia/Tokyo'} == response.data

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_patch_unknown_user(self, api_client, user):
        """
        Test that trying to update preferences for a user who does not exist returns a 403.
        """
        client = self.login_client(api_client, user)
        response = client.patch(
            reverse(self.url_endpoint_name, kwargs={'username': "does_not_exist"}),
            data=json.dumps({"string_pref": "value"}), content_type="application/merge-patch+json"
        )
        assert 403 == response.status_code

    def test_patch_bad_content_type(self):
        """
        Test the behavior of patch when an incorrect content_type is specified.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.send_patch(self.client, {}, content_type="application/json", expected_status=415)
        self.send_patch(self.client, {}, content_type="application/xml", expected_status=415)

    def test_create_preferences(self):
        """
        Test that a client (logged in) can create her own preferences information.
        """
        self._do_create_preferences_test(True)

    def test_create_preferences_inactive(self):
        """
        Test that a client (logged in but not active) can create her own preferences information.
        """
        self._do_create_preferences_test(False)

    def _do_create_preferences_test(self, is_active):
        """
        Internal helper to generalize the creation of a set of preferences
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        if not is_active:
            self.user.is_active = False
            self.user.save()
        self.send_patch(
            self.client,
            {
                "dict_pref": {"int_key": 10},
                "string_pref": "value",
            },
            expected_status=204
        )
        response = self.send_get(self.client)
        # lint-amnesty, pylint: disable=bad-option-value, unicode-format-string
        pref_dict = {"dict_pref": "{'int_key': 10}", "string_pref": "value"}
        assert pref_dict == response.data

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_create_preferences_other_user(self, api_client, user):
        """
        Test that a client (logged in) cannot create preferences for another user.
        """
        client = self.login_client(api_client, user)
        self.send_patch(
            client,
            {
                "dict_pref": {"int_key": 10},
                "string_pref": "value",
            },
            expected_status=403,
        )

    def test_update_preferences(self):
        """
        Test that a client (logged in) can update her own preferences information.
        """
        # Create some test preferences values.
        set_user_preference(self.user, "dict_pref", {"int_key": 10})
        set_user_preference(self.user, "string_pref", "value")
        set_user_preference(self.user, "extra_pref", "extra_value")
        set_user_preference(self.user, "time_zone", "Asia/Macau")

        # Send the patch request
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.send_patch(
            self.client,
            {
                "string_pref": "updated_value",
                "new_pref": "new_value",
                "extra_pref": None,
                "time_zone": "Europe/London",
            },
            expected_status=204
        )

        # Verify that GET returns the updated preferences
        response = self.send_get(self.client)
        expected_preferences = {
            "dict_pref": "{'int_key': 10}",  # lint-amnesty, pylint: disable=bad-option-value, unicode-format-string
            "string_pref": "updated_value",
            "new_pref": "new_value",
            "time_zone": "Europe/London",
        }
        assert expected_preferences == response.data

    def test_update_preferences_bad_data(self):
        """
        Test that a client (logged in) receives appropriate errors for a bad update.
        """
        # Create some test preferences values.
        set_user_preference(self.user, "dict_pref", {"int_key": 10})
        set_user_preference(self.user, "string_pref", "value")
        set_user_preference(self.user, "extra_pref", "extra_value")
        set_user_preference(self.user, "time_zone", "Pacific/Midway")

        # Send the patch request
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_patch(
            self.client,
            {
                "string_pref": "updated_value",
                TOO_LONG_PREFERENCE_KEY: "new_value",
                "new_pref": "new_value",
                "empty_pref_ȻħȺɍłɇs": "",
                "time_zone": "Asia/Africa",
            },
            expected_status=400
        )
        assert response.data.get('field_errors', None)
        field_errors = response.data["field_errors"]
        assert field_errors == {TOO_LONG_PREFERENCE_KEY: {'developer_message': get_expected_validation_developer_message(TOO_LONG_PREFERENCE_KEY, 'new_value'), 'user_message': get_expected_key_error_user_message(TOO_LONG_PREFERENCE_KEY, 'new_value')}, 'empty_pref_ȻħȺɍłɇs': {'developer_message': "Preference 'empty_pref_ȻħȺɍłɇs' cannot be set to an empty value.", 'user_message': "Preference 'empty_pref_ȻħȺɍłɇs' cannot be set to an empty value."}, 'time_zone': {'developer_message': "Value 'Asia/Africa' not valid for preference 'time_zone': Not in timezone set.", 'user_message': "Value 'Asia/Africa' is not a valid time zone selection."}}  # pylint: disable=line-too-long

        # Verify that GET returns the original preferences
        response = self.send_get(self.client)
        expected_preferences = {
            "dict_pref": "{'int_key': 10}",
            "string_pref": "value",
            "extra_pref": "extra_value",
            "time_zone": "Pacific/Midway",
        }
        assert expected_preferences == response.data

    def test_update_preferences_bad_request(self):
        """
        Test that a client (logged in) receives appropriate errors for a bad request.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

        # Verify a non-dict request
        response = self.send_patch(self.client, "non_dict_request", expected_status=400)
        assert response.data ==\
               {'developer_message': 'No data provided for user preference update',
                'user_message': 'No data provided for user preference update'}

        # Verify an empty dict request
        response = self.send_patch(self.client, {}, expected_status=400)
        assert response.data ==\
               {'developer_message': 'No data provided for user preference update',
                'user_message': 'No data provided for user preference update'}

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_update_preferences_other_user(self, api_client, user):
        """
        Test that a client (logged in) cannot update preferences for another user.
        """
        # Create some test preferences values.
        set_user_preference(self.user, "dict_pref", {"int_key": 10})
        set_user_preference(self.user, "string_pref", "value")
        set_user_preference(self.user, "extra_pref", "extra_value")

        # Send the patch request
        client = self.login_client(api_client, user)
        self.send_patch(
            client,
            {
                "string_pref": "updated_value",
                "new_pref": "new_value",
                "extra_pref": None,
            },
            expected_status=403
        )


@skip_unless_lms
class TestPreferencesAPITransactions(TransactionTestCase):
    """
    Tests the transactional behavior of the preferences API
    """
    test_password = "test"

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = UserFactory.create(password=TEST_PASSWORD)
        self.url = reverse("preferences_api", kwargs={'username': self.user.username})

    @patch('openedx.core.djangoapps.user_api.models.UserPreference.delete')
    def test_update_preferences_rollback(self, delete_user_preference):
        """
        Verify that updating preferences is transactional when a failure happens.
        """
        # Create some test preferences values.
        set_user_preference(self.user, "a", "1")
        set_user_preference(self.user, "b", "2")
        set_user_preference(self.user, "c", "3")

        # Send a PATCH request with two updates and a delete. The delete should fail
        # after one of the updates has happened, in which case the whole operation
        # should be rolled back.
        delete_user_preference.side_effect = [Exception, None]
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        json_data = {
            "a": "2",
            "b": None,
            "c": "1",
        }
        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")
        assert 400 == response.status_code

        # Verify that GET returns the original preferences
        response = self.client.get(self.url)
        expected_preferences = {
            "a": "1",
            "b": "2",
            "c": "3",
        }
        assert expected_preferences == response.data


@ddt.ddt
@skip_unless_lms
class TestPreferencesDetailAPI(UserAPITestCase):
    """
    Unit tests /api/user/v1/accounts/{username}/{preference_key}
    """
    def setUp(self):
        super().setUp()
        self.test_pref_key = "test_key"
        self.test_pref_value = "test_value"
        set_user_preference(self.user, self.test_pref_key, self.test_pref_value)
        self.url_endpoint_name = "preferences_detail_api"
        self._set_url(self.test_pref_key)

    def _set_url(self, preference_key):
        """
        Sets the url attribute including the username and provided preference key
        """
        self.url = reverse(
            self.url_endpoint_name,
            kwargs={'username': self.user.username, 'preference_key': preference_key}
        )

    def test_anonymous_user_access(self):
        """
        Test that an anonymous client (logged in) cannot manipulate preferences.
        """
        self.send_get(self.anonymous_client, expected_status=401)
        self.send_put(self.anonymous_client, "new_value", expected_status=401)
        self.send_delete(self.anonymous_client, expected_status=401)

    def test_unsupported_methods(self):
        """
        Test that POST and PATCH are not supported.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        assert 405 == self.client.post(self.url).status_code
        assert 405 == self.client.patch(self.url).status_code

    def test_different_user_access(self):
        """
        Test that a client (logged in) cannot manipulate a preference for a different client.
        """
        self.different_client.login(username=self.different_user.username, password=TEST_PASSWORD)
        self.send_get(self.different_client, expected_status=403)
        self.send_put(self.different_client, "new_value", expected_status=403)
        self.send_delete(self.different_client, expected_status=403)

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_get_unknown_user(self, api_client, username):
        """
        Test that requesting a user who does not exist returns a 404 for staff users, but 403 for others.
        """
        client = self.login_client(api_client, username)
        response = client.get(
            reverse(self.url_endpoint_name, kwargs={'username': "does_not_exist", 'preference_key': self.test_pref_key})
        )
        assert (404 if (username == 'staff_user') else 403) == response.status_code

    def test_get_preference_does_not_exist(self):
        """
        Test that a 404 is returned if the user does not have a preference with the given preference_key.
        """
        self._set_url("does_not_exist")
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_get(self.client, expected_status=404)
        assert response.data is None

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_get_preference(self, api_client, user):
        """
        Test that a client (logged in) can get her own preferences information. Also verifies that a "is_staff"
        user can get the preferences information for other users.
        """
        client = self.login_client(api_client, user)
        response = self.send_get(client)
        assert self.test_pref_value == response.data

        # Test a different value.
        set_user_preference(self.user, "dict_pref", {"int_key": 10})
        self._set_url("dict_pref")
        response = self.send_get(client)
        assert "{'int_key': 10}" == response.data

    def test_create_preference(self):
        """
        Test that a client (logged in) can create a preference.
        """
        self._do_create_preference_test(True)

    def test_create_preference_inactive(self):
        """
        Test that a client (logged in but not active) can create a preference.
        """
        self._do_create_preference_test(False)

    def _do_create_preference_test(self, is_active):
        """
        Generalization of the actual test workflow
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        if not is_active:
            self.user.is_active = False
            self.user.save()
        self._set_url("new_key")
        new_value = "new value"
        self.send_put(self.client, new_value)
        response = self.send_get(self.client)
        assert new_value == response.data

    @ddt.data(
        (None,),
        ("",),
        ("  ",),
    )
    @ddt.unpack
    def test_create_empty_preference(self, preference_value):
        """
        Test that a client (logged in) cannot create an empty preference.
        """
        self._set_url("new_key")
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_put(self.client, preference_value, expected_status=400)
        assert response.data ==\
               {'developer_message': "Preference 'new_key' cannot be set to an empty value.",
                'user_message': "Preference 'new_key' cannot be set to an empty value."}
        self.send_get(self.client, expected_status=404)

    def test_create_preference_too_long_key(self):
        """
        Test that a client cannot create preferences with bad keys
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

        too_long_preference_key = "x" * 256
        new_value = "new value"
        self._set_url(too_long_preference_key)
        response = self.send_put(self.client, new_value, expected_status=400)
        assert response.data ==\
               {'developer_message': get_expected_validation_developer_message(too_long_preference_key, new_value),
                'user_message': get_expected_key_error_user_message(too_long_preference_key, new_value)}

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_create_preference_other_user(self, api_client, user):
        """
        Test that a client (logged in) cannot create a preference for a different user.
        """
        # Verify that a new preference cannot be created
        self._set_url("new_key")
        client = self.login_client(api_client, user)
        new_value = "new value"
        self.send_put(client, new_value, expected_status=403)

    @ddt.data(
        ("new value",),
        (10,),
        ({"int_key": 10},)
    )
    @ddt.unpack
    def test_update_preference(self, preference_value):
        """
        Test that a client (logged in) can update a preference.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.send_put(self.client, preference_value)
        response = self.send_get(self.client)
        assert str(preference_value) == response.data

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_update_preference_other_user(self, api_client, user):
        """
        Test that a client (logged in) cannot update a preference for another user.
        """
        client = self.login_client(api_client, user)
        new_value = "new value"
        self.send_put(client, new_value, expected_status=403)

    @ddt.data(
        (None,),
        ("",),
        ("  ",),
    )
    @ddt.unpack
    def test_update_preference_to_empty(self, preference_value):
        """
        Test that a client (logged in) cannot update a preference to null.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_put(self.client, preference_value, expected_status=400)
        assert response.data == {'developer_message': "Preference 'test_key' cannot be set to an empty value.",
                                 'user_message': "Preference 'test_key' cannot be set to an empty value."}
        response = self.send_get(self.client)
        assert self.test_pref_value == response.data

    def test_delete_preference(self):
        """
        Test that a client (logged in) can delete her own preference.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

        # Verify that a preference can be deleted
        self.send_delete(self.client)
        self.send_get(self.client, expected_status=404)

        # Verify that deleting a non-existent preference throws a 404
        self.send_delete(self.client, expected_status=404)

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_delete_preference_other_user(self, api_client, user):
        """
        Test that a client (logged in) cannot delete a preference for another user.
        """
        client = self.login_client(api_client, user)
        self.send_delete(client, expected_status=403)
