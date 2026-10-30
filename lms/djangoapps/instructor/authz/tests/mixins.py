"""
Test helpers to assign AuthZ roles in Instructor Dashboard tests.
"""

from openedx_authz.engine.enforcer import AuthzEnforcer

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor.authz import constants as authz_constants
from openedx.core.djangoapps.authz.tests.mixins import AuthzTestMixin

COURSE_SCOPE_POLICY = "course-v1^*"

# TEMPORARY: the role assignments of the Instructor Dashboard permissions depend
# on a separate issue that will define them in openedx-authz. They are seeded
# here only for testing.
_INSTRUCTOR_DASHBOARD_ROLE_PERMISSIONS = (
    authz_constants.COURSES_VIEW_COURSE_INFO,
    authz_constants.COURSES_VIEW_ENROLLMENTS,
    authz_constants.COURSES_ENROLL_LEARNERS,
    authz_constants.COURSES_UNENROLL_LEARNERS,
    authz_constants.COURSES_VIEW_GRADEBOOK,
    authz_constants.COURSES_VIEW_GRADING_CONFIGURATION,
    authz_constants.COURSES_MANAGE_LEARNER_GRADING,
    authz_constants.COURSES_VIEW_LEARNER_EXTENSIONS,
    authz_constants.COURSES_MANAGE_LEARNER_EXTENSIONS,
    authz_constants.COURSES_VIEW_COHORTS,
    authz_constants.COURSES_MANAGE_COHORTS,
)
INSTRUCTOR_DASHBOARD_ROLE_PERMISSIONS = {
    "course_admin": _INSTRUCTOR_DASHBOARD_ROLE_PERMISSIONS,
    "course_staff": _INSTRUCTOR_DASHBOARD_ROLE_PERMISSIONS,
}


def seed_instructor_dashboard_policies():
    """
    Add to the enforcer the policies granted by the Instructor Dashboard.

    TEMPORARY: openedx-authz has no API to grant a permission to a role, so the
    policies are added straight to the enforcer. Once the Instructor Dashboard
    permissions are assigned to the default roles in the openedx-authz static
    schema (loaded with ``load_authz_schema``), this helper is removed.
    """
    enforcer = AuthzEnforcer.get_enforcer()
    for role, permissions in INSTRUCTOR_DASHBOARD_ROLE_PERMISSIONS.items():
        for permission in permissions:
            enforcer.add_policy(f"role^{role}", f"act^{permission}", COURSE_SCOPE_POLICY, "allow")


class InstructorAuthzTestMixin(AuthzTestMixin):
    """
    Mixin to test the Instructor Dashboard with the AuthZ flag on or off.

    By default the flag is enabled; call ``set_authz_enabled(False)`` to test
    the legacy behavior. The enforcer is seeded with the static AuthZ policies
    plus the Instructor Dashboard ones.
    """

    def setUp(self):
        super().setUp()
        seed_instructor_dashboard_policies()

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
