"""
Tests for DiscussionsConfiguration admin view
"""
from django.test import TestCase
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider


class DiscussionsConfigurationAdminTest(TestCase):
    """
    Tests for discussion config admin
    """
    def setUp(self):
        super().setUp()
        self.superuser = UserFactory(is_staff=True, is_superuser=True)
        self.client.login(username=self.superuser.username, password="Password1234")

    def test_change_view(self):
        """
        Test that the DiscussionAdmin's change_view processes the context_key correctly and returns a successful
        response.
        """
        discussion_config = DiscussionsConfiguration.objects.create(
            context_key='course-v1:test+test+06_25_2024',
            provider_type=Provider.OPEN_EDX,
        )
        url = reverse('admin:discussions_discussionsconfiguration_change', args=[discussion_config.context_key])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertContains(response, 'course-v1:test+test+06_25_2024')
