""" Mixins for testing course-scoped AuthZ endpoints. """

from unittest.mock import patch

import casbin
import pkg_resources
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import COURSE_STAFF
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.engine.utils import migrate_policy_between_enforcers
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles


class CourseAuthoringAuthzTestMixin:
    """
    Base mixin for testing AuthZ in the course authoring context.

    Responsibilities:
    - Enable course authoring AuthZ feature flag
    - Seed policies into the AuthZ enforcer
    - Provide authenticated test clients
    - Provide helpers for assigning roles within a course scope
    """

    @classmethod
    def setUpClass(cls):
        cls.toggle_patcher = patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True,
        )
        cls.toggle_patcher.start()
        cls.password = 'test'
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.toggle_patcher.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()

        self._seed_policies()

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

    def tearDown(self):
        super().tearDown()
        AuthzEnforcer.get_enforcer().clear_policy()

    def add_user_to_role_in_course(self, user, role, course_key):
        """Helper method to add a user to a role for the course."""
        assign_role_to_user_in_scope(
            user.username,
            role,
            str(course_key)
        )
        AuthzEnforcer.get_enforcer().load_policy()

    @classmethod
    def _seed_policies(cls):
        """Seed the database with AuthZ policies."""
        global_enforcer = AuthzEnforcer.get_enforcer()
        global_enforcer.load_policy()

        model_path = pkg_resources.resource_filename(
            "openedx_authz.engine",
            "config/model.conf",
        )

        policy_path = pkg_resources.resource_filename(
            "openedx_authz.engine",
            "config/authz.policy",
        )

        migrate_policy_between_enforcers(
            source_enforcer=casbin.Enforcer(model_path, policy_path),
            target_enforcer=global_enforcer,
        )


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
