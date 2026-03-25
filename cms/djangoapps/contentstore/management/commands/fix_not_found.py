"""
Script for fixing the item not found errors in a course
"""


from django.core.management.base import BaseCommand, CommandError  # noqa: I001
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

# To run from command line: ./manage.py cms fix_not_found course-v1:org+course+run


class Command(BaseCommand):
    """Fix a course's item not found errors"""
    help = "Fix a course's ItemNotFound errors"

    def add_arguments(self, parser):
        parser.add_argument('course_id')

    def handle(self, *args, **options):
        """Execute the command"""
        course_id = options['course_id']

        course_key = CourseKey.from_string(course_id)
        # for now only support on split mongo
        # pylint: disable=protected-access
        owning_store = modulestore()._get_modulestore_for_courselike(course_key)
        if hasattr(owning_store, 'fix_not_found'):
            owning_store.fix_not_found(course_key, ModuleStoreEnum.UserID.mgmt_command)
        else:
            raise CommandError("The owning modulestore does not support this command.")
