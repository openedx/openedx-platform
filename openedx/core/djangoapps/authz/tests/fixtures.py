""" Fixtures for AuthZ-aware tests """
import casbin
import pkg_resources
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.engine.utils import migrate_policy_between_enforcers


def seed_policies():
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
