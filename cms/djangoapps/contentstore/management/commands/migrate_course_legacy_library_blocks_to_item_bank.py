"""
Management command to migrate legacy library content blocks to Item Bank blocks for course(s).

This command can be run for a specific list of courses or for all courses.
"""
from __future__ import annotations

import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.tasks import migrate_course_legacy_library_blocks_to_item_bank
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Migrate legacy library content blocks to Item Bank blocks for course(s).

    Examples:
        # Migrate specific courses.
        $ ./manage.py cms migrate_course_legacy_library_blocks_to_item_bank \
        --course-ids course-v1:edX+DemoX+2024,course-v1:edX+Demo2+2024 --user-id 3

        # Migrate all courses.
        $ ./manage.py cms migrate_course_legacy_library_blocks_to_item_bank --all-courses --user-id 3

        # Migrate all courses, re-publishing blocks that were published before the migration.
        $ ./manage.py cms migrate_course_legacy_library_blocks_to_item_bank --all-courses --user-id 3 \
        --persist-publish-state
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--course-ids',
            help='Comma-separated list of course keys to migrate.',
        )
        parser.add_argument(
            '--all-courses',
            action='store_true',
            help='Migrate legacy library content blocks for all courses.',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            required=True,
            help='ID of the user performing the migration.',
        )
        parser.add_argument(
            '--persist-publish-state',
            action='store_true',
            help='Re-publish blocks that were published before the migration. Defaults to False.',
        )

    def handle(self, *args, **options):
        course_ids = options['course_ids']
        all_courses = options['all_courses']
        user_id = options['user_id']
        persist_publish_state = options['persist_publish_state']

        if not course_ids and not all_courses:
            raise CommandError('Either --course-ids or --all-courses argument should be provided.')
        if course_ids and all_courses:
            raise CommandError('Only one of --course-ids or --all-courses argument should be provided.')

        try:
            User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise CommandError(f'No user found with id: {user_id}')  # pylint: disable=raise-missing-from  # noqa: B904

        if all_courses:
            raw_course_ids = CourseOverview.get_all_course_keys()
        else:
            raw_course_ids = [course_id.strip() for course_id in course_ids.split(',') if course_id.strip()]

        course_keys = []
        for raw_course_id in raw_course_ids:
            try:
                course_key = CourseKey.from_string(str(raw_course_id))
            except InvalidKeyError:
                log.error(f'Invalid course key: {raw_course_id}, skipping..')
                continue
            if not all_courses and not modulestore().get_course(course_key):
                log.warning(f'Course not found: {course_key}, skipping..')
                continue
            course_keys.append(course_key)

        for course_key in course_keys:
            log.info(f'Dispatching legacy library migration for course: {course_key}')
            migrate_course_legacy_library_blocks_to_item_bank.delay(user_id, str(course_key), persist_publish_state)

        log.info(f'Dispatched migration for {len(course_keys)} course(s)')
