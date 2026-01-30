# lint-amnesty, pylint: disable=missing-module-docstring

from django.apps import AppConfig
from django.conf import settings


class ThirdPartyAuthConfig(AppConfig):  # lint-amnesty, pylint: disable=missing-class-docstring
    name = 'common.djangoapps.third_party_auth'
    verbose_name = "Third-party authentication"

    def ready(self):
        # Import signal handlers to register them
        from .signals import handlers  # noqa: F401 pylint: disable=unused-import

        # Note: Third-party auth settings are now defined statically in lms/envs/common.py
        # However, the enterprise pipeline step must be inserted dynamically because
        # it requires checking if enterprise is enabled, which can't be done at
        # settings load time.
        from openedx.features.enterprise_support.api import insert_enterprise_pipeline_elements
        insert_enterprise_pipeline_elements(settings.SOCIAL_AUTH_PIPELINE)
