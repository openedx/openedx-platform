"""
Compile the translation files for the edx_django_utils.plugins.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from ... import i18n_api
from ...constants import plugins_locale_root


class Command(BaseCommand):
    """
    Compile the translation files for the edx_django_utils.plugins.
    """
    def handle(self, *args, **options):
        i18n_api.compile_po_files(settings.REPO_ROOT / plugins_locale_root)
