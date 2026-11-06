"""Fixtures for AuthZ-aware tests"""

from functools import lru_cache

from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.engine.renderer import PolicyRenderer, SchemaApplier
from openedx_authz.engine.schema.pipeline import SchemaPipeline


@lru_cache(maxsize=1)
def _compiled_schema():
    """
    Compile and render the static AuthZ schema once per process.

    Discovering, validating and compiling the schema is the expensive part of
    ``load_authz_schema`` and does not depend on the database, so it is shared
    by every test.
    """
    schema = SchemaPipeline().compile()
    return schema, PolicyRenderer().render(schema)


def seed_policies():
    """
    Seed the database with the static AuthZ schema and load it into the enforcer.

    It applies the same schema as the ``load_authz_schema`` command, but reuses
    the compiled schema, so only the (per test) database write is repeated.
    """
    schema, rendered = _compiled_schema()
    SchemaApplier().apply(rendered, schema)
    AuthzEnforcer.get_enforcer().load_policy()
