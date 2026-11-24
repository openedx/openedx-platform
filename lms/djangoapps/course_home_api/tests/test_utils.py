""" Tests for course home api utils """

from contextlib import contextmanager
from unittest import mock

import ddt
import pytest
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.http import Http404
from django.test import RequestFactory
from django.utils.module_loading import import_string
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import force_authenticate

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.utils import get_course_or_403, get_student_user, resolve_course_learner
from lms.djangoapps.courseware.access_response import AccessError
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.courseware.masquerade import MASQUERADE_DATA_KEY, MASQUERADE_SETTINGS_KEY, CourseMasquerade
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


def make_drf_request(user):
    """
    Build the DRF ``Request`` openedx-core's views pass to the resolver, authenticated as ``user``.

    The session is a ``SessionBase``, not a dict: ``setup_masquerade`` sets ``session.modified`` when it
    drops a masquerade whose learner has since unenrolled.
    """
    http_request = RequestFactory().get('/')
    http_request.session = SessionBase()
    force_authenticate(http_request, user=user)
    return Request(http_request)


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
        """ Build a request for the requester """
        return make_drf_request(self.requester)

    def test_self_when_student_id_is_requester_id(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, self.requester.id, False) == self.requester

    def test_self_when_student_id_is_requester_id_string(self):
        request = self.make_request()
        assert get_student_user(request, self.course.id, str(self.requester.id), False) == self.requester

    def test_non_integer_string_is_404(self):
        request = self.make_request()
        with pytest.raises(Http404) as exc_info:
            get_student_user(request, self.course.id, 'not-an-id', True)
        assert exc_info.value.args == ()

    def test_ccx_coach_gets_other_user(self):
        request = self.make_request()
        with mock.patch('lms.djangoapps.course_home_api.utils.has_ccx_coach_role', return_value=True):
            assert get_student_user(request, self.course.id, self.other_user.id, False) == self.other_user


@ddt.ddt
class ResolveCourseLearnerTest(ModuleStoreTestCase):
    """ Tests for resolve_course_learner """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.other_course = CourseFactory.create()
        self.requester = UserFactory()
        self.learner = UserFactory()

    def make_request(self, user=None):
        """ Build a DRF request for the given user (default: the requester) """
        return make_drf_request(user or self.requester)

    def make_staff_requester(self):
        """ Make the requester staff of the course, without enrolling them """
        CourseStaffRole(self.course.id).add_users(self.requester)

    def masquerade_as(self, request, **kwargs):
        """ Start a masquerade for the course on the request's session """
        request.session[MASQUERADE_SETTINGS_KEY] = {
            self.course.id: CourseMasquerade(self.course.id, **kwargs),
        }

    def assert_bare_404(self, request, learner_id):
        """ Assert that resolving ``learner_id`` raises ``Http404`` with no message """
        with pytest.raises(Http404) as exc_info:
            resolve_course_learner(request, self.course.id, learner_id)
        assert exc_info.value.args == ()

    def test_enrolled_learner_resolves_to_self(self):
        CourseEnrollment.enroll(self.requester, self.course.id)
        assert resolve_course_learner(self.make_request(), self.course.id, None) == self.requester

    def test_enrolled_learner_naming_self_resolves_to_self(self):
        CourseEnrollment.enroll(self.requester, self.course.id)
        assert resolve_course_learner(self.make_request(), self.course.id, self.requester.id) == self.requester

    def test_staff_naming_enrolled_learner(self):
        self.make_staff_requester()
        CourseEnrollment.enroll(self.learner, self.course.id)
        assert resolve_course_learner(self.make_request(), self.course.id, self.learner.id) == self.learner

    def test_staff_naming_unenrolled_learner(self):
        self.make_staff_requester()
        CourseEnrollment.enroll(self.learner, self.course.id)
        CourseEnrollment.unenroll(self.learner, self.course.id)
        assert resolve_course_learner(self.make_request(), self.course.id, self.learner.id) == self.learner

    def test_staff_masquerading_as_enrolled_learner(self):
        self.make_staff_requester()
        CourseEnrollment.enroll(self.learner, self.course.id)
        request = self.make_request()
        self.masquerade_as(request, user_name=self.learner.username)
        assert resolve_course_learner(request, self.course.id, None) == self.learner

    def test_staff_masquerading_as_learner_naming_own_id_gets_that_learner(self):
        self.make_staff_requester()
        CourseEnrollment.enroll(self.learner, self.course.id)
        request = self.make_request()
        self.masquerade_as(request, user_name=self.learner.username)
        assert resolve_course_learner(request, self.course.id, self.requester.id) == self.learner

    def test_non_staff_with_stale_masquerade_resolves_to_requester(self):
        # A requester who lost their staff role can still hold a masquerade in the session. Only the
        # requester's staff access, computed before masquerading, keeps that session from being honored.
        CourseEnrollment.enroll(self.requester, self.course.id)
        CourseEnrollment.enroll(self.learner, self.course.id)
        request = self.make_request()
        self.masquerade_as(request, user_name=self.learner.username)
        assert resolve_course_learner(request, self.course.id, None) == self.requester

    def test_staff_masquerading_as_learner_role_resolves_to_requester(self):
        # The requester is not enrolled: the enrollment refusal must not apply to staff.
        self.make_staff_requester()
        assert not CourseEnrollment.is_enrolled(self.requester, self.course.id)
        request = self.make_request()
        self.masquerade_as(request, role='student')
        assert resolve_course_learner(request, self.course.id, None) == self.requester

    def test_staff_masquerading_as_content_group_resolves_to_requester(self):
        self.make_staff_requester()
        request = self.make_request()
        self.masquerade_as(request, role='student', user_partition_id=50, group_id=1)
        assert resolve_course_learner(request, self.course.id, None) == self.requester

    def test_staff_masquerading_as_since_unenrolled_learner_resolves_to_requester(self):
        self.make_staff_requester()
        CourseEnrollment.enroll(self.learner, self.course.id)
        request = self.make_request()
        self.masquerade_as(request, user_name=self.learner.username)
        CourseEnrollment.unenroll(self.learner, self.course.id)
        assert resolve_course_learner(request, self.course.id, None) == self.requester

    def test_staff_request_clears_masquerade_field_data(self):
        self.make_staff_requester()
        request = self.make_request()
        request.session[MASQUERADE_DATA_KEY] = {'field': 'written while masquerading'}
        resolve_course_learner(request, self.course.id, None)
        assert MASQUERADE_DATA_KEY not in request.session

    def test_forbidden_and_unknown_ids_get_identical_responses(self):
        CourseEnrollment.enroll(self.requester, self.course.id)
        CourseEnrollment.enroll(self.learner, self.course.id)
        staff = UserFactory()
        CourseStaffRole(self.course.id).add_users(staff)
        with pytest.raises(Http404) as forbidden:
            resolve_course_learner(self.make_request(), self.course.id, self.learner.id)
        with pytest.raises(Http404) as unknown_for_non_staff:
            resolve_course_learner(self.make_request(), self.course.id, 99999999)
        # Staff are not refused by the visibility check, so this reaches the user lookup.
        with pytest.raises(Http404) as unknown_for_staff:
            resolve_course_learner(self.make_request(staff), self.course.id, 99999999)
        assert type(forbidden.value) is type(unknown_for_non_staff.value) is type(unknown_for_staff.value)
        assert forbidden.value.args == unknown_for_non_staff.value.args == unknown_for_staff.value.args == ()

    def test_staff_of_another_course_naming_learner_is_bare_404(self):
        CourseStaffRole(self.other_course.id).add_users(self.requester)
        CourseEnrollment.enroll(self.learner, self.course.id)
        self.assert_bare_404(self.make_request(), self.learner.id)

    @ddt.data(
        # (was enrolled before, names own id)
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    )
    @ddt.unpack
    def test_requester_without_active_enrollment_is_denied(self, was_enrolled, names_own_id):
        if was_enrolled:
            CourseEnrollment.enroll(self.requester, self.course.id)
            CourseEnrollment.unenroll(self.requester, self.course.id)
        learner_id = self.requester.id if names_own_id else None
        with pytest.raises(PermissionDenied):
            resolve_course_learner(self.make_request(), self.course.id, learner_id)

    @ddt.data(True, False)
    def test_learner_id_zero_is_bare_404(self, is_staff):
        CourseEnrollment.enroll(self.requester, self.course.id)
        if is_staff:
            self.make_staff_requester()
        self.assert_bare_404(self.make_request(), 0)

    def test_unenrolled_non_staff_naming_enrolled_learner_is_404_not_403(self):
        CourseEnrollment.enroll(self.learner, self.course.id)
        self.assert_bare_404(self.make_request(), self.learner.id)

    def test_ccx_coach_naming_unenrolled_learner_is_denied(self):
        # The requester is enrolled, so only a check of the resolved learner's enrollment raises.
        CourseEnrollment.enroll(self.requester, self.course.id)
        with mock.patch('lms.djangoapps.course_home_api.utils.has_ccx_coach_role', return_value=True):
            with pytest.raises(PermissionDenied):
                resolve_course_learner(self.make_request(), self.course.id, self.learner.id)

    def test_setting_points_at_the_resolver(self):
        assert import_string(settings.OPENEDX_LEARNING_LEARNER_RESOLVER) is resolve_course_learner
