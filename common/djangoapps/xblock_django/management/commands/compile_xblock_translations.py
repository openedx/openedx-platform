"""
Compile the translation files for the XBlocks.
"""

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.plugins.i18n_api import compile_po_files
from xmodule.modulestore import api as xmodule_api

from ...translation import compile_xblock_js_messages


class Command(BaseCommand):
    """
    Compile the translation files for the XBlocks.
    """
    def handle(self, *args, **options):
        compile_po_files(xmodule_api.get_python_locale_root())
        compile_xblock_js_messages()
