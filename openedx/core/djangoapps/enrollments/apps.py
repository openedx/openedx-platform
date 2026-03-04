"""
Enrollments Application Configuration

Signal handlers are connected here.
"""


from django.apps import AppConfig
from django.conf import settings
from edx_proctoring.runtime import set_runtime_service


class EnrollmentsConfig(AppConfig):
    """
    Application Configuration for Enrollments.
    """
    name = 'openedx.core.djangoapps.enrollments'

    def ready(self):
        """
        Connect handlers to fetch enrollments.
        """
        if settings.ENABLE_SPECIAL_EXAMS:
            from .services import EnrollmentsService
            set_runtime_service('enrollments', EnrollmentsService())
