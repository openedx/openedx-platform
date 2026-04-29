# pylint: disable=missing-module-docstring
from django.apps import AppConfig


class BulkEmailConfig(AppConfig):
    """
    Application Configuration for bulk_email.
    """
    name = 'lms.djangoapps.bulk_email'

    def ready(self):
        from edx_ace.signals import ACE_MESSAGE_SENT  # pylint: disable=unused-import  # noqa: F401

        import lms.djangoapps.bulk_email.signals  # pylint: disable=unused-import  # noqa: F401
