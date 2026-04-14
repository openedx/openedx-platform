"""
Dump the course_ids available to the lms, excluding courses
that have ended prior to the given date.

Output is UTF-8 encoded by default.
The output format is one course_id per line.
"""

import datetime
from textwrap import dedent

from django.core.management.base import BaseCommand
from django.db.models import Q

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class Command(BaseCommand):  # lint-amnesty, pylint: disable=missing-class-docstring
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument('--end',
                            default=None,
                            help='Date to filter out courses that have ended before the provided date')

    def handle(self, *args, **options):
        course_overviews = CourseOverview.objects.all()
        courseoverview_filter = Q()
        if options['end']:
            courseoverview_filter |= Q(end__gte=datetime.datetime.strptime(options['end'], "%Y-%m-%d"))
            courseoverview_filter |= Q(end=None)
            course_overviews = course_overviews.filter(courseoverview_filter)

        output = '\n'.join(str(course_overview.id) for course_overview in course_overviews) + '\n'
        return output
