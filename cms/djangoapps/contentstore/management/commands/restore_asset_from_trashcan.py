"""Management command to restore assets from trash"""

from django.core.management.base import BaseCommand

from xmodule.contentstore.utils import restore_asset_from_trashcan


class Command(BaseCommand):
    """Command class to handle asset restore"""

    help = """Restore a deleted asset from the trashcan back to it's original course.
⚠️ This command is deprecated and will be removed in the future."""

    def add_arguments(self, parser):
        parser.add_argument('location')

    def handle(self, *args, **options):
        restore_asset_from_trashcan(options['location'])
