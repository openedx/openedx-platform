"""
Survey Application Configuration
"""


from django.apps import AppConfig


class SurveyConfig(AppConfig):
    """
    Application Configuration for survey.
    """
    name = 'lms.djangoapps.survey'
    verbose_name = 'Student Surveys'

    def ready(self):
        """
        Connect signal handlers.
        """
        from . import signals  # pylint: disable=unused-import  # noqa: F401
