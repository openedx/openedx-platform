""" Mixins for testing course-scoped AuthZ endpoints. """

import casbin
import pkg_resources

from unittest.mock import patch
from rest_framework.test import APIClient
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import COURSE_STAFF
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.engine.utils import migrate_policy_between_enforcers

from openedx.core import toggles as core_toggles
from common.djangoapps.student.tests.factories import UserFactory


class CourseAuthzTestMixin:
    """
    Reusable mixin for testing course-scoped AuthZ endpoints.
    """

    authz_roles_to_assign = [COURSE_STAFF.external_key]

    @classmethod
    def setUpClass(cls):
        cls.toggle_patcher = patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True
        )
        cls.toggle_patcher.start()

        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.toggle_patcher.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()

        self._seed_database_with_policies()

        self.authorized_user = UserFactory()
        self.unauthorized_user = UserFactory()

        for role in self.authz_roles_to_assign:
            assign_role_to_user_in_scope(
                self.authorized_user.username,
                role,
                str(self.course_key)
            )

        AuthzEnforcer.get_enforcer().load_policy()

        self.authorized_client = APIClient()
        self.authorized_client.force_authenticate(user=self.authorized_user)

        self.unauthorized_client = APIClient()
        self.unauthorized_client.force_authenticate(user=self.unauthorized_user)

    def add_user_to_role(self, user, role):
        """Helper method to add a user to a role for the course."""
        assign_role_to_user_in_scope(
            user.username,
            role,
            str(self.course_key)
        )
        AuthzEnforcer.get_enforcer().load_policy()

    def tearDown(self):
        super().tearDown()
        AuthzEnforcer.get_enforcer().clear_policy()

    @classmethod
    def _seed_database_with_policies(cls):
        """Seed the database with AuthZ policies."""
        global_enforcer = AuthzEnforcer.get_enforcer()
        global_enforcer.load_policy()

        model_path = pkg_resources.resource_filename(
            "openedx_authz.engine",
            "config/model.conf"
        )

        policy_path = pkg_resources.resource_filename(
            "openedx_authz.engine",
            "config/authz.policy"
        )

        migrate_policy_between_enforcers(
            source_enforcer=casbin.Enforcer(model_path, policy_path),
            target_enforcer=global_enforcer,
        )
