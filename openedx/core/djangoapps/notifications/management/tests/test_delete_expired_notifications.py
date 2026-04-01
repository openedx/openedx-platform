"""
Test for generate_notification_preferences management command.
"""

from unittest import mock

from django.core.management import call_command

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestDeleteExpiredNotification(ModuleStoreTestCase):
    """
    Tests for delete_expired_notifications management command.
    """

    @mock.patch(
        'openedx.core.djangoapps.notifications.tasks.delete_expired_notifications'
    )
    def test_delete_expired_notifications(self, mock_task):
        """
        Test generate_course_notification_preferences command.
        """
        call_command('delete_expired_notifications')
        mock_task.delay.assert_called_once()
