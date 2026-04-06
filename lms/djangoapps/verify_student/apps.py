"""
Student Identity Verification Application Configuration
"""


from django.apps import AppConfig


class VerifyStudentConfig(AppConfig):
    """
    Application Configuration for verify_student.
    """
    name = 'lms.djangoapps.verify_student'
    verbose_name = 'Student Identity Verification'

    def ready(self):
        """
        Connect signal handlers.
        """
        from lms.djangoapps.verify_student import tasks  # pylint: disable=unused-import  # noqa: F401
        from lms.djangoapps.verify_student.signals import handlers, signals  # pylint: disable=unused-import  # noqa: F401
