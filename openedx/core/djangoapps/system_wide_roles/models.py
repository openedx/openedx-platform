"""
Django models for system wide roles.
"""


from edx_rbac.models import UserRole, UserRoleAssignment


class SystemWideRole(UserRole):  # pylint: disable=model-missing-unicode
    """
    User role definitions to govern non-enterprise system wide roles.
     .. no_pii:
    """

    def __str__(self):
        """
        Return human-readable string representation.
        """
        str_representation = "<SystemWideRole {role}>"
        return str_representation.format(role=self.name)

    def __repr__(self):
        """
        Return uniquely identifying string representation.
        """
        return self.__str__()


class SystemWideRoleAssignment(UserRoleAssignment):  # pylint: disable=model-missing-unicode
    """
    Model to map users to a SystemWideRole.
     .. no_pii:
    """

    role_class = SystemWideRole

    def __str__(self):
        """
        Return human-readable string representation.
        """
        str_representation = "<SystemWideRoleAssignment for User {user} assigned to role {role}>"
        return str_representation.format(
            user=self.user.id,
            role=self.role.name  # pylint: disable=no-member
        )

    def __repr__(self):
        """
        Return uniquely identifying string representation.
        """
        return self.__str__()
