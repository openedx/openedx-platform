""" Tests for course home api utils """

from contextlib import contextmanager
from unittest import mock

import pytest
from django.http import Http404
from django.test import RequestFactory
from rest_framework.exceptions import PermissionDenied

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.utils import get_course_or_403, get_student_user
from lms.djangoapps.courseware.access_response import AccessError
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.courseware.masquerade import MASQUERADE_SETTINGS_KEY, CourseMasquerade
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


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
                assert str(e.detail) == mock_access_error.user_message  # noqa: PT017
                assert e.detail.code == mock_access_error.error_code  # noqa: PT017

    def test_other_exception(self):
        """ Any other exception should not be caught """
        class MyException(Exception):
            pass

        with self.mock_get_course(side_effect=MyException()):
            with self.assertRaises(MyException):  # noqa: PT027
                get_course_or_403()


class GetStudentUserTest(ModuleStoreTestCase):
    """ Tests for get_student_user """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.requester = UserFactory()
        self.other_user = UserFactory()

    def make_request(self):
        """ Build a request for the requester with a dict-backed session """
        request = RequestFactory().get('/')
        request.user = self.requester
        request.session = {}
        return request

    def test_self_when_student_id_is_none(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, None, False) == self.requester

    def test_self_when_student_id_is_requester_id(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, self.requester.id, False) == self.requester

    def test_self_when_student_id_is_requester_id_string(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, str(self.requester.id), False) == self.requester

    def test_staff_gets_other_user(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, self.other_user.id, True) == self.other_user

    def test_non_staff_other_user_is_404(self):
        request = self.make_request()
        with pytest.raises(Http404) as exc_info:
            get_student_user(request, self.course.id, self.other_user.id, False)
        assert exc_info.value.args == ()

    def test_unknown_id_is_404(self):
        request = self.make_request()
        with pytest.raises(Http404) as exc_info:
            get_student_user(request, self.course.id, 99999999, True)
        assert exc_info.value.args == ()

    def test_non_integer_string_is_404(self):
        request = self.make_request()
        with pytest.raises(Http404) as exc_info:
            get_student_user(request, self.course.id, 'not-an-id', True)
        assert exc_info.value.args == ()

    def test_ccx_coach_gets_other_user(self):
        request = self.make_request()
        with mock.patch('lms.djangoapps.course_home_api.utils.has_ccx_coach_role', return_value=True):
            assert get_student_user(request, self.course.id, self.other_user.id, False) == self.other_user

    def test_staff_masquerading_as_learner_gets_that_learner(self):
        CourseEnrollment.enroll(self.other_user, self.course.id)
        request = self.make_request()
        request.session[MASQUERADE_SETTINGS_KEY] = {
            self.course.id: CourseMasquerade(self.course.id, user_name=self.other_user.username),
        }
        assert get_student_user(request, self.course.id, None, True) == self.other_user
