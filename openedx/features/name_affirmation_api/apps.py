"""
Name Affirmation API App Configuration
"""


from django.apps import AppConfig
from django.conf import settings
from edx_proctoring.runtime import set_runtime_service

from openedx.features.name_affirmation_api.utils import get_name_affirmation_service


class NameAffirmationApiConfig(AppConfig):
    """
    Application Configuration for Name Affirmation API.
    """
    name = 'openedx.features.name_affirmation_api'

    def ready(self):
        """
        Connect services.
        """
        if settings.ENABLE_SPECIAL_EXAMS:
            name_affirmation_service = get_name_affirmation_service()
            if name_affirmation_service:
                set_runtime_service('name_affirmation', name_affirmation_service)
