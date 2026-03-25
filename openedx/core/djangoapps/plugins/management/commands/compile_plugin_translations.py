"""
Compile the translation files for the edx_django_utils.plugins.
"""

from django.core.management.base import BaseCommand  # noqa: I001
from django.conf import settings


from ...constants import plugins_locale_root

from ... import i18n_api


class Command(BaseCommand):
    """
    Compile the translation files for the edx_django_utils.plugins.
    """
    def handle(self, *args, **options):
        i18n_api.compile_po_files(settings.REPO_ROOT / plugins_locale_root)
