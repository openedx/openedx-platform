"""
Tests for system wide roles' django models.
"""


from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.system_wide_roles.models import SystemWideRole, SystemWideRoleAssignment


class SystemWideRoleTests(TestCase):
    """ Tests for SystemWideRole in system_wide_roles app """

    def setUp(self):
        super().setUp()
        self.role = SystemWideRole.objects.create(name='TestRole')

    def test_str(self):
        assert str(self.role) == '<SystemWideRole TestRole>'

    def test_repr(self):
        assert repr(self.role) == '<SystemWideRole TestRole>'


class SystemWideRoleAssignmentTests(TestCase):
    """ Tests for SystemWideRoleAssignment in system_wide_roles app """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.role = SystemWideRole.objects.create(name='TestRole')

    def test_str(self):
        role_assignment = SystemWideRoleAssignment.objects.create(role=self.role, user=self.user)
        assert str(role_assignment) == \
               f'<SystemWideRoleAssignment for User {self.user.id} assigned to role {self.role.name}>'

    def test_repr(self):
        role_assignment = SystemWideRoleAssignment.objects.create(role=self.role, user=self.user)
        assert repr(role_assignment) == \
               f'<SystemWideRoleAssignment for User {self.user.id} assigned to role {self.role.name}>'
