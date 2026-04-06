"""
Tests for User deactivation API permissions
"""


from django.test import RequestFactory, TestCase

from common.djangoapps.student.tests.factories import (  # lint-amnesty, pylint: disable=line-too-long
    AdminFactory,
    ContentTypeFactory,
    PermissionFactory,
    SuperuserFactory,
    UserFactory,
)
from openedx.core.djangoapps.user_api.accounts.permissions import (
    CanCancelUserRetirement,
    CanDeactivateUser,
    CanRetireUser,
)


class CanDeactivateUserTest(TestCase):
    """ Tests for user deactivation API permissions """

    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get('/test/url')

    def test_api_permission_superuser(self):
        self.request.user = SuperuserFactory()

        result = CanDeactivateUser().has_permission(self.request, None)
        assert result

    def test_api_permission_user_granted_permission(self):
        user = UserFactory()
        permission = PermissionFactory(
            codename='can_deactivate_users',
            content_type=ContentTypeFactory(
                app_label='student'
            )
        )
        user.user_permissions.add(permission)
        self.request.user = user

        result = CanDeactivateUser().has_permission(self.request, None)
        assert result

    def test_api_permission_user_without_permission(self):
        self.request.user = UserFactory()
        result = CanDeactivateUser().has_permission(self.request, None)
        assert not result


class CanRetireUserTest(TestCase):
    """ Tests for user retirement API permissions """

    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get('/test/url')

    def test_api_permission_superuser(self):
        self.request.user = SuperuserFactory()

        result = CanRetireUser().has_permission(self.request, None)
        assert result

    def test_api_permission_user_granted_permission(self):
        user = UserFactory()
        self.request.user = user

        with self.settings(RETIREMENT_SERVICE_WORKER_USERNAME=user.username):
            result = CanRetireUser().has_permission(self.request, None)
            assert result

    def test_api_permission_user_without_permission(self):
        self.request.user = UserFactory()
        result = CanRetireUser().has_permission(self.request, None)
        assert not result

    def test_api_permission_staff_without_permission(self):
        self.request.user = AdminFactory()
        result = CanRetireUser().has_permission(self.request, None)
        assert not result

    def test_api_permission_staff_granted_permission(self):
        self.request.user = AdminFactory()
        permission = PermissionFactory(
            codename='add_userretirementrequest',
            content_type=ContentTypeFactory(
                app_label='user_api'
            )
        )
        self.request.user.user_permissions.add(permission)
        result = CanRetireUser().has_permission(self.request, None)
        assert result


class CanCancelUserRetirementTest(TestCase):
    """ Tests for cancel user retirement API permissions """

    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get('/test/url')

    def test_permission_superuser(self):
        self.request.user = SuperuserFactory()

        can_cancel_retirement = CanCancelUserRetirement().has_permission(self.request, None)
        assert can_cancel_retirement is True

    def test_permission_user_granted_permission(self):
        user = AdminFactory()
        permission = PermissionFactory(
            codename='change_userretirementstatus',
            content_type=ContentTypeFactory(
                app_label='user_api'
            )
        )
        user.user_permissions.add(permission)
        self.request.user = user

        can_cancel_retirement = CanCancelUserRetirement().has_permission(self.request, None)
        assert can_cancel_retirement is True

    def test_api_permission_user_without_permission(self):
        self.request.user = UserFactory()
        can_cancel_retirement = CanCancelUserRetirement().has_permission(self.request, None)
        assert can_cancel_retirement is False
