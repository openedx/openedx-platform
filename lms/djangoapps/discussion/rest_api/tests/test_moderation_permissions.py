"""
Tests for discussion moderation permissions.
"""
from unittest.mock import Mock

from rest_framework.test import APIRequestFactory

from common.djangoapps.student.roles import CourseStaffRole, CourseInstructorRole, GlobalStaff
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.discussion.rest_api.permissions import (
    IsAllowedToBulkDelete,
    can_take_action_on_spam,
)
from openedx.core.djangoapps.django_comment_common.models import Role
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class CanTakeActionOnSpamTest(ModuleStoreTestCase):
    """Tests for can_take_action_on_spam permission helper function."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = self.course.id

    def test_global_staff_has_permission(self):
        """Global staff should have permission."""
        user = UserFactory.create(is_staff=True)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_global_staff_role_has_permission(self):
        """Users with GlobalStaff role should have permission."""
        user = UserFactory.create()
        GlobalStaff().add_users(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_course_staff_has_permission(self):
        """Course staff should have permission for their course."""
        user = UserFactory.create()
        CourseStaffRole(self.course_key).add_users(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_course_instructor_has_permission(self):
        """Course instructors should have permission for their course."""
        user = UserFactory.create()
        CourseInstructorRole(self.course_key).add_users(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_forum_moderator_has_permission(self):
        """Forum moderators should have permission for their course."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course_key)
        role.users.add(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_forum_administrator_has_permission(self):
        """Forum administrators should have permission for their course."""
        user = UserFactory.create()
        role = Role.objects.create(name='Administrator', course_id=self.course_key)
        role.users.add(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_regular_student_no_permission(self):
        """Regular students should not have permission."""
        user = UserFactory.create()
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_community_ta_no_permission(self):
        """Community TAs should not have bulk delete permission."""
        user = UserFactory.create()
        role = Role.objects.create(name='Community TA', course_id=self.course_key)
        role.users.add(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_staff_different_course_no_permission(self):
        """Staff from a different course should not have permission."""
        other_course = CourseFactory.create(org='OtherX', number='CS201', run='2024')
        user = UserFactory.create()
        CourseStaffRole(other_course.id).add_users(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_accepts_string_course_id(self):
        """Function should accept string course_id and convert it."""
        user = UserFactory.create()
        CourseStaffRole(self.course_key).add_users(user)
        self.assertTrue(can_take_action_on_spam(user, str(self.course_key)))


class IsAllowedToBulkDeleteTest(ModuleStoreTestCase):
    """Tests for IsAllowedToBulkDelete permission class."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = str(self.course.id)
        self.factory = APIRequestFactory()
        self.permission = IsAllowedToBulkDelete()

    def _create_view_with_kwargs(self, course_id=None):
        """Helper to create a mock view with kwargs."""
        view = Mock()
        view.kwargs = {'course_id': course_id} if course_id else {}
        return view

    def _create_request_with_data(self, user, course_id=None, method='POST'):
        """Helper to create a request with data."""
        if method == 'POST':
            request = self.factory.post('/api/discussion/v1/moderation/bulk-delete-ban/')
        else:
            request = self.factory.get('/api/discussion/v1/moderation/banned-users/')

        request.user = user
        request.data = {'course_id': course_id} if course_id else {}
        return request

    def test_unauthenticated_user_denied(self):
        """Unauthenticated users should be denied."""
        request = self.factory.post('/api/discussion/v1/moderation/bulk-delete-ban/')
        request.user = Mock(is_authenticated=False)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_global_staff_with_course_id_in_data(self):
        """Global staff should have permission when course_id is in request data."""
        user = UserFactory.create(is_staff=True)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_course_staff_with_course_id_in_data(self):
        """Course staff should have permission when course_id is in request data."""
        user = UserFactory.create()
        CourseStaffRole(self.course.id).add_users(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_course_instructor_with_course_id_in_data(self):
        """Course instructors should have permission when course_id is in request data."""
        user = UserFactory.create()
        CourseInstructorRole(self.course.id).add_users(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_forum_moderator_with_course_id_in_data(self):
        """Forum moderators should have permission when course_id is in request data."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course.id)
        role.users.add(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_regular_student_denied(self):
        """Regular students should be denied."""
        user = UserFactory.create()
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_course_id_in_url_kwargs(self):
        """Permission should work when course_id is in URL kwargs."""
        user = UserFactory.create()
        CourseStaffRole(self.course.id).add_users(user)
        request = self.factory.get('/api/discussion/v1/moderation/banned-users/')
        request.user = user
        request.data = {}
        request.query_params = {}
        view = self._create_view_with_kwargs(self.course_key)

        self.assertTrue(self.permission.has_permission(request, view))

    def test_no_course_id_only_global_staff_allowed(self):
        """When no course_id provided, only global staff should be allowed."""
        # Global staff allowed
        global_staff = UserFactory.create(is_staff=True)
        request = self._create_request_with_data(global_staff)
        view = self._create_view_with_kwargs()
        self.assertTrue(self.permission.has_permission(request, view))

        # Regular user denied
        regular_user = UserFactory.create()
        request = self._create_request_with_data(regular_user)
        view = self._create_view_with_kwargs()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_staff_different_course_denied(self):
        """Staff from different course should be denied."""
        other_course = CourseFactory.create(org='OtherX', number='CS201', run='2024')
        user = UserFactory.create()
        CourseStaffRole(other_course.id).add_users(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))
