"""
Management command to update user stats for all users in a course.
"""
import logging  # noqa: I001

from django.core.management.base import BaseCommand
from opaque_keys.edx.keys import CourseKey

import openedx.core.djangoapps.django_comment_common.comment_client.course as cc

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Invoke with:

        python manage.py lms update_user_discussion_stats <course_id>

    """
    help = 'Update the user stats for all users for a particular course.'

    def add_arguments(self, parser):
        parser.add_argument('course_id', help="ID of the Course to update user stats for")

    def handle(self, *args, **options):
        course_id = options['course_id']
        course_key = CourseKey.from_string(course_id)
        data = cc.update_course_users_stats(course_key)
        log.info(f"Updated user stats for {data['user_count']} users in {course_key}")
