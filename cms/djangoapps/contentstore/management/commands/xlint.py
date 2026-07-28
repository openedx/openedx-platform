"""
Verify the structure of courseware as to it's suitability for import
"""


from argparse import REMAINDER

from django.core.management.base import BaseCommand

from xmodule.modulestore.xml_importer import perform_xlint


class Command(BaseCommand):
    """Verify the structure of courseware as to its suitability for import"""
    help = """
    Verify the structure of courseware as to its suitability for import.
    To run: manage.py cms <data directory> [<course dir>...]
    """

    def add_arguments(self, parser):
        parser.add_argument('data_dir')
        parser.add_argument('source_dirs', nargs=REMAINDER)

    def handle(self, *args, **options):
        """Execute the command"""

        data_dir = options['data_dir']
        source_dirs = options['source_dirs']

        print("Importing.  Data_dir={data}, source_dirs={courses}".format(  # noqa: UP032
            data=data_dir,
            courses=source_dirs))

        perform_xlint(data_dir, source_dirs, load_error_blocks=False)
