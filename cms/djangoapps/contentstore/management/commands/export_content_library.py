"""
Script for exporting a content library from Mongo to a tar.gz file
"""


import os
import shutil

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator

from cms.djangoapps.contentstore import tasks
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


class Command(BaseCommand):
    """
    Export the specified content library into a directory.  Output will need to be tar zxcf'ed.
    """
    help = 'Export the specified content library into a directory'

    def add_arguments(self, parser):
        parser.add_argument('library_id')
        parser.add_argument('output_path', nargs='?')

    def handle(self, *args, **options):
        """
        Given a content library id, and an output_path folder.  Export the
        corresponding course from mongo and put it directly in the folder.
        """
        module_store = modulestore()
        try:
            library_key = CourseKey.from_string(options['library_id'])
        except InvalidKeyError:
            raise CommandError('Invalid library ID: "{}".'.format(options['library_id']))  # pylint: disable=raise-missing-from  # noqa: B904
        if not isinstance(library_key, LibraryLocator):
            raise CommandError('Argument "{}" is not a library key'.format(options['library_id']))

        library = module_store.get_library(library_key)
        if library is None:
            raise CommandError('Library "{}" not found.'.format(options['library_id']))

        dest_path = options['output_path'] or '.'
        if not os.path.isdir(dest_path):
            raise CommandError(f'Output path "{dest_path}" not found.')

        try:
            # Generate archive using the handy tasks implementation
            tarball = tasks.create_export_tarball(library, library_key, {}, None)
        except Exception as e:
            raise CommandError(f'Failed to export "{library_key}" with "{e}"')  # pylint: disable=raise-missing-from  # noqa: B904
        with tarball:
            # Save generated archive with keyed filename
            prefix, suffix, n = str(library_key).replace(':', '+'), '.tar.gz', 0
            while os.path.exists(prefix + suffix):
                n += 1
                prefix = '{}_{}'.format(prefix.rsplit('_', 1)[0], n) if n > 1 else f'{prefix}_1'
            filename = prefix + suffix
            target = os.path.join(dest_path, filename)
            tarball.file.seek(0)
            with open(target, 'wb') as f:
                shutil.copyfileobj(tarball.file, f)
            print(f'Library "{library.location.library_key}" exported to "{target}"')
