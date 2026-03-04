# lint-amnesty, pylint: disable=missing-module-docstring

from django.apps import AppConfig
from django.conf import settings


class ThirdPartyAuthConfig(AppConfig):  # lint-amnesty, pylint: disable=missing-class-docstring
    name = 'common.djangoapps.third_party_auth'
    verbose_name = "Third-party authentication"

    def ready(self):
        # Import signal handlers to register them
        from .signals import handlers  # noqa: F401 pylint: disable=unused-import

        # To override the settings before loading social_django.
        if settings.ENABLE_THIRD_PARTY_AUTH:
            self._enable_third_party_auth()

    def _enable_third_party_auth(self):
        """
        Enable the use of third_party_auth, which allows users to sign in to edX
        using other identity providers. For configuration details, see
        common/djangoapps/third_party_auth/settings.py.
        """
        from common.djangoapps.third_party_auth import settings as auth_settings
        auth_settings.apply_settings(settings)
