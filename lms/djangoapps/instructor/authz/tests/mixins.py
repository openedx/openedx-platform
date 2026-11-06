"""
Test helpers to assign AuthZ roles in Instructor Dashboard tests.
"""

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import AuthzTestMixin


class InstructorAuthzTestMixin(AuthzTestMixin):
    """
    Mixin to test the Instructor Dashboard with the AuthZ flag on or off.

    By default the flag is enabled; call ``set_authz_enabled(False)`` to test
    the legacy behavior. The enforcer is seeded with the static AuthZ schema,
    which already grants the Instructor Dashboard permissions to the default roles.
    """

    def create_user_with_authz_role(self, role, course_key, **kwargs):
        """
        Create a user and assign it an AuthZ role in the course.

        Args:
            role (str): The AuthZ role external key.
            course_key (CourseKey): The course where the role is assigned.
            **kwargs: Extra arguments for ``UserFactory``.

        Returns:
            User: The new user.
        """
        user = UserFactory(**kwargs)
        self.assign_authz_role(user, role, course_key)
        return user
