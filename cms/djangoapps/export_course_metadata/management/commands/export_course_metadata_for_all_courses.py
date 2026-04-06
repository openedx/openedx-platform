"""
Export course metadata for all courses
"""

from django.core.management.base import BaseCommand

from cms.djangoapps.export_course_metadata.signals import export_course_metadata  # pylint: disable=unused-import
from cms.djangoapps.export_course_metadata.tasks import export_course_metadata_task
from xmodule.modulestore.django import modulestore


class Command(BaseCommand):
    """
    Export course metadata for all courses
    """
    help = 'Export course metadata for all courses'

    def handle(self, *args, **options):
        """
        Execute the command
        """
        export_course_metadata_for_all_courses()


def export_course_metadata_for_all_courses():
    """
    Export course metadata for all courses
    """
    courses = modulestore().get_course_summaries()
    for course in courses:
        export_course_metadata_task.delay(str(course.id))
