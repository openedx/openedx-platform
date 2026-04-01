"""
Unit tests for settings_override module using real base_notification configurations.
"""
from django.test import TestCase, override_settings

from openedx.core.djangoapps.notifications.base_notification import (
    _COURSE_NOTIFICATION_APPS,
    _COURSE_NOTIFICATION_TYPES
)
from openedx.core.djangoapps.notifications.settings_override import (
    get_notification_apps_config,
    get_notification_types_config
)


class SettingsOverrideIntegrationTest(TestCase):
    """
    Integration tests for settings_override using the REAL base_notification configurations.
    """

    @override_settings(NOTIFICATION_TYPES_OVERRIDE={
        'new_comment_on_response': {
            'email': True,
            'email_cadence': 'immediately',
            'use_app_defaults': False
        }
    })
    def test_override_notification_types_real_config(self):
        """
        Test overriding 'new_comment_on_response' which exists in the real config.
        We verify that allowed keys change and forbidden keys (use_app_defaults) do not.
        """
        config = get_notification_types_config()

        target_notification = config['new_comment_on_response']

        self.assertTrue(
            target_notification['email'],
            "The 'email' setting should be overridden to True."
        )
        self.assertTrue(
            target_notification['use_app_defaults'],
            "The 'use_app_defaults' field should not be overridable via settings."
        )

        # IMMUTABILITY CHECK: Ensure the global module variable wasn't touched
        self.assertFalse(
            _COURSE_NOTIFICATION_TYPES['new_discussion_post']['email'],
            "The original global _COURSE_NOTIFICATION_TYPES must remain immutable."
        )

    @override_settings(NOTIFICATION_TYPES_OVERRIDE={
        'non_existent_notification': {'email': True}
    })
    def test_override_types_ignores_unknown_keys(self):
        """
        Test that defining a key in settings that doesn't exist in base_notification
        is safely ignored.
        """
        config = get_notification_types_config()
        self.assertNotIn('non_existent_notification', config)

    @override_settings(NOTIFICATION_APPS_OVERRIDE={
        'discussion': {
            'email': False,
            'enabled': False
        }
    })
    def test_override_notification_apps_real_config(self):
        """
        Test overriding the 'discussion' app which exists in the real config.
        """
        config = get_notification_apps_config()

        target_app = config['discussion']

        self.assertFalse(
            target_app['email'],
            "The 'email' setting should be overridden to False."
        )

        self.assertTrue(
            target_app['enabled'],
            "The 'enabled' field should not be overridable via settings."
        )

        self.assertTrue(
            _COURSE_NOTIFICATION_APPS['discussion']['email'],
            "The original global _COURSE_NOTIFICATION_APPS must remain immutable."
        )

    @override_settings(NOTIFICATION_TYPES_OVERRIDE={
        'course_updates': {'web': False}
    })
    def test_partial_update_preserves_other_fields(self):
        """
        Test that overriding one field (web) does not wipe out other fields (email).
        """
        config = get_notification_types_config()
        target = config['course_updates']

        self.assertFalse(target['web'])

        self.assertTrue(
            target['email'],
            "The 'email' field should be preserved from the default config."
        )

    @override_settings(NOTIFICATION_TYPES_OVERRIDE={
        'new_discussion_post': {
            'email_cadence': 'Weekly'
        }
    })
    def test_override_notification_types_email_cadence(self):
        """
        Test overriding email_cadence for an existing notification type.
        Ensures the override is applied and the module-level default isn't mutated.
        """
        config = get_notification_types_config()
        target = config['new_discussion_post']

        self.assertEqual(
            target.get('email_cadence'),
            'Weekly',
            "The 'email_cadence' setting should be overridden to 'Weekly'."
        )

    @override_settings(NOTIFICATION_APPS_OVERRIDE={
        'discussion': {
            'email_cadence': 'Immediately'
        }
    })
    def test_override_notification_apps_email_cadence(self):
        """
        Test overriding email_cadence for an existing notification app.
        Ensures the override is applied and the module-level default isn't mutated.
        """
        config = get_notification_apps_config()
        target_app = config['discussion']

        self.assertEqual(
            target_app.get('email_cadence'),
            'Immediately',
            "The 'email_cadence' setting should be overridden to 'Immediately'."
        )
