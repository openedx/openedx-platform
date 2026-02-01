"""
Test cases to cover Accounts-related behaviors of the User API application
"""

import datetime
import hashlib
import json
from copy import deepcopy
from unittest import mock
from urllib.parse import quote

import ddt
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.test.testcases import TransactionTestCase
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from storages.backends.s3boto3 import S3Boto3Storage

from common.djangoapps.student.models import PendingEmailChange, UserProfile
from common.djangoapps.student.models_api import do_name_change_request, get_pending_name_change
from common.djangoapps.student.tests.factories import (
    TEST_PASSWORD,
    ContentTypeFactory,
    PermissionFactory,
    RegistrationFactory,
    UserFactory
)
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.accounts import ACCOUNT_VISIBILITY_PREF_KEY
from openedx.core.djangoapps.user_api.accounts.tests.factories import (
    RetirementStateFactory,
    UserRetirementStatusFactory
)
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_storage
from openedx.core.djangoapps.user_api.models import UserPreference, UserRetirementStatus
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, FilteredQueryCountMixin, skip_unless_lms
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service

from .. import ALL_USERS_VISIBILITY, CUSTOM_VISIBILITY, PRIVATE_VISIBILITY

TEST_PROFILE_IMAGE_UPLOADED_AT = datetime.datetime(2002, 1, 9, 15, 43, 1, tzinfo=ZoneInfo("UTC"))

# this is used in one test to check the behavior of profile image url
# generation with a relative url in the config.
TEST_PROFILE_IMAGE_BACKEND = deepcopy(settings.PROFILE_IMAGE_BACKEND)
TEST_PROFILE_IMAGE_BACKEND['options']['base_url'] = '/profile-images/'

TEST_BIO_VALUE = "Tired mother of twins"
TEST_LANGUAGE_PROFICIENCY_CODE = "hi"


class UserAPITestCase(APITestCase):
    """
    The base class for all tests of the User API
    """
    VERIFIED_NAME = "Verified User"

    def setUp(self):
        super().setUp()

        self.anonymous_client = APIClient()
        self.different_user = UserFactory.create(password=TEST_PASSWORD)
        self.different_client = APIClient()
        self.staff_user = UserFactory(is_staff=True, password=TEST_PASSWORD)
        self.staff_client = APIClient()
        self.user = UserFactory.create(password=TEST_PASSWORD)  # will be assigned to self.client by default
        self.name_affirmation_service = get_name_affirmation_service()

    def login_client(self, api_client, user):
        """Helper method for getting the client and user and logging in. Returns client. """
        client = getattr(self, api_client)
        user = getattr(self, user)
        client.login(username=user.username, password=TEST_PASSWORD)
        return client

    def send_post(self, client, json_data, content_type='application/json', expected_status=201):
        """
        Helper method for sending a post to the server, defaulting to application/json content_type.
        Verifies the expected status and returns the response.
        """
        # pylint: disable=no-member
        response = client.post(self.url, data=json.dumps(json_data), content_type=content_type)
        assert expected_status == response.status_code
        return response

    def send_patch(self, client, json_data, content_type="application/merge-patch+json", expected_status=200):
        """
        Helper method for sending a patch to the server, defaulting to application/merge-patch+json content_type.
        Verifies the expected status and returns the response.
        """
        # pylint: disable=no-member
        response = client.patch(self.url, data=json.dumps(json_data), content_type=content_type)
        assert expected_status == response.status_code
        return response

    def post_search_api(self, client, json_data, content_type='application/json', expected_status=200):
        """
        Helper method for sending a post to the server, defaulting to application/merge-patch+json content_type.
        Verifies the expected status and returns the response.
        """
        # pylint: disable=no-member
        response = client.post(self.search_api_url, data=json.dumps(json_data), content_type=content_type)
        assert expected_status == response.status_code
        return response

    def send_get(self, client, query_parameters=None, expected_status=200):
        """
        Helper method for sending a GET to the server. Verifies the expected status and returns the response.
        """
        url = self.url + '?' + query_parameters if query_parameters else self.url  # pylint: disable=no-member
        response = client.get(url)
        assert expected_status == response.status_code
        return response

    # pylint: disable=no-member
    def send_put(self, client, json_data, content_type="application/json", expected_status=204):
        """
        Helper method for sending a PUT to the server. Verifies the expected status and returns the response.
        """
        response = client.put(self.url, data=json.dumps(json_data), content_type=content_type)
        assert expected_status == response.status_code
        return response

    # pylint: disable=no-member
    def send_delete(self, client, expected_status=204):
        """
        Helper method for sending a DELETE to the server. Verifies the expected status and returns the response.
        """
        response = client.delete(self.url)
        assert expected_status == response.status_code
        return response

    def create_mock_profile(self, user):
        """
        Helper method that creates a mock profile for the specified user
        :return:
        """
        legacy_profile = UserProfile.objects.get(id=user.id)
        legacy_profile.country = "US"
        legacy_profile.state = "MA"
        legacy_profile.level_of_education = "m"
        legacy_profile.year_of_birth = 2000
        legacy_profile.goals = "world peace"
        legacy_profile.mailing_address = "Park Ave"
        legacy_profile.gender = "f"
        legacy_profile.bio = TEST_BIO_VALUE
        legacy_profile.profile_image_uploaded_at = TEST_PROFILE_IMAGE_UPLOADED_AT
        legacy_profile.language_proficiencies.create(code=TEST_LANGUAGE_PROFICIENCY_CODE)
        legacy_profile.phone_number = "+18005555555"
        legacy_profile.save()

    def create_mock_verified_name(self, user):
        """
        Helper method to create an approved VerifiedName entry in name affirmation.
        Will not do anything if Name Affirmation is not installed.
        """
        if self.name_affirmation_service:
            legacy_profile = UserProfile.objects.get(id=user.id)
            self.name_affirmation_service.create_verified_name(
                user,
                self.VERIFIED_NAME,
                legacy_profile.name,
                status='approved'
            )

    def create_user_registration(self, user):
        """
        Helper method that creates a registration object for the specified user
        """
        RegistrationFactory(user=user)

    def _get_num_queries(self, num_queries):
        """
        If Name Affirmation is installed, it will add an extra query
        """
        if self.name_affirmation_service:
            return num_queries + 1
        return num_queries

    def _verify_profile_image_data(self, data, has_profile_image):
        """
        Verify the profile image data in a GET response for self.user
        corresponds to whether the user has or hasn't set a profile
        image.
        """
        template = '{root}/{filename}_{{size}}.{extension}'
        if has_profile_image:
            url_root = 'http://example-storage.com/profile-images'
            filename = hashlib.md5(('secret' + self.user.username).encode('utf-8')).hexdigest()
            file_extension = 'jpg'
            template += '?v={}'.format(TEST_PROFILE_IMAGE_UPLOADED_AT.strftime("%s"))
        else:
            url_root = 'http://testserver/static'
            filename = 'default'
            file_extension = 'png'
        template = template.format(root=url_root, filename=filename, extension=file_extension)
        assert data['profile_image'] == {'has_image': has_profile_image,
                                         'image_url_full': template.format(size=50),
                                         'image_url_small': template.format(size=10)}


@ddt.ddt
@skip_unless_lms
class TestOwnUsernameAPI(FilteredQueryCountMixin, CacheIsolationTestCase, UserAPITestCase):
    """
    Unit tests for the Accounts API.
    """

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()

        self.url = reverse("own_username_api")

    def _verify_get_own_username(self, queries, expected_status=200):
        """
        Internal helper to perform the actual assertion
        """
        with self.assertNumQueries(queries, table_ignorelist=WAFFLE_TABLES):
            response = self.send_get(self.client, expected_status=expected_status)
        if expected_status == 200:
            data = response.data
            assert 1 == len(data)
            assert self.user.username == data['username']

    def test_get_username(self):
        """
        Test that a client (logged in) can get her own username.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self._verify_get_own_username(18)

    def test_get_username_inactive(self):
        """
        Test that a logged-in client can get their
        username, even if inactive.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.user.is_active = False
        self.user.save()
        self._verify_get_own_username(18)

    def test_get_username_not_logged_in(self):
        """
        Test that a client (not logged in) gets a 401
        when trying to retrieve their username.
        """

        # verify that the endpoint is inaccessible when not logged in
        self._verify_get_own_username(11, expected_status=401)


@skip_unless_lms
class TestCancelAccountRetirementStatusView(UserAPITestCase):
    """
    Unit tests for CancelAccountRetirementStatusView
    """
    def setUp(self):
        super().setUp()
        permission = PermissionFactory(
            codename='change_userretirementstatus',
            content_type=ContentTypeFactory(
                app_label='user_api'
            )
        )
        self.staff_user.user_permissions.add(permission)
        self.client = self.login_client('staff_client', 'staff_user')

    def test_cancel_retirement_bad_request(self):
        """
        Test that cancel_retirement throws 400 if no retirement_id is given.
        """
        client = self.login_client('staff_client', 'staff_user')
        url = reverse("cancel_account_retirement")
        response = client.post(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'message': 'retirement_id must be specified.'}

    def test_cancel_retirement_does_not_exist(self):
        """
        Test that cancel_retirement throws 400 if no retirement status exists.
        """
        client = self.login_client('staff_client', 'staff_user')
        url = reverse("cancel_account_retirement")
        response = client.post(url, data={'retirement_id': 1})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"message": 'Retirement does not exist!'}

    def test_cancel_retirement_not_pending(self):
        """
        Test that cancel_retirement throws 400 if retirement state is not PENDING.
        """
        client = self.login_client('staff_client', 'staff_user')
        retirement_state = RetirementStateFactory.create(state_name='NOT_PENDING', state_execution_order=1)
        user_retirement_status = UserRetirementStatusFactory.create(
            user=self.user,
            current_state=retirement_state,
            last_state=retirement_state,
            original_email=self.user.email,
            created=datetime.datetime.now(ZoneInfo("UTC"))
        )
        url = reverse("cancel_account_retirement")
        response = client.post(url, data={'retirement_id': user_retirement_status.id})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {
            "message": f"Retirement requests can only be cancelled for users in the PENDING state. "
                       f"Current request state for '{user_retirement_status.original_username}': "
                       f"{user_retirement_status.current_state.state_name}"
        }

    def test_cancel_retirement_successful(self):
        """
        Test that cancel_retirement does the following things properly:
        1. Restore user's email
        2. Reset user's password
        3. Delete Retirement Status entry
        """
        client = self.login_client('staff_client', 'staff_user')
        retirement_state = RetirementStateFactory.create(state_name='PENDING', state_execution_order=1)
        user_retirement_status = UserRetirementStatusFactory.create(
            user=self.user,
            current_state=retirement_state,
            last_state=retirement_state,
            original_email=self.user.email,
            created=datetime.datetime.now(ZoneInfo("UTC"))
        )
        user_retirement_status.user.set_unusable_password()
        assert UserRetirementStatus.objects.count() == 1
        assert user_retirement_status.user.has_usable_password() is False

        url = reverse("cancel_account_retirement")
        response = client.post(url, data={'retirement_id': user_retirement_status.id})
        self.user.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"success": True}
        assert user_retirement_status.user.email == user_retirement_status.original_email
        assert self.user.has_usable_password() is True

        assert UserRetirementStatus.objects.count() == 0


@ddt.ddt
@skip_unless_lms
@mock.patch('openedx.core.djangoapps.user_api.accounts.image_helpers._PROFILE_IMAGE_SIZES', [50, 10])
@mock.patch.dict(
    'django.conf.settings.PROFILE_IMAGE_SIZES_MAP',
    {'full': 50, 'small': 10},
    clear=True
)
class TestAccountsAPI(FilteredQueryCountMixin, CacheIsolationTestCase, UserAPITestCase):
    """
    Unit tests for the Accounts API.
    """

    ENABLED_CACHES = ['default']
    TOTAL_QUERY_COUNT = 26
    FULL_RESPONSE_FIELD_COUNT = 29

    def setUp(self):
        super().setUp()

        self.url = reverse("accounts_api", kwargs={'username': self.user.username})
        self.search_api_url = reverse("accounts_search_emails_api")

    def _set_user_age_to_10_years(self, user):
        """
        Sets the given user's age to 10.
        Returns the calculated year of birth.
        """
        legacy_profile = UserProfile.objects.get(id=user.id)
        current_year = datetime.datetime.now().year
        year_of_birth = current_year - 10
        legacy_profile.year_of_birth = year_of_birth
        legacy_profile.save()
        return year_of_birth

    def _verify_full_shareable_account_response(self, response, account_privacy=None):
        """
        Verify that the shareable fields from the account are returned
        """
        data = response.data
        assert 11 == len(data)

        # public fields (3)
        assert account_privacy == data['account_privacy']
        self._verify_profile_image_data(data, True)
        assert self.user.username == data['username']

        # additional shareable fields (8)
        assert TEST_BIO_VALUE == data['bio']
        assert 'US' == data['country']
        assert data['date_joined'] is not None
        assert [{'code': TEST_LANGUAGE_PROFICIENCY_CODE}] == data['language_proficiencies']
        assert 'm' == data['level_of_education']
        assert data['social_links'] is not None
        assert data['time_zone'] is None

    def _verify_private_account_response(self, response, requires_parental_consent=False, has_profile_image=True):
        """
        Verify that only the public fields are returned if a user does not want to share account fields
        """
        data = response.data
        assert 3 == len(data)
        assert PRIVATE_VISIBILITY == data['account_privacy']
        self._verify_profile_image_data(data, has_profile_image)
        assert self.user.username == data['username']

    def _verify_full_account_response(self, response, requires_parental_consent=False,
                                      has_profile_image=True, year_of_birth=2000):
        """
        Verify that all account fields are returned (even those that are not shareable).
        """
        data = response.data
        assert self.FULL_RESPONSE_FIELD_COUNT == len(data)

        # public fields (3)
        expected_account_privacy = (
            PRIVATE_VISIBILITY if requires_parental_consent else
            UserPreference.get_value(self.user, 'account_privacy')
        )
        assert expected_account_privacy == data['account_privacy']
        self._verify_profile_image_data(data, has_profile_image)
        assert self.user.username == data['username']

        # additional shareable fields (8)
        assert TEST_BIO_VALUE == data['bio']
        assert 'US' == data['country']
        assert data['date_joined'] is not None
        assert data['last_login'] is not None
        assert [{'code': TEST_LANGUAGE_PROFICIENCY_CODE}] == data['language_proficiencies']
        assert 'm' == data['level_of_education']
        assert data['social_links'] is not None
        assert UserPreference.get_value(self.user, 'time_zone') == data['time_zone']
        assert ((self.user.first_name + ' ') + self.user.last_name) == data['name']

        # additional admin fields (13)
        assert self.user.email == data['email']
        assert self.user.id == data['id']
        assert data['extended_profile'] is not None
        assert 'MA' == data['state']
        assert 'f' == data['gender']
        assert 'world peace' == data['goals']
        assert data['is_active']
        assert 'Park Ave' == data['mailing_address']
        assert requires_parental_consent == data['requires_parental_consent']
        assert data['secondary_email'] is None
        assert data['secondary_email_enabled'] is None
        assert year_of_birth == data['year_of_birth']
        if self.name_affirmation_service:
            assert self.VERIFIED_NAME == data['verified_name']
        else:
            assert data['verified_name'] is None

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

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_get_account_unknown_user(self, api_client, user):
        """
        Test that requesting a user who does not exist returns a 404.
        """
        client = self.login_client(api_client, user)
        response = client.get(reverse("accounts_api", kwargs={'username': "does_not_exist"}))
        assert 404 == response.status_code

    @ddt.data(
        ("client", "user"),
    )
    @ddt.unpack
    def test_regsitration_activation_key(self, api_client, user):
        """
        Test that registration activation key has a value.

        UserFactory does not auto-generate registration object for the test users.
        It is created only for users that signup via email/API.  Therefore, activation key has to be tested manually.
        """
        self.create_user_registration(self.user)

        client = self.login_client(api_client, user)
        response = self.send_get(client)

        assert response.data["activation_key"] is not None

    def test_successful_get_account_by_email(self):
        """
        Test that request using email by a staff user successfully retrieves Account Info.
        """
        api_client = "staff_client"
        user = "staff_user"
        client = self.login_client(api_client, user)
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)

        response = self.send_get(client, query_parameters=f'email={self.user.email}')
        self._verify_full_account_response(response)

    def test_unsuccessful_get_account_by_email(self):
        """
        Test that request using email by a normal user fails to retrieve Account Info.
        """
        api_client = "client"
        user = "user"
        client = self.login_client(api_client, user)
        self.create_mock_profile(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)

        response = self.send_get(
            client, query_parameters=f'email={self.user.email}', expected_status=status.HTTP_403_FORBIDDEN
        )
        assert response.data.get('detail') == 'You do not have permission to perform this action.'

    def test_successful_get_account_by_user_id(self):
        """
        Test that request using lms user id by a staff user successfully retrieves Account Info.
        """
        api_client = "staff_client"
        user = "staff_user"
        url = reverse("accounts_detail_api")
        client = self.login_client(api_client, user)
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)

        response = client.get(url + f'?lms_user_id={self.user.id}')
        assert response.status_code == status.HTTP_200_OK
        response.data = response.data[0]
        self._verify_full_account_response(response)

    def test_unsuccessful_get_account_by_user_id(self):
        """
        Test that requesting using lms user id by a normal user fails to retrieve Account Info.
        """
        api_client = "client"
        user = "user"
        url = reverse("accounts_detail_api")
        client = self.login_client(api_client, user)
        self.create_mock_profile(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)

        response = client.get(url + f'?lms_user_id={self.user.id}')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data.get('detail') == 'You do not have permission to perform this action.'

    @ddt.data('abc', '2f', '1.0', "2/8")
    def test_get_account_by_user_id_non_integer(self, non_integer_id):
        """
        Test that request using a non-integer lms user id by a staff user fails to retrieve Account Info.
        """
        api_client = "staff_client"
        user = "staff_user"
        url = reverse("accounts_detail_api")
        client = self.login_client(api_client, user)
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)

        response = client.get(url + f'?lms_user_id={non_integer_id}')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @mock.patch('openedx.core.djangoapps.user_api.accounts.views.is_email_retired')
    @ddt.data(
        (datetime.datetime.now(ZoneInfo("UTC")), True),
        (datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=15), False)
    )
    @ddt.unpack
    def test_search_emails_retired_before_cooloff_period(self, created_date, can_cancel, mock_is_email_retired):
        """
        Tests either of the two possibilities i.e. either the retirement is created before the cool off time
        or after the cool off time.
        """
        mock_is_email_retired.return_value = True
        client = self.login_client('staff_client', 'staff_user')
        retirement_state = RetirementStateFactory.create(state_name='PENDING', state_execution_order=1)
        user_retirement_status = UserRetirementStatusFactory.create(
            user=self.user,
            current_state=retirement_state,
            last_state=retirement_state,
            original_email=self.user.email,
            created=created_date
        )
        url = reverse("accounts_detail_api")
        response = client.get(url + f'?email={quote(self.user.email)}')
        assert response.data == {
            "error_msg": "This email is associated to a retired account.", "can_cancel_retirement": can_cancel,
            "retirement_id": user_retirement_status.id if can_cancel else None
        }

    def test_search_emails(self):
        client = self.login_client('staff_client', 'staff_user')
        json_data = {'emails': [self.user.email]}
        response = self.post_search_api(client, json_data=json_data)
        assert response.data == [{'email': self.user.email, 'id': self.user.id, 'username': self.user.username}]

    def test_search_emails_with_non_staff_user(self):
        client = self.login_client('client', 'user')
        json_data = {'emails': [self.user.email]}
        response = self.post_search_api(client, json_data=json_data, expected_status=404)
        assert response.data == {
            'developer_message': "not_found",
            'user_message': "Not Found"
        }

    def test_search_emails_with_non_existing_email(self):
        client = self.login_client('staff_client', 'staff_user')
        json_data = {"emails": ['non_existant_email@example.com']}
        response = self.post_search_api(client, json_data=json_data)
        assert response.data == []

    def test_search_emails_with_invalid_param(self):
        client = self.login_client('staff_client', 'staff_user')
        json_data = {'invalid_key': [self.user.email]}
        response = self.post_search_api(client, json_data=json_data, expected_status=400)
        assert response.data == {
            'developer_message': "'emails' field is required",
            'user_message': "'emails' field is required"
        }

    # Note: using getattr so that the patching works even if there is no configuration.
    # This is needed when testing CMS as the patching is still executed even though the
    # suite is skipped.
    @mock.patch.dict(getattr(settings, "ACCOUNT_VISIBILITY_CONFIGURATION", {}), {"default_visibility": "all_users"})
    def test_get_account_different_user_visible(self):
        """
        Test that a client (logged in) can only get the shareable fields for a different user.
        This is the case when default_visibility is set to "all_users".
        """
        self.different_client.login(username=self.different_user.username, password=TEST_PASSWORD)
        self.create_mock_profile(self.user)
        with self.assertNumQueries(self._get_num_queries(self.TOTAL_QUERY_COUNT), table_ignorelist=WAFFLE_TABLES):
            response = self.send_get(self.different_client)
        self._verify_full_shareable_account_response(response, account_privacy=ALL_USERS_VISIBILITY)

    # Note: using getattr so that the patching works even if there is no configuration.
    # This is needed when testing CMS as the patching is still executed even though the
    # suite is skipped.
    @mock.patch.dict(getattr(settings, "ACCOUNT_VISIBILITY_CONFIGURATION", {}), {"default_visibility": "private"})
    def test_get_account_different_user_private(self):
        """
        Test that a client (logged in) can only get the shareable fields for a different user.
        This is the case when default_visibility is set to "private".
        """
        self.different_client.login(username=self.different_user.username, password=TEST_PASSWORD)
        self.create_mock_profile(self.user)
        with self.assertNumQueries(self._get_num_queries(self.TOTAL_QUERY_COUNT), table_ignorelist=WAFFLE_TABLES):
            response = self.send_get(self.different_client)
        self._verify_private_account_response(response)

    @ddt.data(
        ("client", "user", PRIVATE_VISIBILITY),
        ("different_client", "different_user", PRIVATE_VISIBILITY),
        ("staff_client", "staff_user", PRIVATE_VISIBILITY),
        ("client", "user", ALL_USERS_VISIBILITY),
        ("different_client", "different_user", ALL_USERS_VISIBILITY),
        ("staff_client", "staff_user", ALL_USERS_VISIBILITY),
    )
    @ddt.unpack
    def test_get_account_private_visibility(self, api_client, requesting_username, preference_visibility):
        """
        Test the return from GET based on user visibility setting.
        """

        def verify_fields_visible_to_all_users(response):
            """
            Confirms that private fields are private, and public/shareable fields are public/shareable
            """
            if preference_visibility == PRIVATE_VISIBILITY:
                self._verify_private_account_response(response)
            else:
                self._verify_full_shareable_account_response(response, ALL_USERS_VISIBILITY)

        client = self.login_client(api_client, requesting_username)

        # Update user account visibility setting.
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, preference_visibility)
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        response = self.send_get(client)

        if requesting_username == "different_user":
            verify_fields_visible_to_all_users(response)
        else:
            self._verify_full_account_response(response)

        # Verify how the view parameter changes the fields that are returned.
        response = self.send_get(client, query_parameters='view=shared')
        verify_fields_visible_to_all_users(response)

        response = self.send_get(client, query_parameters=f'view=shared&username={self.user.username}')
        verify_fields_visible_to_all_users(response)

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
        ("different_client", "different_user"),
    )
    @ddt.unpack
    def test_custom_visibility_over_age(self, api_client, requesting_username):
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        # set user's custom visibility preferences
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, CUSTOM_VISIBILITY)
        shared_fields = ("bio", "language_proficiencies", "name")
        for field_name in shared_fields:
            set_user_preference(self.user, f"visibility.{field_name}", ALL_USERS_VISIBILITY)

        # make API request
        client = self.login_client(api_client, requesting_username)
        response = self.send_get(client)

        # verify response
        if requesting_username == "different_user":
            data = response.data
            assert 6 == len(data)

            # public fields
            assert self.user.username == data['username']
            assert UserPreference.get_value(self.user, 'account_privacy') == data['account_privacy']
            self._verify_profile_image_data(data, has_profile_image=True)

            # custom shared fields
            assert TEST_BIO_VALUE == data['bio']
            assert [{'code': TEST_LANGUAGE_PROFICIENCY_CODE}] == data['language_proficiencies']
            assert ((self.user.first_name + ' ') + self.user.last_name) == data['name']
        else:
            self._verify_full_account_response(response)

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
        ("different_client", "different_user"),
    )
    @ddt.unpack
    def test_custom_visibility_under_age(self, api_client, requesting_username):
        self.create_mock_profile(self.user)
        self.create_mock_verified_name(self.user)
        year_of_birth = self._set_user_age_to_10_years(self.user)

        # set user's custom visibility preferences
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, CUSTOM_VISIBILITY)
        shared_fields = ("bio", "language_proficiencies")
        for field_name in shared_fields:
            set_user_preference(self.user, f"visibility.{field_name}", ALL_USERS_VISIBILITY)

        # make API request
        client = self.login_client(api_client, requesting_username)
        response = self.send_get(client)

        # verify response
        if requesting_username == "different_user":
            self._verify_private_account_response(response, requires_parental_consent=True)
        else:
            self._verify_full_account_response(
                response,
                requires_parental_consent=True,
                year_of_birth=year_of_birth,
            )

    def test_get_account_default(self):
        """
        Test that a client (logged in) can get her own account information (using default legacy profile information,
        as created by the test UserFactory).
        """

        def verify_get_own_information(queries):
            """
            Internal helper to perform the actual assertions
            """
            with self.assertNumQueries(queries, table_ignorelist=WAFFLE_TABLES):
                response = self.send_get(self.client)
            data = response.data
            assert self.FULL_RESPONSE_FIELD_COUNT == len(data)
            assert self.user.username == data['username']
            assert ((self.user.first_name + ' ') + self.user.last_name) == data['name']
            for empty_field in ("year_of_birth", "level_of_education", "mailing_address", "bio"):
                assert data[empty_field] is None
            assert data['country'] is None
            assert data['state'] is None
            assert 'm' == data['gender']
            assert 'Learn a lot' == data['goals']
            assert self.user.email == data['email']
            assert self.user.id == data['id']
            assert data['date_joined'] is not None
            assert data['last_login'] is not None
            assert self.user.is_active == data['is_active']
            self._verify_profile_image_data(data, False)
            assert data['requires_parental_consent']
            assert [] == data['language_proficiencies']
            assert PRIVATE_VISIBILITY == data['account_privacy']
            assert data['time_zone'] is None

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        verify_get_own_information(self._get_num_queries(24))

        # Now make sure that the user can get the same information, even if not active
        self.user.is_active = False
        self.user.save()
        verify_get_own_information(self._get_num_queries(16))

    def test_get_account_empty_string(self):
        """
        Test the conversion of empty strings to None for certain fields.
        """
        legacy_profile = UserProfile.objects.get(id=self.user.id)
        legacy_profile.country = ""
        legacy_profile.state = ""
        legacy_profile.level_of_education = ""
        legacy_profile.gender = ""
        legacy_profile.bio = ""
        legacy_profile.save()

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        with self.assertNumQueries(self._get_num_queries(24), table_ignorelist=WAFFLE_TABLES):
            response = self.send_get(self.client)
        for empty_field in ("level_of_education", "gender", "country", "state", "bio",):
            assert response.data[empty_field] is None

    @ddt.data(
        ("different_client", "different_user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_patch_account_disallowed_user(self, api_client, user):
        """
        Test that a client cannot call PATCH on a different client's user account (even with
        is_staff access).
        """
        client = self.login_client(api_client, user)
        self.send_patch(client, {}, expected_status=403)

    @ddt.data(
        ("client", "user"),
        ("staff_client", "staff_user"),
    )
    @ddt.unpack
    def test_patch_account_unknown_user(self, api_client, user):
        """
        Test that trying to update a user who does not exist returns a 403.
        """
        client = self.login_client(api_client, user)
        response = client.patch(
            reverse("accounts_api", kwargs={'username': "does_not_exist"}),
            data=json.dumps({}), content_type="application/merge-patch+json"
        )
        assert 403 == response.status_code

    @ddt.data(
        ("gender", "f", "not a gender", '"not a gender" is not a valid choice.'),
        ("level_of_education", "none", "ȻħȺɍłɇs", '"ȻħȺɍłɇs" is not a valid choice.'),
        ("country", "GB", "XY", '"XY" is not a valid choice.'),
        ("state", "MA", "PY", '"PY" is not a valid choice.'),
        ("year_of_birth", 2009, "not_an_int", "A valid integer is required."),
        ("name", "bob", "z" * 256, "Ensure this field has no more than 255 characters."),
        ("name", "ȻħȺɍłɇs", "   ", "The name field must be at least 1 character long."),
        ("goals", "Smell the roses"),
        ("mailing_address", "Sesame Street"),
        # Note that we store the raw data, so it is up to client to escape the HTML.
        (
            "bio", "<html>Lacrosse-playing superhero 壓是進界推日不復女</html>",
            "z" * 301, "The about me field must be at most 300 characters long."
        ),
        ("account_privacy", ALL_USERS_VISIBILITY),
        ("account_privacy", PRIVATE_VISIBILITY),
        # Note that email is tested below, as it is not immediately updated.
        # Note that language_proficiencies is tested below as there are multiple error and success conditions.
    )
    @ddt.unpack
    def test_patch_account(self, field, value, fails_validation_value=None, developer_validation_message=None):
        """
        Test the behavior of patch, when using the correct content_type.
        """
        client = self.login_client("client", "user")

        if field == 'account_privacy':
            # Ensure the user has birth year set, and is over 13, so
            # account_privacy behaves normally
            legacy_profile = UserProfile.objects.get(id=self.user.id)
            legacy_profile.year_of_birth = 2000
            legacy_profile.save()

        response = self.send_patch(client, {field: value})
        assert value == response.data[field]

        if fails_validation_value:
            error_response = self.send_patch(client, {field: fails_validation_value}, expected_status=400)
            expected_user_message = 'This value is invalid.'
            if field == 'bio':
                expected_user_message = "The about me field must be at most 300 characters long."

            assert expected_user_message == error_response.data['field_errors'][field]['user_message']

            assert "Value '{value}' is not valid for field '{field}': {messages}".format(
                value=fails_validation_value,
                field=field,
                messages=[developer_validation_message]
            ) == error_response.data['field_errors'][field]['developer_message']

        elif field != "account_privacy":
            # If there are no values that would fail validation, then empty string should be supported;
            # except for account_privacy, which cannot be an empty string.
            response = self.send_patch(client, {field: ""})
            assert '' == response.data[field]

    def test_patch_inactive_user(self):
        """ Verify that a user can patch her own account, even if inactive. """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.user.is_active = False
        self.user.save()
        response = self.send_patch(self.client, {"goals": "to not activate account"})
        assert 'to not activate account' == response.data['goals']

    @ddt.unpack
    def test_patch_account_noneditable(self):
        """
        Tests the behavior of patch when a read-only field is attempted to be edited.
        """
        client = self.login_client("client", "user")

        def verify_error_response(field_name, data):
            """
            Internal helper to check the error messages returned
            """
            assert 'This field is not editable via this API' == data['field_errors'][field_name]['developer_message']
            assert "The '{}' field cannot be edited.".format(
                field_name
            ) == data['field_errors'][field_name]['user_message']

        for field_name in ["username", "date_joined", "is_active", "profile_image", "requires_parental_consent"]:
            response = self.send_patch(client, {field_name: "will_error", "gender": "o"}, expected_status=400)
            verify_error_response(field_name, response.data)

        # Make sure that gender did not change.
        response = self.send_get(client)
        assert 'm' == response.data['gender']

        # Test error message with multiple read-only items
        response = self.send_patch(client, {"username": "will_error", "date_joined": "xx"}, expected_status=400)
        assert 2 == len(response.data['field_errors'])
        verify_error_response("username", response.data)
        verify_error_response("date_joined", response.data)

    def test_patch_bad_content_type(self):
        """
        Test the behavior of patch when an incorrect content_type is specified.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.send_patch(self.client, {}, content_type="application/json", expected_status=415)
        self.send_patch(self.client, {}, content_type="application/xml", expected_status=415)

    def test_patch_account_empty_string(self):
        """
        Tests the behavior of patch when attempting to set fields with a select list of options to the empty string.
        Also verifies the behaviour when setting to None.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        for field_name in ["gender", "level_of_education", "country", "state"]:
            response = self.send_patch(self.client, {field_name: ""})
            # Although throwing a 400 might be reasonable, the default DRF behavior with ModelSerializer
            # is to convert to None, which also seems acceptable (and is difficult to override).
            assert response.data[field_name] is None

            # Verify that the behavior is the same for sending None.
            response = self.send_patch(self.client, {field_name: ""})
            assert response.data[field_name] is None

    def test_patch_name_metadata(self):
        """
        Test the metadata stored when changing the name field.
        """

        def get_name_change_info(expected_entries):
            """
            Internal method to encapsulate the retrieval of old names used
            """
            legacy_profile = UserProfile.objects.get(id=self.user.id)
            name_change_info = legacy_profile.get_meta()["old_names"]
            assert expected_entries == len(name_change_info)
            return name_change_info

        def verify_change_info(change_info, old_name, requester, new_name):
            """
            Internal method to validate name changes
            """
            assert 3 == len(change_info)
            assert old_name == change_info[0]
            assert f'Name change requested through account API by {requester}' == change_info[1]
            assert change_info[2] is not None
            # Verify the new name was also stored.
            get_response = self.send_get(self.client)
            assert new_name == get_response.data['name']

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        legacy_profile = UserProfile.objects.get(id=self.user.id)
        assert {} == legacy_profile.get_meta()
        old_name = legacy_profile.name

        # First change the name as the user and verify meta information.
        self.send_patch(self.client, {"name": "Mickey Mouse"})
        name_change_info = get_name_change_info(1)
        verify_change_info(name_change_info[0], old_name, self.user.username, "Mickey Mouse")

        # Now change the name again and verify meta information.
        self.send_patch(self.client, {"name": "Donald Duck"})
        name_change_info = get_name_change_info(2)
        verify_change_info(name_change_info[0], old_name, self.user.username, "Donald Duck", )
        verify_change_info(name_change_info[1], "Mickey Mouse", self.user.username, "Donald Duck")

    @mock.patch.dict(
        'django.conf.settings.PROFILE_IMAGE_SIZES_MAP',
        {'full': 50, 'medium': 30, 'small': 10},
        clear=True
    )
    def test_patch_email(self):
        """
        Test that the user can request an email change through the accounts API.
        Full testing of the helper method used (do_email_change_request) exists in the package with the code.
        Here just do minimal smoke testing.
        """
        client = self.login_client("client", "user")
        old_email = self.user.email
        new_email = "newemail@example.com"
        response = self.send_patch(client, {"email": new_email, "goals": "change my email"})

        # Since request is multi-step, the email won't change on GET immediately (though goals will update).
        assert old_email == response.data['email']
        assert 'change my email' == response.data['goals']

        # Now call the method that will be invoked with the user clicks the activation key in the received email.
        # First we must get the activation key that was sent.
        pending_change = PendingEmailChange.objects.filter(user=self.user)
        assert 1 == len(pending_change)
        activation_key = pending_change[0].activation_key
        confirm_change_url = reverse(
            "confirm_email_change", kwargs={'key': activation_key}
        )
        response = self.client.post(confirm_change_url)
        assert 200 == response.status_code
        get_response = self.send_get(client)
        assert new_email == get_response.data['email']

    @override_settings(EMAIL_CHANGE_RATE_LIMIT='1/m')
    def test_patch_email_ratelimit(self):
        """
        Tests if rate limit is applied on email patch
        """
        client = self.login_client("client", "user")
        self.send_patch(client, {"email": "new_email_01@example.com"}, expected_status=status.HTTP_200_OK)
        self.send_patch(client, {"email": "new_email_02@example.com"},
                        expected_status=status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(EMAIL_CHANGE_RATE_LIMIT='')
    def test_ratelimit_is_disabled_on_email_patch_if_settings_is_empty(self):
        """
        Tests if rate limit doesn't applied on email patch if EMAIL_CHANGE_RATE_LIMIT is empty string or None
        """
        client = self.login_client("client", "user")
        self.send_patch(client, {"email": "email_new_01@example.com"}, expected_status=status.HTTP_200_OK)
        self.send_patch(client, {"email": "email_new_02@example.com"}, expected_status=status.HTTP_200_OK)

    @override_settings(EMAIL_CHANGE_RATE_LIMIT='1/d')
    def test_ratelimit_is_only_on_email_change(self):
        """
        Tests if rate limit is only applied for email attribute i.e. when user changes email
        """
        client = self.login_client("client", "user")
        for i in range(5):
            self.send_patch(client, {"name": f"new_name_{i}"}, expected_status=status.HTTP_200_OK)

    @ddt.data(
        ("not_an_email",),
        ("",),
        (None,),
    )
    @ddt.unpack
    def test_patch_invalid_email(self, bad_email):
        """
        Test a few error cases for email validation (full test coverage lives with do_email_change_request).
        """
        client = self.login_client("client", "user")

        # Try changing to an invalid email to make sure error messages are appropriately returned.
        error_response = self.send_patch(client, {"email": bad_email}, expected_status=400)
        field_errors = error_response.data["field_errors"]
        assert "Error thrown from validate_new_email: 'Valid e-mail address required.'" == \
               field_errors['email']['developer_message']
        assert 'Valid e-mail address required.' == field_errors['email']['user_message']

    @override_settings(SECONDARY_EMAIL_RATE_LIMIT='1/m')
    def test_patch_secondary_email_ratelimit(self):
        """
        Tests if rate limit is applied on secondary_email patch
        """
        client = self.login_client("client", "user")
        self.send_patch(client, {"secondary_email": "new_email_01@example.com"},
                        expected_status=status.HTTP_200_OK)
        self.send_patch(client, {"secondary_email": "new_email_02@example.com"},
                        expected_status=status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(SECONDARY_EMAIL_RATE_LIMIT='')
    def test_ratelimit_is_disabled_on_secondary_email_patch_if_settings_is_empty(self):
        """
        Tests rate limit doesn't applied on secondary_email patch if SECONDARY_EMAIL_RATE_LIMIT is empty string or None
        """
        client = self.login_client("client", "user")
        self.send_patch(client, {"secondary_email": "email_new_01@example.com"},
                        expected_status=status.HTTP_200_OK)
        self.send_patch(client, {"secondary_email": "email_new_02@example.com"},
                        expected_status=status.HTTP_200_OK)

    @override_settings(SECONDARY_EMAIL_RATE_LIMIT='1/d')
    def test_ratelimit_is_only_on_secondary_email_change(self):
        """
        Tests if rate limit is only applied for secondary_email attribute i.e. when user changes recovery email
        """
        client = self.login_client("client", "user")
        for i in range(5):
            self.send_patch(client, {"name": f"new_name_{i}"}, expected_status=status.HTTP_200_OK)

    @mock.patch('common.djangoapps.student.views.management.do_email_change_request')
    def test_patch_duplicate_email(self, do_email_change_request):
        """
        Test that same success response will be sent to user even if the given email already used.
        """
        existing_email = "same@example.com"
        UserFactory.create(email=existing_email)

        client = self.login_client("client", "user")

        # Try changing to an existing email to make sure no error messages returned.
        response = self.send_patch(client, {"email": existing_email})
        assert 200 == response.status_code

        # Verify that no actual request made for email change
        assert not do_email_change_request.called

    def test_patch_language_proficiencies(self):
        """
        Verify that patching the language_proficiencies field of the user
        profile completely overwrites the previous value.
        """
        client = self.login_client("client", "user")

        # Patching language_proficiencies exercises the
        # `LanguageProficiencySerializer.get_identity` method, which compares
        # identifies language proficiencies based on their language code rather
        # than django model id.
        for proficiencies in ([{"code": "en"}, {"code": "fr"}, {"code": "es"}], [{"code": "fr"}], [{"code": "aa"}], []):
            response = self.send_patch(client, {"language_proficiencies": proficiencies})
            self.assertCountEqual(response.data["language_proficiencies"], proficiencies)

    @ddt.data(
        (
            "not_a_list",
            {'non_field_errors': ['Expected a list of items but got type "unicode".']}
        ),
        (
            ["not_a_JSON_object"],
            [{'non_field_errors': ['Invalid data. Expected a dictionary, but got unicode.']}]
        ),
        (
            [{}],
            [{'code': ['This field is required.']}]
        ),
        (
            [{"code": "invalid_language_code"}],
            [{'code': ['"invalid_language_code" is not a valid choice.']}]
        ),
        (
            [{"code": "kw"}, {"code": "el"}, {"code": "kw"}],
            ['The language_proficiencies field must consist of unique languages.']
        ),
    )
    @ddt.unpack
    def test_patch_invalid_language_proficiencies(self, patch_value, expected_error_message):
        """
        Verify we handle error cases when patching the language_proficiencies
        field.
        """
        expected_error_message = str(expected_error_message).replace('unicode', 'str')

        client = self.login_client("client", "user")
        response = self.send_patch(client, {"language_proficiencies": patch_value}, expected_status=400)
        assert response.data['field_errors']['language_proficiencies']['developer_message'] == \
               f"Value '{patch_value}' is not valid for field 'language_proficiencies': {expected_error_message}"

    @mock.patch('openedx.core.djangoapps.user_api.accounts.serializers.AccountUserSerializer.save')
    def test_patch_serializer_save_fails(self, serializer_save):
        """
        Test that AccountUpdateErrors are passed through to the response.
        """
        serializer_save.side_effect = [Exception("bummer"), None]
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        error_response = self.send_patch(self.client, {"goals": "save an account field"}, expected_status=400)
        assert "Error thrown when saving account updates: 'bummer'" == error_response.data['developer_message']
        assert error_response.data['user_message'] is None

    def test_profile_image_backend(self):
        # settings file contains the `VIDEO_IMAGE_SETTINGS` but dont'have STORAGE_CLASS
        # so it returns the default storage.
        storage = get_profile_image_storage()
        storage_class = storage.__class__
        self.assertEqual(
            settings.PROFILE_IMAGE_BACKEND['class'],
            f"{storage_class.__module__}.{storage_class.__name__}",
        )
        self.assertEqual(storage.base_url, settings.PROFILE_IMAGE_BACKEND['options']['base_url'])

    @override_settings(PROFILE_IMAGE_BACKEND={
        'class': 'storages.backends.s3boto3.S3Boto3Storage',
        'options': {
            'bucket_name': 'test',
            'default_acl': 'public',
            'location': 'abc/def'
        }
    })
    def test_profile_backend_with_params(self):
        storage = get_profile_image_storage()
        self.assertIsInstance(storage, S3Boto3Storage)
        self.assertEqual(storage.bucket_name, "test")
        self.assertEqual(storage.default_acl, 'public')
        self.assertEqual(storage.location, "abc/def")

    @override_settings(PROFILE_IMAGE_BACKEND={'class': None, 'options': {}})
    def test_profile_backend_without_backend(self):
        storage = get_profile_image_storage()
        self.assertIsInstance(storage, FileSystemStorage)

    @override_settings(PROFILE_IMAGE_BACKEND=TEST_PROFILE_IMAGE_BACKEND)
    def test_convert_relative_profile_url(self):
        """
        Test that when TEST_PROFILE_IMAGE_BACKEND['base_url'] begins
        with a '/', the API generates the full URL to profile images based on
        the URL of the request.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self.send_get(self.client)
        assert response.data['profile_image'] == \
               {'has_image': False,
                'image_url_full': 'http://testserver/static/default_50.png',
                'image_url_small': 'http://testserver/static/default_10.png'}

    @override_settings(
        PROFILE_IMAGE_BACKEND={},
        STORAGES={
            'profile_image': {
                'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
                'OPTIONS': {
                    'bucket_name': 'profiles',
                    'default_acl': 'public',
                    'location': 'profile/images',
                }
            }
        }
    )
    def test_profile_backend_with_profile_image_settings(self):
        """ It will use the storages dict with profile_images backend"""
        storage = get_profile_image_storage()
        self.assertIsInstance(storage, S3Boto3Storage)
        self.assertEqual(storage.bucket_name, "profiles")
        self.assertEqual(storage.default_acl, 'public')
        self.assertEqual(storage.location, "profile/images")

    @override_settings(
        PROFILE_IMAGE_BACKEND={},
    )
    def test_profile_backend_with_default_hardcoded_backend(self):
        """ In case of empty storages scenario uses the hardcoded backend."""
        del settings.STORAGES
        storage = get_profile_image_storage()
        self.assertIsInstance(storage, FileSystemStorage)

    @ddt.data(
        ("client", "user", True),
        ("different_client", "different_user", False),
        ("staff_client", "staff_user", True),
    )
    @ddt.unpack
    def test_parental_consent(self, api_client, requesting_username, has_full_access):
        """
        Verifies that under thirteens never return a public profile.
        """
        client = self.login_client(api_client, requesting_username)

        year_of_birth = self._set_user_age_to_10_years(self.user)
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, ALL_USERS_VISIBILITY)

        # Verify that the default view is still private (except for clients with full access)
        response = self.send_get(client)
        if has_full_access:
            data = response.data
            assert self.FULL_RESPONSE_FIELD_COUNT == len(data)
            assert self.user.username == data['username']
            assert ((self.user.first_name + ' ') + self.user.last_name) == data['name']
            assert self.user.email == data['email']
            assert self.user.id == data['id']
            assert year_of_birth == data['year_of_birth']
            for empty_field in ("country", "level_of_education", "mailing_address", "bio", "state",):
                assert data[empty_field] is None
            assert 'm' == data['gender']
            assert 'Learn a lot' == data['goals']
            assert data['is_active']
            assert data['date_joined'] is not None
            assert data['last_login'] is not None
            self._verify_profile_image_data(data, False)
            assert data['requires_parental_consent']
            assert PRIVATE_VISIBILITY == data['account_privacy']
        else:
            self._verify_private_account_response(response, requires_parental_consent=True, has_profile_image=False)

        # Verify that the shared view is still private
        response = self.send_get(client, query_parameters='view=shared')
        self._verify_private_account_response(response, requires_parental_consent=True, has_profile_image=False)


@skip_unless_lms
class TestAccountAPITransactions(TransactionTestCase):
    """
    Tests the transactional behavior of the account API
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = UserFactory.create(password=TEST_PASSWORD)
        self.url = reverse("accounts_api", kwargs={'username': self.user.username})

    @mock.patch('common.djangoapps.student.views.do_email_change_request')
    def test_update_account_settings_rollback(self, mock_email_change):
        """
        Verify that updating account settings is transactional when a failure happens.
        """
        # Send a PATCH request with updates to both profile information and email.
        # Throw an error from the method that is used to process the email change request
        # (this is the last thing done in the api method). Verify that the profile did not change.
        mock_email_change.side_effect = [ValueError, "mock value error thrown"]
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        old_email = self.user.email

        json_data = {"email": "foo@bar.com", "gender": "o"}
        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")
        assert 400 == response.status_code

        # Verify that GET returns the original preferences
        response = self.client.get(self.url)
        data = response.data
        assert old_email == data['email']
        assert 'm' == data['gender']


@skip_unless_lms
class TestAccountsAPIExtendedProfile(UserAPITestCase):
    """
    Tests for extended profile validation in the Accounts API
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("accounts_api", kwargs={"username": self.user.username})
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    @mock.patch("openedx.core.djangoapps.user_api.accounts.views.validate_and_get_extended_profile_form")
    def test_patch_account_with_valid_extended_profile(self, mock_validate_form: mock.Mock):
        """
        Test that PATCH with valid extended_profile data succeeds
        """
        mock_form = mock.Mock()
        mock_validate_form.return_value = (mock_form, {})
        extended_profile_data = [
            {"field_name": "department", "field_value": "Engineering"},
            {"field_name": "title", "field_value": "Software Engineer"},
        ]
        json_data = {"extended_profile": extended_profile_data, "bio": "Test bio"}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 200)
        mock_validate_form.assert_called_once_with(extended_profile_data, self.user)

    @mock.patch("openedx.core.djangoapps.user_api.accounts.views.validate_and_get_extended_profile_form")
    def test_patch_account_with_invalid_extended_profile(self, mock_validate_form: mock.Mock):
        """
        Test that PATCH with invalid extended_profile data returns 400
        """
        field_errors = {
            "department": {"developer_message": "This field is required", "user_message": "This field is required"}
        }
        mock_validate_form.return_value = (None, field_errors)
        extended_profile_data = [{"field_name": "department", "field_value": ""}]
        json_data = {"extended_profile": extended_profile_data}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("field_errors", response.data)
        self.assertIn("department", response.data["field_errors"])
        mock_validate_form.assert_called_once_with(extended_profile_data, self.user)

    def test_patch_account_without_extended_profile(self: UserAPITestCase):
        """
        Test that PATCH without extended_profile data works normally
        """
        json_data = {"bio": "Test bio without extended profile"}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["bio"], "Test bio without extended profile")

    @mock.patch("openedx.core.djangoapps.user_api.accounts.views.validate_and_get_extended_profile_form")
    def test_patch_account_extended_profile_with_empty_list(self, mock_validate_form: mock.Mock):
        """
        Test that PATCH with empty extended_profile list works
        """
        mock_validate_form.return_value = (None, {})
        json_data = {"extended_profile": [], "bio": "Test bio"}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 200)
        mock_validate_form.assert_called_once_with([], self.user)

    @mock.patch("openedx.core.djangoapps.user_api.accounts.views.validate_and_get_extended_profile_form")
    def test_patch_account_extended_profile_form_exception(self, mock_validate_form: mock.Mock):
        """
        Test that exceptions in form validation return appropriate errors
        """
        field_errors = {
            "extended_profile": {"developer_message": "Unexpected error", "user_message": "An error occurred"}
        }
        mock_validate_form.return_value = (None, field_errors)
        extended_profile_data = [{"field_name": "department", "field_value": "Engineering"}]
        json_data = {"extended_profile": extended_profile_data}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("field_errors", response.data)

    @mock.patch("openedx.core.djangoapps.user_api.accounts.views.validate_and_get_extended_profile_form")
    def test_patch_account_extended_profile_multiple_fields(self, mock_validate_form: mock.Mock):
        """
        Test PATCH with multiple extended_profile fields
        """
        mock_form = mock.Mock()
        mock_validate_form.return_value = (mock_form, {})
        extended_profile_data = [
            {"field_name": "department", "field_value": "Engineering"},
            {"field_name": "title", "field_value": "Software Engineer"},
            {"field_name": "company", "field_value": "EdX"},
            {"field_name": "location", "field_value": "Remote"},
        ]
        json_data = {"extended_profile": extended_profile_data}

        response = self.client.patch(self.url, data=json.dumps(json_data), content_type="application/merge-patch+json")

        self.assertEqual(response.status_code, 200)
        mock_validate_form.assert_called_once_with(extended_profile_data, self.user)

    def test_patch_account_extended_profile_unauthorized(self):
        """
        Test that unauthorized users cannot update extended_profile
        """
        self.different_client.login(username=self.different_user.username, password=TEST_PASSWORD)
        extended_profile_data = [{"field_name": "department", "field_value": "Engineering"}]
        json_data = {"extended_profile": extended_profile_data}

        response = self.different_client.patch(
            self.url, data=json.dumps(json_data), content_type="application/merge-patch+json"
        )

        self.assertIn(response.status_code, [403, 404])


@ddt.ddt
class NameChangeViewTests(UserAPITestCase):
    """ NameChangeView tests """

    def _send_create(self, client, json_data):
        """
        Helper method to send a create request to the server, defaulting to application/json
        content_type and returning the response.
        """
        return client.post(
            reverse('request_name_change'),
            data=json.dumps(json_data),
            content_type='application/json'
        )

    def _send_confirm(self, client, username):
        """
        Helper method to send a confirm request to the server, defaulting to application/json
        content_type and returning the response.
        """
        return client.post(reverse(
            'confirm_name_change',
            kwargs={'username': username}
        ))

    def test_create_request_succeeds(self):
        """
        Test that a valid name change request succeeds.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self._send_create(self.client, {'name': 'New Name'})
        self.assertEqual(response.status_code, 201)

    def test_create_unauthenticated(self):
        """
        Test that a name change request fails for an unauthenticated user.
        """
        response = self._send_create(self.client, {'name': 'New Name'})
        self.assertEqual(response.status_code, 401)

    def test_create_empty_request(self):
        """
        Test that an empty request fails.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self._send_create(self.client, {})
        self.assertEqual(response.status_code, 400)

    def test_create_blank_name(self):
        """
        Test that a blank name string fails.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self._send_create(self.client, {'name': ''})
        self.assertEqual(response.status_code, 400)

    @ddt.data('<html>invalid name</html>', 'https://invalid.com')
    def test_create_fails_validation(self, invalid_name):
        """
        Test that an invalid name will return an error.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        response = self._send_create(self.client, {'name': invalid_name})
        self.assertEqual(response.status_code, 400)

    def test_confirm_succeeds(self):
        """
        Test that a staff user can successfully confirm a name change.
        """
        self.staff_client.login(username=self.staff_user.username, password=TEST_PASSWORD)
        do_name_change_request(self.user, 'New Name', 'test')
        response = self._send_confirm(self.staff_client, self.user.username)
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_pending_name_change(self.user))

    def test_confirm_non_staff(self):
        """
        Test that non-staff users cannot confirm name changes.
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        do_name_change_request(self.user, 'New Name', 'test')
        response = self._send_confirm(self.client, self.user.username)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(get_pending_name_change(self.user).new_name, 'New Name')

    def test_confirm_no_pending_name_change(self):
        """
        Test that attempting to confirm a non-existent name change request will result in a 404.
        """
        self.staff_client.login(username=self.staff_user.username, password=TEST_PASSWORD)
        response = self._send_confirm(self.staff_client, self.user.username)
        self.assertEqual(response.status_code, 404)


@ddt.ddt
@mock.patch('django.conf.settings.USERNAME_REPLACEMENT_WORKER', 'test_replace_username_service_worker')
class UsernameReplacementViewTests(APITestCase):
    """ Tests UsernameReplacementView """
    SERVICE_USERNAME = 'test_replace_username_service_worker'

    def setUp(self):
        super().setUp()
        self.service_user = UserFactory(username=self.SERVICE_USERNAME)
        self.url = reverse("username_replacement")

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {'HTTP_AUTHORIZATION': f'JWT {token}'}
        return headers

    def call_api(self, user, data):
        """ Helper function to call API with data """
        data = json.dumps(data)
        headers = self.build_jwt_headers(user)
        return self.client.post(self.url, data, content_type='application/json', **headers)

    def test_auth(self):
        """ Verify the endpoint only works with the service worker """
        data = {
            "username_mappings": [
                {"test_username_1": "test_new_username_1"},
                {"test_username_2": "test_new_username_2"}
            ]
        }

        # Test unauthenticated
        response = self.client.post(self.url)
        assert response.status_code == 401

        # Test non-service worker
        random_user = UserFactory()
        response = self.call_api(random_user, data)
        assert response.status_code == 403

        # Test service worker
        response = self.call_api(self.service_user, data)
        assert response.status_code == 200

    @ddt.data(
        [{}, {}],
        {},
        [{"test_key": "test_value", "test_key_2": "test_value_2"}]
    )
    def test_bad_schema(self, mapping_data):
        """ Verify the endpoint rejects bad data schema """
        data = {
            "username_mappings": mapping_data
        }
        response = self.call_api(self.service_user, data)
        assert response.status_code == 400

    def test_existing_and_non_existing_users(self):
        """ Tests a mix of existing and non existing users """
        random_users = [UserFactory() for _ in range(5)]
        fake_usernames = ["myname_" + str(x) for x in range(5)]
        existing_users = [{user.username: user.username + '_new'} for user in random_users]
        non_existing_users = [{username: username + '_new'} for username in fake_usernames]
        data = {
            "username_mappings": existing_users + non_existing_users
        }
        expected_response = {
            'failed_replacements': [],
            'successful_replacements': existing_users + non_existing_users
        }
        response = self.call_api(self.service_user, data)
        assert response.status_code == 200
        assert response.data == expected_response
