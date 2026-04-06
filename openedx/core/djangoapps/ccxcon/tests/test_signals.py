"""
Test for contentstore signals receiver
"""


from unittest import mock

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import SignalHandler, modulestore


class CCXConSignalTestCase(TestCase):
    """
    The only tests currently implemented are for verifying that
    the call for the ccxcon update are performed correctly by the
    course_published signal handler
    """

    @mock.patch('openedx.core.djangoapps.ccxcon.tasks.update_ccxcon.delay')
    def test_course_published_ccxcon_call(self, mock_upc):
        """
        Tests the async call to the ccxcon task.
        It bypasses all the other calls.
        """
        mock_response = mock.MagicMock(return_value=None)
        mock_upc.return_value = mock_response

        course_id = 'course-v1:OrgFoo+CN199+CR-FALL01'
        course_key = CourseKey.from_string(course_id)

        signal_handler = SignalHandler(modulestore())
        signal_handler.send('course_published', course_key=course_key)

        mock_upc.assert_called_once_with(course_id)
