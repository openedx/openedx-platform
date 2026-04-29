"""
Credit Application Configuration
"""


from django.apps import AppConfig
from django.conf import settings
from edx_proctoring.runtime import set_runtime_service


class CreditConfig(AppConfig):
    """
    Default configuration for the "openedx.core.djangoapps.credit" Django application.
    """
    name = 'openedx.core.djangoapps.credit'

    def ready(self):
        from .signals import handlers  # pylint: disable=unused-import  # noqa: F401
        if settings.FEATURES.get('ENABLE_SPECIAL_EXAMS'):
            from .services import CreditService
            set_runtime_service('credit', CreditService())
