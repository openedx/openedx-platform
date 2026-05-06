"""Tests for the user API at the HTTP request level. """

import ddt
import pytest
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from pytz import common_timezones, common_timezones_set, country_timezones

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.django_comment_common import models
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from openedx.core.lib.api.test_utils import TEST_API_KEY, ApiTestCase
from openedx.core.lib.time_zone_utils import get_display_time_zone
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

from ..accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    RetirementTestCase,  # noqa: F401
    fake_requested_retirement,  # noqa: F401
    setup_retirement_states,  # noqa: F401
)
from ..models import UserOrgTag
from ..tests.factories import UserPreferenceFactory

USER_LIST_URI = "/api/user/v1/users/"
USER_PREFERENCE_LIST_URI = "/api/user/v1/user_prefs/"
ROLE_LIST_URI = "/api/user/v1/forum_roles/Moderator/users/"


class UserAPITestCase(ApiTestCase):
    """
    Parent test case for User API workflow coverage
    """
    LIST_URI = USER_LIST_URI
    TEST_PASSWORD = 'Password1234'

    def get_uri_for_user(self, target_user):
        """Given a user object, get the URI for the corresponding resource"""
        users = self.get_json(USER_LIST_URI)["results"]
        for user in users:
            if user["id"] == target_user.id:
                return user["url"]
        self.fail()

    def get_uri_for_pref(self, target_pref):
        """Given a user preference object, get the URI for the corresponding resource"""
        prefs = self.get_json(USER_PREFERENCE_LIST_URI)["results"]
        for pref in prefs:
            if pref["user"]["id"] == target_pref.user.id and pref["key"] == target_pref.key:
                return pref["url"]
        self.fail()

    def assertUserIsValid(self, user):
        """Assert that the given user result is valid"""
        self.assertCountEqual(list(user.keys()), ["email", "id", "name", "username", "preferences", "url"])  # noqa: PT009  # pylint: disable=line-too-long
        self.assertCountEqual(  # noqa: PT009
            list(user["preferences"].items()),
            [(pref.key, pref.value) for pref in self.prefs if pref.user.id == user["id"]]  # pylint: disable=no-member
        )
        self.assertSelfReferential(user)

    def assertPrefIsValid(self, pref):
        """
        Assert that the given preference is acknowledged by the system
        """
        self.assertCountEqual(list(pref.keys()), ["user", "key", "value", "url"])  # noqa: PT009
        self.assertSelfReferential(pref)
        self.assertUserIsValid(pref["user"])


@skip_unless_lms
class EmptyUserTestCase(UserAPITestCase):
    """
    Test that the endpoint supports empty user result sets
    """
    def test_get_list_empty(self):
        result = self.get_json(self.LIST_URI)
        assert result['count'] == 0
        assert result['next'] is None
        assert result['previous'] is None
        assert result['results'] == []


@skip_unless_lms
class EmptyRoleTestCase(UserAPITestCase):
    """Test that the endpoint supports empty result sets"""
    course_id = CourseKey.from_string("org/course/run")
    LIST_URI = ROLE_LIST_URI + "?course_id=" + str(course_id)

    def test_get_list_empty(self):
        """Test that the endpoint properly returns empty result sets"""
        result = self.get_json(self.LIST_URI)
        assert result['count'] == 0
        assert result['next'] is None
        assert result['previous'] is None
        assert result['results'] == []


class UserApiTestCase(UserAPITestCase):
    """
    Generalized test case class for specific implementations below
    """
    def setUp(self):
        super().setUp()
        self.users = [
            UserFactory.create(
                email=f"test{i}@test.org",
                profile__name=f"Test {i}"
            )
            for i in range(5)
        ]
        self.prefs = [
            UserPreferenceFactory.create(user=self.users[0], key="key0"),
            UserPreferenceFactory.create(user=self.users[0], key="key1"),
            UserPreferenceFactory.create(user=self.users[1], key="key0")
        ]


@skip_unless_lms
class RoleTestCase(UserApiTestCase):
    """
    Test cases covering Role-related views and their behaviors
    """
    course_id = CourseKey.from_string("org/course/run")
    LIST_URI = ROLE_LIST_URI + "?course_id=" + str(course_id)

    def setUp(self):
        super().setUp()
        (role, _) = models.Role.objects.get_or_create(
            name=models.FORUM_ROLE_MODERATOR,
            course_id=self.course_id
        )
        for user in self.users:
            user.roles.add(role)

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_post_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.LIST_URI))

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.LIST_URI))

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=False)
    @override_settings(EDX_API_KEY=TEST_API_KEY)
    def test_basic_auth(self):
        # ensure that having basic auth headers in the mix does not break anything
        self.assertHttpOK(
            self.request_with_auth("get", self.LIST_URI,
                                   **self.basic_auth("someuser", "somepass")))
        self.assertHttpNotAuthorized(
            self.client.get(self.LIST_URI, **self.basic_auth("someuser", "somepass")))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        users = result["results"]
        assert result['count'] == len(self.users)
        assert len(users) == len(self.users)
        assert result['next'] is None
        assert result['previous'] is None
        for user in users:
            self.assertUserIsValid(user)

    def test_required_parameter(self):
        response = self.request_with_auth("get", ROLE_LIST_URI)
        self.assertHttpBadRequest(response)

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={
            "page_size": 3,
            "course_id": str(self.course_id),
        })
        assert first_page['count'] == 5
        first_page_next_uri = first_page["next"]
        assert first_page['previous'] is None
        first_page_users = first_page["results"]
        assert len(first_page_users) == 3

        second_page = self.get_json(first_page_next_uri)
        assert second_page['count'] == 5
        assert second_page['next'] is None
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        assert len(second_page_users) == 2

        assert self.get_json(second_page_prev_uri) == first_page

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        assert len(set(all_user_uris)) == 5


@skip_unless_lms
class UserViewSetTest(UserApiTestCase):
    """
    Test cases covering the User DRF view set class and its various behaviors
    """
    LIST_URI = USER_LIST_URI

    def setUp(self):
        super().setUp()
        self.detail_uri = self.get_uri_for_user(self.users[0])

    # List view tests

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_post_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.LIST_URI))

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.LIST_URI))

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=False)
    @override_settings(EDX_API_KEY=TEST_API_KEY)
    def test_basic_auth(self):
        # ensure that having basic auth headers in the mix does not break anything
        self.assertHttpOK(
            self.request_with_auth("get", self.LIST_URI,
                                   **self.basic_auth('someuser', 'somepass')))
        self.assertHttpNotAuthorized(
            self.client.get(self.LIST_URI, **self.basic_auth('someuser', 'somepass')))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        assert result['count'] == 5
        assert result['next'] is None
        assert result['previous'] is None
        users = result["results"]
        assert len(users) == 5
        for user in users:
            self.assertUserIsValid(user)

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 3})
        assert first_page['count'] == 5
        first_page_next_uri = first_page["next"]
        assert first_page['previous'] is None
        first_page_users = first_page["results"]
        assert len(first_page_users) == 3

        second_page = self.get_json(first_page_next_uri)
        assert second_page['count'] == 5
        assert second_page['next'] is None
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        assert len(second_page_users) == 2

        assert self.get_json(second_page_prev_uri) == first_page

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        assert len(set(all_user_uris)) == 5

    # Detail view tests

    def test_options_detail(self):
        self.assertAllowedMethods(self.detail_uri, ["OPTIONS", "GET", "HEAD"])

    def test_post_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.detail_uri))

    def test_put_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.detail_uri))

    def test_patch_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.detail_uri))

    def test_delete_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.detail_uri))

    def test_get_detail_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.detail_uri))

    def test_get_detail(self):
        user = self.users[1]
        uri = self.get_uri_for_user(user)
        assert self.get_json(uri) ==\
               {'email': user.email, 'id': user.id, 'name': user.profile.name, 'username': user.username,
                'preferences': {user_pref.key: user_pref.value for user_pref in self.prefs if user_pref.user == user},
                'url': uri}


@skip_unless_lms
class UserPreferenceViewSetTest(CacheIsolationTestCase, UserApiTestCase):
    """
    Test cases covering the User Preference DRF view class and its various behaviors
    """
    LIST_URI = USER_PREFERENCE_LIST_URI

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.detail_uri = self.get_uri_for_pref(self.prefs[0])

    # List view tests

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.LIST_URI))

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        assert result['count'] == 3
        assert result['next'] is None
        assert result['previous'] is None
        prefs = result["results"]
        assert len(prefs) == 3
        for pref in prefs:
            self.assertPrefIsValid(pref)

    def test_get_list_filter_key_empty(self):
        result = self.get_json(self.LIST_URI, data={"key": "non-existent"})
        assert result['count'] == 0
        assert result['results'] == []

    def test_get_list_filter_key_nonempty(self):
        result = self.get_json(self.LIST_URI, data={"key": "key0"})
        assert result['count'] == 2
        prefs = result["results"]
        assert len(prefs) == 2
        for pref in prefs:
            self.assertPrefIsValid(pref)
            assert pref['key'] == 'key0'

    def test_get_list_filter_user_empty(self):
        def test_id(user_id):
            result = self.get_json(self.LIST_URI, data={"user": user_id})
            assert result['count'] == 0
            assert result['results'] == []
        test_id(self.users[2].id)
        # TODO: If the given id does not match a user, then the filter is a no-op
        # test_id(42)
        # test_id("asdf")

    def test_get_list_filter_user_nonempty(self):
        user_id = self.users[0].id
        result = self.get_json(self.LIST_URI, data={"user": user_id})
        assert result['count'] == 2
        prefs = result["results"]
        assert len(prefs) == 2
        for pref in prefs:
            self.assertPrefIsValid(pref)
            assert pref['user']['id'] == user_id

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 2})
        assert first_page['count'] == 3
        first_page_next_uri = first_page["next"]
        assert first_page['previous'] is None
        first_page_prefs = first_page["results"]
        assert len(first_page_prefs) == 2

        second_page = self.get_json(first_page_next_uri)
        assert second_page['count'] == 3
        assert second_page['next'] is None
        second_page_prev_uri = second_page["previous"]
        second_page_prefs = second_page["results"]
        assert len(second_page_prefs) == 1

        assert self.get_json(second_page_prev_uri) == first_page

        for pref in first_page_prefs + second_page_prefs:
            self.assertPrefIsValid(pref)
        all_pref_uris = [pref["url"] for pref in first_page_prefs + second_page_prefs]
        assert len(set(all_pref_uris)) == 3

    # Detail view tests

    def test_options_detail(self):
        self.assertAllowedMethods(self.detail_uri, ["OPTIONS", "GET", "HEAD"])

    def test_post_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.detail_uri))

    def test_put_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.detail_uri))

    def test_patch_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.detail_uri))

    def test_delete_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.detail_uri))

    def test_detail_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.detail_uri))

    def test_get_detail(self):
        pref = self.prefs[1]
        uri = self.get_uri_for_pref(pref)
        assert self.get_json(uri) ==\
               {'user': {'email': pref.user.email, 'id': pref.user.id, 'name': pref.user.profile.name,
                         'username': pref.user.username,
                         'preferences': {user_pref.key: user_pref.value for user_pref in self.prefs if
                                         user_pref.user == pref.user}, 'url': self.get_uri_for_user(pref.user)},
                'key': pref.key, 'value': pref.value, 'url': uri}


@skip_unless_lms
class PreferenceUsersListViewTest(UserApiTestCase):
    """
    Test cases covering the list viewing behavior for user preferences
    """
    LIST_URI = "/api/user/v1/preferences/key0/users/"

    def test_options(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_put_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("patch", self.LIST_URI))

    def test_delete_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_unauthorized(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpNotAuthorized(self.client.get(self.LIST_URI))

    def test_get_basic(self):
        result = self.get_json(self.LIST_URI)
        assert result['count'] == 2
        assert result['next'] is None
        assert result['previous'] is None
        users = result["results"]
        assert len(users) == 2
        for user in users:
            self.assertUserIsValid(user)

    def test_get_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 1})
        assert first_page['count'] == 2
        first_page_next_uri = first_page["next"]
        assert first_page['previous'] is None
        first_page_users = first_page["results"]
        assert len(first_page_users) == 1

        second_page = self.get_json(first_page_next_uri)
        assert second_page['count'] == 2
        assert second_page['next'] is None
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        assert len(second_page_users) == 1

        assert self.get_json(second_page_prev_uri) == first_page

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        assert len(set(all_user_uris)) == 2


@ddt.ddt
@skip_unless_lms
class UpdateEmailOptInTestCase(UserAPITestCase, SharedModuleStoreTestCase):
    """Tests the UpdateEmailOptInPreference view. """

    USERNAME = "steve"
    EMAIL = "steve@isawesome.com"
    PASSWORD = "steveopolis"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.url = reverse("preferences_email_opt_in")

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    @ddt.data(
        ("True", "True"),
        ("true", "True"),
        ("TrUe", "True"),
        ("Banana", "False"),
        ("strawberries", "False"),
        ("False", "False"),
    )
    @ddt.unpack
    def test_update_email_opt_in(self, opt, result):
        """Tests the email opt in preference"""
        # Register, which should trigger an activation email
        response = self.client.post(self.url, {
            "course_id": str(self.course.id),
            "email_opt_in": opt
        })
        self.assertHttpOK(response)
        preference = UserOrgTag.objects.get(
            user=self.user, org=self.course.id.org, key="email-optin"
        )
        assert preference.value == result

    @ddt.data(
        (True, False),
        (False, True),
        (False, False)
    )
    @ddt.unpack
    def test_update_email_opt_in_wrong_params(self, use_course_id, use_opt_in):
        """Tests the email opt in preference"""
        params = {}
        if use_course_id:
            params["course_id"] = str(self.course.id)
        if use_opt_in:
            params["email_opt_in"] = "True"

        response = self.client.post(self.url, params)
        self.assertHttpBadRequest(response)

    def test_update_email_opt_in_inactive_user(self):
        """Test that an inactive user can still update their email optin preference."""
        self.user.is_active = False
        self.user.save()
        # Register, which should trigger an activation email
        response = self.client.post(self.url, {
            "course_id": str(self.course.id),
            "email_opt_in": "True"
        })
        self.assertHttpOK(response)
        preference = UserOrgTag.objects.get(
            user=self.user, org=self.course.id.org, key="email-optin"
        )
        assert preference.value == 'True'

    def test_update_email_opt_in_anonymous_user(self):
        """
        Test that an anonymous user gets 401 response when
        updating email opt-in preference.
        """
        self.client.logout()
        response = self.client.post(self.url, {
            "course_id": str(self.course.id),
            "email_opt_in": "True"
        })
        assert response.status_code == 403

    def test_update_email_opt_with_invalid_course_key(self):
        """
        Test that with invalid key it returns bad request
        and not update their email optin preference.
        """
        response = self.client.post(self.url, {
            "course_id": 'invalid',
            "email_opt_in": "True"
        })
        self.assertHttpBadRequest(response)
        with pytest.raises(UserOrgTag.DoesNotExist):
            UserOrgTag.objects.get(user=self.user, org=self.course.id.org, key="email-optin")


@ddt.ddt
@skip_unless_lms
class CountryTimeZoneListViewTest(UserApiTestCase):
    """
    Test cases covering the list viewing behavior for country time zones
    """
    ALL_TIME_ZONES_URI = "/api/user/v1/preferences/time_zones/"
    COUNTRY_TIME_ZONES_URI = "/api/user/v1/preferences/time_zones/?country_code=cA"

    @ddt.data(ALL_TIME_ZONES_URI, COUNTRY_TIME_ZONES_URI)
    def test_options(self, country_uri):
        """ Verify that following options are allowed """
        self.assertAllowedMethods(country_uri, ['OPTIONS', 'GET', 'HEAD'])

    @ddt.data(ALL_TIME_ZONES_URI, COUNTRY_TIME_ZONES_URI)
    def test_methods_not_allowed(self, country_uri):
        """ Verify that put, patch, and delete are not allowed """
        unallowed_methods = ['put', 'patch', 'delete']
        for unallowed_method in unallowed_methods:
            self.assertHttpMethodNotAllowed(self.request_with_auth(unallowed_method, country_uri))

    def _assert_time_zone_is_valid(self, time_zone_info):
        """ Asserts that the time zone is a valid pytz time zone """
        time_zone_name = time_zone_info['time_zone']
        assert time_zone_name in common_timezones_set
        assert time_zone_info['description'] == get_display_time_zone(time_zone_name)

    def test_get_country_timezones(self):
        """ Verify that correct time zone info is returned """
        results = self.get_json(self.COUNTRY_TIME_ZONES_URI)
        assert len(results) == len(country_timezones['cA'])
        for time_zone_info in results:
            self._assert_time_zone_is_valid(time_zone_info)

    def test_get_all_common_timezones(self):
        """ Verify that correct time zone info is returned """
        results = self.get_json(self.ALL_TIME_ZONES_URI)
        assert len(results) == len(common_timezones)
        for time_zone_info in results:
            self._assert_time_zone_is_valid(time_zone_info)
