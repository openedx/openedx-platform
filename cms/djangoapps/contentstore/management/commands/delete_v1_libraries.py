"""A Command to  delete V1 Content Libraries index entires."""

import logging
from textwrap import dedent

from celery import group
from django.core.management import BaseCommand, CommandError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator

from cms.djangoapps.contentstore.tasks import delete_v1_library
from xmodule.modulestore.django import modulestore

from .prompt import query_yes_no

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Delete V1 Content Libraries (default all) index entires.
    Specfiy --all for all libraries, or space-seperated library ids for specific libraries.
    Note this will leave orphans behind in mongo. use mongo prune to clean them up.

    Example usage:
        ./manage.py cms delete_v1_libraries 'library-v1:edx+eaa'
        ./manage.py cms delete_v1_libraries --all

    Note:
       This Command also produces an "output file" which contains the mapping of locators and the status of the copy.
    """

    help = dedent(__doc__)
    CONFIRMATION_PROMPT = "Deleting all libraries might be a time consuming operation. Do you want to continue?"

    def add_arguments(self, parser):
        """arguements for command"""

        parser.add_argument(
            'library_ids',
            nargs='*',
            help='A space-seperated list of v1 library ids to delete'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            dest='all',
            help='Delete all libraries'
        )
        parser.add_argument(
            'output_csv',
            nargs='?',
            default=None,
            help='a file path to write the tasks output to. Without this the result is simply logged.'
        )

    def _parse_library_key(self, raw_value):
        """ Parses library key from string """
        result = CourseKey.from_string(raw_value)

        if not isinstance(result, LibraryLocator):
            raise CommandError(f"Argument {raw_value} is not a library key")
        return result

    def handle(self, *args, **options):  # lint-amnesty, pylint: disable=unused-argument
        """Parse args and generate tasks for deleting content."""

        if (not options['library_ids'] and not options['all']) or (options['library_ids'] and options['all']):
            raise CommandError("delete_v1_libraries requires one or more <library_id>s or the --all flag.")

        if options['all']:
            store = modulestore()
            if query_yes_no(self.CONFIRMATION_PROMPT, default="no"):
                v1_library_keys = [
                    library.location.library_key.replace(branch=None) for library in store.get_libraries()
                ]
            else:
                return
        else:
            v1_library_keys = list(map(self._parse_library_key, options['library_ids']))

        delete_libary_task_group = group([
            delete_v1_library.s(str(v1_library_key)) for v1_library_key in v1_library_keys
        ])

        group_result = delete_libary_task_group.apply_async().get()
        log.info(group_result)
        if options['output_csv']:
            with open(options['output_csv'][0], 'w', encoding='utf-8', newline='') as output_writer:
                output_writer.writerow("v1_library_id", "v2_library_id", "status", "error_msg")
                for result in group_result:
                    output_writer.write(result.keys())
