"""
Configuration for the ``student`` Django application.
"""


import os

from django.apps import AppConfig


class StudentConfig(AppConfig):
    """
    Default configuration for the ``student`` application.
    """
    name = 'common.djangoapps.student'

    def ready(self):
        # Connect signal handlers.
        from .signals import receivers  # pylint: disable=unused-import

        # The django-simple-history model on CourseEnrollment creates performance
        # problems in testing, we mock it here so that the mock impacts all tests.
        if os.environ.get('DISABLE_COURSEENROLLMENT_HISTORY', False):
            from unittest.mock import MagicMock

            import common.djangoapps.student.models as student_models

            student_models.CourseEnrollment.history = MagicMock()
