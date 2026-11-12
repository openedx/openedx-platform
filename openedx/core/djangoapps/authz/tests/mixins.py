""" Mixins for testing course-scoped AuthZ endpoints. """

from unittest.mock import patch

from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import COURSE_STAFF
from openedx_authz.engine.enforcer import AuthzEnforcer
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.tests.fixtures import seed_policies


class AuthzTestMixin:
    """
    Base mixin for tests that run with the AuthZ course authoring flag.

    Responsibilities:
    - Enable the ``authz.enable_course_authoring`` flag, and let tests change it
    - Seed policies into the AuthZ enforcer and clear them afterwards
    - Provide a helper to assign roles within a course scope

    The flag is enabled for every course at the start of each test. The patched
    ``is_enabled`` is available as ``authz_flag_mock``.
    """

    @classmethod
    def setUpClass(cls):
        cls.toggle_patcher = patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True,
        )
        cls.authz_flag_mock = cls.toggle_patcher.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.toggle_patcher.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.set_authz_enabled(True)
        seed_policies()

    def tearDown(self):
        super().tearDown()
        AuthzEnforcer.get_enforcer().clear_policy()

    def set_authz_enabled(self, enabled: bool):
        """
        Enable or disable ``authz.enable_course_authoring`` for every course.

        Args:
            enabled (bool): The value of the flag.
        """
        self.authz_flag_mock.side_effect = None
        self.authz_flag_mock.return_value = enabled

    def set_authz_enabled_for(self, *course_keys):
        """
        Enable ``authz.enable_course_authoring`` only for the given courses.

        It is disabled for any other course and when no course is given.

        Args:
            *course_keys (CourseKey): The courses with the flag enabled.
        """
        enabled_keys = {str(course_key) for course_key in course_keys}
        self.authz_flag_mock.side_effect = lambda course_key=None, **_: str(course_key) in enabled_keys

    def assign_authz_role(self, user, role, course_key):
        """
        Assign an AuthZ role to the user in the course.

        Args:
            user (User): The user receiving the role.
            role (str): The AuthZ role external key.
            course_key (CourseKey): The course where the role is assigned.
        """
        assign_role_to_user_in_scope(user.username, role, str(course_key))


class CourseAuthoringAuthzTestMixin(AuthzTestMixin):
    """
    Base mixin for testing AuthZ in the course authoring context.

    Besides what ``AuthzTestMixin`` provides:
    - Provide authenticated test clients
    """

    @classmethod
    def setUpClass(cls):
        cls.password = 'test'
        super().setUpClass()

    def setUp(self):
        super().setUp()

        self.authorized_user = UserFactory(password=self.password)
        self.unauthorized_user = UserFactory(password=self.password)

        self.authorized_client = APIClient()
        self.authorized_client.force_authenticate(user=self.authorized_user)

        self.unauthorized_client = APIClient()
        self.unauthorized_client.force_authenticate(user=self.unauthorized_user)

        self.super_user = UserFactory(is_superuser=True, password=self.password)
        self.super_client = APIClient()
        self.super_client.force_authenticate(user=self.super_user)

        self.staff_user = UserFactory(is_staff=True, password=self.password)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(user=self.staff_user)

    def add_user_to_role_in_course(self, user, role, course_key):
        """Helper method to add a user to a role for the course."""
        self.assign_authz_role(user, role, course_key)


class CourseAuthzTestMixin(CourseAuthoringAuthzTestMixin):
    """
    Reusable mixin for testing course-scoped AuthZ endpoints.
    """

    authz_roles_to_assign = [COURSE_STAFF.external_key]

    @property
    def course_key(self):
        """
        Must be defined by subclasses.
        """
        raise NotImplementedError("Tests using CourseAuthzTestMixin must define 'course_key'")

    def setUp(self):
        super().setUp()
        for role in self.authz_roles_to_assign:
            assign_role_to_user_in_scope(
                self.authorized_user.username,
                role,
                str(self.course_key)
            )

        AuthzEnforcer.get_enforcer().load_policy()

    def add_user_to_role(self, user, role):
        """Helper method to add a user to a role for the course."""
        self.add_user_to_role_in_course(user, role, self.course_key)
