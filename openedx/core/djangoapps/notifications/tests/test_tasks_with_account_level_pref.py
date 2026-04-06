"""
Tests for notifications tasks.
"""

import datetime
from unittest.mock import patch

import ddt
from django.conf import settings
from django.core.exceptions import ValidationError
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..config.waffle import DISABLE_NOTIFICATIONS
from ..models import Notification, NotificationPreference
from ..tasks import delete_notifications, send_notifications
from .utils import create_notification


@ddt.ddt
class SendNotificationsTest(ModuleStoreTestCase):
    """
    Tests for send_notifications.
    """

    def setUp(self):
        """
        Create a course and users for the course.
        """

        super().setUp()
        self.user = UserFactory()
        self.course_1 = CourseFactory.create(
            org='testorg',
            number='testcourse',
            run='testrun'
        )

    @ddt.data(
        ('discussion', 'new_comment_on_response'),  # core notification
        ('discussion', 'new_response'),  # non core notification
    )
    @ddt.unpack
    def test_send_notifications(self, app_name, notification_type):
        """
        Test whether send_notifications creates a new notification.
        """
        context = {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        }
        content_url = 'https://example.com/'

        # Call the `send_notifications` function.
        with patch('openedx.core.djangoapps.notifications.tasks.notification_generated_event') as event_mock:
            send_notifications([self.user.id], str(self.course_1.id), app_name, notification_type, context, content_url)
            assert event_mock.called
            assert event_mock.call_args[0][0] == [self.user.id]
            assert event_mock.call_args[0][1] == app_name
            assert event_mock.call_args[0][2] == notification_type

        # Assert that `Notification` objects have been created for the users.
        notification = Notification.objects.filter(user_id=self.user.id).first()
        # Removing uuid from content_context for assertion
        if 'uuid' in notification.content_context:
            notification.content_context.pop('uuid')
        # Assert that the `Notification` objects have the correct properties.
        self.assertEqual(notification.user_id, self.user.id)  # noqa: PT009
        self.assertEqual(notification.app_name, app_name)  # noqa: PT009
        self.assertEqual(notification.notification_type, notification_type)  # noqa: PT009
        self.assertEqual(notification.content_context, context)  # noqa: PT009
        self.assertEqual(notification.content_url, content_url)  # noqa: PT009
        self.assertEqual(notification.course_id, self.course_1.id)  # noqa: PT009

    @ddt.data(True, False)
    def test_enable_notification_flag(self, flag_value):
        """
        Tests that notification is NOT sent when DISABLE_NOTIFICATIONS flag is enabled,
        and IS sent when the flag is disabled.
        """
        app_name = "discussion"
        notification_type = "new_response"
        context = {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        }
        content_url = 'https://example.com/'
        with override_waffle_flag(DISABLE_NOTIFICATIONS, active=flag_value):
            send_notifications([self.user.id], str(self.course_1.id), app_name, notification_type, context, content_url)
        created_notifications_count = 0 if flag_value else 1
        self.assertEqual(len(Notification.objects.all()), created_notifications_count)  # noqa: PT009

    def test_notification_not_send_with_preference_disabled(self):
        """
        Tests notification not send if preference is disabled
        """
        app_name = "discussion"
        notification_type = "new_response"
        context = {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        }
        content_url = 'https://example.com/'

        account_preferences, __created = NotificationPreference.objects.get_or_create(
            user_id=self.user.id,
            app=app_name,
            type=notification_type,
        )
        account_preferences.web = False
        account_preferences.email = False
        account_preferences.push = False
        account_preferences.save()

        send_notifications([self.user.id], str(self.course_1.id), app_name, notification_type, context, content_url)
        self.assertEqual(len(Notification.objects.all()), 0)  # noqa: PT009

    def test_send_notification_with_grouping_enabled(self):
        """
        Test send_notifications with grouping enabled.
        """

        account_preferences, __created = NotificationPreference.objects.get_or_create(
            user_id=self.user.id,
            app='discussion',
            type='new_discussion_post',
        )
        account_preferences.web = True
        account_preferences.save()
        with patch('openedx.core.djangoapps.notifications.tasks.group_user_notifications') as user_notifications_mock:
            context = {
                'post_title': 'Test Post',
                'username': 'Test Author',
                'group_by_id': 'group_by_id'
            }
            content_url = 'https://example.com/'
            send_notifications(
                [self.user.id],
                str(self.course_1.id),
                'discussion',
                'new_discussion_post',
                {**context},
                content_url
            )
            send_notifications(
                [self.user.id],
                str(self.course_1.id),
                'discussion',
                'new_discussion_post',
                {**context},
                content_url
            )
            self.assertEqual(Notification.objects.filter(user_id=self.user.id).count(), 1)  # noqa: PT009
            user_notifications_mock.assert_called_once()

    def test_notification_not_created_when_context_is_incomplete(self):
        try:
            send_notifications([self.user.id], str(self.course_1.id), "discussion", "new_comment", {}, "")
        except Exception as exc:  # pylint: disable=broad-except
            assert isinstance(exc, ValidationError)  # noqa: PT017


@ddt.ddt
class SendBatchNotificationsTest(ModuleStoreTestCase):
    """
    Test that notification and notification preferences are created in batches
    """

    def setUp(self):
        """
        Setups test case
        """
        super().setUp()
        self.course = CourseFactory.create(
            org='test_org',
            number='test_course',
            run='test_run'
        )

    def _create_users(self, num_of_users):
        """
        Create users and enroll them in course
        """
        users = [
            UserFactory.create(username=f'user{i}', email=f'user{i}@example.com')
            for i in range(num_of_users)
        ]
        for user in users:
            CourseEnrollment.enroll(user=user, course_key=self.course.id)
        return users

    @ddt.data(
        (settings.NOTIFICATION_CREATION_BATCH_SIZE, 11, 4),
        (settings.NOTIFICATION_CREATION_BATCH_SIZE + 10, 13, 6),
        (settings.NOTIFICATION_CREATION_BATCH_SIZE - 10, 11, 4),
    )
    @ddt.unpack
    def test_notification_is_send_in_batch(self, creation_size, prefs_query_count, notifications_query_count):
        """
        Tests notifications and notification preferences are created in batches
        """
        notification_app = "discussion"
        notification_type = "new_discussion_post"
        users = self._create_users(creation_size)
        user_ids = [user.id for user in users]
        context = {
            "post_title": "Test Post",
            "username": "Test Author"
        }

        # Creating preferences and asserting query count
        with self.assertNumQueries(prefs_query_count):
            send_notifications(user_ids, str(self.course.id), notification_app, notification_type,
                               context, "http://test.url")

        # Creating notifications and asserting query count
        with self.assertNumQueries(notifications_query_count):
            send_notifications(user_ids, str(self.course.id), notification_app, notification_type,
                               context, "http://test.url")

    def test_preference_not_created_for_default_off_preference(self):
        """
        Tests if new preferences are NOT created when default preference for
        notification type is False
        """
        notification_app = "discussion"
        notification_type = "new_discussion_post"
        users = self._create_users(20)
        user_ids = [user.id for user in users]
        context = {
            "post_title": "Test Post",
            "username": "Test Author"
        }
        with self.assertNumQueries(11):
            send_notifications(user_ids, str(self.course.id), notification_app, notification_type,
                               context, "http://test.url")

    def test_preference_created_for_default_on_preference(self):
        """
        Tests if new preferences are created when default preference for
        notification type is True
        """
        notification_app = "discussion"
        notification_type = "new_comment"
        users = self._create_users(20)
        NotificationPreference.objects.all().delete()
        user_ids = [user.id for user in users]
        context = {
            "post_title": "Test Post",
            "author_name": "Test Author",
            "replier_name": "Replier Name"
        }
        with self.assertNumQueries(13):
            send_notifications(user_ids, str(self.course.id), notification_app, notification_type,
                               context, "http://test.url")

    @ddt.data(
        ("new_response", 2),
        ("new_response", 2),
        ("new_response", 2),
        ("new_discussion_post", 0),
        ("new_discussion_post", 0),
        ("new_discussion_post", 0),
    )
    @ddt.unpack
    def test_preference_enabled_in_batch_audience(self, notification_type, generated_count):
        """
        Tests if users with preference enabled in batch gets notification
        """
        users = self._create_users(2)
        user_ids = [user.id for user in users]

        app_name = "discussion"
        context = {
            'post_title': 'Post title',
            'username': 'Username',
            'replier_name': 'replier name',
            'author_name': 'Authorname'
        }
        content_url = 'https://example.com/'
        send_notifications(user_ids, str(self.course.id), app_name, notification_type, context, content_url)
        self.assertEqual(len(Notification.objects.all()), generated_count)  # noqa: PT009


class TestDeleteNotificationTask(ModuleStoreTestCase):
    """
    Tests delete_notification_function
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course_1 = CourseFactory.create(org='org', number='num', run='run_01')
        self.course_2 = CourseFactory.create(org='org', number='num', run='run_02')
        Notification.objects.all().delete()

    def test_app_name_param(self):
        """
        Tests if app_name parameter works as expected
        """
        assert not Notification.objects.all()
        create_notification(self.user, self.course_1.id, app_name='discussion', notification_type='new_comment')
        create_notification(self.user, self.course_1.id, app_name='updates', notification_type='course_updates')
        delete_notifications({'app_name': 'discussion'})
        assert not Notification.objects.filter(app_name='discussion')
        assert Notification.objects.filter(app_name='updates')

    def test_notification_type_param(self):
        """
        Tests if notification_type parameter works as expected
        """
        assert not Notification.objects.all()
        create_notification(self.user, self.course_1.id, app_name='discussion', notification_type='new_comment')
        create_notification(self.user, self.course_1.id, app_name='discussion', notification_type='new_response')
        delete_notifications({'notification_type': 'new_comment'})
        assert not Notification.objects.filter(notification_type='new_comment')
        assert Notification.objects.filter(notification_type='new_response')

    def test_created_param(self):
        """
        Tests if created parameter works as expected
        """
        assert not Notification.objects.all()
        create_notification(self.user, self.course_1.id, created=datetime.datetime(2024, 2, 10))
        create_notification(self.user, self.course_2.id, created=datetime.datetime(2024, 3, 12, 5))
        kwargs = {
            'created': {
                'created__gte': datetime.datetime(2024, 3, 12, 0, 0, 0),
                'created__lte': datetime.datetime(2024, 3, 12, 23, 59, 59),
            }
        }
        delete_notifications(kwargs)
        self.assertEqual(Notification.objects.all().count(), 1)  # noqa: PT009

    def test_course_id_param(self):
        """
        Tests if course_id parameter works as expected
        """
        assert not Notification.objects.all()
        create_notification(self.user, self.course_1.id)
        create_notification(self.user, self.course_2.id)
        delete_notifications({'course_id': self.course_1.id})
        assert not Notification.objects.filter(course_id=self.course_1.id)
        assert Notification.objects.filter(course_id=self.course_2.id)


@ddt.ddt
class NotificationCreationOnChannelsTests(ModuleStoreTestCase):
    """
    Tests for notification creation and channels value.
    """

    def setUp(self):
        """
        Create a course and users for tests.
        """

        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(
            org='testorg',
            number='testcourse',
            run='testrun'
        )

        self.account_preference, __created = NotificationPreference.objects.get_or_create(
            user_id=self.user.id,
            app='discussion',
            type='new_discussion_post',
            web=False,
            email=False,
        )

    @ddt.data(
        (False, False, 0),
        (False, True, 1),
        (True, False, 1),
        (True, True, 1),
    )
    @ddt.unpack
    def test_notification_is_created_when_any_channel_is_account_level(self, web_value, email_value, generated_count):
        """
        Tests if notification is created if any preference is enabled on account level preferences
        """
        app_name = 'discussion'
        notification_type = 'new_discussion_post'
        self.account_preference.web = web_value
        self.account_preference.email = email_value
        self.account_preference.save()
        kwargs = {
            'user_ids': [self.user.id],
            'course_key': str(self.course.id),
            'app_name': app_name,
            'notification_type': notification_type,
            'content_url': 'https://example.com/',
            'context': {
                'post_title': 'Post title',
                'username': 'user name',
            },
        }
        with patch('openedx.core.djangoapps.notifications.tasks.notification_generated_event') as event_mock:  # noqa: F841
            send_notifications(**kwargs)
            notifications = Notification.objects.all()
            assert len(notifications) == generated_count
            if notifications:
                notification = Notification.objects.all()[0]
                assert notification.web == web_value
                assert notification.email == email_value
