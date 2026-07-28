"""Script for deleting orphans"""


from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import delete_orphans
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order


class Command(BaseCommand):
    """Command for deleting orphans"""
    help = '''
    Delete orphans from a MongoDB backed course. Takes two arguments:
    <course_id>: the course id of the course whose orphans you want to delete
    |--commit|: optional argument. If not provided, will dry run delete
    '''

    def add_arguments(self, parser):
        parser.add_argument('course_id')
        parser.add_argument('--commit', action='store_true', help='Commit to deleting the orphans')

    def handle(self, *args, **options):
        try:
            course_key = CourseKey.from_string(options['course_id'])
        except InvalidKeyError:
            raise CommandError("Invalid course key.")  # pylint: disable=raise-missing-from  # noqa: B904

        if options['commit']:
            print('Deleting orphans from the course:')
            deleted_items = delete_orphans(
                course_key, ModuleStoreEnum.UserID.mgmt_command, options['commit']
            )
            print("Success! Deleted the following orphans from the course:")
            print("\n".join(deleted_items))
        else:
            print('Dry run. The following orphans would have been deleted from the course:')
            deleted_items = delete_orphans(
                course_key, ModuleStoreEnum.UserID.mgmt_command, options['commit']
            )
            print("\n".join(deleted_items))
