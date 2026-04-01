"""
Tests for base_notification
"""
from openedx.core.djangoapps.notifications import base_notification
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class NotificationPreferenceValidationTest(ModuleStoreTestCase):
    """
    Tests to validate if notification preference constants are valid
    """

    def test_validate_notification_apps(self):
        """
        Tests if COURSE_NOTIFICATION_APPS constant has all required keys with valid
        data type for new notification app
        """
        bool_keys = ['enabled', 'web', 'push', 'email']
        notification_apps = base_notification.COURSE_NOTIFICATION_APPS
        assert "" not in notification_apps
        for app_data in notification_apps.values():
            assert 'info' in app_data.keys()
            assert isinstance(app_data['non_editable'], list)
            assert isinstance(app_data['email_cadence'], str)
            for key in bool_keys:
                assert isinstance(app_data[key], bool)

    def test_validate_core_notification_types(self):
        """
        Tests if COURSE_NOTIFICATION_TYPES constant has all required keys with valid
        data type for core notification type
        """
        str_keys = ['notification_app', 'name']
        notification_types = base_notification.COURSE_NOTIFICATION_TYPES
        assert "" not in notification_types
        for notification_type in notification_types.values():
            if not notification_type.get('use_app_defaults', False):
                continue
            assert isinstance(notification_type['use_app_defaults'], bool)
            assert isinstance(notification_type['content_context'], dict)
            assert 'content_template' in notification_type.keys()
            for key in str_keys:
                assert isinstance(notification_type[key], str)

    def test_validate_non_core_notification_types(self):
        """
        Tests if COURSE_NOTIFICATION_TYPES constant has all required keys with valid
        data type for non-core notification type
        """
        str_keys = ['notification_app', 'name', 'info']
        bool_keys = ['web', 'email', 'push']
        notification_types = base_notification.COURSE_NOTIFICATION_TYPES
        assert "" not in notification_types
        for notification_type in notification_types.values():
            if notification_type.get('use_app_defaults', False):
                continue
            assert 'content_template' in notification_type.keys()
            assert isinstance(notification_type['content_context'], dict)
            assert isinstance(notification_type['non_editable'], list)
            assert isinstance(notification_type['email_cadence'], str)
            for key in str_keys:
                assert isinstance(notification_type[key], str)
            for key in bool_keys:
                assert isinstance(notification_type[key], bool)
