"""
Tests for notification grouping module
"""

import ddt
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.notifications.grouping_notifications import (
    BaseNotificationGrouper,
    NotificationRegistry,
    group_user_notifications,
    get_user_existing_notifications, NewPostGrouper
)
from openedx.core.djangoapps.notifications.models import Notification
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestNotificationRegistry(unittest.TestCase):
    """
    Tests for the NotificationRegistry class
    """

    @patch.dict(
        'openedx.core.djangoapps.notifications.base_notification.COURSE_NOTIFICATION_TYPES',
        {'test_notification': 'Test Notification'}
    )
    def test_register_and_get_grouper(self):
        """
        Test that the register and get_grouper methods work as expected
        """

        class TestGrouper(BaseNotificationGrouper):
            def group(self, new_notification, old_notification):
                pass

        NotificationRegistry.register('test_notification')(TestGrouper)
        grouper = NotificationRegistry.get_grouper('test_notification')
        self.assertIsInstance(grouper, TestGrouper)

    def test_get_grouper_returns_none_for_unregistered_type(self):
        """
        Test that get_grouper returns None for an unregistered notification type
        """
        grouper = NotificationRegistry.get_grouper('non_existent')
        self.assertIsNone(grouper)


class TestNewPostGrouper(unittest.TestCase):
    """
    Tests for the NewPostGrouper class
    """

    def test_group(self):
        """
        Test that the function groups new post notifications based on the author name
        """
        new_notification = MagicMock(spec=Notification)
        old_notification = MagicMock(spec=Notification)
        old_notification.content_context = {
            'author_name': 'User1',
            'username': 'User1'
        }

        updated_context = NewPostGrouper().group(new_notification, old_notification)

        self.assertTrue(updated_context['grouped'])
        self.assertEqual(updated_context['replier_name'], new_notification.content_context['replier_name'])

    def test_new_post_with_same_user(self):
        """
        Test that the function does not group notifications with same authors if notification is not
        already grouped
        """
        new_notification = MagicMock(spec=Notification)
        old_notification = MagicMock(spec=Notification)
        old_notification.content_context = {
            'username': 'User1',
            'grouped': False
        }
        new_notification.content_context = {
            'username': 'User1',
        }

        updated_context = NewPostGrouper().group(new_notification, old_notification)

        self.assertFalse(updated_context.get('grouped', False))


@ddt.ddt
class TestGroupUserNotifications(ModuleStoreTestCase):
    """
    Tests for the group_user_notifications function
    """

    @patch('openedx.core.djangoapps.notifications.grouping_notifications.NotificationRegistry.get_grouper')
    def test_group_user_notifications(self, mock_get_grouper):
        """
        Test that the function groups notifications using the appropriate grou
        """
        # Mock the grouper
        mock_grouper = MagicMock(spec=NewPostGrouper)
        mock_get_grouper.return_value = mock_grouper

        new_notification = MagicMock(spec=Notification)
        old_notification = MagicMock(spec=Notification)

        group_user_notifications(new_notification, old_notification)

        mock_grouper.group.assert_called_once_with(new_notification, old_notification)
        self.assertTrue(old_notification.save.called)
        self.assertIsNone(old_notification.last_read)
        self.assertIsNone(old_notification.last_seen)
        self.assertIsNotNone(old_notification.created)

    def test_group_user_notifications_no_grouper(self):
        """
        Test that the function does nothing if no grouper is found
        """
        new_notification = MagicMock(spec=Notification)
        old_notification = MagicMock(spec=Notification)

        group_user_notifications(new_notification, old_notification)

        self.assertFalse(old_notification.save.called)

    @ddt.data(datetime(2023, 1, 1, tzinfo=ZoneInfo("UTC")), None)
    def test_not_grouped_when_notification_is_seen(self, last_seen):
        """
        Notification is not grouped if the notification is marked as seen
        """
        course = CourseFactory()
        user = UserFactory()
        notification_params = {
            'app_name': 'discussion',
            'notification_type': 'new_discussion_post',
            'course_id': course.id,
            'group_by_id': course.id,
            'content_url': 'http://example.com',
            'user': user,
            'last_seen': last_seen,
        }
        Notification.objects.create(content_context={
            'username': 'User1',
            'post_title': ' Post title',
            'replier_name': 'User 1',

        }, **notification_params)
        existing_notifications = get_user_existing_notifications(
            [user.id], 'new_discussion_post', course.id, course.id
        )
        if last_seen is None:
            assert existing_notifications[user.id] is not None
        else:
            assert existing_notifications[user.id] is None


class TestGetUserExistingNotifications(unittest.TestCase):
    """
    Tests for the get_user_existing_notifications function
    """

    @patch('openedx.core.djangoapps.notifications.models.Notification.objects.filter')
    def test_get_user_existing_notifications(self, mock_filter):
        """
        Test that the function returns the last notification for each user
        """
        # Mock the notification objects returned by the filter
        mock_notification1 = MagicMock(spec=Notification)
        mock_notification1.user_id = 1
        mock_notification1.created = datetime(2023, 9, 1, tzinfo=ZoneInfo("UTC"))

        mock_notification2 = MagicMock(spec=Notification)
        mock_notification2.user_id = 1
        mock_notification2.created = datetime(2023, 9, 2, tzinfo=ZoneInfo("UTC"))

        mock_filter.return_value = [mock_notification1, mock_notification2]

        user_ids = [1, 2]
        notification_type = 'new_discussion_post'
        group_by_id = 'group_id_1'
        course_id = 'course_1'

        result = get_user_existing_notifications(user_ids, notification_type, group_by_id, course_id)

        # Verify the results
        self.assertEqual(result[1], mock_notification2)
        self.assertIsNone(result[2])  # user 2 has no notifications
