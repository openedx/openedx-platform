"""
Send segment events for failed learners.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.utils import timezone

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.track import segment
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)

PAID_ENROLLMENT_MODES = [
    CourseMode.MASTERS,
    CourseMode.VERIFIED,
    CourseMode.CREDIT_MODE,
    CourseMode.PROFESSIONAL,
    CourseMode.NO_ID_PROFESSIONAL_MODE,
]
EVENT_NAME = 'edx.course.learner.failed'


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py lms send_segment_events_for_failed_learners
    """

    help = 'Send segment events for failed learners.'

    def add_arguments(self, parser):
        """
        Entry point to add arguments.
        """
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Dry Run, print log messages without firing the segment event.',
        )

    def get_courses(self):
        """
        Find all courses where end date has passed 31 days ago course grade override date is course.end + 30 days
        but we are adding grace period of 1 day to mitigate any edge cases due to last minute grade override.
        """
        thirty_one_days_ago = timezone.now().date() - timedelta(days=31)
        courses = CourseOverview.objects.exclude(end__isnull=True).filter(end__date=thirty_one_days_ago)
        thirty_one_days_ago_ended_course_keys = [str(course) for course in courses.values_list('id', flat=True)]
        log.info(f"Found {thirty_one_days_ago_ended_course_keys} courses that were ended on [{thirty_one_days_ago}]")
        return courses

    def get_failed_enrollment_and_user_ids(self, course):
        """
        Get list of all the enrolled users that failed the given course. This method will only consider paid enrolments.

        Arguments:
            course (CourseOverview): Course overview instance whose failed enrolments should be returned.

        Returns:
            (generator): An iterator with paginated user ids, each iteration will return 500 item list of user ids.
        """
        failed_grade_user_ids = PersistentCourseGrade.objects.filter(
            passed_timestamp__isnull=True,
            course_id=course.id,
        ).values_list('user_id', flat=True)

        paginator = Paginator(failed_grade_user_ids, 500)
        for page_number in paginator.page_range:
            page = paginator.page(page_number)

            failed_grade_user_ids = list(page.object_list)
            # exclude all non-paid enrollments
            failed_enrollment_and_user_ids = CourseEnrollment.objects.filter(
                course_id=course.id,
                user_id__in=failed_grade_user_ids,
                mode__in=PAID_ENROLLMENT_MODES,
                is_active=True
            ).values_list('id', 'user_id')
            failed_enrollment_and_user_ids = list(failed_enrollment_and_user_ids)

            yield failed_enrollment_and_user_ids

    def handle(self, *args, **options):
        """
        Command's entery point.
        """
        should_fire_event = not options['dry_run']

        log_prefix = '[SEND_SEGMENT_EVENTS_FOR_FAILED_LEARNERS]'
        if not should_fire_event:
            log_prefix = '[DRY RUN]'

        stats = {
            'failed_course_enrollment_ids': {},
        }

        log.info(f'{log_prefix} Command started.')

        for course in self.get_courses():
            # course metadata for event
            course_org = course.org
            course_id = str(course.id)
            course_display_name = course.display_name

            stats['failed_course_enrollment_ids'][course_id] = []

            for enrollment_and_user_ids in self.get_failed_enrollment_and_user_ids(course):
                # for each failed enrollment, send a segment event
                for failed_enrollment_id, failed_user_id in enrollment_and_user_ids:
                    event_properties = {
                        'LMS_ENROLLMENT_ID': failed_enrollment_id,
                        'COURSE_TITLE': course_display_name,
                        'COURSE_ORG_NAME': course_org,
                    }
                    if should_fire_event:
                        segment.track(failed_user_id, EVENT_NAME, event_properties)

                    stats['failed_course_enrollment_ids'][course_id].append(failed_enrollment_id)

                    log.info(
                        "{} Segment event fired for failed learner. Event: [{}], Data: [{}]".format(  # noqa: UP032
                            log_prefix,
                            EVENT_NAME,
                            event_properties
                        )
                    )

        log.info(f"{log_prefix} Command completed. Stats: [{stats['failed_course_enrollment_ids']}]")
