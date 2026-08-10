"""
Tests for base_notification
"""
import pytest

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


@pytest.mark.parametrize(
    ('user_input', 'escaped'),
    [
        ('<style>body{background:red}</style>evil', '&lt;style&gt;body{background:red}&lt;/style&gt;evil'),
        ('<script>alert(1)</script>', '&lt;script&gt;alert(1)&lt;/script&gt;'),
        ('AT&T "quoted"', 'AT&amp;T &quot;quoted&quot;'),
    ],
)
def test_get_notification_content_escapes_user_input(user_input, escaped):
    """
    Regression test for GHSA-rv5w-f4r5-h77g: user-controlled context values
    must be HTML-escaped before being interpolated into a content_template
    via `str.format`. Structural context keys (`p`, `strong`) are exempt so
    the template can still emit real <p>/<strong> tags.
    """
    context = {'replier_name': 'alice', 'post_title': user_input}
    content = base_notification.get_notification_content('new_response', context)
    assert '<style>' not in content
    assert '<script>' not in content
    assert escaped in content


def test_get_notification_content_preserves_structural_tags():
    """
    Companion to test_get_notification_content_escapes_user_input: verify
    that the structural `p` and `strong` keys still produce real HTML tags
    after the escape pass, and that innocuous user input renders as plain
    text alongside them.
    """
    context = {'replier_name': 'alice', 'post_title': 'Hello world'}
    content = base_notification.get_notification_content('new_response', context)
    assert '<p>' in content
    assert '</p>' in content
    assert '<strong>alice</strong>' in content
    assert '<strong>Hello world</strong>' in content
