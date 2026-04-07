"""
Test utils.py
"""
import datetime
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.http.response import Http404
from django.test.utils import override_settings
from pytz import utc

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.notifications.email import ONE_CLICK_EMAIL_UNSUB_KEY
from openedx.core.djangoapps.notifications.email.utils import (
    add_additional_attributes_to_notifications,
    create_app_notifications_dict,
    create_datetime_string,
    create_email_digest_context,
    create_email_template_context,
    decrypt_string,
    encrypt_string,
    get_course_info,
    get_time_ago,
    get_unsubscribe_link,
    update_user_preferences_from_patch,
)
from openedx.core.djangoapps.notifications.models import Notification
from openedx.core.djangoapps.user_api.models import UserPreference
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from .utils import assert_list_equal, create_notification


class TestUtilFunctions(ModuleStoreTestCase):
    """
    Test utils functions
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

    def test_additional_attributes(self):
        """
        Tests additional attributes are added when notifications list is passed to
        add_additional_attributes_to_notifications function
        """
        notification = create_notification(self.user, self.course.id)
        additional_params = ['course_name', 'icon', 'time_ago']
        for param in additional_params:
            assert not hasattr(notification, param)
        add_additional_attributes_to_notifications([notification])
        for param in additional_params:
            assert hasattr(notification, param)

    def test_create_app_notifications_dict(self):
        """
        Tests notifications are divided based on their app_name
        """
        Notification.objects.all().delete()
        create_notification(self.user, self.course.id, app_name='discussion', notification_type='new_comment')
        create_notification(self.user, self.course.id, app_name='updates', notification_type='course_updates')
        app_dict = create_app_notifications_dict(Notification.objects.all())
        assert len(app_dict.keys()) == 2
        for key in ['discussion', 'updates']:
            assert key in app_dict.keys()
            assert app_dict[key]['count'] == 1
            assert len(app_dict[key]['notifications']) == 1

    def test_get_course_info(self):
        """
        Tests get_course_info function
        """
        assert get_course_info(self.course.id) == {'name': 'test course'}

    def test_get_time_ago(self):
        """
        Tests time_ago string
        """
        current_datetime = utc.localize(datetime.datetime.now())
        assert "Today" == get_time_ago(current_datetime)
        assert "1d" == get_time_ago(current_datetime - datetime.timedelta(days=1))
        assert "1w" == get_time_ago(current_datetime - datetime.timedelta(days=7))

    def test_datetime_string(self):
        """Test datetime is formatted as 'Weekday, Mon DD'."""
        dt = datetime.datetime(2024, 3, 25)
        assert create_datetime_string(dt) == "Monday, Mar 25"

    def test_get_unsubscribe_link_uses_site_config(self):
        """Test unsubscribe link uses site-configured MFE URL and encrypted username."""
        with patch('openedx.core.djangoapps.notifications.email.utils.configuration_helpers.get_value',
                   return_value='https://learning.siteconf') as mock_get_value, \
            patch('openedx.core.djangoapps.notifications.email.utils.encrypt_string',
                  return_value='ENC') as mock_encrypt:
            url = get_unsubscribe_link(self.user.username)

        assert url == 'https://learning.siteconf/preferences-unsubscribe/ENC/'
        mock_get_value.assert_called_once_with('LEARNING_MICROFRONTEND_URL', settings.LEARNING_MICROFRONTEND_URL)
        mock_encrypt.assert_called_once_with(self.user.username)

    def test_get_unsubscribe_link_falls_back_to_settings(self):
        """Test unsubscribe link falls back to settings when site config is absent."""
        default_url = 'https://learning.default'

        with override_settings(LEARNING_MICROFRONTEND_URL=default_url):
            with patch('openedx.core.djangoapps.notifications.email.utils.configuration_helpers.get_value',
                       side_effect=lambda k, d: d) as mock_get_value, \
                patch('openedx.core.djangoapps.notifications.email.utils.encrypt_string',
                      return_value='ENC') as mock_encrypt:
                url = get_unsubscribe_link(self.user.username)

        assert url == f'{default_url}/preferences-unsubscribe/ENC/'
        mock_get_value.assert_called_once_with('LEARNING_MICROFRONTEND_URL', default_url)
        mock_encrypt.assert_called_once_with(self.user.username)


@ddt.ddt
class TestContextFunctions(ModuleStoreTestCase):
    """
    Test template context functions in utils.py
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

    def test_email_template_context(self):
        """
        Tests common header and footer context
        """
        context = create_email_template_context(self.user.username)
        keys = ['platform_name', 'mailing_address', 'logo_url', 'social_media',
                'notification_settings_url', 'unsubscribe_url']
        for key in keys:
            assert key in context

    @ddt.data('Daily', 'Weekly')
    def test_email_digest_context(self, digest_frequency):
        """
        Tests context for email digest
        """
        Notification.objects.all().delete()
        discussion_notification = create_notification(self.user, self.course.id, app_name='discussion',
                                                      notification_type='new_comment')
        update_notification = create_notification(self.user, self.course.id, app_name='updates',
                                                  notification_type='course_updates')
        app_dict = create_app_notifications_dict(Notification.objects.all())
        end_date = datetime.datetime(2024, 3, 24, 12, 0)
        params = {
            "app_notifications_dict": app_dict,
            "username": self.user.username,
            "start_date": end_date - datetime.timedelta(days=0 if digest_frequency == "Daily" else 6),
            "end_date": end_date,
            "digest_frequency": digest_frequency,
            "courses_data": None
        }
        context = create_email_digest_context(**params)
        expected_start_date = 'Sunday, Mar 24' if digest_frequency == 'Daily' else 'Monday, Mar 18'
        expected_digest_updates = [
            {'title': 'Total Notifications', 'translated_title': 'Total Notifications', 'count': 2},
            {'title': 'Discussion', 'translated_title': 'Discussion', 'count': 1},
            {'title': 'Updates', 'translated_title': 'Updates', 'count': 1},
        ]
        expected_email_content = [
            {
                'title': 'Discussion', 'help_text': '', 'help_text_url': '',
                'translated_title': 'Discussion',
                'notifications': [discussion_notification],
                'total': 1, 'show_remaining_count': False, 'remaining_count': 0,
                'url': 'http://learner-home-mfe/?showNotifications=true&app=discussion'
            },
            {
                'title': 'Updates', 'help_text': '', 'help_text_url': '',
                'translated_title': 'Updates',
                'notifications': [update_notification],
                'total': 1, 'show_remaining_count': False, 'remaining_count': 0,
                'url': 'http://learner-home-mfe/?showNotifications=true&app=updates'
            }
        ]
        assert context['start_date'] == expected_start_date
        assert context['end_date'] == 'Sunday, Mar 24'
        assert context['digest_frequency'] == digest_frequency
        assert_list_equal(context['email_digest_updates'], expected_digest_updates)
        assert_list_equal(context['email_content'], expected_email_content)

    def test_email_template_context_notification_settings_url_uses_site_config(self):
        """
        When site configuration defines ACCOUNT_MICROFRONTEND_URL (with a trailing slash),
        the context should build notification_settings_url from it and strip the slash.
        """
        siteconf_url = "https://accounts.siteconf.example/"

        with patch(
            "openedx.core.djangoapps.notifications.email.utils.configuration_helpers.get_value",
            side_effect=lambda key, default=None, *a, **k:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default,
        ):
            ctx = create_email_template_context(self.user.username)

        assert ctx["notification_settings_url"] == "https://accounts.siteconf.example/#notifications"

    def test_email_template_context_notification_settings_url_falls_back_to_settings(self):
        """
        If site config doesn't override, the context should fall back to
        settings.ACCOUNT_MICROFRONTEND_URL (also stripping any trailing slash).
        """
        fallback = "https://accounts.settings.example/"

        with override_settings(ACCOUNT_MICROFRONTEND_URL=fallback):
            with patch(
                "openedx.core.djangoapps.notifications.email.utils.configuration_helpers.get_value",
                side_effect=lambda key, default=None, *a, **k: default,
            ):
                ctx = create_email_template_context(self.user.username)

        assert ctx["notification_settings_url"] == "https://accounts.settings.example/#notifications"


class TestEncryption(ModuleStoreTestCase):
    """
    Tests all encryption methods
    """

    def test_string_encryption(self):
        """
        Tests if decrypted string is equal original string
        """
        string = "edx"
        encrypted = encrypt_string(string)
        decrypted = decrypt_string(encrypted)
        assert string == decrypted


@ddt.ddt
class TestUpdatePreferenceFromPatch(ModuleStoreTestCase):
    """
    Tests if preferences are update according to patch data
    this needs to be reimplemented as tests were removed in
    """

    def setUp(self):
        """
        Setup test cases
        """
        super().setUp()
        self.user = UserFactory()
        self.course_1 = CourseFactory.create(display_name='test course 1', run="Testing_course_1")
        self.course_2 = CourseFactory.create(display_name='test course 2', run="Testing_course_2")

    def test_preference_not_updated_if_invalid_username(self):
        """
        Tests if no preference is updated when username is not valid
        """
        username = f"{self.user.username}-updated"
        enc_username = encrypt_string(username)
        with pytest.raises(Http404):
            update_user_preferences_from_patch(enc_username)

    def test_user_preference_created_on_email_unsubscribe(self):
        """
        Test that the user's email unsubscribe preference is correctly created after unsubscribing digest email.
        """
        encrypted_username = encrypt_string(self.user.username)
        update_user_preferences_from_patch(encrypted_username)
        self.assertTrue(
            UserPreference.objects.filter(user=self.user, key=ONE_CLICK_EMAIL_UNSUB_KEY).exists()
        )
