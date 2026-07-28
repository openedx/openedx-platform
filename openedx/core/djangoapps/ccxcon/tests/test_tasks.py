"""
Tests for the CCXCon celery tasks
"""


from unittest import mock

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.ccxcon import api, tasks


class CCXConTaskTestCase(TestCase):
    """
    Tests for CCXCon tasks.
    """

    @mock.patch('openedx.core.djangoapps.ccxcon.api.course_info_to_ccxcon')
    def test_update_ccxcon_task_ok(self, mock_citc):
        """
        Test task with no problems
        """
        mock_response = mock.Mock()
        mock_citc.return_value = mock_response

        course_id = 'course-v1:OrgFoo+CN199+CR-FALL01'
        tasks.update_ccxcon.delay(course_id)

        mock_citc.assert_called_once_with(CourseKey.from_string(course_id))

    @mock.patch('openedx.core.djangoapps.ccxcon.api.course_info_to_ccxcon')
    def test_update_ccxcon_task_retry(self, mock_citc):
        """
        Test task with exception that triggers a retry
        """
        mock_citc.side_effect = api.CCXConnServerError()
        course_id = 'course-v1:OrgFoo+CN199+CR-FALL01'
        tasks.update_ccxcon.delay(course_id)

        assert mock_citc.call_count == 6
        course_key = CourseKey.from_string(course_id)
        for call in mock_citc.call_args_list:
            c_args, c_kwargs = call
            assert c_kwargs == {}
            assert len(c_args) == 1
            assert c_args[0] == course_key
