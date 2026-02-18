"""
Tests for Cohort API
"""


import tempfile

import ddt
from django.urls import reverse

from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory, AccessTokenFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.views import link_cohort_to_partition_group
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory

USERNAME = 'honor'
USER_MAIL = 'honor@example.com'
SETTINGS_PAYLOAD = '{"is_cohorted": true}'
HANDLER_POST_PAYLOAD = '{"name":"Default","user_count":0,"assignment_type":"random","user_partition_id":null\
,"group_id":null}'
HANDLER_PATCH_PAYLOAD = '{"name":"Default Group","group_id":null,"user_partition_id":null,"assignment_type":"random"}'
ADD_USER_PAYLOAD = {'users': [USER_MAIL, ]}
CSV_DATA = f'''email,cohort\n{USER_MAIL},DEFAULT'''


@skip_unless_lms
@ddt.ddt
class TestCohortOauth(SharedModuleStoreTestCase):
    """
    Tests for cohort API oauth authentication
    """

    password = 'password'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory(username=USERNAME, email=USER_MAIL, password=cls.password)
        cls.staff_user = UserFactory(is_staff=True, password=cls.password)
        cls.course_key = ToyCourseFactory.create().id
        cls.course_str = str(cls.course_key)

    @ddt.data({'path_name': 'api_cohorts:cohort_settings'},
              {'path_name': 'api_cohorts:cohort_handler'}, )
    @ddt.unpack
    def test_oauth_list(self, path_name):
        """ Verify the endpoints supports OAuth, and only allows authorization for staff users. """
        path = reverse(path_name, kwargs={'course_key_string': self.course_str})
        user = UserFactory(is_staff=False)
        oauth_client = ApplicationFactory.create()
        access_token = AccessTokenFactory.create(user=user, application=oauth_client).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }

        # Non-staff users should not have access to the API
        response = self.client.get(path=path, **headers)
        assert response.status_code == 403

        # Staff users should have access to the API
        user.is_staff = True
        user.save()
        response = self.client.get(path=path, **headers)
        assert response.status_code == 200

    def test_oauth_users(self):
        """ Verify the endpoint supports OAuth, and only allows authorization for staff users. """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        path = reverse('api_cohorts:cohort_users', kwargs={'course_key_string': self.course_str, 'cohort_id': 1})
        user = UserFactory(is_staff=False)
        oauth_client = ApplicationFactory.create()
        access_token = AccessTokenFactory.create(user=user, application=oauth_client).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }
        data = {
            'users': [user.username]
        }

        # Non-staff users should not have access to the API
        response = self.client.post(path=path, data=data, **headers)
        assert response.status_code == 403

        # Staff users should have access to the API
        user.is_staff = True
        user.save()
        response = self.client.post(path=path, data=data, **headers)
        assert response.status_code == 200

    def test_oauth_csv(self):
        """ Verify the endpoint supports OAuth, and only allows authorization for staff users. """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        path = reverse('api_cohorts:cohort_users_csv', kwargs={'course_key_string': self.course_str})
        user = UserFactory(is_staff=False)
        oauth_client = ApplicationFactory.create()
        access_token = AccessTokenFactory.create(user=user, application=oauth_client).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }

        # Non-staff users should not have access to the API
        response = self.client.post(path=path, **headers)
        assert response.status_code == 403

        # Staff users should have access to the API
        user.is_staff = True
        user.save()
        response = self.client.post(path=path, **headers)
        assert response.status_code == 400


@skip_unless_lms
@ddt.ddt
class TestCohortApi(SharedModuleStoreTestCase):
    """
    Tests for cohort API endpoints
    """

    password = 'password'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory(username=USERNAME, email=USER_MAIL, password=cls.password)
        cls.staff_user = UserFactory(is_staff=True, password=cls.password)
        cls.course_key = ToyCourseFactory.create().id
        cls.course_str = str(cls.course_key)

    @ddt.data(
        {'is_staff': True, 'status': 200},
        {'is_staff': False, 'status': 403},
    )
    @ddt.unpack
    def test_cohort_settings_staff_access_required(self, is_staff, status):
        """
        Test that staff access is required for the endpoints.
        """
        path = reverse('api_cohorts:cohort_settings', kwargs={'course_key_string': self.course_str})
        user = self.staff_user if is_staff else self.user
        self.client.login(username=user.username, password=self.password)

        response = self.client.get(path=path)
        assert response.status_code == status

        response = self.client.put(path=path, data=SETTINGS_PAYLOAD, content_type='application/json')
        assert response.status_code == status

    def test_cohort_settings_non_existent_course(self):
        """
        Test getting and updating the cohort settings of a non-existent course.
        """
        path = reverse('api_cohorts:cohort_settings', kwargs={'course_key_string': 'e/d/X'})
        self.client.login(username=self.staff_user.username, password=self.password)

        response = self.client.get(path=path)
        assert response.status_code == 404

        response = self.client.put(path=path, data=SETTINGS_PAYLOAD, content_type='application/json')
        assert response.status_code == 404

    @ddt.data(
        {'data': '', 'status': 400},
        {'data': 'abcd', 'status': 400},
        {'data': {'is_course_cohorted': 'abcd'}, 'status': 400},
    )
    @ddt.unpack
    def test_put_cohort_settings_invalid_request(self, data, status):
        """
        Test the endpoint with invalid requests
        """
        path = reverse('api_cohorts:cohort_settings', kwargs={'course_key_string': self.course_str})
        self.client.login(username=self.staff_user.username, password=self.password)

        response = self.client.put(path=path, data=data, content_type='application/json')
        assert response.status_code == status

    @ddt.data(
        {'data': '', 'kwargs': {'cohort_id': 1}, 'status': 405},
        {'data': '{"a": 1}', 'kwargs': {}, 'status': 400},
        {'data': '{"name": "c1"}', 'kwargs': {}, 'status': 400},
        {'data': '{"assignment_type": "manual"}', 'kwargs': {}, 'status': 400},
    )
    @ddt.unpack
    def test_post_cohort_handler_invalid_requests(self, data, kwargs, status):
        """
        Test the endpoint with invalid requests.
        """
        url_kwargs = {'course_key_string': self.course_str}
        if kwargs:
            url_kwargs.update(kwargs)

        path = reverse('api_cohorts:cohort_handler', kwargs=url_kwargs)
        user = self.staff_user
        assert self.client.login(username=user.username, password=self.password)

        response = self.client.post(path=path, data=data, content_type='application/json')
        assert response.status_code == status

    @ddt.data({'is_staff': False, 'payload': HANDLER_POST_PAYLOAD, 'status': 403},
              {'is_staff': True, 'payload': HANDLER_POST_PAYLOAD, 'status': 200},
              {'is_staff': False, 'payload': '', 'status': 403},
              {'is_staff': True, 'payload': '', 'status': 200}, )
    @ddt.unpack
    def test_cohort_handler(self, is_staff, payload, status):
        """
        Test GET and POST methods of cohort handler endpoint
        """
        path = reverse('api_cohorts:cohort_handler', kwargs={'course_key_string': self.course_str})
        user = self.staff_user if is_staff else self.user
        assert self.client.login(username=user.username, password=self.password)
        if payload:
            response = self.client.post(
                path=path,
                data=payload,
                content_type='application/json')
        else:
            response = self.client.get(path=path)
        assert response.status_code == status

    def test_cohort_handler_patch_without_cohort_id(self):
        path = reverse('api_cohorts:cohort_handler', kwargs={'course_key_string': self.course_str})
        self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.patch(path=path, data=HANDLER_PATCH_PAYLOAD, content_type='application/json')
        assert response.status_code == 405

    @ddt.data(
        {'payload': '', 'status': 400},
        {'payload': '{"name": "C2"}', 'status': 400},
        {'payload': '{"assignment_type": "automatic"}', 'status': 400},
    )
    @ddt.unpack
    def test_cohort_handler_patch_invalid_request(self, payload, status):
        """
        Test the endpoint with invalid requests.
        """
        cohorts.add_cohort(self.course_key, "C1", "random")
        cohorts.add_cohort(self.course_key, "C2", "automatic")
        path = reverse(
            'api_cohorts:cohort_handler',
            kwargs={'course_key_string': self.course_str, 'cohort_id': 1}
        )
        self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.patch(path=path, data=payload, content_type='application/json')
        assert response.status_code == status

    @ddt.data({'is_staff': False, 'payload': HANDLER_PATCH_PAYLOAD, 'status': 403},
              {'is_staff': True, 'payload': HANDLER_PATCH_PAYLOAD, 'status': 204},
              {'is_staff': False, 'payload': '', 'status': 403},
              {'is_staff': True, 'payload': '', 'status': 200}, )
    @ddt.unpack
    def test_cohort_handler_patch(self, is_staff, payload, status):
        """
        Test GET and PATCH methods of cohort handler endpoint for a specific cohort
        """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        cohort_id = 1
        path = reverse('api_cohorts:cohort_handler',
                       kwargs={'course_key_string': self.course_str, 'cohort_id': cohort_id})
        user = self.staff_user if is_staff else self.user
        assert self.client.login(username=user.username, password=self.password)
        if payload:
            response = self.client.patch(
                path=path,
                data=payload,
                content_type='application/json')
        else:
            response = self.client.get(path=path)
        assert response.status_code == status

    def test_list_users_in_cohort_non_existent_cohort(self):
        """
        Test listing the users in a non-existent cohort.
        """
        path = reverse(
            'api_cohorts:cohort_users',
            kwargs={'course_key_string': self.course_str, 'cohort_id': 99}
        )
        assert self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.get(path=path)
        assert response.status_code == 404

    @ddt.data(
        {'is_staff': False, 'status': 403},
        {'is_staff': True, 'status': 200},
    )
    @ddt.unpack
    def test_list_users_in_cohort(self, is_staff, status):
        """
        Test GET method for listing users in a cohort.
        """
        users = [UserFactory() for _ in range(5)]
        cohort = CohortFactory(course_id=self.course_key, users=users)
        path = reverse(
            'api_cohorts:cohort_users',
            kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id}
        )
        self.user = self.staff_user if is_staff else self.user
        assert self.client.login(username=self.user.username, password=self.password)
        response = self.client.get(
            path=path
        )
        assert response.status_code == status

        if status == 200:
            results = response.json()['results']
            expected_results = [{
                'username': user.username,
                'email': user.email,
                'name': f'{user.first_name} {user.last_name}'
            } for user in users]
            assert results == expected_results

    def test_add_users_to_cohort_non_existent_cohort(self):
        """
        Test adding users to a non-existent cohort.
        """
        path = reverse(
            'api_cohorts:cohort_users',
            kwargs={'course_key_string': self.course_str, 'cohort_id': 99}
        )
        assert self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.post(
            path=path,
            data=ADD_USER_PAYLOAD,
            content_type='application/json'
        )
        assert response.status_code == 404

    def test_add_users_to_cohort_username_in_url(self):
        """
        Test adding a user to cohort by passing the username in URL.
        """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        path = reverse(
            'api_cohorts:cohort_users',
            kwargs={'course_key_string': self.course_str, 'cohort_id': 1, 'username': self.staff_user.username}
        )
        assert self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.post(path=path, data='', content_type='application/json')
        assert response.status_code == 200

    def test_add_users_to_cohort_missing_users(self):
        """
        Test adding users to cohort without providing the users.
        """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")

        path = reverse('api_cohorts:cohort_users',
                       kwargs={'course_key_string': self.course_str, 'cohort_id': 1})
        assert self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.post(path=path, data='', content_type='application/json')
        assert response.status_code == 400

    @ddt.data({'is_staff': False, 'payload': ADD_USER_PAYLOAD, 'status': 403},
              {'is_staff': True, 'payload': ADD_USER_PAYLOAD, 'status': 200}, )
    @ddt.unpack
    def test_add_users_to_cohort(self, is_staff, payload, status):
        """
        Test POST method for adding users to a cohort
        """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        cohort_id = 1
        path = reverse('api_cohorts:cohort_users',
                       kwargs={'course_key_string': self.course_str, 'cohort_id': cohort_id})
        user = self.staff_user if is_staff else self.user
        assert self.client.login(username=user.username, password=self.password)
        response = self.client.post(
            path=path,
            data=payload,
            content_type='application/json')
        assert response.status_code == status

    def test_add_users_to_cohort_different_types_of_users(self):
        """
        Test adding users of different types - invalid, existing, preassigned, unassigned, to a cohort.
        """
        cohort = cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        cohort2 = cohorts.add_cohort(self.course_key, "C2", "random")
        user1 = UserFactory(username='user1')
        user2 = UserFactory(username='user2')
        user3 = UserFactory(username='user3')
        cohorts.add_user_to_cohort(cohort2, user1)
        cohorts.add_user_to_cohort(cohort, user2)
        path = reverse('api_cohorts:cohort_users',
                       kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id})
        assert self.client.login(username=self.staff_user.username, password=self.password)
        data = '{"users": ["foo@example.com", "user1", "", "user4", "user3", "user2", "foo@bar"]}'
        response = self.client.post(
            path=path,
            data=data,
            content_type='application/json'
        )
        assert response.status_code == 200

        expected_response = {
            "preassigned": ["foo@example.com"],
            "added": [{"username": "user3", "email": user3.email}],
            "success": True,
            "unknown": ["user4"],
            "changed": [{"username": "user1", "email": user1.email, "previous_cohort": "C2"}],
            "invalid": ["foo@bar"],
            "present": ["user2"]
        }
        assert response.json() == expected_response

    def test_remove_user_from_cohort_missing_username(self):
        """
        Test removing a user from cohort without providing the username.
        """
        path = reverse('api_cohorts:cohort_users', kwargs={'course_key_string': self.course_str, 'cohort_id': 1})
        assert self.client.login(username=self.staff_user.username, password=self.password)
        response = self.client.delete(path)
        assert response.status_code == 405

    @ddt.data({'is_staff': False, 'username': USERNAME, 'status': 403},
              {'is_staff': True, 'username': USERNAME, 'status': 204},
              {'is_staff': True, 'username': 'doesnotexist', 'status': 404},
              {'is_staff': False, 'username': None, 'status': 403},
              {'is_staff': True, 'username': None, 'status': 404}, )
    @ddt.unpack
    def test_remove_user_from_cohort(self, is_staff, username, status):
        """
        Test DELETE method for removing an user from a cohort.
        """
        cohort = cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        cohorts.add_user_to_cohort(cohort, USERNAME)
        cohort_id = 1
        path = reverse('api_cohorts:cohort_users',
                       kwargs={'course_key_string': self.course_str, 'cohort_id': cohort_id, 'username': username})
        user = self.staff_user if is_staff else self.user
        assert self.client.login(username=user.username, password=self.password)
        response = self.client.delete(path=path)
        assert response.status_code == status

    @ddt.data({'is_staff': False, 'payload': CSV_DATA, 'status': 403},
              {'is_staff': True, 'payload': CSV_DATA, 'status': 204},
              {'is_staff': True, 'payload': '', 'status': 400},
              {'is_staff': False, 'payload': '', 'status': 403}, )
    @ddt.unpack
    def test_add_users_csv(self, is_staff, payload, status):
        """
        Test adding users to cohorts using a CSV file
        """
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")
        # this temporary file will be removed in `self.tearDown()`
        __, file_name = tempfile.mkstemp(suffix='.csv', dir=tempfile.mkdtemp())
        with open(file_name, 'w') as file_pointer:
            file_pointer.write(payload)
        path = reverse('api_cohorts:cohort_users_csv', kwargs={'course_key_string': self.course_str})
        user = self.staff_user if is_staff else self.user
        assert self.client.login(username=user.username, password=self.password)
        with open(file_name) as file_pointer:
            response = self.client.post(path=path,
                                        data={'uploaded-file': file_pointer})
            assert response.status_code == status

    def test_post_cohort_with_group_id(self):
        """
        Test creating a cohort with group_id and user_partition_id.
        """
        path = reverse('api_cohorts:cohort_handler', kwargs={'course_key_string': self.course_str})
        self.client.login(username=self.staff_user.username, password=self.password)

        payload = {
            'name': 'TestCohort',
            'assignment_type': 'manual',
            'group_id': 1,
            'user_partition_id': 50
        }
        response = self.client.post(path=path, data=payload, content_type='application/json')
        assert response.status_code == 200

        data = response.json()
        assert data['name'] == 'TestCohort'
        assert data['assignment_type'] == 'manual'
        assert data['group_id'] == 1
        assert data['user_partition_id'] == 50
        assert data['user_count'] == 0
        assert 'id' in data

    def test_post_cohort_with_group_id_missing_partition_id(self):
        """
        Test that creating a cohort with group_id but without user_partition_id returns an error.
        """
        path = reverse('api_cohorts:cohort_handler', kwargs={'course_key_string': self.course_str})
        self.client.login(username=self.staff_user.username, password=self.password)

        payload = {
            'name': 'TestCohort',
            'assignment_type': 'manual',
            'group_id': 1
        }
        response = self.client.post(path=path, data=payload, content_type='application/json')
        assert response.status_code == 400

        data = response.json()
        assert data['developer_message'] == 'If group_id is specified, user_partition_id must also be specified.'
        assert data['error_code'] == 'missing-user-partition-id'

    def test_patch_cohort_set_group_id(self):
        """
        Test updating a cohort to set group_id and user_partition_id.
        """
        cohort = cohorts.add_cohort(self.course_key, "TestCohort", "manual")
        path = reverse(
            'api_cohorts:cohort_handler',
            kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id}
        )
        self.client.login(username=self.staff_user.username, password=self.password)

        payload = {
            'group_id': 2,
            'user_partition_id': 50
        }
        response = self.client.patch(path=path, data=payload, content_type='application/json')
        assert response.status_code == 204

        # Verify by fetching the cohort
        response = self.client.get(path=path)
        data = response.json()
        assert data['id'] == cohort.id
        assert data['name'] == 'TestCohort'
        assert data['assignment_type'] == 'manual'
        assert data['group_id'] == 2
        assert data['user_partition_id'] == 50

    def test_patch_cohort_remove_group_id(self):
        """
        Test updating a cohort to remove the group_id association by setting it to null.
        """
        cohort = cohorts.add_cohort(self.course_key, "TestCohort", "manual")
        link_cohort_to_partition_group(cohort, 50, 1)

        path = reverse(
            'api_cohorts:cohort_handler',
            kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id}
        )
        self.client.login(username=self.staff_user.username, password=self.password)

        # Verify the cohort has a group_id
        response = self.client.get(path=path)
        data = response.json()
        assert data['id'] == cohort.id
        assert data['name'] == 'TestCohort'
        assert data['group_id'] == 1
        assert data['user_partition_id'] == 50

        # Remove the group_id by setting it to null
        payload = {'group_id': None}
        response = self.client.patch(path=path, data=payload, content_type='application/json')
        assert response.status_code == 204

        # Verify the group_id was removed but other fields unchanged
        response = self.client.get(path=path)
        data = response.json()
        assert data['id'] == cohort.id
        assert data['name'] == 'TestCohort'
        assert data['assignment_type'] == 'manual'
        assert data['group_id'] is None
        assert data['user_partition_id'] is None

    def test_patch_cohort_with_group_id_missing_partition_id(self):
        """
        Test that updating a cohort with group_id but without user_partition_id returns an error.
        """
        cohort = cohorts.add_cohort(self.course_key, "TestCohort", "manual")
        path = reverse(
            'api_cohorts:cohort_handler',
            kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id}
        )
        self.client.login(username=self.staff_user.username, password=self.password)

        payload = {'group_id': 2}
        response = self.client.patch(path=path, data=payload, content_type='application/json')
        assert response.status_code == 400

        data = response.json()
        assert data['developer_message'] == 'If group_id is specified, user_partition_id must also be specified.'
        assert data['error_code'] == 'missing-user-partition-id'

    def test_patch_cohort_with_name_only(self):
        """
        Test that PATCH with only name is now valid (previously required assignment_type too).
        """
        cohort = cohorts.add_cohort(self.course_key, "OldName", "manual")
        path = reverse(
            'api_cohorts:cohort_handler',
            kwargs={'course_key_string': self.course_str, 'cohort_id': cohort.id}
        )
        self.client.login(username=self.staff_user.username, password=self.password)

        payload = {'name': 'NewName'}
        response = self.client.patch(path=path, data=payload, content_type='application/json')
        assert response.status_code == 204

        # Verify the name was updated and other fields unchanged
        response = self.client.get(path=path)
        data = response.json()
        assert data['id'] == cohort.id
        assert data['name'] == 'NewName'
        assert data['assignment_type'] == 'manual'
        assert data['group_id'] is None
        assert data['user_partition_id'] is None
