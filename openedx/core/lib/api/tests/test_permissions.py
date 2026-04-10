""" Tests for API permissions classes. """
import ddt
import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase
from opaque_keys.edx.keys import CourseKey
from rest_framework.generics import GenericAPIView

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.lib.api.permissions import (
    IsCourseStaffInstructor,
    IsMasterCourseStaffInstructor,
    IsStaffOrOwner,
    IsUserInUrlOrStaff,
)


class TestObject:
    """ Fake class for object permission tests. """
    def __init__(self, user=None, course_id=None):
        self.user = user
        self.course_id = course_id


class TestCcxObject(TestObject):
    """ Fake class for object permission for CCX Courses """
    def __init__(self, user=None, course_id=None):
        super().__init__(user, course_id)
        self.coach = user


class IsCourseStaffInstructorTests(TestCase):
    """ Test for IsCourseStaffInstructor permission class. """

    def setUp(self):
        super().setUp()
        self.permission = IsCourseStaffInstructor()
        self.coach = UserFactory()
        self.user = UserFactory()
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.course_key = CourseKey.from_string('edx/test123/run')
        self.obj = TestCcxObject(user=self.coach, course_id=self.course_key)

    def test_course_staff_has_access(self):
        CourseStaffRole(course_key=self.course_key).add_users(self.user)
        assert self.permission.has_object_permission(self.request, None, self.obj)

    def test_course_instructor_has_access(self):
        CourseInstructorRole(course_key=self.course_key).add_users(self.user)
        assert self.permission.has_object_permission(self.request, None, self.obj)

    def test_course_coach_has_access(self):
        self.request.user = self.coach
        assert self.permission.has_object_permission(self.request, None, self.obj)

    def test_any_user_has_no_access(self):
        assert not self.permission.has_object_permission(self.request, None, self.obj)

    def test_anonymous_has_no_access(self):
        self.request.user = AnonymousUser()
        assert not self.permission.has_object_permission(self.request, None, self.obj)


class IsMasterCourseStaffInstructorTests(TestCase):
    """ Test for IsMasterCourseStaffInstructorTests permission class. """

    def setUp(self):
        super().setUp()
        self.permission = IsMasterCourseStaffInstructor()
        master_course_id = 'edx/test123/run'
        self.user = UserFactory()
        self.get_request = RequestFactory().get(f'/?master_course_id={master_course_id}')
        self.get_request.user = self.user
        self.post_request = RequestFactory().post('/', data={'master_course_id': master_course_id})
        self.post_request.user = self.user
        self.course_key = CourseKey.from_string(master_course_id)

    def test_course_staff_has_access(self):
        CourseStaffRole(course_key=self.course_key).add_users(self.user)
        assert self.permission.has_permission(self.get_request, None)
        assert self.permission.has_permission(self.post_request, None)

    def test_course_instructor_has_access(self):
        CourseInstructorRole(course_key=self.course_key).add_users(self.user)
        assert self.permission.has_permission(self.get_request, None)
        assert self.permission.has_permission(self.post_request, None)

    def test_any_user_has_partial_access(self):
        assert not self.permission.has_permission(self.get_request, None)
        assert not self.permission.has_permission(self.post_request, None)

    def test_anonymous_has_no_access(self):
        user = AnonymousUser()
        self.get_request.user = user
        self.post_request.user = user
        assert not self.permission.has_permission(self.get_request, None)
        assert not self.permission.has_permission(self.post_request, None)

    def test_wrong_course_id_raises(self):
        get_request = RequestFactory().get('/?master_course_id=this_is_invalid')
        with pytest.raises(Http404):
            self.permission.has_permission(get_request, None)
        post_request = RequestFactory().post('/', data={'master_course_id': 'this_is_invalid'})
        with pytest.raises(Http404):
            self.permission.has_permission(post_request, None)


@ddt.ddt
class IsStaffOrOwnerTests(TestCase):
    """ Tests for IsStaffOrOwner permission class. """

    def setUp(self):
        super().setUp()
        self.permission = IsStaffOrOwner()
        self.request = RequestFactory().get('/')
        self.obj = TestObject()

    def assert_user_has_object_permission(self, user, permitted):
        """
        Asserts whether or not the user has permission to access an object.

        Arguments
            user (User)
            permitted (boolean)
        """
        self.request.user = user
        assert self.permission.has_object_permission(self.request, None, self.obj) == permitted

    def test_staff_user(self):
        """ Staff users should be permitted. """
        user = UserFactory(is_staff=True)
        self.assert_user_has_object_permission(user, True)

    def test_owner(self):
        """ Owners should be permitted. """
        user = UserFactory()
        self.obj.user = user
        self.assert_user_has_object_permission(user, True)

    def test_non_staff_test_non_owner_or_staff_user(self):
        """ Non-staff and non-owner users should not be permitted. """
        user = UserFactory()
        self.assert_user_has_object_permission(user, False)

    def test_has_permission_as_staff(self):
        """ Staff users always have permission. """
        self.request.user = UserFactory(is_staff=True)
        assert self.permission.has_permission(self.request, None)

    def test_has_permission_as_owner_with_get(self):
        """ Owners always have permission to make GET actions. """
        user = UserFactory()
        request = RequestFactory().get(f'/?username={user.username}')
        request.user = user
        assert self.permission.has_permission(request, None)

    def test_has_permission_with_view_kwargs_as_owner_with_get(self):
        """ Owners always have permission to make GET actions. """
        user = UserFactory()
        self.request.user = user
        view = GenericAPIView()
        view.kwargs = {'username': user.username}
        assert self.permission.has_permission(self.request, view)

    @ddt.data('patch', 'post', 'put')
    def test_has_permission_as_owner_with_edit(self, action):
        """ Owners always have permission to edit. """
        user = UserFactory()

        data = {'username': user.username}
        request = getattr(RequestFactory(), action)('/', data, format='json')
        request.user = user
        request.data = data  # Note (CCB): This is a hack that should be fixed. (ECOM-3171)
        assert self.permission.has_permission(request, None)

    def test_has_permission_as_non_owner(self):
        """ Non-owners should not have permission. """
        user = UserFactory()
        request = RequestFactory().get(f'/?username={user.username}')
        request.user = UserFactory()
        assert not self.permission.has_permission(request, None)

    def test_has_permission_with_view_kwargs_as_non_owner(self):
        """ Non-owners should not have permission. """
        user = UserFactory()
        self.request.user = user
        view = GenericAPIView()
        view.kwargs = {'username': UserFactory().username}
        assert not self.permission.has_permission(self.request, view)


class IsUserInUrlOrStaffTests(TestCase):
    """
    Regression tests for IsUserInUrlOrStaff.

    The previous implementation returned ``IsStaff | IsUserInUrl``, a
    composite class object that is always truthy. The permission
    therefore unconditionally granted access to every authenticated
    caller regardless of the URL subject.
    """

    def setUp(self):
        super().setUp()
        self.permission = IsUserInUrlOrStaff()
        self.factory = RequestFactory()
        self.owner = UserFactory(username='owner')
        self.other = UserFactory(username='intruder')
        self.staff = UserFactory(username='gremlin', is_staff=True)

    def _make_request(self, user, url_username):
        request = self.factory.get(f'/api/user/v1/preferences/{url_username}/')
        request.user = user
        request.parser_context = {'kwargs': {'username': url_username}}
        return request

    def test_sec_broken_permission_staff_granted_regression(self):
        request = self._make_request(self.staff, 'owner')
        assert self.permission.has_permission(request, None) is True

    def test_sec_broken_permission_user_matching_url_granted_regression(self):
        request = self._make_request(self.owner, 'owner')
        assert self.permission.has_permission(request, None) is True

    def test_sec_broken_permission_user_mismatch_denied_regression(self):
        """
        Non-staff attempting to access another user's URL must be denied.
        The regression is that previously this returned a truthy class
        object (``IsStaff | IsUserInUrl``) and granted access silently.
        Now the method must return a concrete ``False`` for mismatched
        non-staff callers.
        """
        request = self._make_request(self.other, 'owner')
        assert self.permission.has_permission(request, None) is False

    def test_sec_broken_permission_anonymous_denied_regression(self):
        request = self._make_request(AnonymousUser(), 'owner')
        assert self.permission.has_permission(request, None) is False

    def test_sec_broken_permission_returns_boolean_not_class_regression(self):
        """
        Ensure the return value is a bool, not a DRF permission class
        object (which would always be truthy).
        """
        request = self._make_request(self.staff, 'owner')
        result = self.permission.has_permission(request, None)
        assert isinstance(result, bool)
