"""Django app configuration for authz app."""

from django.apps import AppConfig


class AuthzConfig(AppConfig):
    """Django application configuration for the Open edX Authorization (AuthZ) app.

    This app provides a centralized location for integrations with the
    openedx-authz library, including permission helpers, decorators,
    and other utilities used to enforce RBAC-based authorization across
    the platform."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'openedx.core.djangoapps.authz'
    verbose_name = "Open edX Authorization Framework"
