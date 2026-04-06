# lint-amnesty, pylint: disable=missing-module-docstring
from django.apps import AppConfig


class BulkEmailConfig(AppConfig):
    """
    Application Configuration for bulk_email.
    """
    name = 'lms.djangoapps.bulk_email'

    def ready(self):
        from edx_ace.signals import ACE_MESSAGE_SENT  # lint-amnesty, pylint: disable=unused-import

        import lms.djangoapps.bulk_email.signals  # lint-amnesty, pylint: disable=unused-import
