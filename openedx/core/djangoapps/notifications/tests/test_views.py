"""
Tests for the views in the notifications app.
"""
from datetime import datetime, timedelta
from unittest import mock

import ddt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from zoneinfo import ZoneInfo
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole, CourseInstructorRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR
)
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS
from openedx.core.djangoapps.notifications.email.utils import encrypt_string
from openedx.core.djangoapps.notifications.models import (
    Notification,
    NotificationPreference
)
from openedx.core.djangoapps.notifications.serializers import (
    add_info_to_notification_config,
    add_non_editable_in_preference
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..base_notification import COURSE_NOTIFICATION_APPS, NotificationTypeManager, COURSE_NOTIFICATION_TYPES, \
    get_default_values_of_preferences
from ..utils import get_notification_types_with_visibility_settings, exclude_inaccessible_preferences

User = get_user_model()


@ddt.ddt
class NotificationListAPIViewTest(APITestCase):
    """
    Tests suit for the NotificationListAPIView.
    """

    def setUp(self):
        self.TEST_PASSWORD = 'Password1234'
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.url = reverse('notifications-list')

    def test_list_notifications(self):
        """
        Test that the view can list notifications.
        """
        # Create a notification for the user.
        Notification.objects.create(
            user=self.user,
            app_name='discussion',
            notification_type='new_response',
            content_context={
                'replier_name': 'test_user',
                'post_title': 'This is a test post.',
            }
        )
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view.
        response = self.client.get(self.url)

        # Assert that the response is successful.

        self.assertEqual(response.status_code, 200)
        data = response.data['results']
        # Assert that the response contains the notification.
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['app_name'], 'discussion')
        self.assertEqual(data[0]['notification_type'], 'new_response')
        self.assertEqual(
            data[0]['content'],
            '<p><strong>test_user</strong> responded to your post <strong>This is a test post.</strong></p>'
        )

    def test_list_notifications_with_app_name_filter(self):
        """
        Test that the view can filter notifications by app name.
        """
        # Create two notifications for the user, one for each app name.
        Notification.objects.create(
            user=self.user,
            app_name='discussion',
            notification_type='new_response',
            content_context={
                'replier_name': 'test_user',
                'post_title': 'This is a test post.',
            }
        )
        Notification.objects.create(
            user=self.user,
            app_name='app2',
            notification_type='info',
        )
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view with the app_name query parameter set to 'app1'.
        response = self.client.get(self.url + "?app_name=discussion")

        # Assert that the response is successful.
        self.assertEqual(response.status_code, 200)

        # Assert that the response contains only the notification for app1.
        data = response.data['results']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['app_name'], 'discussion')
        self.assertEqual(data[0]['notification_type'], 'new_response')
        self.assertEqual(
            data[0]['content'],
            '<p><strong>test_user</strong> responded to your post <strong>This is a test post.</strong></p>'
        )

    @ddt.data(
        ([], 0),
        (['web'], 1),
        (['email'], 0),
        (['web', 'email'], 1),
        (['web', 'email', 'push'], 1),
    )
    @ddt.unpack
    def test_list_notifications_with_channels(self, channels, expected_count):
        """
        Test that the view can filter notifications by app name and channels.
        """

        Notification.objects.create(
            user=self.user,
            app_name='discussion',
            notification_type='new_response',
            content_context={
                'replier_name': 'test_user',
                'post_title': 'This is a test post.',
            },
            web='web' in channels,
            email='email' in channels
        )

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view with the app_name query parameter set to 'app1'.
        response = self.client.get(self.url + "?app_name=discussion")

        # Assert that the response is successful.
        self.assertEqual(response.status_code, 200)

        # Assert that the response contains expected results i.e. channels contains web or is null.
        data = response.data['results']
        self.assertEqual(len(data), expected_count)

    @mock.patch("eventtracking.tracker.emit")
    def test_list_notifications_with_tray_opened_param(self, mock_emit):
        """
        Test event emission with tray_opened param is provided.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view with the tray_opened query parameter set to True.
        response = self.client.get(self.url + "?tray_opened=True")

        # Assert that the response is successful.
        self.assertEqual(response.status_code, 200)

        event_name, event_data = mock_emit.call_args[0]
        self.assertEqual(event_name, 'edx.notifications.tray_opened')
        self.assertEqual(event_data['user_id'], self.user.id)
        self.assertEqual(event_data['unseen_notifications_count'], 0)

    def test_list_notifications_without_authentication(self):
        """
        Test that the view returns 401 if the user is not authenticated.
        """
        # Make a request to the view without authenticating.
        response = self.client.get(self.url)

        # Assert that the response is unauthorized.
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_notifications_with_expiry_date(self):
        """
        Test that the view can filter notifications by expiry date.
        """
        today = datetime.now(ZoneInfo("UTC"))

        # Create two notifications for the user, one with current date and other with expiry date.
        Notification.objects.create(
            user=self.user,
            notification_type='info',
            created=today
        )
        Notification.objects.create(
            user=self.user,
            notification_type='info',
            created=today - timedelta(days=settings.NOTIFICATIONS_EXPIRY)
        )
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view
        response = self.client.get(self.url)

        # Assert that the response is successful.
        self.assertEqual(response.status_code, 200)

        # Assert that the response contains only the notification for current date.
        data = response.data['results']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['created'], today.strftime('%Y-%m-%dT%H:%M:%S.%fZ'))

    def test_list_notifications_with_order_by_reverse_id(self):
        """
        Test that the view can filter notifications and order by reverse id.
        """

        # Create two notifications for the user
        notification1 = Notification.objects.create(
            user=self.user,
            notification_type='info',
        )
        notification2 = Notification.objects.create(
            user=self.user,
            notification_type='info',
        )
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Make a request to the view
        response = self.client.get(self.url)

        # Assert that the response is successful.
        self.assertEqual(response.status_code, 200)

        # Assert that the response id list is in reverse order.
        data = response.data['results']
        self.assertEqual(len(data), 2)
        self.assertEqual([data[0]['id'], data[1]['id']], [notification2.id, notification1.id])


@ddt.ddt
class NotificationCountViewSetTestCase(ModuleStoreTestCase):
    """
    Tests for the NotificationCountViewSet.
    """

    def setUp(self):
        # Create a user.
        super().setUp()
        self.user = UserFactory()
        self.client = APIClient()

        course = CourseFactory.create(
            org='testorg',
            number='testcourse',
            run='testrun'
        )

        course_overview = CourseOverviewFactory.create(id=course.id, org='AwesomeOrg')
        self.enrollment = CourseEnrollment.objects.create(
            user=self.user,
            course=course_overview,
            is_active=True,
            mode='audit'
        )

        self.url = reverse('notifications-count')

        # Create some notifications for the user.
        Notification.objects.create(user=self.user, app_name='App Name 1', notification_type='Type A')
        Notification.objects.create(user=self.user, app_name='App Name 1', notification_type='Type B')
        Notification.objects.create(user=self.user, app_name='App Name 2', notification_type='Type A')
        Notification.objects.create(user=self.user, app_name='App Name 3', notification_type='Type C')
        Notification.objects.create(user=self.user, app_name='App Name 4', notification_type='Type D', web=False)

    @override_waffle_flag(ENABLE_NOTIFICATIONS, active=True)
    @ddt.unpack
    def test_get_unseen_notifications_count_with_show_notifications_tray(self):
        """
        Test that the endpoint returns the correct count of unseen notifications and show_notifications_tray value.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        # Make a request to the view
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 4)
        self.assertEqual(response.data['count_by_app_name'], {
            'App Name 1': 2, 'App Name 2': 1, 'App Name 3': 1, 'discussion': 0,
            'updates': 0, 'grading': 0})
        self.assertEqual(response.data['show_notifications_tray'], True)

    def test_get_unseen_notifications_count_for_unauthenticated_user(self):
        """
        Test that the endpoint returns 401 for an unauthenticated user.
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_unseen_notifications_count_for_user_with_no_notifications(self):
        """
        Test that the endpoint returns 0 for a user with no notifications.
        """
        # Create a user with no notifications.
        user = UserFactory()
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['count_by_app_name'], {'discussion': 0, 'updates': 0,
                                                              'grading': 0})

    def test_get_expiry_days_in_count_view(self):
        """
        Tests if "notification_expiry_days" exists in API response
        """
        user = UserFactory()
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['notification_expiry_days'], 60)


class MarkNotificationsSeenAPIViewTestCase(APITestCase):
    """
    Tests for the MarkNotificationsUnseenAPIView.
    """

    def setUp(self):
        self.TEST_PASSWORD = 'Password1234'
        self.user = UserFactory(password=self.TEST_PASSWORD)

        # Create some sample notifications for the user
        Notification.objects.create(user=self.user, app_name='App Name 1', notification_type='Type A')
        Notification.objects.create(user=self.user, app_name='App Name 1', notification_type='Type B')
        Notification.objects.create(user=self.user, app_name='App Name 2', notification_type='Type A')
        Notification.objects.create(user=self.user, app_name='App Name 3', notification_type='Type C')

    def test_mark_notifications_seen(self):
        # Create a POST request to mark notifications as seen for 'App Name 1'
        app_name = 'App Name 1'
        url = reverse('mark-notifications-seen', kwargs={'app_name': app_name})
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.put(url)
        # Assert the response status code is 200 (OK)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Assert the response data contains the expected message
        expected_data = {'message': 'Notifications marked as seen.'}
        self.assertEqual(response.data, expected_data)

        # Assert the notifications for 'App Name 1' are marked as seen for the user
        notifications = Notification.objects.filter(user=self.user, app_name=app_name, last_seen__isnull=False)
        self.assertEqual(notifications.count(), 2)


class NotificationReadAPIViewTestCase(APITestCase):
    """
    Tests for the NotificationReadAPIView.
    """

    def setUp(self):
        self.TEST_PASSWORD = 'Password1234'
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.url = reverse('notifications-read')
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Create some sample notifications for the user with already existing apps and with invalid app name
        Notification.objects.create(user=self.user, app_name='app_name_2', notification_type='Type A')
        for app_name in COURSE_NOTIFICATION_APPS:
            Notification.objects.create(user=self.user, app_name=app_name, notification_type='Type A')
            Notification.objects.create(user=self.user, app_name=app_name, notification_type='Type B')

    @mock.patch("eventtracking.tracker.emit")
    def test_mark_all_notifications_read_with_app_name(self, mock_emit):
        # Create a PATCH request to mark all notifications as read for already existing app e.g 'discussion'
        app_name = next(iter(COURSE_NOTIFICATION_APPS))
        data = {'app_name': app_name}

        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'Notifications marked read.'})
        notifications = Notification.objects.filter(user=self.user, app_name=app_name, last_read__isnull=False)
        self.assertEqual(notifications.count(), 2)
        event_name, event_data = mock_emit.call_args[0]
        self.assertEqual(event_name, 'edx.notifications.app_all_read')
        self.assertEqual(event_data['notification_app'], 'discussion')

    def test_mark_all_notifications_read_with_invalid_app_name(self):
        # Create a PATCH request to mark all notifications as read for 'app_name_1'
        app_name = 'app_name_1'
        data = {'app_name': app_name}

        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {'error': 'Invalid app_name or notification_id.'})

    @mock.patch("eventtracking.tracker.emit")
    def test_mark_notification_read_with_notification_id(self, mock_emit):
        # Create a PATCH request to mark notification as read for notification_id: 2
        notification_id = 2
        data = {'notification_id': notification_id}

        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'Notification marked read.'})
        notifications = Notification.objects.filter(user=self.user, id=notification_id, last_read__isnull=False)
        self.assertEqual(notifications.count(), 1)
        event_name, event_data = mock_emit.call_args[0]
        self.assertEqual(event_name, 'edx.notifications.read')
        self.assertEqual(event_data.get('notification_metadata').get('notification_id'), notification_id)
        self.assertEqual(event_data['notification_app'], 'discussion')
        self.assertEqual(event_data['notification_type'], 'Type A')
        self.assertEqual(event_data['first_read'], True)

    def test_mark_notification_read_with_other_user_notification_id(self):
        # Create a PATCH request to mark notification as read for notification_id: 2 through a different user
        self.client.logout()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        notification_id = 2
        data = {'notification_id': notification_id}
        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        notifications = Notification.objects.filter(user=self.user, id=notification_id, last_read__isnull=False)
        self.assertEqual(notifications.count(), 0)

    def test_mark_notification_read_with_invalid_notification_id(self):
        # Create a PATCH request to mark notification as read for notification_id: 23345
        notification_id = 23345
        data = {'notification_id': notification_id}

        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"].code, 'not_found')

    def test_mark_notification_read_with_app_name_and_notification_id(self):
        # Create a PATCH request to mark notification as read for existing app e.g 'discussion' and notification_id: 2
        # notification_id has higher priority than app_name in this case app_name is ignored
        app_name = next(iter(COURSE_NOTIFICATION_APPS))
        notification_id = 2
        data = {'app_name': app_name, 'notification_id': notification_id}

        response = self.client.patch(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'Notification marked read.'})
        notifications = Notification.objects.filter(
            user=self.user,
            id=notification_id,
            last_read__isnull=False
        )
        self.assertEqual(notifications.count(), 1)

    def test_mark_notification_read_without_app_name_and_notification_id(self):
        # Create a PATCH request to mark notification as read without app_name and notification_id
        response = self.client.patch(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {'error': 'Invalid app_name or notification_id.'})


@ddt.ddt
class UpdatePreferenceFromEncryptedDataView(ModuleStoreTestCase):
    """
    Tests if preference is updated when encrypted url is hit
    """

    def setUp(self):
        """
        Setup test case
        """
        cache.clear()
        super().setUp()
        password = 'password'
        self.user = UserFactory(password=password)
        self.client.login(username=self.user.username, password=password)
        self.course = CourseFactory.create(display_name='test course 1', run="Testing_course_1")

    @override_settings(LMS_BASE="example.com", ONE_CLICK_UNSUBSCRIBE_RATE_LIMIT='1/d')
    def test_rate_limit_on_unsub(self):
        """
        Test rate limit on unsub
        """
        self.client.logout()
        user_hash = encrypt_string(self.user.username)
        url_params = {
            "username": user_hash,
        }
        url = reverse("preference_update_view", kwargs=url_params)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        response = self.client.get(url)
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    @override_settings(LMS_BASE="")
    @ddt.data('get', 'post')
    def test_if_preference_is_updated(self, request_type):
        """
        Tests if preference is updated when url is hit
        """
        prefs = NotificationPreference.create_default_preferences_for_user(self.user.id)
        assert any(pref.email for pref in prefs)
        user_hash = encrypt_string(self.user.username)
        url_params = {
            "username": user_hash,
        }
        url = reverse("preference_update_view", kwargs=url_params)
        func = getattr(self.client, request_type)
        response = func(url)
        assert response.status_code == status.HTTP_200_OK
        preferences = NotificationPreference.objects.filter(user=self.user)
        for preference in preferences:
            assert preference.email is False

    def test_creation_of_missing_preference(self):
        """
        Tests if missing preferences are created when unsubscribe is clicked
        """
        NotificationPreference.objects.filter(user=self.user).delete()
        user_hash = encrypt_string(self.user.username)
        url_params = {
            "username": user_hash,
        }
        url = reverse("preference_update_view", kwargs=url_params)
        self.client.get(url)
        preferences = NotificationPreference.objects.filter(user=self.user)
        assert preferences.count() == len(COURSE_NOTIFICATION_TYPES.keys())


def remove_notifications_with_visibility_settings(expected_response):
    """
    Remove notifications with visibility settings from the expected response.
    """
    not_visible = get_notification_types_with_visibility_settings()
    for expected_response_app in expected_response['notification_preference_config']:
        for notification_type, visibility_settings in not_visible.items():
            types = expected_response['notification_preference_config'][expected_response_app]['notification_types']
            if notification_type in types:
                expected_response['notification_preference_config'][expected_response_app]['notification_types'].pop(
                    notification_type
                )
    return expected_response


@ddt.ddt
class TestNotificationPreferencesView(ModuleStoreTestCase):
    """
    Tests for the NotificationPreferencesView API view.
    """

    def setUp(self):
        # Set up a user and API client
        super().setUp()
        self.default_data = {
            "status": "success",
            "message": "Notification preferences retrieved successfully.",
            "data": {
                "discussion": {
                    "enabled": True,
                    "core_notification_types": [
                        "new_comment_on_response",
                        "new_comment",
                        "new_response",
                        "response_on_followed_post",
                        "comment_on_followed_post",
                        "response_endorsed_on_thread",
                        "response_endorsed"
                    ],
                    "notification_types": {
                        "new_discussion_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "new_question_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "content_reported": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "new_instructor_all_learners_post": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "core": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily"
                        }
                    },
                    "non_editable": {
                        "new_discussion_post": ["push"],
                        "new_question_post": ["push"],
                        "content_reported": ["push"],
                        "new_instructor_all_learners_post": ["push"]
                    }
                },
                "updates": {
                    "enabled": True,
                    "core_notification_types": [],
                    "notification_types": {
                        "course_updates": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "core": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily"
                        }
                    },
                    "non_editable": {
                        "course_updates": ["push"],
                    }
                },
                "grading": {
                    "enabled": True,
                    "core_notification_types": [],
                    "notification_types": {
                        "ora_staff_notifications": {
                            "web": True,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "ora_grade_assigned": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily"
                        },
                        "core": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily"
                        }
                    },
                    "non_editable": {
                        "ora_grade_assigned": ["push"],
                        "ora_staff_notifications": ["push"]
                    }
                },
            }
        }
        self.TEST_PASSWORD = 'testpass'
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notification-preferences-aggregated-v2')  # Adjust with the actual name
        self.course = CourseFactory.create(display_name='test course 1', run="Testing_course_1")

    @ddt.data(
        ("forum", FORUM_ROLE_ADMINISTRATOR, ['content_reported'], ['ora_staff_notifications']),
        ("forum", FORUM_ROLE_MODERATOR, ['content_reported'], ['ora_staff_notifications']),
        ("forum", FORUM_ROLE_COMMUNITY_TA, ['content_reported'], ['ora_staff_notifications']),
        ("course", CourseStaffRole.ROLE, ['ora_staff_notifications'], ['content_reported']),
        ("course", CourseInstructorRole.ROLE, ['ora_staff_notifications'], ['content_reported']),
        (None, None, [], ['ora_staff_notifications', 'content_reported']),
    )
    @ddt.unpack
    def test_get_notification_preferences(self, role_type, role, visible_apps, hidden_apps):
        """
        Test: Notification preferences visibility for users with forum, course, or no role.
        """
        role_instance = None

        if role_type == "course":
            if role == CourseInstructorRole.ROLE:
                CourseStaffRole(self.course.id).add_users(self.user)
            else:
                CourseInstructorRole(self.course.id).add_users(self.user)
            self.client.login(username=self.user.username, password='testpass')

        elif role_type == "forum":
            role_instance = RoleFactory(name=role, course_id=self.course.id)
            role_instance.users.add(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertIn('data', response.data)

        expected_data = exclude_inaccessible_preferences(self.default_data['data'], self.user)
        expected_data = add_non_editable_in_preference(expected_data)
        expected_data = add_info_to_notification_config(expected_data)

        self.assertEqual(response.data['data'], expected_data)

        notification_apps = {}
        for app in ['discussion', 'grading']:
            notification_apps.update(response.data['data'][app]['notification_types'])

        for app in visible_apps:
            self.assertIn(app, notification_apps, msg=f"{app} should be visible for role: {role_type}")

        for app in hidden_apps:
            self.assertNotIn(app, notification_apps, msg=f"{app} should NOT be visible for role: {role_type}")

        if role_type == "forum":
            role_instance.users.clear()
        elif role_type == "course":
            if role == CourseInstructorRole.ROLE:
                CourseStaffRole(self.course.id).remove_users(self.user)
            else:
                CourseInstructorRole(self.course.id).remove_users(self.user)

    def test_if_data_is_correctly_aggregated(self):
        """
        Test case: Check if the data is correctly formatted
        """

        self.client.get(self.url)
        NotificationPreference.objects.all().update(
            web=False,
            push=False,
            email=False,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertIn('data', response.data)
        data = {
            "status": "success",
            "show_preferences": False,
            "message": "Notification preferences retrieved successfully.",
            "data": {
                "discussion": {
                    "enabled": True,
                    "core_notification_types": [
                        "new_comment_on_response",
                        "new_comment",
                        "new_response",
                        "response_on_followed_post",
                        "comment_on_followed_post",
                        "response_endorsed_on_thread",
                        "response_endorsed"
                    ],
                    "notification_types": {
                        "new_discussion_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_question_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_instructor_all_learners_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "core": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": "Notifications for responses and comments on your posts, and the ones you’re "
                                    "following, including endorsements to your responses and on your posts."
                        }
                    },
                    "non_editable": {
                        "new_discussion_post": ["push"],
                        "new_question_post": ["push"],
                        "new_instructor_all_learners_post": ["push"]
                    }
                },
                "updates": {
                    "enabled": True,
                    "core_notification_types": [],
                    "notification_types": {
                        "course_updates": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "core": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily",
                            "info": "Notifications for new announcements and updates from the course team."
                        }
                    },
                    "non_editable": {
                        "course_updates": ["push"],
                    }
                },
                "grading": {
                    "enabled": True,
                    "core_notification_types": [],
                    "notification_types": {
                        "ora_grade_assigned": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "core": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily",
                            "info": "Notifications for submission grading."
                        }
                    },
                    "non_editable": {
                        "ora_grade_assigned": ["push"]
                    }
                },
            }
        }
        self.assertEqual(response.data, data)

    def test_api_view_permissions(self):
        """
        Test case: Ensure the API view has the correct permissions
        """
        # Check if the view requires authentication
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Re-authenticate and check again
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_preferences_core(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "core",
            "notification_channel": "email_cadence",
            "email_cadence": "Weekly"
        }
        __, core_types = NotificationTypeManager().get_notification_app_preference('discussion')
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        cadence_set = NotificationPreference.objects.filter(user=self.user, type__in=core_types).values_list(
            'email_cadence', flat=True
        )
        self.assertEqual(len(set(cadence_set)), 1)
        self.assertIn('Weekly', set(cadence_set))

    def test_update_preferences(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "new_discussion_post",
            "notification_channel": "web",
            "value": True
        }
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        preference = NotificationPreference.objects.get(
            type='new_discussion_post',
            user__id=self.user.id
        )
        self.assertEqual(preference.web, True)

    def test_update_preferences_non_core_email(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "new_discussion_post",
            "notification_channel": "email_cadence",
            "email_cadence": 'Weekly'
        }
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        preference = NotificationPreference.objects.get(
            type='new_discussion_post',
            user__id=self.user.id
        )
        self.assertEqual(preference.email_cadence, 'Weekly')


@ddt.ddt
class TestNotificationPreferencesViewV3(ModuleStoreTestCase):
    """
    Tests for the NotificationPreferencesView API view.
    """

    def setUp(self):
        # Set up a user and API client
        super().setUp()
        self.default_data = {
            "status": "success",
            "message": "Notification preferences retrieved successfully.",
            "data": {
                "discussion": {
                    "enabled": True,
                    "notification_types": {
                        "new_discussion_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_question_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "content_reported": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_instructor_all_learners_post": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "grouped_notification": {
                            "web": True,
                            "email": True,
                            "push": True,
                            "email_cadence": "Daily",
                            "info": 'Notifications for responses and comments on your posts, and the ones you’re '
                                    'following, including endorsements to your responses and on your posts.',
                        }
                    },
                    "non_editable": {
                        "new_discussion_post": ["push"],
                        "new_question_post": ["push"],
                        "content_reported": ["push"],
                        "new_instructor_all_learners_post": ["push"]
                    }
                },
                "updates": {
                    "enabled": True,
                    "notification_types": {
                        "course_updates": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },

                    },
                    "non_editable": {
                        "course_updates": ["push"],
                    }
                },
                "grading": {
                    "enabled": True,
                    "notification_types": {
                        "ora_staff_notifications": {
                            "web": True,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            'info': 'Notifications for when a submission is made for ORA that includes staff '
                                    'grading step.'
                        },
                        "ora_grade_assigned": {
                            "web": True,
                            "email": True,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        }

                    },
                    "non_editable": {
                        "ora_grade_assigned": ["push"],
                        "ora_staff_notifications": ["push"]
                    }
                },
            }
        }
        self.TEST_PASSWORD = 'testpass'
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notification-preferences-aggregated-v3')
        self.course = CourseFactory.create(display_name='test course 1', run="Testing_course_1")

    @ddt.data(
        ("forum", FORUM_ROLE_ADMINISTRATOR, ['content_reported'], ['ora_staff_notifications']),
        ("forum", FORUM_ROLE_MODERATOR, ['content_reported'], ['ora_staff_notifications']),
        ("forum", FORUM_ROLE_COMMUNITY_TA, ['content_reported'], ['ora_staff_notifications']),
        ("course", CourseStaffRole.ROLE, ['ora_staff_notifications'], ['content_reported']),
        ("course", CourseInstructorRole.ROLE, ['ora_staff_notifications'], ['content_reported']),
        (None, None, [], ['ora_staff_notifications', 'content_reported']),
    )
    @ddt.unpack
    def test_get_notification_preferences(self, role_type, role, visible_apps, hidden_apps):
        """
        Test: Notification preferences visibility for users with forum, course, or no role.
        """
        role_instance = None

        if role_type == "course":
            if role == CourseInstructorRole.ROLE:
                CourseStaffRole(self.course.id).add_users(self.user)
            else:
                CourseInstructorRole(self.course.id).add_users(self.user)
            self.client.login(username=self.user.username, password='testpass')

        elif role_type == "forum":
            role_instance = RoleFactory(name=role, course_id=self.course.id)
            role_instance.users.add(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertIn('data', response.data)

        expected_data = exclude_inaccessible_preferences(self.default_data['data'], self.user)
        expected_data = add_non_editable_in_preference(expected_data)

        self.assertEqual(response.data['data'], expected_data)

        notification_apps = {}
        for app in ['discussion', 'grading']:
            notification_apps.update(response.data['data'][app]['notification_types'])

        for app in visible_apps:
            self.assertIn(app, notification_apps, msg=f"{app} should be visible for role: {role_type}")

        for app in hidden_apps:
            self.assertNotIn(app, notification_apps, msg=f"{app} should NOT be visible for role: {role_type}")

        if role_type == "forum":
            role_instance.users.clear()
        elif role_type == "course":
            if role == CourseInstructorRole.ROLE:
                CourseStaffRole(self.course.id).remove_users(self.user)
            else:
                CourseInstructorRole(self.course.id).remove_users(self.user)

    def test_if_data_is_correctly_aggregated(self):
        """
        Test case: Check if the data is correctly formatted
        """

        self.client.get(self.url)
        NotificationPreference.objects.all().update(
            web=False,
            push=False,
            email=False,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertIn('data', response.data)
        data = {
            "status": "success",
            "show_preferences": False,
            "message": "Notification preferences retrieved successfully.",
            "data": {
                "discussion": {
                    "enabled": True,

                    "notification_types": {
                        "new_discussion_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_question_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "new_instructor_all_learners_post": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },
                        "grouped_notification": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": "Notifications for responses and comments on your posts, and the ones you’re "
                                    "following, including endorsements to your responses and on your posts."
                        }
                    },
                    "non_editable": {
                        "new_discussion_post": ["push"],
                        "new_question_post": ["push"],
                        "new_instructor_all_learners_post": ["push"]
                    }
                },
                "updates": {
                    "enabled": True,
                    "notification_types": {
                        "course_updates": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },

                    },
                    "non_editable": {
                        "course_updates": ["push"],
                    }
                },
                "grading": {
                    "enabled": True,
                    "notification_types": {
                        "ora_grade_assigned": {
                            "web": False,
                            "email": False,
                            "push": False,
                            "email_cadence": "Daily",
                            "info": ""
                        },

                    },
                    "non_editable": {
                        "ora_grade_assigned": ["push"]
                    }
                },
            }
        }
        self.assertEqual(response.data, data)

    def test_api_view_permissions(self):
        """
        Test case: Ensure the API view has the correct permissions
        """
        # Check if the view requires authentication
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Re-authenticate and check again
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_preferences_grouped(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "grouped_notification",
            "notification_channel": "email_cadence",
            "email_cadence": "Weekly"
        }
        grouped_types = [
            key
            for key, type_config in get_default_values_of_preferences().items()
            if type_config.get('use_app_defaults')
        ]
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        cadence_set = NotificationPreference.objects.filter(user=self.user, type__in=grouped_types).values_list(
            'email_cadence', flat=True
        )
        self.assertEqual(len(set(cadence_set)), 1)
        self.assertIn('Weekly', set(cadence_set))

    def test_update_preferences(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "new_discussion_post",
            "notification_channel": "web",
            "value": True
        }
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        preference = NotificationPreference.objects.get(
            type='new_discussion_post',
            user__id=self.user.id
        )
        self.assertEqual(preference.web, True)

    def test_update_preferences_non_grouped_email(self):
        """
        Test case: Update notification preferences for the authenticated user
        """
        update_data = {
            "notification_app": "discussion",
            "notification_type": "new_discussion_post",
            "notification_channel": "email_cadence",
            "email_cadence": 'Weekly'
        }
        self.client.get(self.url)
        response = self.client.put(self.url, update_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        preference = NotificationPreference.objects.get(
            type='new_discussion_post',
            user__id=self.user.id
        )
        self.assertEqual(preference.email_cadence, 'Weekly')
