"""
Django management command to extract assignment dates from modulestore and populate ContentDate table.
"""

import logging
from typing import List

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from edx_when.api import update_or_create_assignments_due_dates, models as when_models
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from lms.djangoapps.courseware.courses import get_course_assignments
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    """
    Management command to seed ContentDate table with assignment due dates from modulestore.

    Example usage:
        # Dry run for all courses
        python manage.py lms seed_content_dates --dry-run

        # Seed specific course
        python manage.py lms seed_content_dates --course-id "course-v1:MITx+6.00x+2023_Fall"

        # Seed all courses for specific org
        python manage.py lms seed_content_dates --org "MITx"

        # Force update existing entries
        python manage.py lms seed_content_dates --force-update
    """

    help = "Extract assignment dates from modulestore and populate ContentDate table"
    dry_run = False
    force_update = False
    batch_size = 100

    def add_arguments(self, parser):
        parser.add_argument(
            "--course-id",
            type=str,
            help='Specific course ID to process (e.g., "course-v1:MITx+6.00x+2023_Fall")',
        )
        parser.add_argument("--org", type=str, help="Organization to filter courses by")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be processed without making changes",
        )
        parser.add_argument(
            "--force-update",
            action="store_true",
            help="Update existing ContentDate entries (default: skip existing)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of assignments to process in each batch (default: 100)",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.force_update = options["force_update"]
        self.batch_size = options["batch_size"]

        logging.basicConfig(level=logging.INFO)

        if self.dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN MODE: No changes will be made to the database"
                )
            )

        try:
            course_keys = self._get_course_keys(options)

            total_processed = 0
            total_created = 0
            total_updated = 0
            total_skipped = 0

            for course_key in course_keys:
                self.stdout.write(f"Processing course: {course_key}")

                try:
                    processed, created, updated, skipped = self._process_course(
                        course_key
                    )
                    total_processed += processed
                    total_created += created
                    total_updated += updated
                    total_skipped += skipped

                    self.stdout.write(
                        f"  Course {course_key}: {processed} assignments processed, "
                        f"{created} created, {updated} updated, {skipped} skipped"
                    )

                except Exception as e:  # pylint: disable=broad-exception-caught
                    self.stdout.write(
                        self.style.ERROR(
                            f"Error processing course {course_key}: {str(e)}"
                        )
                    )
                    log.exception(f"Error processing course {course_key}")
                    continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSUMMARY:\n"
                    f"Total assignments processed: {total_processed}\n"
                    f"Total created: {total_created}\n"
                    f"Total updated: {total_updated}\n"
                    f"Total skipped: {total_skipped}"
                )
            )

        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"Command failed: {str(e)}") from e

    def _get_course_keys(self, options) -> List[CourseKey]:
        """
        Get list of course keys to process based on command options.
        """
        course_keys = []

        if options["course_id"]:
            try:
                course_key = CourseKey.from_string(options["course_id"])

                if not CourseOverview.objects.filter(id=course_key).exists():
                    raise CommandError(f"Course not found: {options['course_id']}")
                course_keys.append(course_key)
            except InvalidKeyError as e:
                raise CommandError(f"Invalid course ID format: {options['course_id']}") from e

        else:
            queryset = CourseOverview.objects.all()
            if options["org"]:
                queryset = queryset.filter(org=options["org"])

            course_keys = [overview.id for overview in queryset]

            if not course_keys:
                filter_msg = f" for org '{options['org']}'" if options["org"] else ""
                raise CommandError(f"No courses found{filter_msg}")

        return course_keys

    def _process_course(self, course_key: CourseKey) -> tuple[int, int, int, int]:
        """
        Process a single course and return (processed, created, updated, skipped) counts.
        """
        store = modulestore()

        try:
            course = store.get_course(course_key)
            if not course:
                raise ItemNotFoundError(
                    f"Course not found in modulestore: {course_key}"
                )
        except ItemNotFoundError:
            log.warning(f"Course not found in modulestore: {course_key}")
            return 0, 0, 0, 0

        staff_user = User.objects.filter(is_staff=True).first()
        if not staff_user:
            return
        assignments = get_course_assignments(course_key, staff_user)

        if not assignments:
            log.info(f"No assignments with due dates found in course: {course_key}")
            return 0, 0, 0, 0

        processed = len(assignments)
        created = 0
        updated = 0
        skipped = 0

        if self.dry_run:
            self.stdout.write(f"  Would process {processed} assignments")
            for assignment in assignments[:5]:  # Show first 5 as preview
                self.stdout.write(f"    - {assignment.title} (due: {assignment.date})")
            if len(assignments) > 5:
                self.stdout.write(f"    ... and {len(assignments) - 5} more")
            return processed, 0, 0, 0

        # Process assignments in batches
        for i in range(0, len(assignments), self.batch_size):
            batch = assignments[i: i + self.batch_size]

            with transaction.atomic():
                batch_created, batch_updated, batch_skipped = (
                    self._process_assignment_batch(course_key, batch)
                )
                created += batch_created
                updated += batch_updated
                skipped += batch_skipped

        return processed, created, updated, skipped

    def _process_assignment_batch(self, course_key: CourseKey, assignments) -> tuple[int, int, int]:
        """
        Process a batch of assignments and return (created, updated, skipped) counts.
        """
        created = 0
        updated = 0
        skipped = 0

        for assignment in assignments:
            self.stdout.write(
                f"Processing assignment: {assignment.block_key}/{assignment.assignment_type} (due: {assignment.date})"
            )
            existing = when_models.ContentDate.objects.filter(
                course_id=course_key, location=assignment.block_key, field="due"
            ).first()

            if existing and not self.force_update:
                skipped += 1
                log.info(
                    f"Skipping existing ContentDate for {assignment.title} "
                    f"in course {course_key}"
                )
                continue

            try:
                update_or_create_assignments_due_dates(course_key, [assignment])

                if existing:
                    updated += 1
                    log.info(
                        f"Updated ContentDate for {assignment.title} "
                        f"in course {course_key}"
                    )
                else:
                    created += 1
                    log.info(
                        f"Created ContentDate for {assignment.title} "
                        f"in course {course_key}"
                    )

            except Exception as e:  # pylint: disable=broad-exception-caught
                log.error(
                    f"Failed to process assignment {assignment.title} "
                    f"in course {course_key}: {str(e)}"
                )
                continue

        return created, updated, skipped
