"""
Tests delete_notifications management command
"""
from unittest import mock

import ddt
from django.core.management import call_command

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


@ddt.ddt
class TestDeleteNotifications(ModuleStoreTestCase):
    """
    Tests delete notifications management command
    """

    @ddt.data('app_name', 'notification_type', 'created')
    def test_management_command_fails_if_required_param_is_missing(self, param):
        """
        Tests if all required params are available when running management command
        """
        default_dict = {
            'app_name': 'discussion',
            'notification_type': 'new_comment',
            'created': '2024-02-01'
        }
        default_dict.pop(param)
        try:
            call_command('delete_notifications', **default_dict)
            assert False
        except Exception:    # pylint: disable=broad-except
            pass

    @ddt.data('course_id', None)
    @mock.patch(
        'openedx.core.djangoapps.notifications.tasks.delete_notifications.delay'
    )
    def test_management_command_runs_for_valid_params(self, key_to_remove, mock_func):
        """
        Tests management command runs with valid params optional
        """
        default_dict = {
            'app_name': 'discussion',
            'notification_type': 'new_comment',
            'created': '2024-02-01',
            'course_id': 'test-course'
        }
        if key_to_remove is not None:
            default_dict.pop(key_to_remove)
        call_command('delete_notifications', **default_dict)
        assert mock_func.called
        args = mock_func.call_args[0][0]
        for key, value in default_dict.items():
            assert args[key] == value
