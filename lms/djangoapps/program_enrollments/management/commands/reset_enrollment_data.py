"""
Management command to remove enrollments and any related models created as
a side effect of enrolling students.

Intented for use in integration sandbox environments
"""


import logging  # noqa: I001
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.program_enrollments.models import ProgramEnrollment

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Deletes all enrollments and related data

    Example usage:
        $ ./manage.py lms reset_enrollment_data ca73b4af-676a-4bb3-a9a5-f6b5a3dedd,1c5f61b9-0be5-4a90-9ea5-582d5e066c
    """
    help = dedent(__doc__).strip()
    confirmation_prompt = "Type 'confirm' to continue with deletion\n"

    def add_arguments(self, parser):
        parser.add_argument(
            'programs',
            help='Comma separated list of programs to delete enrollments for'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip manual confirmation step before deleting objects',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        programs = options['programs'].split(',')

        q1_count, deleted_course_enrollment_models = CourseEnrollment.objects.filter(
            programcourseenrollment__program_enrollment__program_uuid__in=programs
        ).delete()
        q2_count, deleted_program_enrollment_models = ProgramEnrollment.objects.filter(
            program_uuid__in=programs
        ).delete()

        log.info(
            'The following records will be deleted:\n%s\n%s\n',
            deleted_course_enrollment_models,
            deleted_program_enrollment_models,
        )

        if not options['force']:
            confirmation = input(self.confirmation_prompt)
            if confirmation != 'confirm':
                raise CommandError('User confirmation required.  No records have been modified')

        log.info('Deleting %s records...', q1_count + q2_count)
