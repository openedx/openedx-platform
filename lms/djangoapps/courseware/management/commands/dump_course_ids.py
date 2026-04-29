"""
Dump the course_ids available to the lms.

Output is UTF-8 encoded by default.
"""


from textwrap import dedent

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument('--modulestore',
                            default='default',
                            help='name of the modulestore to use')

    def handle(self, *args, **options):
        output = '\n'.join(str(course_overview.id) for course_overview in CourseOverview.get_all_courses()) + '\n'

        return output
