"""Script to Empty the trashcan"""

from django.core.management.base import BaseCommand
from opaque_keys.edx.keys import CourseKey

from xmodule.contentstore.utils import empty_asset_trashcan
from xmodule.modulestore.django import modulestore

from .prompt import query_yes_no


class Command(BaseCommand):
    """
    Command to Empty the trashcan.
    """

    help = (
        "Empty the asset trashcan, optionally just for one course.\n"
        + "⚠️ This command is deprecated and will be removed in the future."
    )

    def add_arguments(self, parser):
        parser.add_argument('course_id',
                            help='Course ID to empty, leave off to empty for all courses',
                            nargs='?')

    def handle(self, *args, **options):
        if options['course_id']:
            course_ids = [CourseKey.from_string(options['course_id'])]
        else:
            course_ids = [course.id for course in modulestore().get_courses()]

        if query_yes_no(f"Emptying {len(course_ids)} trashcan(s). Confirm?", default="no"):
            empty_asset_trashcan(course_ids)
