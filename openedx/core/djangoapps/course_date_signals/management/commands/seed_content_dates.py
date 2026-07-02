"""
Django management command to extract assignment dates from modulestore and populate
ContentDate table.
"""

import logging
from typing import List

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from edx_when.api import get_locations_with_due_dates, update_or_create_assignments_due_dates
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.courses import get_course_assignments
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_date_signals.utils import to_edx_when_assignments

log = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    """
    Management command to seed ContentDate table with assignment due dates from modulestore.

    Example usage:
        # Dry run for all courses
        python manage.py lms seed_content_dates --username admin --dry-run

        # Seed specific course
        python manage.py lms seed_content_dates --username admin \\
            --course-id "course-v1:Org+Course+Run"

        # Seed all courses for specific org
        python manage.py lms seed_content_dates --username admin --org "MITx"

        # Force update existing entries
        python manage.py lms seed_content_dates --username admin --force-update
    """

    help = "Extract assignment dates from modulestore and populate ContentDate table"

    def add_arguments(self, parser):
        """
        Define CLI arguments for username, course/org scope, dry-run, and batching.
        """
        parser.add_argument(
            "--username",
            type=str,
            required=True,
            help="Username of an active staff user to use when loading course assignments.",
        )
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
        """
        Resolve the staff user, iterate courses, and print aggregate counts.
        """
        self.dry_run = options["dry_run"]  # pylint: disable=attribute-defined-outside-init
        self.force_update = options["force_update"]  # pylint: disable=attribute-defined-outside-init
        self.batch_size = options["batch_size"]  # pylint: disable=attribute-defined-outside-init

        try:
            staff_user = User.objects.get(
                username=options["username"], is_staff=True, is_active=True
            )
        except User.DoesNotExist as exc:
            raise CommandError(
                f"No active staff user found with username: {options['username']}"
            ) from exc

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
                        course_key, staff_user
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
                        self.style.ERROR(f"Error processing course {course_key}: {str(e)}")
                    )
                    log.exception("Error processing course %s", course_key)
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

    def _process_course(
        self, course_key: CourseKey, staff_user
    ) -> tuple[int, int, int, int]:
        """
        Process a single course and return (processed, created, updated, skipped) counts.
        """
        if not CourseOverview.objects.filter(id=course_key).exists():
            log.warning("Course not found in CourseOverview: %s", course_key)
            return (0, 0, 0, 0)

        assignments = get_course_assignments(course_key, staff_user)

        if not assignments:
            log.info("No assignments with due dates found in course: %s", course_key)
            return (0, 0, 0, 0)

        processed = len(assignments)
        created = 0
        updated = 0
        skipped = 0

        if self.dry_run:
            self.stdout.write(f"  Would process {processed} assignments")
            for assignment in assignments[:5]:
                self.stdout.write(f"    - {assignment.title} (due: {assignment.date})")
            if len(assignments) > 5:
                self.stdout.write(f"    ... and {len(assignments) - 5} more")
            return processed, 0, 0, 0

        existing_due_locations = get_locations_with_due_dates(course_key)

        for i in range(0, len(assignments), self.batch_size):
            batch = assignments[i : i + self.batch_size]
            try:
                batch_created, batch_updated, batch_skipped = self._process_assignment_batch(
                    course_key, batch, existing_due_locations
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                log.error(
                    "Failed to process assignment batch in course %s: %s", course_key, str(e)
                )
                continue
            created += batch_created
            updated += batch_updated
            skipped += batch_skipped

        return processed, created, updated, skipped

    def _process_assignment_batch(
        self,
        course_key: CourseKey,
        assignments: list,
        existing_due_locations: set,
    ) -> tuple[int, int, int]:
        """
        Process a batch of assignments and return (created, updated, skipped) counts.

        Assignments that already exist in ContentDate are skipped unless --force-update is set.
        The entire batch is written atomically; on failure the batch is rolled back and the
        exception is re-raised to the caller.

        existing_due_locations is updated in place when new ContentDate rows are created.
        """
        to_skip = [
            a for a in assignments
            if a.block_key in existing_due_locations and not self.force_update
        ]
        to_process = [
            a for a in assignments
            if a.block_key not in existing_due_locations or self.force_update
        ]

        skipped = len(to_skip)
        created = sum(1 for a in to_process if a.block_key not in existing_due_locations)
        updated = len(to_process) - created

        if to_process:
            with transaction.atomic():
                update_or_create_assignments_due_dates(
                    course_key, to_edx_when_assignments(to_process)
                )
            existing_due_locations.update(a.block_key for a in to_process)

        return created, updated, skipped
