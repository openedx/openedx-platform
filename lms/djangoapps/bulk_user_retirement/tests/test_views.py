"""
Test cases for GDPR User Retirement Views
"""
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus


class BulkUserRetirementViewTests(APITestCase):
    """
    Tests the bulk user retirement api
    """
    def setUp(self):
        super().setUp()
        login_client = APIClient()
        self.user1 = UserFactory.create(
            username='testuser1',
            email='test1@example.com',
            password='test1_password',
            profile__name="Test User1"
        )
        login_client.login(username=self.user1.username, password='test1_password')
        self.user2 = UserFactory.create(
            username='testuser2',
            email='test2@example.com',
            password='test2_password',
            profile__name="Test User2"
        )
        login_client.login(username=self.user2.username, password='test2_password')
        self.user3 = UserFactory.create(
            username='testuser3',
            email='test3@example.com',
            password='test3_password',
            profile__name="Test User3"
        )
        self.user4 = UserFactory.create(
            username='testuser4',
            email='test4@example.com',
            password='test4_password',
            profile__name="Test User4"
        )
        RetirementState.objects.create(
            state_name='PENDING',
            state_execution_order=1,
            is_dead_end_state=False,
            required=True
        )
        self.pending_state = RetirementState.objects.get(state_name='PENDING')
        # Use a separate client for retirement worker (don't mix cookie state)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_gdpr_user_retirement_api(self):
        user_retirement_url = reverse('bulk_retirement_api')
        expected_response = {
            'successful_user_retirements': [self.user2.username],
            'failed_user_retirements': []
        }
        with self.settings(RETIREMENT_SERVICE_WORKER_USERNAME=self.user1.username):
            response = self.client.post(user_retirement_url, {"usernames": self.user2.username})
            assert response.status_code == 200
            assert response.data == expected_response

            retirement_status = UserRetirementStatus.objects.get(user__username=self.user2.username)
            assert retirement_status.current_state == self.pending_state

    def test_retirement_for_non_existing_users(self):
        user_retirement_url = reverse('bulk_retirement_api')
        expected_response = {
            'successful_user_retirements': [],
            'failed_user_retirements': ["non_existing_user"]
        }
        with self.settings(RETIREMENT_SERVICE_WORKER_USERNAME=self.user1.username):
            response = self.client.post(user_retirement_url, {"usernames": "non_existing_user"})
            assert response.status_code == 200
            assert response.data == expected_response

    def test_retirement_for_multiple_users(self):
        user_retirement_url = reverse('bulk_retirement_api')
        expected_response = {
            'successful_user_retirements': [self.user3.username, self.user4.username],
            'failed_user_retirements': []
        }
        with self.settings(RETIREMENT_SERVICE_WORKER_USERNAME=self.user1.username):
            response = self.client.post(user_retirement_url, {
                "usernames": f'{self.user3.username},{self.user4.username}'
            })

            assert response.status_code == 200
            assert sorted(response.data['successful_user_retirements']) == sorted(expected_response['successful_user_retirements'])  # pylint: disable=line-too-long

            retirement_status_1 = UserRetirementStatus.objects.get(user__username=self.user3.username)
            assert retirement_status_1.current_state == self.pending_state

            retirement_status_2 = UserRetirementStatus.objects.get(user__username=self.user4.username)
            assert retirement_status_2.current_state == self.pending_state

    def test_retirement_for_multiple_users_with_some_nonexisting_users(self):
        user_retirement_url = reverse('bulk_retirement_api')
        expected_response = {
            'successful_user_retirements': [self.user3.username, self.user4.username],
            'failed_user_retirements': ['non_existing_user']
        }

        with self.settings(RETIREMENT_SERVICE_WORKER_USERNAME=self.user1.username):
            response = self.client.post(user_retirement_url, {
                "usernames": '{user1},{user2}, non_existing_user'.format(
                    user1=self.user3.username,
                    user2=self.user4.username
                )
            })
            assert response.status_code == 200

            assert sorted(response.data['successful_user_retirements']) == sorted(expected_response['successful_user_retirements'])  # pylint: disable=line-too-long

            retirement_status_1 = UserRetirementStatus.objects.get(user__username=self.user3.username)
            assert retirement_status_1.current_state == self.pending_state

            retirement_status_2 = UserRetirementStatus.objects.get(user__username=self.user4.username)
            assert retirement_status_2.current_state == self.pending_state

    def test_retirement_for_unauthorized_users(self):
        user_retirement_url = reverse('bulk_retirement_api')
        response = self.client.post(user_retirement_url, {"usernames": self.user2.username})
        assert response.status_code == 403
