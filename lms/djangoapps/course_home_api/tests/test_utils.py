""" Tests for course home api utils """

from contextlib import contextmanager
from unittest import mock

from rest_framework.exceptions import PermissionDenied

from lms.djangoapps.course_home_api.utils import get_course_or_403
from lms.djangoapps.courseware.access_response import AccessError
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class GetCourseOr403Test(ModuleStoreTestCase):
    """ Tests for get_course_or_403 """

    @contextmanager
    def mock_get_course(self, *args, **kwargs):
        """ Mock base_get_course_with_access helper """
        with mock.patch(
            'lms.djangoapps.course_home_api.utils.base_get_course_with_access',
            *args,
            **kwargs
        ) as mock_get:
            yield mock_get

    def test_no_exception(self):
        """ If no exception is raised we should return the return of get_course_with_access """
        expected_return = mock.Mock()
        with self.mock_get_course(return_value=expected_return):
            assert get_course_or_403() == expected_return

    def test_redirect(self):
        """ Test for behavior when get_course_with_access raises a redirect error """
        expected_url = "www.testError.access/redirect.php?work=yes"
        mock_access_error = AccessError('code', 'dev_msg', 'usr_msg')
        mock_course_access_redirect = CourseAccessRedirect(expected_url, mock_access_error)

        with self.mock_get_course(side_effect=mock_course_access_redirect):
            try:
                get_course_or_403()
                self.fail('Call to get_course_or_403 should raise exception')
            except PermissionDenied as e:
                assert str(e.detail) == mock_access_error.user_message
                assert e.detail.code == mock_access_error.error_code

    def test_other_exception(self):
        """ Any other exception should not be caught """
        class MyException(Exception):
            pass

        with self.mock_get_course(side_effect=MyException()):
            with self.assertRaises(MyException):
                get_course_or_403()
