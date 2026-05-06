"""
Script for importing courseware from XML format
"""


from django.core.management.base import BaseCommand

from openedx.core.djangoapps.django_comment_common.utils import are_permissions_roles_seeded, seed_permissions_roles
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import SignalHandler, modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.xml_importer import import_course_from_xml  # pylint: disable=wrong-import-order
from xmodule.util.sandboxing import DEFAULT_PYTHON_LIB_FILENAME  # pylint: disable=wrong-import-order


class Command(BaseCommand):
    """
    Import the specified data directory into the default ModuleStore
    """
    help = 'Import the specified data directory into the default ModuleStore.'

    def add_arguments(self, parser):
        parser.add_argument('data_directory')
        parser.add_argument('course_dirs',
                            nargs='*',
                            metavar='course_dir')
        parser.add_argument('--nostatic',
                            action='store_true',
                            help='Skip import of static content')
        parser.add_argument('--nopythonlib',
                            action='store_true',
                            help=(
                                'Skip import of course python library if it exists '
                                '(NOTE: If the static content import is not skipped, the python library '
                                'will be imported and this flag will be ignored)'
                            ))
        parser.add_argument('--python-lib-filename',
                            default=DEFAULT_PYTHON_LIB_FILENAME,
                            help='Filename of the course code library (if it exists)')

    def handle(self, *args, **options):
        data_dir = options['data_directory']
        source_dirs = options['course_dirs']
        if len(source_dirs) == 0:
            source_dirs = None
        do_import_static = not options.get('nostatic', False)
        # If the static content is not skipped, the python lib should be imported regardless
        # of the 'nopythonlib' flag.
        do_import_python_lib = do_import_static or not options.get('nopythonlib', False)
        python_lib_filename = options.get('python_lib_filename')

        output = (  # noqa: UP032
            "Importing...\n"
            "    data_dir={data}, source_dirs={courses}\n"
            "    Importing static content? {import_static}\n"
            "    Importing python lib? {import_python_lib}"
        ).format(
            data=data_dir,
            courses=source_dirs,
            import_static=do_import_static,
            import_python_lib=do_import_python_lib
        )
        self.stdout.write(output)
        mstore = modulestore()

        course_items = import_course_from_xml(
            mstore, ModuleStoreEnum.UserID.mgmt_command, data_dir, source_dirs, load_error_blocks=False,
            static_content_store=contentstore(), verbose=True,
            do_import_static=do_import_static, do_import_python_lib=do_import_python_lib,
            create_if_not_present=True,
            python_lib_filename=python_lib_filename,
        )

        for course in course_items:
            course_id = course.id
            # Importing is an act of publishing so send the course published signal.
            SignalHandler.course_published.send_robust(sender=self, course_key=course_id)

            # Seed forum permission roles if we need to.
            if not are_permissions_roles_seeded(course_id):
                self.stdout.write(f'Seeding forum roles for course {course_id}\n')
                seed_permissions_roles(course_id)
